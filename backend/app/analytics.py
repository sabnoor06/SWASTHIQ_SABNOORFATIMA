"""
analytics.py — the second half of the deterministic layer. NEVER calls an LLM.

Three outputs:
  1. revenue by hour of day (buckets present in the data, in clock order)
  2. top medicines by QUANTITY
  3. top medicines by REVENUE
Rankings 2 and 3 are deliberately separate — they disagree, and that
disagreement is the interesting signal for the clinic owner.

Refund rows are excluded from all three: they represent a prior day's sale
being unwound, so counting their units or revenue today would double-count.
"""

from collections import defaultdict

from .schemas import Analytics, DrugRank, HourBucket, NameAnomaly, VisitRecord

TOP_N = 5


def _hour_label(hour: int) -> str:
    """13 -> '1pm', 9 -> '9am', 0 -> '12am'."""
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12
    if display == 0:
        display = 12
    return f"{display}{suffix}"


def revenue_by_hour(records: list[VisitRecord]) -> list[HourBucket]:
    """
    Bucket collected revenue by hour of the timestamp.

    Uses amount_paid_paise (money actually taken), not billed — "which hour did
    the most business" is a cash-flow question. Timestamps in the source file
    are NOT guaranteed to be in order, so we bucket then sort by hour.
    """
    buckets: dict[int, dict[str, int]] = defaultdict(
        lambda: {"revenue": 0, "visits": 0}
    )
    for r in records:
        if r.is_refund:
            continue
        h = r.timestamp.hour
        buckets[h]["revenue"] += r.amount_paid_paise
        buckets[h]["visits"] += 1

    return [
        HourBucket(
            hour=h,
            label=_hour_label(h),
            revenue_paise=v["revenue"],
            visit_count=v["visits"],
        )
        for h, v in sorted(buckets.items())
    ]


def _drug_totals(records: list[VisitRecord]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"qty": 0, "revenue": 0}
    )
    for r in records:
        if r.is_refund:
            continue
        for li in r.line_items:
            totals[li.drug_name]["qty"] += li.qty
            totals[li.drug_name]["revenue"] += li.qty * li.unit_price_paise
    return totals


def top_by_quantity(totals: dict[str, dict[str, int]]) -> list[DrugRank]:
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1]["qty"], kv[0]))
    return [
        DrugRank(rank=i, drug_name=name, qty=v["qty"])
        for i, (name, v) in enumerate(ordered[:TOP_N], start=1)
    ]


def top_by_revenue(totals: dict[str, dict[str, int]]) -> list[DrugRank]:
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1]["revenue"], kv[0]))
    return [
        DrugRank(rank=i, drug_name=name, revenue_paise=v["revenue"])
        for i, (name, v) in enumerate(ordered[:TOP_N], start=1)
    ]


def detect_name_anomalies(totals: dict[str, dict[str, int]]) -> list[NameAnomaly]:
    """
    Flag drug names that are probably the same drug typed two ways
    (the sample data contains PARACETAMOL and PARACETMOL).

    Deliberate choice: we FLAG but do not silently merge. The deterministic
    layer is ground truth and must report what the data actually says. Merging
    here would change the top-by-quantity ranking, and doing that invisibly
    inside a "reconciliation" endpoint is exactly the kind of quiet data
    rewrite that makes a billing system untrustworthy. Surfacing it lets the
    clinic fix the source record instead.

    Detection: Levenshtein distance <= 2 between names of similar length,
    where one name's total quantity is much smaller than the other's (the
    typo is the rarer spelling).
    """
    anomalies: list[NameAnomaly] = []
    names = list(totals.keys())

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if abs(len(a) - len(b)) > 2:
                continue
            if _levenshtein(a, b) > 2:
                continue
            qty_a, qty_b = totals[a]["qty"], totals[b]["qty"]
            kept, variant = (a, b) if qty_a >= qty_b else (b, a)
            anomalies.append(NameAnomaly(
                kept=kept,
                suspected_variant=variant,
                variant_qty=totals[variant]["qty"],
                note=(f"'{variant}' looks like a misspelling of '{kept}'. "
                      f"Both are reported separately above — totals were NOT "
                      f"merged. Correct the source record to combine them."),
            ))
    return anomalies


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def analyse(records: list[VisitRecord]) -> Analytics:
    hours = revenue_by_hour(records)
    totals = _drug_totals(records)
    peak = max(hours, key=lambda h: h.revenue_paise) if hours else None

    return Analytics(
        revenue_by_hour=hours,
        peak_hour=peak,
        top_by_quantity=top_by_quantity(totals),
        top_by_revenue=top_by_revenue(totals),
        name_anomalies=detect_name_anomalies(totals),
    )
