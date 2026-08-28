"""
schemas.py — request/response contracts.

Validation philosophy: a malformed row must produce a *specific, actionable*
error naming the visit, the field, and what was wrong. Never a generic 500.
We validate rows individually so one bad row does not sink the whole file.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PaymentMode(str, Enum):
    CASH = "cash"
    CARD = "card"
    UPI = "upi"


# ── Input ────────────────────────────────────────────────────────────────────

class LineItem(BaseModel):
    drug_name: str = Field(min_length=1)
    qty: int = Field(gt=0, description="Quantity must be positive")
    unit_price_paise: int = Field(ge=0, description="Integer paise, never float")

    @field_validator("drug_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        # Trim/upper only. We deliberately do NOT fuzzy-merge near-duplicate
        # names here — see analytics.detect_name_anomalies for why.
        return v.strip().upper()


class VisitRecord(BaseModel):
    clinic_id: str = Field(min_length=1)
    visit_id: str = Field(min_length=1)
    timestamp: datetime
    doctor_id: str | None = None          # brief: not required for outputs
    line_items: list[LineItem] = Field(min_length=1)
    payment_mode: PaymentMode
    amount_paid_paise: int
    discount_paise: int = Field(default=0, ge=0)
    is_refund: bool = False

    @property
    def gross_paise(self) -> int:
        return sum(li.qty * li.unit_price_paise for li in self.line_items)

    @property
    def net_billed_paise(self) -> int:
        """Gross less discount. This is what the patient actually owes."""
        return self.gross_paise - self.discount_paise


# ── Rejections ───────────────────────────────────────────────────────────────

class RowError(BaseModel):
    """One rejected row, with enough detail for the front desk to fix it."""
    row_index: int
    visit_id: str | None
    field: str
    message: str


# ── Reconciliation output ────────────────────────────────────────────────────

class ModeBreakdown(BaseModel):
    mode: str
    billed_paise: int
    collected_paise: int
    outstanding_paise: int
    refunds_paise: int


class Reconciliation(BaseModel):
    clinic_id: str | None
    date: str | None
    visit_count: int
    refund_count: int
    total_billed_paise: int
    total_collected_paise: int
    outstanding_paise: int
    refunds_paise: int
    collection_rate_pct: float | None
    by_payment_mode: list[ModeBreakdown]


# ── Analytics output ─────────────────────────────────────────────────────────

class HourBucket(BaseModel):
    hour: int                 # 0-23, UTC as supplied
    label: str                # "1pm"
    revenue_paise: int
    visit_count: int


class DrugRank(BaseModel):
    rank: int
    drug_name: str
    qty: int | None = None
    revenue_paise: int | None = None


class NameAnomaly(BaseModel):
    """Two drug names that look like the same drug typed differently."""
    kept: str
    suspected_variant: str
    variant_qty: int
    note: str


class Analytics(BaseModel):
    revenue_by_hour: list[HourBucket]
    peak_hour: HourBucket | None
    top_by_quantity: list[DrugRank]
    top_by_revenue: list[DrugRank]
    name_anomalies: list[NameAnomaly]


# ── Narrative output ─────────────────────────────────────────────────────────

class TracedFigure(BaseModel):
    """One number in the narrative, mapped back to the field it came from."""
    display: str
    source_field: str


class Narrative(BaseModel):
    text: str
    traced_figures: list[TracedFigure]
    status: Literal["success", "degraded", "unavailable"]
    status_detail: str | None = None


# ── Top-level ────────────────────────────────────────────────────────────────

class ReportResponse(BaseModel):
    reconciliation: Reconciliation
    analytics: Analytics
    narrative: Narrative | None = None
    rejected_rows: list[RowError]
    accepted_row_count: int
