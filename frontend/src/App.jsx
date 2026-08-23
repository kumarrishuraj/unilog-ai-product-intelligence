import React, { useEffect, useState } from 'react'
import { api } from './lib/api'
import Dashboard from './pages/Dashboard'
import Upload from './pages/Upload'
import Processing from './pages/Processing'
import Products from './pages/Products'
import ProductDetail from './pages/ProductDetail'
import Review from './pages/Review'
import Evaluation from './pages/Evaluation'

const NAV = [
  ['dashboard', 'Dashboard'],
  ['upload', 'Upload'],
  ['processing', 'Processing'],
  ['products', 'Products'],
  ['review', 'Human review'],
  ['evaluation', 'Evaluation'],
]

export default function App() {
  const [page, setPage] = useState('upload')
  const [jobId, setJobId] = useState(null)
  const [rowIndex, setRowIndex] = useState(null)
  const [config, setConfig] = useState(null)

  useEffect(() => {
    api.config().then(setConfig).catch(() => {})
    api.jobs().then((d) => {
      const done = d.jobs?.find((j) => j.status === 'done')
      if (done) { setJobId(done.id); setPage('dashboard') }
    }).catch(() => {})
  }, [])

  const openProduct = (idx) => { setRowIndex(idx); setPage('detail') }
  const onJobStarted = (id) => { setJobId(id); setPage('processing') }

  return (
    <div className="min-h-screen">
      <header className="border-b border-white/5 bg-ink-800/60 backdrop-blur sticky top-0 z-20">
        <div className="max-w-[1500px] mx-auto px-5 py-3 flex items-center gap-6 flex-wrap">
          <div>
            <h1 className="font-semibold text-slate-50 leading-tight">
              Unilog Product Intelligence
            </h1>
            <p className="text-[11px] text-slate-500">
              Evidence-grounded enrichment · deterministic core + AI layer
            </p>
          </div>

          <nav className="flex gap-1 flex-1">
            {NAV.map(([id, label]) => (
              <button key={id} onClick={() => setPage(id)}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  page === id || (page === 'detail' && id === 'products')
                    ? 'bg-accent/15 text-accent-soft'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-white/5'}`}>
                {label}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-3 text-[11px]">
            {config && (
              <>
                <span className={`chip ${config.llm?.available
                  ? 'text-good border-good/40 bg-good/10'
                  : 'text-mute border-white/10 bg-white/5'}`}>
                  LLM {config.llm?.available ? config.llm.provider : 'offline'}
                </span>
                <span className={`chip ${config.settings?.research_enabled
                  ? 'text-good border-good/40 bg-good/10'
                  : 'text-mute border-white/10 bg-white/5'}`}>
                  research {config.settings?.research_enabled ? 'on' : 'off'}
                </span>
              </>
            )}
            {jobId && (
              <span className="chip border-accent/30 bg-accent/10 text-accent-soft font-mono">
                job {jobId.slice(0, 8)}
              </span>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-[1500px] mx-auto px-5 py-5">
        {page === 'dashboard' && <Dashboard jobId={jobId} onNavigate={setPage} />}
        {page === 'upload' && <Upload onJobStarted={onJobStarted} />}
        {page === 'processing' && <Processing jobId={jobId} onNavigate={setPage} />}
        {page === 'products' && <Products jobId={jobId} onOpenProduct={openProduct} />}
        {page === 'detail' && (
          <ProductDetail jobId={jobId} rowIndex={rowIndex}
            onBack={() => setPage('products')} />
        )}
        {page === 'review' && <Review jobId={jobId} onOpenProduct={openProduct} />}
        {page === 'evaluation' && <Evaluation jobId={jobId} />}
      </main>

      <footer className="max-w-[1500px] mx-auto px-5 py-6 text-[11px] text-slate-600">
        Values are only published when they come from the input, an approved
        vocabulary, manufacturer evidence, or a deterministic transformation.
        Anything else is left blank and flagged.
      </footer>
    </div>
  )
}
