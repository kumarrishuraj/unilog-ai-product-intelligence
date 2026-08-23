import React, { useEffect, useState } from 'react'
import { api, num, pct } from '../lib/api'
import { Card, Empty, ErrorBox, Meter, Spinner, Stat } from '../components/ui'

export default function Evaluation({ jobId }) {
  const [rep, setRep] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!jobId) return
    setRep(null); setError(null)
    api.evaluation(jobId).then(setRep).catch((e) => setError(e.message))
  }, [jobId])

  if (!jobId) return <Empty title="No run yet" hint="Enrich a feed first." />
  if (error) {
    return (
      <div className="space-y-3">
        <ErrorBox error={error} />
        <Empty title="No labelled data matched this run"
          hint="Evaluation compares predictions against a labelled delivery-format file, aligned by part number. Ensure the labelled rows' part numbers are present in the processed feed." />
      </div>
    )
  }
  if (!rep) return <Spinner label="Scoring against labelled data" />

  const exact = rep.fields.filter((f) => f.kind === 'exact')
  const semantic = rep.fields.filter((f) => f.kind === 'semantic')
  const other = rep.fields.filter((f) => f.kind === 'other')

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Labelled rows scored" value={num(rep.rows_evaluated)} />
        <Stat label="Micro field accuracy" value={pct(rep.micro_accuracy)} tone="accent" />
        <Stat label="Semantic similarity" value={pct(rep.mean_semantic_similarity)} />
        <Stat label="Overall quality" value={pct(rep.overall_quality)} tone="good" />
      </div>

      <Card title="Quality components"
        subtitle="Structural correctness is weighted alongside accuracy: schema-breaking copy is worthless downstream.">
        <div className="grid md:grid-cols-2 gap-x-8 gap-y-3">
          <Meter label="Field accuracy (micro)" value={rep.micro_accuracy} tone="accent" />
          <Meter label="Field accuracy (macro)" value={rep.macro_accuracy} tone="accent" />
          <Meter label="Semantic similarity" value={rep.mean_semantic_similarity} tone="accent" />
          <Meter label="LOV compliance" value={rep.lov_compliance} tone="good" />
          <Meter label="Character compliance" value={rep.char_compliance} tone="good" />
          <Meter label="Validation pass rate" value={rep.validation_pass_rate} tone="good" />
          <Meter label="Evidence coverage" value={rep.evidence_coverage} tone="accent" />
          <Meter label="Human review rate" value={rep.review_rate} tone="warn" />
        </div>
      </Card>

      {rep.notes?.length > 0 && (
        <Card title="Methodology notes">
          <ul className="space-y-1">
            {rep.notes.map((n, i) => (
              <li key={i} className="text-xs text-slate-400">• {n}</li>
            ))}
            <li className="text-xs text-slate-400">
              • Accuracy is scored only where the labelled data has a value, so blank
              predictions cannot inflate the number.
            </li>
          </ul>
        </Card>
      )}

      <FieldTable title="Exact-match fields"
        subtitle="Manufacturer, brand, part number, taxonomy" rows={exact} />
      <FieldTable title="Free-text fields"
        subtitle="Scored by order-insensitive token F1" rows={semantic} />
      <FieldTable title="Other delivery-format columns"
        subtitle="Attribute slots, URLs, assets" rows={other} collapsed />
    </div>
  )
}

function FieldTable({ title, subtitle, rows, collapsed = false }) {
  const [open, setOpen] = useState(!collapsed)
  if (!rows.length) return null
  return (
    <Card title={title} subtitle={subtitle}
      right={collapsed && (
        <button className="btn" onClick={() => setOpen(!open)}>
          {open ? 'Hide' : `Show ${rows.length}`}
        </button>
      )}>
      {open && (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Field</th>
                <th className="th text-right">Comparable</th>
                <th className="th text-right">Correct</th>
                <th className="th text-right">Accuracy</th>
                <th className="th text-right">Similarity</th>
                <th className="th text-right">Coverage</th>
              </tr>
            </thead>
            <tbody>
              {rows.sort((a, b) => b.comparable - a.comparable).map((f) => (
                <tr key={f.field}>
                  <td className="td font-mono text-xs">{f.field}</td>
                  <td className="td text-right tabular-nums">{f.comparable}</td>
                  <td className="td text-right tabular-nums">{f.correct}</td>
                  <td className={`td text-right tabular-nums ${
                    f.accuracy >= 0.9 ? 'text-good' : f.accuracy >= 0.5 ? 'text-warn' : 'text-bad'}`}>
                    {pct(f.accuracy)}
                  </td>
                  <td className="td text-right tabular-nums text-slate-400">
                    {pct(f.mean_similarity)}
                  </td>
                  <td className="td text-right tabular-nums text-slate-400">
                    {pct(f.coverage)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
