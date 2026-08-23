import React, { useRef, useState } from 'react'
import { api, num, pct } from '../lib/api'
import { Card, ErrorBox, Spinner, Stat } from '../components/ui'

export default function Upload({ onJobStarted }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [profile, setProfile] = useState(null)
  const [uploadId, setUploadId] = useState(null)
  const [filename, setFilename] = useState('')
  const [limit, setLimit] = useState(200)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef(null)

  const handleFile = async (file) => {
    if (!file) return
    setBusy(true); setError(null); setProfile(null)
    try {
      const res = await api.upload(file)
      setProfile(res.profile)
      setUploadId(res.upload_id)
      setFilename(res.filename)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const start = async () => {
    setBusy(true); setError(null)
    try {
      const job = await api.process(uploadId, Number(limit) || 0)
      onJobStarted(job.id)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const runSample = async () => {
    setBusy(true); setError(null)
    try {
      const job = await api.processSample(Number(limit) || 200)
      onJobStarted(job.id)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4 max-w-5xl">
      <ErrorBox error={error} />

      <Card title="Upload a product feed"
        subtitle="CSV or XLSX. The file is profiled before anything is enriched.">
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault(); setDragging(false)
            handleFile(e.dataTransfer.files?.[0])
          }}
          onClick={() => inputRef.current?.click()}
          className={`rounded-xl border-2 border-dashed p-10 text-center cursor-pointer
            transition-colors ${dragging ? 'border-accent bg-accent/5' : 'border-white/10 hover:border-accent/40'}`}
        >
          <input ref={inputRef} type="file" accept=".csv,.xlsx,.xlsm" className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0])} />
          <p className="text-slate-200 font-medium">
            {filename || 'Drop a CSV or XLSX here'}
          </p>
          <p className="text-xs text-slate-500 mt-1">or click to browse</p>
        </div>

        <div className="flex flex-wrap items-center gap-3 mt-4">
          <label className="text-xs text-slate-400">
            Row limit
            <input type="number" min="0" value={limit}
              onChange={(e) => setLimit(e.target.value)}
              className="ml-2 w-24 bg-ink-700 border border-white/10 rounded px-2 py-1
                         text-sm text-slate-200" />
            <span className="ml-2 text-slate-600">0 = all</span>
          </label>
          <button className="btn btn-primary" disabled={!uploadId || busy} onClick={start}>
            Enrich uploaded file
          </button>
          <button className="btn" disabled={busy} onClick={runSample}>
            Enrich bundled sample
          </button>
        </div>
      </Card>

      {busy && <Spinner label="Profiling" />}

      {profile && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat label="Rows" value={num(profile.row_count)} />
            <Stat label="Columns" value={num(profile.column_count)} />
            <Stat label="Duplicate rows" value={num(profile.duplicate_rows)}
              tone={profile.duplicate_rows ? 'warn' : 'default'} />
            <Stat label="Encoding issues" value={num(profile.encoding_issues)}
              tone={profile.encoding_issues ? 'warn' : 'default'}
              sub="mojibake repaired on read" />
          </div>

          {profile.warnings?.length > 0 && (
            <Card title="Profile warnings"
              subtitle="Surfaced before enrichment so a bad feed is visible immediately">
              <ul className="space-y-1">
                {profile.warnings.map((w, i) => (
                  <li key={i} className="text-sm text-warn/90">• {w}</li>
                ))}
              </ul>
            </Card>
          )}

          <Card title="Column profile"
            subtitle="Placeholder sentinels are counted separately from real values">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="th">Column</th>
                    <th className="th">Type</th>
                    <th className="th text-right">Non-empty</th>
                    <th className="th text-right">Placeholders</th>
                    <th className="th text-right">Effective</th>
                    <th className="th text-right">Fill rate</th>
                    <th className="th text-right">Unique</th>
                    <th className="th">Most common</th>
                  </tr>
                </thead>
                <tbody>
                  {profile.columns.map((c) => (
                    <tr key={c.name} className="hover:bg-white/[0.03]">
                      <td className="td font-medium">{c.name}</td>
                      <td className="td text-slate-400 text-xs">{c.inferred_type}</td>
                      <td className="td text-right tabular-nums">{num(c.non_empty)}</td>
                      <td className={`td text-right tabular-nums ${c.placeholder ? 'text-warn' : 'text-slate-500'}`}>
                        {num(c.placeholder)}
                      </td>
                      <td className="td text-right tabular-nums">{num(c.effective_values)}</td>
                      <td className="td text-right tabular-nums">{pct(c.fill_rate)}</td>
                      <td className="td text-right tabular-nums">{num(c.unique)}</td>
                      <td className="td text-xs text-slate-400 max-w-xs truncate">
                        {(c.top_values || []).slice(0, 2)
                          .map((t) => `${t.value} (${t.count})`).join(', ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
