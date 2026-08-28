"""
narrative.py — the only module permitted to call an LLM.

Design stance: the model is treated as an untrusted text generator, not as a
source of facts. The pipeline is:

    report (ground truth)
        -> build an ALLOWLIST of every figure that may legally appear
        -> prompt the model with only those figures
        -> VERIFY every number in the returned text against the allowlist
        -> if any number is unaccounted for, DISCARD the model output and
           fall back to a deterministic template

Verification is the point. A prompt that says "don't invent numbers" is a
request; scanning the output and rejecting it is a guarantee. The service
degrades to a template rather than ever emitting an ungrounded figure.

Metrics that cannot be computed from the input (profit, margin — cost price is
never supplied) are named in the prompt as forbidden, and the fallback text
states plainly that they are unavailable rather than approximating them.
"""

from __future__ import annotations

import json
import os
import re

from .money import paise_to_rupee_compact
from .schemas import Analytics, Narrative, Reconciliation, TracedFigure

MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Metrics the data cannot support. Named explicitly so the model is told what
# it must refuse rather than left to guess.
UNCOMPUTABLE = ["profit", "margin", "cost price", "COGS", "net income"]


# ── Allowlist construction ───────────────────────────────────────────────────

def build_allowlist(
    rec: Reconciliation, ana: Analytics
) -> list[TracedFigure]:
    """
    Every figure the narrative is permitted to state, paired with the report
    field it came from. This doubles as the "Traced Figures" panel in the UI.
    """
    figs: list[TracedFigure] = [
        TracedFigure(display=paise_to_rupee_compact(rec.total_billed_paise),
                     source_field="reconciliation.total_billed_paise"),
        TracedFigure(display=paise_to_rupee_compact(rec.total_collected_paise),
                     source_field="reconciliation.total_collected_paise"),
        TracedFigure(display=paise_to_rupee_compact(rec.outstanding_paise),
                     source_field="reconciliation.outstanding_paise"),
        TracedFigure(display=paise_to_rupee_compact(rec.refunds_paise),
                     source_field="reconciliation.refunds_paise"),
        TracedFigure(display=str(rec.visit_count),
                     source_field="reconciliation.visit_count"),
        TracedFigure(display=str(rec.refund_count),
                     source_field="reconciliation.refund_count"),
    ]
    if rec.collection_rate_pct is not None:
        figs.append(TracedFigure(
            display=f"{rec.collection_rate_pct:g}%",
            source_field="reconciliation.collection_rate_pct"))

    for mb in rec.by_payment_mode:
        figs.append(TracedFigure(
            display=paise_to_rupee_compact(mb.collected_paise),
            source_field=f"reconciliation.by_payment_mode[{mb.mode}].collected_paise"))

    if ana.peak_hour:
        figs.append(TracedFigure(
            display=ana.peak_hour.label,
            source_field="analytics.peak_hour.label"))
        figs.append(TracedFigure(
            display=paise_to_rupee_compact(ana.peak_hour.revenue_paise),
            source_field="analytics.peak_hour.revenue_paise"))

    for d in ana.top_by_quantity[:3]:
        figs.append(TracedFigure(
            display=f"{d.drug_name} ({d.qty} units)",
            source_field=f"analytics.top_by_quantity[{d.rank}]"))
    for d in ana.top_by_revenue[:3]:
        figs.append(TracedFigure(
            display=f"{d.drug_name} ({paise_to_rupee_compact(d.revenue_paise)})",
            source_field=f"analytics.top_by_revenue[{d.rank}]"))

    return figs


