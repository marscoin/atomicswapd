import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useFlash } from '../live'

export function Card({ title, right, children, className = '' }:
  { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="card-head">
          <h2>{title}</h2>
          {right && <div className="card-right">{right}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

/** A number that eases to its new value and flashes when it changes. */
export function Num({ value, format, className = '' }:
  { value: number | null | undefined; format: (n: number) => string; className?: string }) {
  const [shown, setShown] = useState(value ?? null)
  const from = useRef(value ?? null)
  const flash = useFlash(value)
  useEffect(() => {
    if (value == null) { setShown(null); return }
    const start = from.current ?? value
    const t0 = performance.now()
    let raf = 0
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / 450)
      const eased = 1 - Math.pow(1 - k, 3)
      setShown(start + (value - start) * eased)
      if (k < 1) raf = requestAnimationFrame(step)
      else from.current = value
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value])
  return <span className={`num ${flash ? 'flash' : ''} ${className}`}>{shown == null ? '—' : format(shown)}</span>
}

export function Pill({ tone = 'dim', children, title }:
  { tone?: 'dim' | 'mars' | 'btc' | 'good' | 'warn' | 'bad' | 'live'; children: ReactNode; title?: string }) {
  return <span className={`pill pill-${tone}`} title={title}>{children}</span>
}

export function Dot({ tone }: { tone: 'good' | 'warn' | 'bad' | 'dim' }) {
  return <span className={`dot dot-${tone}`} />
}

export function Stat({ label, children, sub }: { label: string; children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{children}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function MarsLogo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <defs>
        <radialGradient id="mars-g" cx="35%" cy="30%" r="75%">
          <stop offset="0" stopColor="#ff8a5c" />
          <stop offset="0.55" stopColor="#d8461f" />
          <stop offset="1" stopColor="#6e1c0b" />
        </radialGradient>
      </defs>
      <circle cx="16" cy="16" r="14" fill="url(#mars-g)" />
      <ellipse cx="11" cy="12" rx="4" ry="2" fill="#ffffff" opacity="0.12" />
      <path d="M8 19c3 1 6 1 9-1s6-2 8-1" stroke="#5a1506" strokeWidth="1.4" fill="none" opacity="0.5" />
    </svg>
  )
}
