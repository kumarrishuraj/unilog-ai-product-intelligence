# Deployment

How to put this prototype on a public URL, and why it is deployed the way it is.

---

## 1. Architecture decision — one service, not two

The brief suggested *frontend on Vercel, backend on Render*. For this project that
split is worse, and one of the two halves of it does not work at all. Here is the
reasoning, so you can overrule it if you disagree.

### Why the backend cannot go on Vercel

Vercel's Python runtime is **serverless**: every request is a fresh invocation with
its own process memory. This application's `JobStore` is an in-process dictionary —
`POST /api/process` starts a background thread that fills it, and the UI then polls
`GET /api/jobs/{id}` until the job reports `done`.

On serverless, the poll lands on a different invocation than the one holding the
job. The UI would hang on "queued" forever. This is not a tuning problem; it is a
fundamental mismatch between an in-memory job model and a stateless runtime.

### Why splitting the frontend off gains nothing here

Frontend-on-Vercel + backend-on-Render *does* work — `VITE_API_BASE` already exists
for exactly that, and CORS is configurable. But:

| | Single service | Split |
|---|---|---|
| URLs to manage | 1 | 2, kept in sync |
| CORS | not needed (same origin) | required |
| Processing-page polling | local | cross-origin, every 400 ms |
| Deploys per change | 1 | 2 |
| SPA size | 580 KB — FastAPI serves it fine | — |

The SPA is small and static; putting a CDN in front of it solves a problem this
prototype does not have, while adding a second failure point.

**Decision: one container.** FastAPI serves the built SPA from the same origin.
One URL is also simply a better *Working Prototype Link* for a judge to click.

The split remains supported if you want it — see §5.

---

## 2. Deploy to Render (recommended, free tier)

The repository contains a [`render.yaml`](../render.yaml) Blueprint, so this is a
few clicks rather than a form to fill in.

1. Sign in at <https://dashboard.render.com> (GitHub login works).
2. **New → Blueprint**.
3. Connect the repository `kumarrishuraj/unilog-ai-product-intelligence`
   and pick branch `main`.
4. Render reads `render.yaml`, shows one web service, and you press **Apply**.
5. First build takes roughly 5–8 minutes (it compiles the SPA, then installs Python
   deps). Watch the log until it prints `Uvicorn running on http://0.0.0.0:10000`.

Your URL will be `https://unilog-product-intelligence.onrender.com` (Render appends
a suffix if that name is taken — the dashboard shows the real one).

**No API keys are required.** The Blueprint sets `LLM_ENABLED=false` and
`RESEARCH_ENABLED=false`, and every AI stage falls back to its deterministic path.
The dashboard header will show `LLM offline` / `research off`, which is accurate.

### Free-tier behaviour worth knowing before you demo

* The instance **sleeps after 15 minutes idle**, and the next request takes
  **~50 seconds** to wake it. Open the link a minute before presenting, or use any
  uptime pinger against `/api/health` on the day.
* 512 MB RAM. Measured peak for a 1,000-row run is **210 MB**, so there is headroom,
  but the Blueprint caps uploads at 10 MB and processing at 1,000 rows to keep it
  that way.
* Only the last 5 job results are retained; older ones are evicted so a long demo
  session cannot exhaust the container.

---

## 3. Alternatives

Any platform that runs a container with a `$PORT` env var works unchanged.

**Fly.io** — no sleep on the free allowance, so no cold start:

```bash
fly launch --dockerfile Dockerfile --name unilog-product-intelligence --no-deploy
fly deploy
```

**Railway** — connect the repo; it detects the `Dockerfile` automatically. Set
`MAX_ROWS=1000` and `MAX_UPLOAD_MB=10` in the dashboard.

**Any Docker host**:

```bash
docker build -t unilog-pi .
docker run -p 8000:8000 -e PORT=8000 unilog-pi
```

---

## 4. Environment variables

Everything is optional. The defaults produce a working offline demo.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | injected by the platform |
| `CORS_ORIGINS` | `*` | comma-separated allow-list; only matters for a split deploy |
| `MAX_UPLOAD_MB` | `10` | rejects oversized uploads with HTTP 413 |
| `MAX_ROWS` | `2000` | caps rows processed per job |
| `MAX_RETAINED_JOBS` | `5` | job results kept in memory |
| `MAX_WORKERS` | `8` | thread pool; only engaged when network I/O is live |
| `LLM_ENABLED` | `true` | set `false` to hard-disable the model path |
| `LLM_API_KEY` | *(empty)* | **set in the platform dashboard, never in the repo** |
| `SEARCH_API_KEY` | *(empty)* | **set in the platform dashboard, never in the repo** |
| `RESEARCH_ENABLED` | `false` | enables manufacturer research |

> `.env` is git-ignored and `.dockerignore`d. Secrets belong in the platform's
> environment settings, never in a committed file.

---

## 5. Optional: split deploy (SPA on Vercel)

Only worth it if you specifically want a CDN in front of the SPA.

1. Deploy the backend to Render first (§2) and note its URL.
2. On Vercel: **Add New → Project**, import the repo, then set
   * Root directory: `frontend`
   * Framework preset: **Vite**
   * Environment variable: `VITE_API_BASE = https://<your-render-url>`
3. On Render, set `CORS_ORIGINS` to your Vercel URL — do not leave it as `*` once
   the origins are known.

`frontend/vercel.json` is already present with the correct build settings and SPA
rewrite.

---

## 6. Verifying a deployment

Replace `$URL` with your deployed base URL.

```bash
# readiness — expect {"status":"ok","ready":true,...}
curl -s $URL/api/health | python -m json.tool

# the SPA itself
curl -s -o /dev/null -w "%{http_code}\n" $URL/

# run the bundled sample end to end
JOB=$(curl -s -X POST "$URL/api/process/sample?limit=100" | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
sleep 8
curl -s "$URL/api/jobs/$JOB/dashboard" | python -m json.tool | head -30

# export the 252-column delivery file
curl -s -o out.csv "$URL/api/jobs/$JOB/export?fmt=csv" && head -c 300 out.csv
```

In the browser, walk: **Upload → Processing → Dashboard → Products → a product's
Evidence graph → Human review → Evaluation**, then use the CSV/XLSX buttons.

---

## 7. What is deliberately not deployed

* **The official Unilog reference pack.** Licensed material; `data/reference/` ships
  empty. The deployed demo therefore runs on `derived` + `seed` provenance, and the
  dashboard says so. Mount the workbooks as a volume to switch it to `official`.
* **`data/processed/`.** Regenerable, and the products dump is ~15 MB. A committed
  sample of real output lives in [`docs/sample_output/`](sample_output).
* **Any API key.** The demo is offline-first; keys are an upgrade, not a dependency.