def _permitted_number_tokens(figs: list[TracedFigure], rec: Reconciliation,
                             ana: Analytics) -> set[str]:
    """
    The set of numeric tokens allowed to appear in narrative text.

    Includes each figure's digits in both comma-grouped and bare form, since
    a model may write "42,850" or "42850". Also allows the date and small
    ordinals that are structural rather than factual claims.
    """
    allowed: set[str] = set()

    def add_number(n: int | float) -> None:
        allowed.add(f"{int(n):,}")
        allowed.add(str(int(n)))
        if isinstance(n, float) and n != int(n):
            allowed.add(f"{n:g}")

    for paise in (rec.total_billed_paise, rec.total_collected_paise,
                  rec.outstanding_paise, rec.refunds_paise):
        add_number(paise // 100)
        add_number(paise)
    add_number(rec.visit_count)
    add_number(rec.refund_count)
    if rec.collection_rate_pct is not None:
        add_number(rec.collection_rate_pct)
        allowed.add(f"{rec.collection_rate_pct:g}")

    for mb in rec.by_payment_mode:
        for paise in (mb.billed_paise, mb.collected_paise,
                      mb.outstanding_paise, mb.refunds_paise):
            add_number(paise // 100)

    if ana.peak_hour:
        add_number(ana.peak_hour.revenue_paise // 100)
        add_number(ana.peak_hour.visit_count)
        # hour labels like "1pm" contribute their digits
        allowed.add(str(ana.peak_hour.hour))
        allowed.add(str(ana.peak_hour.hour % 12 or 12))
    for h in ana.revenue_by_hour:
        allowed.add(str(h.hour))
        allowed.add(str(h.hour % 12 or 12))

    for d in ana.top_by_quantity:
        if d.qty is not None:
            add_number(d.qty)
        add_number(d.rank)
    for d in ana.top_by_revenue:
        if d.revenue_paise is not None:
            add_number(d.revenue_paise // 100)
        add_number(d.rank)

    if rec.date:
        y, m, dd = rec.date.split("-")
        allowed.update({y, m, dd, str(int(m)), str(int(dd))})

    return allowed


NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def verify_grounding(
    text: str, allowed: set[str]
) -> tuple[bool, list[str]]:
    """
    Scan the narrative for numeric tokens and confirm each one is allowed.
    Returns (is_grounded, list_of_violations).
    """
    violations: list[str] = []
    for match in NUMBER_RE.finditer(text):
        token = match.group(0).rstrip(".").rstrip(",")
        if not token:
            continue
        bare = token.replace(",", "")
        if token in allowed or bare in allowed:
            continue
        # tolerate a trailing .00 on an allowed integer
        if bare.endswith(".00") and bare[:-3] in allowed:
            continue
        violations.append(token)
    return (len(violations) == 0), violations


# ── Prompt ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You write end-of-day summaries for Indian clinic owners, sent over WhatsApp.

ABSOLUTE RULES:
1. You may ONLY state figures that appear in the FIGURES list given to you.
   Copy them exactly as written. Do not round, rescale, recompute, or combine
   them into new numbers. Inventing or deriving a number is a critical failure.
2. Never state profit, margin, or cost. Cost price is not in the data. If it
   would be natural to mention profitability, write one short line saying cost
   data is not available so profit cannot be calculated.
3. Warm, plain, direct. No emoji spam, at most one. 4-6 short lines.
4. Reply with ONLY a JSON object: {"summary": "<your text>"}
   No markdown fences, no commentary outside the JSON."""


def build_user_prompt(rec: Reconciliation, ana: Analytics,
                      figs: list[TracedFigure]) -> str:
    lines = [f"DATE: {rec.date or 'unknown'}", f"CLINIC: {rec.clinic_id or 'unknown'}", "", "FIGURES (the only numbers you may use):"]
    for f in figs:
        lines.append(f"  {f.display}   <- {f.source_field}")
    lines += [
        "",
        f"Visits billed: {rec.visit_count}. Refunds processed: {rec.refund_count}.",
        f"FORBIDDEN metrics (not computable from this data): {', '.join(UNCOMPUTABLE)}.",
        "",
        "Write the WhatsApp summary now as JSON.",
    ]
    return "\n".join(lines)


# ── Model call ───────────────────────────────────────────────────────────────

def _call_llm(system: str, user: str) -> str | None:
    if GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(MODEL)
            resp = model.generate_content(
                f"{system}\n\n{user}",
                generation_config={"temperature": 0.2},
            )
            return resp.text
        except Exception as exc:  # network, quota, safety block, anything
            print(f"[narrative] Gemini call failed: {exc}")
    if OPENAI_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.2,
            )
            return resp.choices[0].message.content
        except Exception as exc:
            print(f"[narrative] OpenAI call failed: {exc}")
    return None


def _extract_summary(raw: str) -> str | None:
    """
    Pull the summary out of a model response that may be wrapped in fences,
    prefixed with chatter, or not be JSON at all. Returns None if unusable.
    """
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    # Try strict JSON first, then the first {...} block in the response.
    for candidate in (cleaned, _first_json_object(cleaned)):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            val = obj.get("summary")
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


# ── Deterministic fallback ───────────────────────────────────────────────────

def template_narrative(rec: Reconciliation, ana: Analytics) -> str:
    """
    Built entirely from the report with no model involved. Used when the LLM
    is unconfigured, unreachable, returns garbage, or fails grounding.
    """
    date = rec.date or "today"
    if rec.visit_count == 0 and rec.refund_count == 0:
        return (f"Summary for {date}: no billable visits were recorded. "
                f"Nothing to reconcile.")

    parts = [f"Here's your summary for {date}:"]
    parts.append(
        f"{paise_to_rupee_compact(rec.total_billed_paise)} billed across "
        f"{rec.visit_count} visits, "
        f"{paise_to_rupee_compact(rec.total_collected_paise)} collected"
        + (f" ({rec.collection_rate_pct:g}%)." if rec.collection_rate_pct is not None else ".")
    )
    if rec.outstanding_paise:
        parts.append(
            f"{paise_to_rupee_compact(rec.outstanding_paise)} is still outstanding.")
    if rec.refunds_paise:
        parts.append(
            f"{paise_to_rupee_compact(rec.refunds_paise)} refunded across "
            f"{rec.refund_count} visits.")
    if ana.peak_hour:
        parts.append(
            f"Busiest hour: {ana.peak_hour.label}, with "
            f"{paise_to_rupee_compact(ana.peak_hour.revenue_paise)} in revenue.")
    if ana.top_by_quantity:
        d = ana.top_by_quantity[0]
        parts.append(f"Top mover by quantity: {d.drug_name} ({d.qty} units).")
    if ana.top_by_revenue:
        d = ana.top_by_revenue[0]
        parts.append(
            f"Top by revenue: {d.drug_name} "
            f"({paise_to_rupee_compact(d.revenue_paise)}).")
    parts.append("Note: cost data isn't available, so this is revenue, "
                 "not profit — flagging rather than estimating.")
    return "\n".join(parts)


# ── Entry point ──────────────────────────────────────────────────────────────

def generate_narrative(
    rec: Reconciliation, ana: Analytics, use_llm: bool = True
) -> Narrative:
    figs = build_allowlist(rec, ana)
    allowed = _permitted_number_tokens(figs, rec, ana)

    if not use_llm or not (GEMINI_API_KEY or OPENAI_API_KEY):
        return Narrative(
            text=template_narrative(rec, ana),
            traced_figures=figs,
            status="degraded",
            status_detail=("No LLM key configured — deterministic template used. "
                           "Every figure is still traced to the report."),
        )

    raw = _call_llm(SYSTEM_PROMPT, build_user_prompt(rec, ana, figs))
    if raw is None:
        return Narrative(
            text=template_narrative(rec, ana), traced_figures=figs,
            status="degraded",
            status_detail="Model unreachable — deterministic template used.",
        )

    summary = _extract_summary(raw)
    if summary is None:
        return Narrative(
            text=template_narrative(rec, ana), traced_figures=figs,
            status="degraded",
            status_detail=("Model returned an off-schema response that could not "
                           "be parsed as JSON — deterministic template used."),
        )

    grounded, violations = verify_grounding(summary, allowed)
    if not grounded:
        return Narrative(
            text=template_narrative(rec, ana), traced_figures=figs,
            status="degraded",
            status_detail=("Model output contained figures absent from the report "
                           f"({', '.join(violations[:5])}) and was rejected. "
                           "Deterministic template used instead."),
        )

    return Narrative(
        text=summary, traced_figures=figs, status="success",
        status_detail="All figures verified against the deterministic report.",
    )
