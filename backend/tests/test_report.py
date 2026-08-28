"""
Tests for the deterministic layer and the narrative grounding guard.

Coverage includes three non-happy-path days:
  2026-07-26  empty file (zero rows)
  2026-07-25  all rows are refunds, all amounts negative
  2026-07-27  one row missing payment_mode; three underpayments; a misspelled
              drug name that changes the top-by-quantity ranking
"""

import json
from pathlib import Path

import pytest

from app.analytics import analyse
from app.narrative import (
    _extract_summary,
    _permitted_number_tokens,
    build_allowlist,
    generate_narrative,
    template_narrative,
    verify_grounding,
)
from app.reconciliation import parse_rows, reconcile

DATA = Path(__file__).resolve().parent.parent / "data"


def load(date: str) -> list:
    return json.loads((DATA / f"billing_log_{date}.json").read_text())


def build(date: str):
    records, rejected = parse_rows(load(date))
    return reconcile(records), analyse(records), rejected


# ── Happy path: 27 Jul ───────────────────────────────────────────────────────

class TestJul27:
    def test_one_row_rejected_for_missing_payment_mode(self):
        _, _, rejected = build("2026-07-27")
        assert len(rejected) == 1
        err = rejected[0]
        assert err.visit_id == "V-20260727-019"
        assert err.field == "payment_mode"
        assert "missing" in err.message.lower()

    def test_rejection_message_is_actionable_not_generic(self):
        _, _, rejected = build("2026-07-27")
        msg = rejected[0].message
        assert "payment_mode" in msg
        assert msg != "Internal Server Error"
        assert len(msg) > 20

    def test_totals(self):
        rec, _, _ = build("2026-07-27")
        assert rec.visit_count == 18
        assert rec.total_billed_paise == 319_000
        assert rec.total_collected_paise == 317_200
        assert rec.outstanding_paise == 1_800
        assert rec.refunds_paise == 0

    def test_outstanding_equals_sum_of_three_shortfalls(self):
        rec, _, _ = build("2026-07-27")
        assert rec.outstanding_paise == 500 + 500 + 800

    def test_mode_split_sums_to_total(self):
        rec, _, _ = build("2026-07-27")
        assert sum(m.billed_paise for m in rec.by_payment_mode) == rec.total_billed_paise
        assert sum(m.collected_paise for m in rec.by_payment_mode) == rec.total_collected_paise

    def test_peak_hour_is_1pm(self):
        _, ana, _ = build("2026-07-27")
        assert ana.peak_hour.hour == 13
        assert ana.peak_hour.label == "1pm"
        assert ana.peak_hour.revenue_paise == 75_500

    def test_out_of_order_timestamps_are_bucketed_correctly(self):
        # File has 09:10 before 09:00; hours must still come out sorted.
        _, ana, _ = build("2026-07-27")
        hours = [h.hour for h in ana.revenue_by_hour]
        assert hours == sorted(hours)
        assert hours[0] == 9

    def test_quantity_and_revenue_rankings_differ(self):
        _, ana, _ = build("2026-07-27")
        assert ana.top_by_quantity[0].drug_name == "OMEPRAZOLE"
        assert ana.top_by_revenue[0].drug_name == "ATORVASTATIN"

    def test_rankings_carry_only_their_own_metric(self):
        _, ana, _ = build("2026-07-27")
        assert all(d.qty is not None and d.revenue_paise is None
                   for d in ana.top_by_quantity)
        assert all(d.revenue_paise is not None and d.qty is None
                   for d in ana.top_by_revenue)

    def test_misspelled_drug_is_flagged_not_silently_merged(self):
        _, ana, _ = build("2026-07-27")
        anomalies = {(a.kept, a.suspected_variant) for a in ana.name_anomalies}
        assert ("PARACETAMOL", "PARACETMOL") in anomalies

        names = [d.drug_name for d in ana.top_by_quantity]
        # Reported separately, so the merged total (13) never appears.
        assert "PARACETAMOL" in names
        qty = {d.drug_name: d.qty for d in ana.top_by_quantity}
        assert qty["PARACETAMOL"] == 11


# ── Non-happy path: empty day ────────────────────────────────────────────────

class TestEmptyDay:
    def test_empty_file_does_not_crash(self):
        rec, ana, rejected = build("2026-07-26")
        assert rec.visit_count == 0
        assert rejected == []

    def test_empty_day_zeroes_not_nulls(self):
        rec, _, _ = build("2026-07-26")
        assert rec.total_billed_paise == 0
        assert rec.total_collected_paise == 0
        assert rec.outstanding_paise == 0

    def test_collection_rate_is_none_not_divide_by_zero(self):
        rec, _, _ = build("2026-07-26")
        assert rec.collection_rate_pct is None

    def test_no_peak_hour_on_empty_day(self):
        _, ana, _ = build("2026-07-26")
        assert ana.peak_hour is None
        assert ana.revenue_by_hour == []

    def test_narrative_handles_empty_day(self):
        rec, ana, _ = build("2026-07-26")
        text = template_narrative(rec, ana)
        assert "no billable visits" in text.lower()


# ── Non-happy path: all-refund day ───────────────────────────────────────────

