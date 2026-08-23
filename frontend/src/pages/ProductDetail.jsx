import React, { useEffect, useState } from 'react'
import { api, bandColor, pct, tierLabel } from '../lib/api'
import { BandChip, Card, Chip, ErrorBox, KV, Meter, Spinner, StatusChip } from '../components/ui'

const PIPELINE = [
  'Raw product', 'AI understanding', 'Classification', 'LOV retrieval',
  'Attribute extraction', 'Evidence', 'Normalization', 'Description generation',
  'Validation', 'Confidence', 'Final product',
]

export default function ProductDetail({ jobId, rowIndex, onBack }) {
  const [p, setP] = useState(null)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('compare')

  useEffect(() => {
    if (jobId == null || rowIndex == null) return
    setP(null)
    api.product(jobId, rowIndex).then(setP).catch((e) => setError(e.message))
  }, [jobId, rowIndex])

  if (error) return <ErrorBox error={error} />
  if (!p) return <Spinner label="Loading product" />

  const attrs = p.attributes || []
  const filled = attrs.filter((a) => a.value)

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <button className="btn mb-2" onClick={onBack}>← Back to products</button>
          <h2 className="text-xl font-semibold font-mono text-accent-soft">
            {p.mpn?.value || `Row ${p.row_index}`}
          </h2>
          <p className="text-sm text-slate-400 mt-0.5">{p.cleaned?.Part_Desc}</p>
        </div>
        <div className="flex items-center gap-2">
          <StatusChip status={p.status} />
          <BandChip band={p.confidence_band} />
          <span className="text-2xl font-semibold tabular-nums">{pct(p.confidence)}</span>
        </div>
      </div>

      {/* Pipeline ribbon */}
      <Card title="Enrichment path" subtitle="Every stage this record passed through">
        <div className="flex flex-wrap gap-1.5">
          {PIPELINE.map((s, i) => (
            <React.Fragment key={s}>
              <span className="chip border-accent/30 bg-accent/10 text-accent-soft">{s}</span>
              {i < PIPELINE.length - 1 && <span className="text-slate-700 self-center">→</span>}
            </React.Fragment>
          ))}
        </div>
      </Card>

      <div className="flex gap-1 border-b border-white/10">
        {[['compare', 'Raw vs Enriched'], ['attributes', `Attributes (${filled.length}/${attrs.length})`],
          ['evidence', 'Evidence graph'], ['confidence', 'Confidence'],
          ['descriptions', 'Generated copy'], ['row', 'Delivery row'],
          ['log', 'Stage log']].map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === id ? 'border-accent text-accent-soft'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'compare' && (
        <div className="grid lg:grid-cols-3 gap-4">
          <Card title="Raw input" subtitle="Exactly as supplied">
            {Object.entries(p.raw || {}).map(([k, v]) => <KV key={k} label={k} value={v} mono />)}
            {p.placeholders?.length > 0 && (
              <p className="text-xs text-warn mt-3">
                Placeholder sentinels detected and neutralised: {p.placeholders.join(', ')}
              </p>
            )}
          </Card>

          <Card title="Resolved entities" subtitle="With the method that produced each">
            {[['MANUFACTURER_NAME', p.manufacturer], ['BRAND_NAME', p.brand],
              ['Supplier (Part_Manuf)', p.supplier], ['Classpath', p.classpath],
              ['Product Name', p.product_name], ['UNSPSC', p.unspsc]].map(([label, fv]) => (
              <FieldRow key={label} label={label} fv={fv} />
            ))}
          </Card>

          <Card title="Classification candidates"
            subtitle="Ranked, so ambiguity is visible rather than hidden">
            {(p.classification_candidates || []).slice(0, 5).map((c, i) => (
              <div key={c.leaf_id} className="py-1.5 border-b border-white/5 last:border-0">
                <div className="flex justify-between gap-2 items-baseline">
                  <span className={i === 0 ? 'text-slate-100 text-sm' : 'text-slate-400 text-sm'}>
                    {c.classpath?.split('>').pop() || c.leaf_id}
                  </span>
                  <span className="tabular-nums text-xs text-slate-500">
                    {c.score?.toFixed(1)}
                  </span>
                </div>
                <div className="text-[11px] text-slate-500 mt-0.5">
                  matched: {(c.matched || []).join(', ') || '—'}
                </div>
              </div>
            ))}
            {(p.classification_candidates || []).length === 0 && (
              <p className="text-sm text-slate-500">No candidate matched; generic template used.</p>
            )}
          </Card>
        </div>
      )}

      {tab === 'attributes' && (
        <Card className="p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-white/[0.03]">
                <tr>
                  <th className="th">Attribute</th>
                  <th className="th">Raw</th>
                  <th className="th">Normalized value</th>
                  <th className="th">UOM</th>
                  <th className="th">Transformation</th>
                  <th className="th text-center">LOV</th>
                  <th className="th text-center">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {attrs.map((a) => (
                  <tr key={a.label} className={a.value ? '' : 'opacity-40'}>
                    <td className="td font-medium whitespace-nowrap">{a.label}</td>
                    <td className="td font-mono text-xs text-slate-400">{a.raw || '—'}</td>
                    <td className="td">{a.value || <span className="italic text-slate-600">empty</span>}</td>
                    <td className="td text-xs text-accent-soft">{a.uom || '—'}</td>
                    <td className="td text-xs text-slate-400 max-w-md">{a.transformation || '—'}</td>
                    <td className="td text-center">
                      {a.lov_compliant === true && <Chip className="text-good border-good/40 bg-good/10">PASS</Chip>}
                      {a.lov_compliant === false && <Chip className="text-bad border-bad/40 bg-bad/10">FAIL</Chip>}
                      {a.lov_compliant == null && <span className="text-slate-600 text-xs">open</span>}
                    </td>
                    <td className="td text-center whitespace-nowrap">
                      {a.value ? (
                        <>
                          <span className="tabular-nums text-xs mr-1.5">{pct(a.confidence)}</span>
                          <BandChip band={a.band} />
                        </>
                      ) : <span className="text-xs text-slate-600">{a.method}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {tab === 'evidence' && <EvidenceGraph graph={p.evidence_graph} />}

      {tab === 'confidence' && (
        <div className="grid lg:grid-cols-2 gap-4">
          <Card title="Confidence breakdown"
            subtitle="Which component limited this record's score">
            <div className="space-y-3">
              {Object.entries(p.confidence_breakdown || {}).map(([k, v]) => (
                <Meter key={k} label={k.replace(/_/g, ' ')} value={v}
                  tone={v >= 0.85 ? 'good' : v >= 0.65 ? 'accent' : v >= 0.35 ? 'warn' : 'bad'} />
              ))}
            </div>
            <div className="mt-4 pt-3 border-t border-white/10 flex justify-between items-baseline">
              <span className="text-sm text-slate-400">Product confidence</span>
              <span className="text-2xl font-semibold tabular-nums">{pct(p.confidence)}</span>
            </div>
          </Card>

          <Card title="Review flags" subtitle="Specific and actionable, not generic">
            {(p.review_flags || []).length === 0 ? (
              <p className="text-sm text-good py-4">No flags — this record is publishable.</p>
            ) : (
              <ul className="space-y-2">
                {p.review_flags.map((f, i) => (
                  <li key={i} className="border-l-2 border-warn/60 pl-3 py-1">
                    <div className="text-sm text-slate-200">{f.reason}</div>
                    {f.field && <div className="text-[11px] text-slate-500">field: {f.field}</div>}
                    {f.detail && <div className="text-xs text-slate-400 mt-0.5">{f.detail}</div>}
                    {f.suggested_value && (
                      <div className="text-xs text-accent-soft mt-0.5">
                        suggested: {f.suggested_value}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {(p.issues || []).length > 0 && (
              <div className="mt-4 pt-3 border-t border-white/10">
                <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">
                  Validation issues
                </div>
                <ul className="space-y-1">
                  {p.issues.map((it, i) => (
                    <li key={i} className={`text-xs ${it.severity === 'error' ? 'text-bad' : 'text-warn'}`}>
                      [{it.code}] {it.field}: {it.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>
        </div>
      )}

      {tab === 'descriptions' && (
        <Card title="Generated copy"
          subtitle="Composed from verified facts only — never free-written">
          {Object.entries(p.descriptions || {}).map(([name, fv]) => (
            <div key={name} className="py-2.5 border-b border-white/5 last:border-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-semibold text-slate-300">{name}</span>
                <span className="text-[11px] text-slate-500 tabular-nums">
                  {(fv.value || '').length} chars
                </span>
                {fv.value && <BandChip band={fv.band} />}
              </div>
              {fv.value ? (
                <p className="text-sm text-slate-200 leading-relaxed">{fv.value}</p>
              ) : (
                <p className="text-xs text-slate-600 italic">
                  {fv.notes?.[0] || 'not populated'}
                </p>
              )}
              {fv.transformation && (
                <p className="text-[11px] text-slate-500 mt-1">{fv.transformation}</p>
              )}
            </div>
          ))}
        </Card>
      )}

      {tab === 'row' && (
        <Card title="Delivery-format row"
          subtitle="Populated columns only — blanks are intentionally blank">
          {Object.entries(p.delivery_row || {}).map(([k, v]) => (
            <KV key={k} label={k} value={v} mono />
          ))}
        </Card>
      )}

      {tab === 'log' && (
        <Card title="Stage log">
          <ol className="space-y-1.5">
            {(p.stage_log || []).map((s, i) => (
              <li key={i} className="flex gap-3 text-sm border-b border-white/5 pb-1.5">
                <span className="w-48 shrink-0 text-xs text-accent-soft font-mono">{s.stage}</span>
                <span className="text-slate-300 flex-1">{s.detail}</span>
              </li>
            ))}
          </ol>
        </Card>
      )}
    </div>
  )
}

function FieldRow({ label, fv }) {
  if (!fv) return null
  return (
    <div className="py-2 border-b border-white/5 last:border-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-slate-400">{label}</span>
        {fv.value && (
          <span className={`chip ${bandColor(fv.band)}`}>{pct(fv.confidence)}</span>
        )}
      </div>
      <div className="text-sm text-slate-100 mt-0.5">
        {fv.value || <span className="italic text-slate-600">unresolved</span>}
      </div>
      {fv.raw && fv.raw !== fv.value && (
        <div className="text-[11px] text-slate-500 mt-0.5 font-mono">raw: {fv.raw}</div>
      )}
      {fv.transformation && (
        <div className="text-[11px] text-slate-500 mt-0.5">{fv.transformation}</div>
      )}
      {!fv.value && fv.notes?.[0] && (
        <div className="text-[11px] text-warn/80 mt-0.5">{fv.notes[0]}</div>
      )}
    </div>
  )
}

function EvidenceGraph({ graph }) {
  if (!graph?.nodes?.length) {
    return <Card><p className="text-sm text-slate-500">No evidence recorded.</p></Card>
  }
  const fields = graph.nodes.filter((n) => n.type === 'field' || n.type === 'attribute')
  const evidenceFor = (id) =>
    graph.edges.filter((e) => e.from === id)
      .map((e) => graph.nodes.find((n) => n.id === e.to))
      .filter((n) => n?.type === 'evidence')

  return (
    <Card title="Evidence graph"
      subtitle="Product → Field → Transformation → Source → Evidence">
      <div className="space-y-2">
        {fields.map((f) => (
          <div key={f.id} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs uppercase tracking-wide text-slate-500">
                {f.type}
              </span>
              <span className="text-sm font-medium text-slate-100">{f.label}</span>
              <span className="text-slate-600">→</span>
              <span className="text-sm text-accent-soft font-mono">
                {f.value}{f.uom ? ` ${f.uom}` : ''}
              </span>
              <span className={`chip ml-auto ${bandColor(f.band)}`}>{pct(f.confidence)}</span>
            </div>
            {f.transformation && (
              <div className="text-[11px] text-slate-400 mt-1.5">
                <span className="text-slate-600">transformation: </span>{f.transformation}
              </div>
            )}
            <div className="mt-2 flex flex-wrap gap-1.5">
              {evidenceFor(f.id).map((e, i) => (
                <span key={i}
                  className="chip border-white/10 bg-white/5 text-slate-300"
                  title={e.snippet}>
                  {tierLabel(e.tier)}
                  {e.url && (
                    <a href={e.url} target="_blank" rel="noreferrer"
                      className="text-accent-soft underline ml-1">source</a>
                  )}
                </span>
              ))}
              {evidenceFor(f.id).length === 0 && (
                <span className="text-[11px] text-warn/70">no evidence recorded</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}
