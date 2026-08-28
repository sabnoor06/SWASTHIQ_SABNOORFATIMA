export default function Narrative({ report }) {
  const n = report.narrative
  const r = report.reconciliation
  if (!n) return <p className="empty">No narrative generated.</p>

  const badge = {
    success: { cls: 'badge-ok', text: 'VERIFIED' },
    degraded: { cls: 'badge-warn', text: 'FALLBACK' },
    unavailable: { cls: 'badge-err', text: 'UNAVAILABLE' },
  }[n.status] ?? { cls: 'badge-warn', text: n.status }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>AI Narrative Summary</h1>
          <p className="subtitle">
            Generated from the reconciliation — {r.date ?? '—'}
          </p>
        </div>
        <span className="ai-chip">AI GENERATED</span>
      </header>

      <div className="two-col">
        <section className="card">
          <h2>Sent to Dr. Anand Mehta · WhatsApp</h2>
          <div className="whatsapp">
            {n.text.split('\n').map((line, i) => (
              <p key={i}>{line}</p>
            ))}
          </div>
          <div className={`badge ${badge.cls}`}>{badge.text}</div>
          {n.status_detail && <p className="hint">{n.status_detail}</p>}
        </section>

        <section className="card">
          <h2>Traced Figures</h2>
          <p className="hint">
            Every number the summary is allowed to use, mapped to the report
            field it came from. Output is rejected if it contains anything else.
          </p>
          <ul className="traced">
            {n.traced_figures.map((f, i) => (
              <li key={i}>
                <span className="traced-val">{f.display}</span>
                <span className="traced-src mono">{f.source_field}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
