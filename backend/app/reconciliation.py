"""
reconciliation.py — the deterministic layer. NEVER calls an LLM.

This module is ground truth. Everything downstream (analytics, narrative) is
checked against what this produces.

Money semantics, stated explicitly because the brief leaves the choice open:

  billed      = sum of (gross line items - discount) for non-refund visits.
                Discount is a price reduction, not an outstanding balance:
                a discounted visit that is paid in full is fully collected.
  collected   = sum of amount_paid_paise for non-refund visits.
  outstanding = billed - collected, floored at 0 per visit so that an
                overpayment on one visit cannot mask a shortfall on another.
  refunds     = sum of abs(amount_paid_paise) for is_refund visits, reported
                as a separate line. Refunds are NOT subtracted from billed or
                collected, because they settle a *prior* day's sale; netting
                them into today would misstate today's trading.
"""

from collections import defaultdict

from pydantic import ValidationError

from .schemas import (
    ModeBreakdown,
    Reconciliation,
    RowError,
    VisitRecord,
)


def parse_rows(raw: list[dict]) -> tuple[list[VisitRecord], list[RowError]]:
    """
    Validate each row independently. One malformed row is rejected with a
    specific error; the rest of the day still processes.
    """
    accepted: list[VisitRecord] = []
    rejected: list[RowError] = []

    seen_visit_ids: set[str] = set()

    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            rejected.append(RowError(
                row_index=idx, visit_id=None, field="<row>",
                message=f"Expected a JSON object, got {type(row).__name__}.",
            ))
            continue

        vid = row.get("visit_id")

        try:
            record = VisitRecord.model_validate(row)
        except ValidationError as exc:
            for err in exc.errors():
                field = ".".join(str(p) for p in err["loc"]) or "<row>"
                rejected.append(RowError(
                    row_index=idx,
                    visit_id=vid if isinstance(vid, str) else None,
                    field=field,
                    message=_humanise(field, err),
                ))
            continue

        # Duplicate visit_id is a real-world double-submit; reject the second.
        if record.visit_id in seen_visit_ids:
            rejected.append(RowError(
                row_index=idx, visit_id=record.visit_id, field="visit_id",
                message=(f"Duplicate visit_id '{record.visit_id}' — already "
                         f"present earlier in this file. Row skipped."),
            ))
            continue

        seen_visit_ids.add(record.visit_id)
        accepted.append(record)

    return accepted, rejected


def _humanise(field: str, err: dict) -> str:
    """Turn a pydantic error into something a clinic operator could act on."""
    etype = err.get("type", "")
    if etype == "missing":
        return f"Required field '{field}' is missing from this row."
    if etype == "enum":
        return (f"'{field}' must be one of: cash, card, upi. "
                f"Got {err.get('input')!r}.")
    if "int" in etype:
        return (f"'{field}' must be an integer (paise, not rupees). "
                f"Got {err.get('input')!r}.")
    if etype in ("greater_than", "greater_than_equal"):
        return f"'{field}' is out of range: {err.get('msg')}. Got {err.get('input')!r}."
    if "datetime" in etype:
        return (f"'{field}' must be an ISO-8601 UTC timestamp "
                f"(e.g. 2026-07-27T09:00:00Z). Got {err.get('input')!r}.")
    if etype == "too_short":
        return f"'{field}' cannot be empty."
    return f"'{field}': {err.get('msg', 'invalid value')}."


def reconcile(records: list[VisitRecord]) -> Reconciliation:
    """Compute the EOD reconciliation. Pure integer arithmetic."""
    sales = [r for r in records if not r.is_refund]
    refunds = [r for r in records if r.is_refund]

    total_billed = sum(r.net_billed_paise for r in sales)
    total_collected = sum(r.amount_paid_paise for r in sales)

    # Floor per visit: an overpayment on one visit must not offset a
    # shortfall on another. Outstanding is money genuinely still owed.
    outstanding = sum(
        max(0, r.net_billed_paise - r.amount_paid_paise) for r in sales
    )
    refunds_total = sum(abs(r.amount_paid_paise) for r in refunds)

    by_mode: dict[str, dict[str, int]] = defaultdict(
        lambda: {"billed": 0, "collected": 0, "outstanding": 0, "refunds": 0}
    )
    for r in sales:
        m = by_mode[r.payment_mode.value]
        m["billed"] += r.net_billed_paise
        m["collected"] += r.amount_paid_paise
        m["outstanding"] += max(0, r.net_billed_paise - r.amount_paid_paise)
    for r in refunds:
        by_mode[r.payment_mode.value]["refunds"] += abs(r.amount_paid_paise)

    breakdown = [
        ModeBreakdown(
            mode=mode,
            billed_paise=v["billed"],
            collected_paise=v["collected"],
            outstanding_paise=v["outstanding"],
            refunds_paise=v["refunds"],
        )
        for mode, v in sorted(by_mode.items())
    ]

    rate = round(total_collected / total_billed * 100, 1) if total_billed else None

    clinic_id = records[0].clinic_id if records else None
    date = records[0].timestamp.date().isoformat() if records else None

    return Reconciliation(
        clinic_id=clinic_id,
        date=date,
        visit_count=len(sales),
        refund_count=len(refunds),
        total_billed_paise=total_billed,
        total_collected_paise=total_collected,
        outstanding_paise=outstanding,
        refunds_paise=refunds_total,
        collection_rate_pct=rate,
        by_payment_mode=breakdown,
    )
