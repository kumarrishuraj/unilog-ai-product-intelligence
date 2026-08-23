import React from 'react'
import { bandColor, pct, statusColor } from '../lib/api'

export function Card({ title, subtitle, right, children, className = '' }) {
  return (
    <div className={`card p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-start justify-between gap-3 mb-3">
          <div>
            {title && <h3 className="text-sm font-semibold text-slate-100">{title}</h3>}
            {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
          </div>
          {right}
        </div>
      )}
      {children}
    </div>
  )
}

export function Stat({ label, value, sub, tone = 'default' }) {
  const toneCls = {
    default: 'text-slate-100',
    good: 'text-good',
    warn: 'text-warn',
    bad: 'text-bad',
    accent: 'text-accent-soft',
  }[tone]
  return (
    <div className="card p-4">
      <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className={`text-2xl font-semibold mt-1 tabular-nums ${toneCls}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
    </div>
  )
}

export function Chip({ children, className = '' }) {
  return <span className={`chip ${className}`}>{children}</span>
}

export function BandChip({ band }) {
  return <Chip className={bandColor(band)}>{band}</Chip>
}

export function StatusChip({ status }) {
  return <Chip className={statusColor(status)}>{status?.replace('_', ' ')}</Chip>
}

export function Meter({ value, tone = 'accent', label }) {
  const v = Math.max(0, Math.min(1, value || 0))
  const bar = { accent: 'bg-accent', good: 'bg-good', warn: 'bg-warn', bad: 'bg-bad' }[tone]
  return (
    <div>
      {label && (
        <div className="flex justify-between text-xs mb-1">
          <span className="text-slate-400">{label}</span>
          <span className="tabular-nums text-slate-300">{pct(v)}</span>
        </div>
      )}
      <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
        <div className={`h-full ${bar} transition-all duration-500`} style={{ width: `${v * 100}%` }} />
      </div>
    </div>
  )
}

export function Spinner({ label = 'Loading' }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-400 py-8 justify-center">
      <span className="inline-block w-3.5 h-3.5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      {label}
    </div>
  )
}

export function Empty({ title, hint }) {
  return (
    <div className="text-center py-14">
      <p className="text-slate-300 font-medium">{title}</p>
      {hint && <p className="text-sm text-slate-500 mt-1 max-w-md mx-auto">{hint}</p>}
    </div>
  )
}

export function ErrorBox({ error }) {
  if (!error) return null
  return (
    <div className="card p-3 border-bad/30 bg-bad/5 text-sm text-bad">{String(error)}</div>
  )
}

export function KV({ label, value, mono = false }) {
  return (
    <div className="flex gap-3 py-1 text-sm border-b border-white/5 last:border-0">
      <div className="w-44 shrink-0 text-slate-400 text-xs pt-0.5">{label}</div>
      <div className={`flex-1 break-words ${mono ? 'font-mono text-xs' : ''} text-slate-200`}>
        {value === null || value === undefined || value === '' ? (
          <span className="text-slate-600 italic">not populated</span>
        ) : value}
      </div>
    </div>
  )
}
