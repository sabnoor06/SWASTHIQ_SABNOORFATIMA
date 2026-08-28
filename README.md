# EOD Billing & Analytics Agent

SwasthiQ SDE Intern assignment. A Python REST API that ingests a clinic's daily
billing log and produces a deterministic reconciliation, analytics, and an
LLM-generated narrative that is **verified** against those numbers — plus a
React frontend with the three required screens.

```
backend/    Python REST API (FastAPI)
frontend/   React application (Vite)
```

---

## Quick start

**Backend** — runs on `http://localhost:8000`

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # add GEMINI_API_KEY (optional — see below)
uvicorn app.main:app --reload
```

**Frontend** — runs on `http://localhost:5173`

```bash
cd frontend
npm install
npm run dev
```

**Tests**

```bash
cd backend && pytest -v          # 35 tests
```

The app runs without any LLM key. With no key configured the narrative layer
degrades to a deterministic template and labels itself `FALLBACK` in the UI —
it never fabricates and never crashes.

---

## Architecture

```
POST /api/report
      │
      ▼
┌──────────────────────┐
│ parse_rows()         │  per-row validation; one bad row is rejected
│ schemas.py           │  with a specific error, the rest still process
└──────────┬───────────┘
           │ accepted records            rejected rows ──┐
           ▼                                             │
┌──────────────────────┐  ┌──────────────────────┐       │
│ reconcile()          │  │ analyse()            │       │
│ reconciliation.py    │  │ analytics.py         │       │
│ NEVER calls an LLM   │  │ NEVER calls an LLM   │       │
└──────────┬───────────┘  └──────────┬───────────┘       │
           └───────────┬─────────────┘                   │
                       ▼  GROUND TRUTH                    │
           ┌───────────────────────────┐                  │
           │ build_allowlist()         │  every legal figure
           │ + source_field for each   │  + where it came from
           └───────────┬───────────────┘                  │
                       ▼                                  │
                  ┌─────────┐                             │
                  │   LLM   │  untrusted text generator    │
                  └────┬────┘                             │
                       ▼                                  │
           ┌───────────────────────────┐                  │
           │ verify_grounding()        │  scan every number
           │ fails → template fallback │  reject if unlisted
           └───────────┬───────────────┘                  │
                       ▼                                  │
                  ReportResponse ◄────────────────────────┘
```

The deterministic layer is ground truth and makes no model call. The narrative
layer is the only place an LLM is used, and its output is treated as untrusted
until verified.

---

## API contracts

### `GET /api/days`
Lists the sample clinic-days bundled with the service.
```json
{ "days": ["2026-07-25", "2026-07-26", "2026-07-27"] }
```

### `GET /api/report/{date}?llm=true`
Full report for a stored day. `llm=false` skips the model call entirely.

### `POST /api/report?llm=true`
Body: a JSON array of visit records. Returns the same `ReportResponse`.

### `POST /api/reconcile`
Deterministic layer only — guaranteed to make no model call. Useful for
verifying the narrative independently.

**`ReportResponse`**
```jsonc
{
  "reconciliation": {
    "clinic_id": "CLN-KNP-014",
    "date": "2026-07-27",
    "visit_count": 18,
    "refund_count": 0,
    "total_billed_paise": 319000,
    "total_collected_paise": 317200,
    "outstanding_paise": 1800,
    "refunds_paise": 0,
    "collection_rate_pct": 99.4,
    "by_payment_mode": [
      { "mode": "card", "billed_paise": 83500, "collected_paise": 82700,
        "outstanding_paise": 800, "refunds_paise": 0 }
    ]
  },
  "analytics": {
    "revenue_by_hour": [{ "hour": 13, "label": "1pm",
                          "revenue_paise": 75500, "visit_count": 3 }],
    "peak_hour":       { "hour": 13, "label": "1pm", "revenue_paise": 75500 },
    "top_by_quantity": [{ "rank": 1, "drug_name": "OMEPRAZOLE", "qty": 19 }],
    "top_by_revenue":  [{ "rank": 1, "drug_name": "ATORVASTATIN",
                          "revenue_paise": 120000 }],
    "name_anomalies":  [{ "kept": "PARACETAMOL",
                          "suspected_variant": "PARACETMOL", "variant_qty": 2 }]
  },
  "narrative": {
    "text": "...",
    "traced_figures": [{ "display": "₹3,190",
                         "source_field": "reconciliation.total_billed_paise" }],
    "status": "success",
    "status_detail": "All figures verified against the deterministic report."
  },
  "rejected_rows": [
    { "row_index": 18, "visit_id": "V-20260727-019", "field": "payment_mode",
      "message": "Required field 'payment_mode' is missing from this row." }
  ],
  "accepted_row_count": 18
}
```

Money is **integer paise everywhere** in the API. Rupee conversion happens only
in the UI formatter and in narrative text — never in arithmetic.

---

## How the narrative stays grounded

A prompt saying "don't invent numbers" is a request. This service verifies
instead:

1. **Allowlist** — `build_allowlist()` derives every figure the narrative may
   legally state from the report, each paired with the `source_field` it came
   from. This same list is what the UI renders as the *Traced Figures* panel.
2. **Constrained prompt** — the model receives only those figures and is told
   to copy them exactly, plus an explicit list of forbidden metrics.
3. **Verification** — `verify_grounding()` regex-scans the returned text for
   every numeric token and checks each against the allowlist (comma-grouped and
   bare forms both accepted).
