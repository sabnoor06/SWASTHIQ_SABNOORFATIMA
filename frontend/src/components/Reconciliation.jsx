import { rupees } from '../api'

export default function Reconciliation({ report }) {
  const r = report.reconciliation
  const rejected = report.rejected_rows ?? []

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>EOD Reconciliation</h1>
          <p className="subtitle">
            Mehta Multi-Specialty Clinic — Kanpur, Uttar Pradesh
          </p>
        </div>
        <span className="date-chip">{r.date ?? '—'}</span>
      </header>

      <section className="stat-row">
        <Stat
          label="Total Billed"
          value={rupees(r.total_billed_paise)}
          sub={`${r.visit_count} visits`}
        />
        <Stat
          label="Total Collected"
          value={rupees(r.total_collected_paise)}
          sub={
            r.collection_rate_pct != null
              ? `${r.collection_rate_pct}% of billed`
              : 'nothing billed'
          }
        />
        <Stat
          label="Outstanding"
          value={rupees(r.outstanding_paise)}
          sub={r.outstanding_paise ? 'still owed' : 'fully settled'}
          tone={r.outstanding_paise ? 'amber' : undefined}
        />
        <Stat
          label="Refunds"
          value={rupees(r.refunds_paise)}
          sub={`${r.refund_count} refund${r.refund_count === 1 ? '' : 's'}`}
          tone={r.refunds_paise ? 'red' : undefined}
        />
      </section>

      <section className="card">
        <h2>Payment Mode Breakdown</h2>
        {r.by_payment_mode.length === 0 ? (
          <p className="empty">No transactions recorded for this day.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Mode</th>
                <th>Billed</th>
                <th>Collected</th>
                <th>Outstanding</th>
                <th>Refunds</th>
              </tr>
            </thead>
            <tbody>
              {r.by_payment_mode.map((m) => (
                <tr key={m.mode}>
                  <td className="strong">{m.mode.toUpperCase()}</td>
                  <td>{rupees(m.billed_paise)}</td>
                  <td>{rupees(m.collected_paise)}</td>
                  <td>{rupees(m.outstanding_paise)}</td>
                  <td>{rupees(m.refunds_paise)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {rejected.length > 0 && (
        <section className="card card-warn">
          <h2>Rejected Rows ({rejected.length})</h2>
          <p className="hint">
            These rows were not counted in any figure above. Fix them at source
            and re-upload.
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>Row</th>
                <th>Visit</th>
                <th>Field</th>
                <th>Problem</th>
              </tr>
            </thead>
            <tbody>
              {rejected.map((e, i) => (
                <tr key={i}>
                  <td>{e.row_index}</td>
                  <td>{e.visit_id ?? '—'}</td>
                  <td className="mono">{e.field}</td>
                  <td>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}

function Stat({ label, value, sub, tone }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className={`stat-sub ${tone ? 'tone-' + tone : ''}`}>{sub}</div>
    </div>
  )
}
