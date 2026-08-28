import { rupees } from '../api'

export default function Analytics({ report }) {
  const a = report.analytics
  const r = report.reconciliation
  const max = Math.max(1, ...a.revenue_by_hour.map((h) => h.revenue_paise))

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Analytics</h1>
          <p className="subtitle">
            Mehta Multi-Specialty Clinic — {r.date ?? '—'}
          </p>
        </div>
      </header>

      <section className="card">
        <h2>Revenue by Hour of Day</h2>
        {a.revenue_by_hour.length === 0 ? (
          <p className="empty">No revenue recorded for this day.</p>
        ) : (
          <>
            {a.peak_hour && (
              <p className="peak-callout">
                Peak: {a.peak_hour.label} — {rupees(a.peak_hour.revenue_paise)}
              </p>
            )}
            <div className="chart">
              {a.revenue_by_hour.map((h) => {
                const isPeak = a.peak_hour && h.hour === a.peak_hour.hour
                return (
                  <div className="chart-col" key={h.hour}>
                    <div
                      className={`bar ${isPeak ? 'bar-peak' : ''}`}
                      style={{ height: `${(h.revenue_paise / max) * 100}%` }}
                      title={`${h.label}: ${rupees(h.revenue_paise)} across ${h.visit_count} visits`}
                    />
                    <div className="chart-label">{h.label}</div>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </section>

      <div className="two-col">
        <section className="card">
          <h2>Top Medicines — by Quantity</h2>
          <RankList
            rows={a.top_by_quantity}
            render={(d) => `${d.qty} units`}
          />
        </section>

        <section className="card">
          <h2>Top Medicines — by Revenue</h2>
          <RankList
            rows={a.top_by_revenue}
            render={(d) => rupees(d.revenue_paise)}
          />
        </section>
      </div>

      {a.name_anomalies.length > 0 && (
        <section className="card card-warn">
          <h2>Data Quality — Possible Duplicate Names</h2>
          <ul className="anomaly-list">
            {a.name_anomalies.map((n, i) => (
              <li key={i}>{n.note}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function RankList({ rows, render }) {
  if (!rows.length) return <p className="empty">Nothing sold on this day.</p>
  return (
    <ol className="rank-list">
      {rows.map((d) => (
        <li key={d.drug_name}>
          <span className="rank-n">{d.rank}</span>
          <span className="rank-name">{d.drug_name}</span>
          <span className="rank-val">{render(d)}</span>
        </li>
      ))}
    </ol>
  )
}
