"""
main.py — REST API surface.

Endpoints
  GET  /health                      liveness
  GET  /api/days                    list available sample clinic-days
  GET  /api/report/{date}           full report for a stored day
  POST /api/report                  upload a billing log, get a full report
  POST /api/reconcile               deterministic layer only (no LLM)

Every response is assembled from the deterministic layer first; the narrative
is attached last and can never alter the numbers above it.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .analytics import analyse
from .narrative import generate_narrative
from .reconciliation import parse_rows, reconcile
from .schemas import ReportResponse
from .store import ReportStore

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

app = FastAPI(
    title="SwasthiQ EOD Billing & Analytics Agent",
    version="1.0.0",
    description="Deterministic EOD reconciliation with a grounded LLM narrative.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

store = ReportStore(os.getenv("DB_PATH", ":memory:"))


def _build_report(raw: list, use_llm: bool) -> ReportResponse:
    """The one pipeline every entry point goes through."""
    records, rejected = parse_rows(raw)
    rec = reconcile(records)
    ana = analyse(records)
    narrative = generate_narrative(rec, ana, use_llm=use_llm)
    return ReportResponse(
        reconciliation=rec,
        analytics=ana,
        narrative=narrative,
        rejected_rows=rejected,
        accepted_row_count=len(records),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/days")
def list_days() -> dict:
    """Sample clinic-days shipped with the service."""
    if not DATA_DIR.exists():
        return {"days": []}
    days = sorted(
        p.stem.replace("billing_log_", "")
        for p in DATA_DIR.glob("billing_log_*.json")
    )
    return {"days": days}


@app.get("/api/report/{date}", response_model=ReportResponse)
def get_report(
    date: str,
    llm: bool = Query(True, description="Set false to skip the LLM call"),
) -> ReportResponse:
    path = DATA_DIR / f"billing_log_{date}.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(f"No billing log for {date}. "
                    f"Call GET /api/days to see what is available."),
        )
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Billing log for {date} is not valid JSON: {exc}",
        ) from exc
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=422,
            detail=(f"Billing log for {date} must be a JSON array of visit "
                    f"records, got {type(raw).__name__}."),
        )

    report = _build_report(raw, use_llm=llm)
    store.save(date, report)
    return report


@app.post("/api/report", response_model=ReportResponse)
def post_report(
    payload: list = Body(..., description="JSON array of visit records"),
    llm: bool = Query(True),
) -> ReportResponse:
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=422,
            detail=("Request body must be a JSON array of visit records, "
                    f"got {type(payload).__name__}."),
        )
    report = _build_report(payload, use_llm=llm)
    if report.reconciliation.date:
        store.save(report.reconciliation.date, report)
    return report


@app.post("/api/reconcile")
def post_reconcile(payload: list = Body(...)) -> dict:
    """Deterministic layer only. Guaranteed to make no model call."""
    records, rejected = parse_rows(payload)
    return {
        "reconciliation": reconcile(records).model_dump(),
        "analytics": analyse(records).model_dump(),
        "rejected_rows": [r.model_dump() for r in rejected],
        "accepted_row_count": len(records),
    }