class TestRefundDay:
    def test_refunds_counted_separately_from_sales(self):
        rec, _, _ = build("2026-07-25")
        assert rec.refund_count == 3
        assert rec.visit_count == 0

    def test_refund_total_is_positive_magnitude(self):
        rec, _, _ = build("2026-07-25")
        assert rec.refunds_paise == 24_000 + 22_000 + 3_000
        assert rec.refunds_paise > 0

    def test_refunds_do_not_contaminate_billed_or_collected(self):
        rec, _, _ = build("2026-07-25")
        assert rec.total_billed_paise == 0
        assert rec.total_collected_paise == 0

    def test_refund_units_excluded_from_drug_rankings(self):
        _, ana, _ = build("2026-07-25")
        assert ana.top_by_quantity == []
        assert ana.top_by_revenue == []


# ── Validation ───────────────────────────────────────────────────────────────

class TestValidation:
    def test_bad_payment_mode_names_allowed_values(self):
        rows = [{
            "clinic_id": "C", "visit_id": "V1",
            "timestamp": "2026-07-27T09:00:00Z", "doctor_id": "D",
            "line_items": [{"drug_name": "X", "qty": 1, "unit_price_paise": 100}],
            "payment_mode": "bitcoin", "amount_paid_paise": 100,
            "discount_paise": 0, "is_refund": False,
        }]
        _, rejected = parse_rows(rows)
        assert len(rejected) == 1
        assert "cash" in rejected[0].message and "upi" in rejected[0].message

    def test_negative_quantity_rejected(self):
        rows = [{
            "clinic_id": "C", "visit_id": "V1",
            "timestamp": "2026-07-27T09:00:00Z", "doctor_id": "D",
            "line_items": [{"drug_name": "X", "qty": -2, "unit_price_paise": 100}],
            "payment_mode": "cash", "amount_paid_paise": 100,
            "discount_paise": 0, "is_refund": False,
        }]
        _, rejected = parse_rows(rows)
        assert len(rejected) == 1
        assert "qty" in rejected[0].field

    def test_duplicate_visit_id_second_row_rejected(self):
        row = {
            "clinic_id": "C", "visit_id": "V1",
            "timestamp": "2026-07-27T09:00:00Z", "doctor_id": "D",
            "line_items": [{"drug_name": "X", "qty": 1, "unit_price_paise": 100}],
            "payment_mode": "cash", "amount_paid_paise": 100,
            "discount_paise": 0, "is_refund": False,
        }
        accepted, rejected = parse_rows([row, dict(row)])
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert "duplicate" in rejected[0].message.lower()

    def test_one_bad_row_does_not_sink_the_file(self):
        good = {
            "clinic_id": "C", "visit_id": "V1",
            "timestamp": "2026-07-27T09:00:00Z", "doctor_id": "D",
            "line_items": [{"drug_name": "X", "qty": 1, "unit_price_paise": 100}],
            "payment_mode": "cash", "amount_paid_paise": 100,
            "discount_paise": 0, "is_refund": False,
        }
        accepted, rejected = parse_rows([good, {"visit_id": "V2"}])
        assert len(accepted) == 1
        assert len(rejected) >= 1


# ── Narrative grounding ──────────────────────────────────────────────────────

class TestGrounding:
    @pytest.fixture
    def ctx(self):
        rec, ana, _ = build("2026-07-27")
        figs = build_allowlist(rec, ana)
        return rec, ana, _permitted_number_tokens(figs, rec, ana)

    def test_template_narrative_is_self_grounded(self, ctx):
        rec, ana, allowed = ctx
        ok, violations = verify_grounding(template_narrative(rec, ana), allowed)
        assert ok, f"template emitted ungrounded figures: {violations}"

    def test_invented_number_is_caught(self, ctx):
        _, _, allowed = ctx
        ok, violations = verify_grounding(
            "Today you billed ₹99,999 across 18 visits.", allowed)
        assert not ok
        assert "99,999" in violations

    def test_real_figure_passes(self, ctx):
        _, _, allowed = ctx
        ok, _ = verify_grounding("₹3,190 billed across 18 visits.", allowed)
        assert ok

    def test_llm_disabled_degrades_not_crashes(self):
        rec, ana, _ = build("2026-07-27")
        nar = generate_narrative(rec, ana, use_llm=False)
        assert nar.status == "degraded"
        assert nar.text
        assert len(nar.traced_figures) > 0

    def test_every_traced_figure_names_a_source_field(self):
        rec, ana, _ = build("2026-07-27")
        for fig in build_allowlist(rec, ana):
            assert fig.source_field
            assert "." in fig.source_field

    def test_narrative_states_profit_is_unavailable(self):
        rec, ana, _ = build("2026-07-27")
        text = template_narrative(rec, ana)
        assert "profit" in text.lower()
        assert "cost data" in text.lower()


class TestMalformedModelResponse:
    def test_plain_prose_instead_of_json_is_rejected(self):
        assert _extract_summary("Sure! Here's your summary: sales were good.") is None

    def test_empty_response_rejected(self):
        assert _extract_summary("") is None
        assert _extract_summary("   ") is None

    def test_fenced_json_is_recovered(self):
        out = _extract_summary('```json\n{"summary": "All good today."}\n```')
        assert out == "All good today."

    def test_json_with_preamble_is_recovered(self):
        out = _extract_summary('Here you go:\n{"summary": "Day complete."}')
        assert out == "Day complete."

    def test_wrong_key_rejected(self):
        assert _extract_summary('{"narrative": "wrong key"}') is None

    def test_non_string_summary_rejected(self):
        assert _extract_summary('{"summary": 42}') is None