4. **Rejection** — if any number is unaccounted for, the model output is
   **discarded** and a deterministic template is used instead. Status flips to
   `degraded` with the offending figures named.

The service degrades to a template rather than ever emitting an ungrounded
figure. Four failure modes are handled distinctly and all fall back safely:
no key configured, model unreachable, off-schema response, and grounded-check
failure.

**Uncomputable metrics.** Cost price is never supplied, so profit and margin
cannot be derived. These are named as forbidden in the prompt, and the fallback
text says so plainly rather than presenting revenue as if it were profit.

**Malformed model responses** are recovered where safe: fenced JSON, JSON with
a preamble. Plain prose, wrong keys, and non-string summaries are rejected
outright. Six tests cover this.

---

## Data consistency on update

Reports are keyed by clinic date and written with
`INSERT ... ON CONFLICT(date) DO UPDATE` inside a single transaction, so
re-ingesting a day replaces it atomically rather than accumulating duplicates.
A concurrent reader sees either the whole previous report or the whole new one,
never a mix. Writes are guarded by a lock since SQLite is opened with
`check_same_thread=False` for the ASGI worker.

The report is stored as serialised JSON with integer paise intact — no float
round-trip at any point.

---

## Accounting decisions

The brief leaves these open, so they are stated explicitly rather than assumed.

| Decision | Rule applied | Why |
|---|---|---|
| Discount | `billed = gross − discount` | A discount is a price reduction, not an unpaid balance. A discounted visit paid in full is fully collected — treating the discount as outstanding would overstate debt. |
| Outstanding | `Σ max(0, billed − paid)` per visit | Floored per visit so an overpayment on one cannot silently mask a shortfall on another. |
| Refunds | Reported separately; not netted into billed or collected | A refund settles a *prior* day's sale. Netting it into today would misstate today's trading. |
| Refund units | Excluded from both drug rankings | Counting them would double-count stock that was already counted on the original sale day. |
| Hour bucketing | On `amount_paid_paise` | "Which hour did the most business" is a cash-flow question, not a billing one. |
| Misspelled drugs | Flagged, **not** merged | See below. |

### The PARACETMOL decision

The 27 Jul data contains both `PARACETAMOL` (11 units) and `PARACETMOL`
(2 units). Merging them changes the top-by-quantity ranking — merged
Paracetamol reaches 13 units and moves above Amoxicillin at 11.

The service **flags but does not merge**. The deterministic layer is ground
truth and must report what the data actually says; silently rewriting a drug
name inside a billing reconciliation is exactly the kind of invisible data
mutation that makes a clinical system untrustworthy. Detection is Levenshtein
distance ≤ 2 with a quantity asymmetry, surfaced as a `name_anomalies` entry
and shown as a data-quality panel in the UI so the clinic can fix the source
record.

---

## Edge cases in the sample data

All three clinic-days run through the same pipeline with no special-casing.

| Day | Edge case | Handling |
|---|---|---|
| 27 Jul | `V-...-019` missing `payment_mode` | Rejected with a specific field-level error; the other 18 rows still process |
| 27 Jul | 3 visits underpaid (₹5, ₹5, ₹8) | ₹18 outstanding, split correctly by mode |
| 27 Jul | Timestamps out of chronological order | Bucketed then sorted; ordering is never assumed |
| 27 Jul | `PARACETMOL` misspelling | Flagged as an anomaly, totals not merged |
| 26 Jul | Empty file, zero rows | Valid zero report; `collection_rate_pct` is `null`, not a divide-by-zero |
| 25 Jul | Every row a refund, all amounts negative | `refunds_paise` positive magnitude; billed and collected stay at 0 |

---

## Verified figures — 27 Jul 2026

| Metric | Value |
|---|---|
| Total billed | ₹3,190.00 (319,000 paise) |
| Total collected | ₹3,172.00 (317,200 paise) |
| Outstanding | ₹18.00 (1,800 paise) |
| Collection rate | 99.4% |
| Visits | 18 billed, 1 row rejected |
| Peak hour | 1pm — ₹755.00 |
| Top by quantity | OMEPRAZOLE (19 units) |
| Top by revenue | ATORVASTATIN (₹1,200.00) |

The two rankings disagree at the top, which is the point of keeping them
separate — the highest-volume drug is not the highest-earning one.

---

## Tests

35 tests, `cd backend && pytest -v`:

- **9** reconciliation and validation on the happy path
- **5** empty-day behaviour
- **4** all-refund-day behaviour
- **4** row-level validation and actionable errors
- **6** narrative grounding, including a deliberately invented figure that must be caught
- **6** malformed model responses
- **1** money-arithmetic invariant

---

## Deployment

**Backend** — any container host:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Frontend** — Vercel or Netlify:
```
Build command:      npm run build
Publish directory:  dist
Env var:            VITE_API_BASE=https://<your-backend-host>
```
Set `CORS_ORIGINS` on the backend to the deployed frontend origin.

---

## Known limitations

- Hour bucketing uses the UTC hour as supplied; a clinic in IST would want a
  configurable timezone before this is production-real.
- Anomaly detection is edit-distance based and would need a drug dictionary to
  distinguish genuine similar names from typos at scale.
- Refunds are not matched back to the original visit — the schema has no
  reference field for it, so cross-day linkage isn't possible from this input.
- Storage is SQLite per the brief's constraint; no migration tooling.

## AI tool usage

Claude was used for architecture review, test-case brainstorming, and README
drafting. The grounding-verification approach, accounting rules, and edge-case
handling decisions are documented above with their rationale.
