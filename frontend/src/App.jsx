import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { fetchDays, fetchReport } from './api'
import Reconciliation from './components/Reconciliation.jsx'
import Analytics from './components/Analytics.jsx'
import Narrative from './components/Narrative.jsx'

export default function App() {
  const [days, setDays] = useState([])
  const [date, setDate] = useState(null)
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchDays()
      .then((d) => {
        setDays(d)
        if (d.length) setDate(d[d.length - 1])
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!date) return
    setLoading(true)
    setError(null)
    fetchReport(date)
      .then(setReport)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [date])

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">S</span>
          <span className="brand-name">SwasthiQ</span>
        </div>

        <nav className="nav">
          <NavLink to="/reconciliation" className="nav-link">
            Reconciliation
          </NavLink>
          <NavLink to="/analytics" className="nav-link">
            Analytics
          </NavLink>
          <NavLink to="/summary" className="nav-link">
            AI Summary
          </NavLink>
        </nav>

        <div className="sidebar-section">
          <label className="sidebar-label" htmlFor="day">
            Clinic day
          </label>
          <select
            id="day"
            className="day-select"
            value={date ?? ''}
            onChange={(e) => setDate(e.target.value)}
          >
            {days.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        {report?.rejected_rows?.length > 0 && (
          <div className="sidebar-warn">
            {report.rejected_rows.length} row
            {report.rejected_rows.length > 1 ? 's' : ''} rejected
          </div>
        )}
      </aside>

      <main className="main">
        {error && <div className="banner banner-error">{error}</div>}
        {loading && <div className="banner">Loading report…</div>}

        {report && !loading && (
          <Routes>
            <Route path="/" element={<Navigate to="/reconciliation" replace />} />
            <Route
              path="/reconciliation"
              element={<Reconciliation report={report} />}
            />
            <Route path="/analytics" element={<Analytics report={report} />} />
            <Route path="/summary" element={<Narrative report={report} />} />
          </Routes>
        )}
      </main>
    </div>
  )
}
