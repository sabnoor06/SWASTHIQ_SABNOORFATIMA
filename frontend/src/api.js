const BASE = import.meta.env.VITE_API_BASE || ''

export async function fetchDays() {
  const r = await fetch(`${BASE}/api/days`)
  if (!r.ok) throw new Error('Could not load available days')
  return (await r.json()).days
}

export async function fetchReport(date, { llm = true } = {}) {
  const r = await fetch(`${BASE}/api/report/${date}?llm=${llm}`)
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail || `Report failed (${r.status})`)
  }
  return r.json()
}

/** Integer paise -> "₹42,850" / "₹42,850.50". Display only. */
export function rupees(paise) {
  if (paise == null) return '—'
  const neg = paise < 0
  const p = Math.abs(paise)
  const whole = Math.floor(p / 100)
  const sub = p % 100
  const s = whole.toLocaleString('en-IN')
  return `${neg ? '-' : ''}₹${s}${sub ? '.' + String(sub).padStart(2, '0') : ''}`
}
