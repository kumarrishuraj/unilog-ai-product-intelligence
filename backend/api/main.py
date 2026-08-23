"""
FastAPI application.

Jobs run in a background thread with progress reported through an in-memory store,
so the dashboard can show live pipeline stages without a task broker.  For a
production deployment the ``JobStore`` is the seam to swap for Celery/RQ.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import STAGE_ORDER, get_settings
from backend.models.product import EnrichedProduct
from backend.pipeline.io import (
    profile_input, read_table, write_csv, write_review_csv, write_xlsx,
)
from backend.pipeline.orchestrator import Orchestrator, RunResult
from evaluation.metrics import compute_quality_metrics

settings = get_settings()
UPLOAD_DIR = settings.data_dir / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


# Deployment guards. A public demo is an open door: without these, one oversized
# upload or one very large row count exhausts a 512 MB free-tier container, and
# accumulated job results leak memory until the process is killed.
MAX_UPLOAD_BYTES = _int_env("MAX_UPLOAD_MB", 10) * 1024 * 1024
MAX_ROWS = _int_env("MAX_ROWS", 2000)
MAX_RETAINED_JOBS = _int_env("MAX_RETAINED_JOBS", 5)

app = FastAPI(
    title="Unilog Product Intelligence API",
    version="1.0.0",
    description="Evidence-grounded enrichment pipeline for industrial product data.",
)
# CORS. A single-service deployment (the SPA is served from this same origin) needs
# none of this, but a split deploy -- SPA on a CDN, API here -- does. Set
# CORS_ORIGINS to a comma-separated allow-list in production; the "*" default keeps
# the public read-only demo usable from anywhere.
_cors_env = os.getenv("CORS_ORIGINS", "*").strip()
CORS_ORIGINS = ["*"] if _cors_env in ("", "*") else [
    o.strip() for o in _cors_env.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ORIGINS != ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    filename: str
    status: str = "queued"            # queued | running | done | error
    phase: str = ""
    processed: int = 0
    total: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[RunResult] = None
    profile: Optional[Dict[str, Any]] = None
    stage_status: Dict[str, str] = field(default_factory=dict)
    exports: Dict[str, str] = field(default_factory=dict)

    @property
    def progress(self) -> float:
        return self.processed / self.total if self.total else 0.0

    def summary(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id, "filename": self.filename, "status": self.status,
            "phase": self.phase, "processed": self.processed, "total": self.total,
            "progress": round(self.progress, 4),
            "elapsed": round((self.finished_at or time.time()) - self.started_at, 2),
            "error": self.error,
            "stages": [{"name": s, "status": self.stage_status.get(s, "pending")}
                       for s in STAGE_ORDER if settings.stages.get(s, True)],
            "exports": dict(self.exports),
        }
        if self.result is not None:
            out["stats"] = self.result.stats.as_dict()
            out["schema"] = self.result.schema.summary()
            out["reference"] = self.result.reference_summary
        if self.profile is not None:
            out["profile_warnings"] = self.profile.get("warnings", [])
        return out


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, filename: str, total: int) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], filename=filename, total=total)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_locked()
        return job

    def evict(self) -> None:
        """Trim retained results. Safe to call from a worker thread."""
        with self._lock:
            self._evict_locked()

    def _evict_locked(self) -> None:
        """
        Keep only the most recent MAX_RETAINED_JOBS results.

        Each finished job holds its full RunResult -- every EnrichedProduct plus its
        252-column row -- which is roughly 15 MB per 1,000 rows. Unbounded, a demo
        that is exercised all afternoon will exhaust the container.
        """
        finished = [j for j in self._jobs.values()
                    if j.status in ("done", "error")]
        if len(finished) <= MAX_RETAINED_JOBS:
            return
        for job in sorted(finished, key=lambda j: j.started_at)[:-MAX_RETAINED_JOBS]:
            job.result = None
            self._jobs.pop(job.id, None)

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self) -> List[Job]:
        return sorted(self._jobs.values(), key=lambda j: -j.started_at)

    def latest_done(self) -> Optional[Job]:
        return next((j for j in self.list() if j.status == "done"), None)


JOBS = JobStore()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class StageToggle(BaseModel):
    stage: str
    enabled: bool


class ReviewDecision(BaseModel):
    row_index: int
    field: str
    action: str = Field(description="approve | reject | override")
    value: Optional[str] = None
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------
def _run_job(job: Job, rows: List[Dict[str, Any]]) -> None:
    job.status = "running"
    active = [s for s in STAGE_ORDER if settings.stages.get(s, True)]
    for s in active:
        job.stage_status[s] = "pending"

    def progress(done: int, total: int, phase: str) -> None:
        job.processed, job.total, job.phase = done, total, phase
        # Mark the stages covered by the current phase.
        first_phase = ("input_analysis", "cleaning", "entity_resolution", "classification")
        later = [s for s in active if s not in first_phase]
        group = first_phase if phase.startswith("resolve") else later
        for s in group:
            if s in job.stage_status:
                job.stage_status[s] = "running" if done < total else "done"
        if phase.startswith("enrich") and done == total:
            for s in active:
                job.stage_status[s] = "done"

    try:
        orch = Orchestrator(settings)
        result = orch.run(rows, progress=progress)
        orch.close()
        job.result = result

        outdir = settings.processed_dir / job.id
        outdir.mkdir(parents=True, exist_ok=True)
        stem = Path(job.filename).stem or "enriched"
        csv_path = write_csv(result.rows, result.schema, outdir / f"{stem}_delivery_format.csv")
        review_path = write_review_csv(result.review_queue, outdir / f"{stem}_review_queue.csv")
        job.exports = {"csv": str(csv_path), "review": str(review_path)}
        try:
            xlsx_path = write_xlsx(result.rows, result.schema,
                                   outdir / f"{stem}_delivery_format.xlsx")
            job.exports["xlsx"] = str(xlsx_path)
        except Exception:
            pass

        for s in active:
            job.stage_status[s] = "done"
        job.status = "done"
    except Exception as exc:
        job.error = f"{type(exc).__name__}: {exc}"
        job.status = "error"
    finally:
        job.finished_at = time.time()
        # Also evict here: eviction on create alone always leaves one extra, since
        # the newest job is still running when that pass looks for finished jobs.
        JOBS.evict()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, Any]:
    """
    Readiness probe for the platform's health check.

    Reports whether the two things a request actually needs are present: the
    delivery-format template (without it the pipeline cannot build an output row)
    and the packaged vocabulary. Returns 200 with ``status: degraded`` rather than a
    failure code when optional pieces are missing, so a platform health check does
    not restart a container that is working perfectly well offline.
    """
    template = settings.delivery_template
    template_ok = bool(template and Path(template).exists())
    packs_ok = (settings.pack_dir / "taxonomy.json").exists()
    spa_ok = _FRONTEND_DIST.exists()

    ready = template_ok and packs_ok
    return {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "checks": {
            "delivery_template": template_ok,
            "vocabulary_packs": packs_ok,
            "frontend_bundle": spa_ok,
        },
        "mode": {
            "offline_capable": True,
            "llm_configured": bool(settings.llm_api_key) and settings.llm_enabled,
            "research_configured": bool(settings.search_api_key)
            and settings.research_enabled,
        },
        "limits": {
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "max_rows": MAX_ROWS,
            "max_retained_jobs": MAX_RETAINED_JOBS,
        },
        "version": app.version,
    }


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    from backend.llm.client import LlmClient
    return {"settings": settings.as_dict(),
            "stages": STAGE_ORDER,
            "llm": LlmClient(settings).status()}


@app.post("/api/config/stage")
def set_stage(toggle: StageToggle) -> Dict[str, Any]:
    if toggle.stage not in STAGE_ORDER:
        raise HTTPException(400, f"unknown stage '{toggle.stage}'")
    settings.enable(toggle.stage, toggle.enabled)
    return {"stages": settings.stages}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Store an uploaded feed and return its profile without enriching it."""
    suffix = Path(file.filename or "upload.csv").suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xlsm"):
        raise HTTPException(400, "only CSV and XLSX uploads are supported")
    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"file is {len(payload) / 1048576:.1f} MB; this deployment accepts "
                 f"up to {MAX_UPLOAD_BYTES // 1048576} MB")
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    dest.write_bytes(payload)
    try:
        rows = read_table(dest)
    except Exception as exc:
        raise HTTPException(400, f"could not parse file: {exc}")
    profile = profile_input(rows, file.filename or dest.name)
    return {"upload_id": dest.name, "filename": file.filename,
            "profile": profile.as_dict()}


@app.post("/api/process")
def process(upload_id: str = Query(...), limit: int = Query(0, ge=0)) -> Dict[str, Any]:
    path = UPLOAD_DIR / upload_id
    if not path.exists():
        raise HTTPException(404, "upload not found")
    rows = read_table(path)
    if limit:
        rows = rows[:limit]
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS]
    job = JOBS.create(upload_id, len(rows))
    job.profile = profile_input(rows, upload_id).as_dict()
    threading.Thread(target=_run_job, args=(job, rows), daemon=True).start()
    return job.summary()


@app.post("/api/process/sample")
def process_sample(limit: int = Query(200, ge=1, le=5000)) -> Dict[str, Any]:
    """Convenience endpoint: enrich the bundled sample feed."""
    candidates = sorted(settings.raw_dir.glob("*input*.csv")) or \
        sorted(settings.raw_dir.glob("*.csv"))
    if not candidates:
        raise HTTPException(404, "no sample input found in data/raw")
    rows = read_table(candidates[0])[:min(limit, MAX_ROWS)]
    job = JOBS.create(candidates[0].name, len(rows))
    job.profile = profile_input(rows, candidates[0].name).as_dict()
    threading.Thread(target=_run_job, args=(job, rows), daemon=True).start()
    return job.summary()


@app.get("/api/jobs")
def list_jobs() -> Dict[str, Any]:
    return {"jobs": [j.summary() for j in JOBS.list()]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job.summary()


def _require_result(job_id: str) -> Job:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.result is None:
        raise HTTPException(409, f"job is {job.status}, no result yet")
    return job


@app.get("/api/jobs/{job_id}/dashboard")
def dashboard(job_id: str) -> Dict[str, Any]:
    job = _require_result(job_id)
    assert job.result is not None
    products = job.result.products
    limits = {"MOBILE_DESC": 80, "INVOICE_DESC": 40, "SHORT_DESC": 240,
              "LONG_DESC1": 4000, "RETAIL_DESC": 240}
    quality = compute_quality_metrics(products, limits)

    bands: Dict[str, int] = {}
    statuses: Dict[str, int] = {}
    categories: Dict[str, int] = {}
    flags: Dict[str, int] = {}
    for p in products:
        bands[p.band.value] = bands.get(p.band.value, 0) + 1
        statuses[p.status.value] = statuses.get(p.status.value, 0) + 1
        key = p.classpath.value or "Unclassified"
        categories[key] = categories.get(key, 0) + 1
        for f in p.review_flags:
            flags[f.reason] = flags.get(f.reason, 0) + 1

    enriched = sum(1 for p in products
                   if p.classpath.present and any(d.present for d in p.descriptions.values()))
    return {
        "job": job.summary(),
        "totals": {
            "processed": len(products),
            "enriched": enriched,
            "needs_review": sum(1 for p in products if p.needs_review),
            "failed": sum(1 for p in products if p.status.value == "FAILED"),
        },
        "quality": {k: round(v, 4) for k, v in quality.items()},
        "confidence_bands": bands,
        "statuses": statuses,
        "top_categories": sorted(categories.items(), key=lambda kv: -kv[1])[:12],
        "top_review_reasons": sorted(flags.items(), key=lambda kv: -kv[1])[:12],
        "reference": job.result.reference_summary,
    }


@app.get("/api/jobs/{job_id}/products")
def products(job_id: str, offset: int = 0, limit: int = 50,
             status: Optional[str] = None, q: Optional[str] = None,
             needs_review: Optional[bool] = None) -> Dict[str, Any]:
    job = _require_result(job_id)
    assert job.result is not None
    items = job.result.products
    if status:
        items = [p for p in items if p.status.value == status.upper()]
    if needs_review is not None:
        items = [p for p in items if p.needs_review == needs_review]
    if q:
        needle = q.lower()
        items = [p for p in items
                 if needle in (p.mpn.value or "").lower()
                 or needle in (p.cleaned.get("Part_Desc") or "").lower()
                 or needle in (p.brand.value or "").lower()]
    total = len(items)
    page = items[offset: offset + limit]
    return {
        "total": total, "offset": offset, "limit": limit,
        "items": [{
            "row_index": p.row_index,
            "mpn": p.mpn.value,
            "description": p.cleaned.get("Part_Desc"),
            "brand": p.brand.value,
            "manufacturer": p.manufacturer.value,
            "classpath": p.classpath.value,
            "product_name": p.product_name.value,
            "confidence": round(p.confidence, 4),
            "band": p.band.value,
            "status": p.status.value,
            "attributes_filled": len(p.populated_attributes()),
            "attributes_total": len(p.attributes),
            "flags": len(p.review_flags),
        } for p in page],
    }


@app.get("/api/jobs/{job_id}/products/{row_index}")
def product_detail(job_id: str, row_index: int) -> Dict[str, Any]:
    job = _require_result(job_id)
    assert job.result is not None
    match = next((p for p in job.result.products if p.row_index == row_index), None)
    if match is None:
        raise HTTPException(404, "product not found")
    row = job.result.rows[job.result.products.index(match)]
    detail = match.as_dict()
    detail["delivery_row"] = {k: v for k, v in row.items() if v}
    detail["evidence_graph"] = _evidence_graph(match)
    return detail


def _evidence_graph(p: EnrichedProduct) -> Dict[str, Any]:
    """Product -> Field -> Transformation -> Source -> Evidence, for the UI."""
    nodes: List[Dict[str, Any]] = [
        {"id": "product", "type": "product",
         "label": p.mpn.value or f"row {p.row_index}"}]
    edges: List[Dict[str, str]] = []

    def add_field(key: str, label: str, fv) -> None:
        if not getattr(fv, "present", False):
            return
        fid = f"field:{key}"
        nodes.append({"id": fid, "type": "field", "label": label,
                      "value": fv.value, "confidence": round(fv.confidence, 4),
                      "band": fv.band.value, "transformation": fv.transformation,
                      "method": fv.method})
        edges.append({"from": "product", "to": fid})
        for i, ev in enumerate(fv.evidence):
            eid = f"{fid}:ev{i}"
            nodes.append({"id": eid, "type": "evidence", "label": ev.source,
                          "tier": ev.tier, "snippet": ev.snippet, "url": ev.url})
            edges.append({"from": fid, "to": eid})

    add_field("manufacturer", "MANUFACTURER_NAME", p.manufacturer)
    add_field("brand", "BRAND_NAME", p.brand)
    add_field("classpath", "Classpath", p.classpath)
    add_field("mpn", "MANUFACTURER_PART_NUMBER", p.mpn)
    for name, fv in p.descriptions.items():
        add_field(name.lower(), name, fv)
    for av in p.populated_attributes():
        fid = f"attr:{av.label}"
        nodes.append({"id": fid, "type": "attribute", "label": av.label,
                      "value": av.value, "uom": av.uom,
                      "confidence": round(av.confidence, 4), "band": av.band.value,
                      "transformation": av.transformation, "method": av.method,
                      "lov_compliant": av.lov_compliant})
        edges.append({"from": "product", "to": fid})
        for i, ev in enumerate(av.evidence):
            eid = f"{fid}:ev{i}"
            nodes.append({"id": eid, "type": "evidence", "label": ev.source,
                          "tier": ev.tier, "snippet": ev.snippet, "url": ev.url})
            edges.append({"from": fid, "to": eid})
    return {"nodes": nodes, "edges": edges}


@app.get("/api/jobs/{job_id}/review")
def review_queue(job_id: str, offset: int = 0, limit: int = 100) -> Dict[str, Any]:
    job = _require_result(job_id)
    assert job.result is not None
    entries: List[Dict[str, Any]] = []
    for p in job.result.review_queue:
        for f in p.review_flags:
            entries.append({
                "row_index": p.row_index,
                "mpn": p.mpn.value,
                "description": p.cleaned.get("Part_Desc"),
                "status": p.status.value,
                "confidence": round(p.confidence, 4),
                "band": p.band.value,
                "reason": f.reason, "field": f.field, "detail": f.detail,
                "suggested_value": f.suggested_value,
                "evidence": [e.as_dict() for e in
                             (p.brand.evidence if f.field == "BRAND_NAME"
                              else p.manufacturer.evidence if f.field == "MANUFACTURER_NAME"
                              else p.classpath.evidence)],
            })
    return {"total": len(entries), "items": entries[offset: offset + limit]}


@app.post("/api/jobs/{job_id}/review")
def apply_review(job_id: str, decision: ReviewDecision) -> Dict[str, Any]:
    """
    Apply a human decision.  Overrides are recorded on the field with full
    provenance ('human_override') so the audit trail survives export.
    """
    job = _require_result(job_id)
    assert job.result is not None
    p = next((x for x in job.result.products if x.row_index == decision.row_index), None)
    if p is None:
        raise HTTPException(404, "product not found")

    if decision.action == "override" and decision.value is not None:
        target = {"BRAND_NAME": "brand", "MANUFACTURER_NAME": "manufacturer",
                  "Classpath": "classpath", "Product Name": "product_name"}.get(decision.field)
        if target:
            fv = getattr(p, target)
            fv.value = decision.value
            fv.confidence = 1.0
            fv.method = "human_override"
            fv.transformation = f"reviewer set this value ({decision.note or 'no note'})"
            fv.validation = "PASS"
        else:
            av = p.attribute(decision.field)
            if av is None:
                raise HTTPException(400, f"unknown field '{decision.field}'")
            av.value = decision.value
            av.confidence = 1.0
            av.method = "human_override"
            av.lov_compliant = True

    p.review_flags = [f for f in p.review_flags if f.field != decision.field]
    if not p.review_flags and not p.errors:
        from backend.models.product import ProcessingStatus
        p.status = ProcessingStatus.SUCCESS

    # Re-project the corrected product onto the delivery-format row so the export
    # reflects the override immediately.
    from backend.pipeline.row_builder import RowBuilder
    idx = job.result.products.index(p)
    job.result.rows[idx] = RowBuilder(job.result.schema).build(p)
    return {"row_index": p.row_index, "status": p.status.value,
            "remaining_flags": len(p.review_flags)}


@app.get("/api/jobs/{job_id}/export")
def export(job_id: str, fmt: str = Query("csv", pattern="^(csv|xlsx|review)$")) -> FileResponse:
    job = _require_result(job_id)
    path = job.exports.get(fmt)
    if not path or not Path(path).exists():
        raise HTTPException(404, f"no {fmt} export available")
    media = ("text/csv" if fmt in ("csv", "review")
             else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return FileResponse(path, media_type=media, filename=Path(path).name)


@app.get("/api/jobs/{job_id}/evaluation")
def evaluate(job_id: str, labelled: Optional[str] = None) -> Dict[str, Any]:
    """Score a finished job against a labelled delivery-format file."""
    job = _require_result(job_id)
    assert job.result is not None
    from evaluation.metrics import EvaluationReport, align_by_key, compare_rows

    gold_path = Path(labelled) if labelled else settings.delivery_template
    if not gold_path or not Path(gold_path).exists():
        raise HTTPException(404, "no labelled file available")
    gold_rows = read_table(Path(gold_path))
    gold_rows = [g for g in gold_rows if any((g.get(k) or "").strip()
                                             for k in ("MANUFACTURER_NAME", "Classpath"))]
    if not gold_rows:
        raise HTTPException(400, "labelled file has no scored rows")

    pred, gold, missing = align_by_key(job.result.rows, gold_rows)
    metrics = compare_rows(pred, gold, job.result.schema.headers)
    quality = compute_quality_metrics(job.result.products)
    report = EvaluationReport(
        rows_evaluated=len(pred),
        fields=[m for m in metrics if m.comparable > 0],
        char_compliance=quality["char_compliance"],
        lov_compliance=quality["lov_compliance"],
        evidence_coverage=quality["evidence_coverage"],
        review_rate=quality["review_rate"],
        validation_pass_rate=quality["validation_pass_rate"],
        mean_confidence=quality["mean_confidence"],
        schema_conformant=True,
    )
    if missing:
        report.notes.append(f"{len(missing)} labelled row(s) not present in this job")
    return report.as_dict()


# ---------------------------------------------------------------------------
# Static frontend (served when the SPA has been built)
# ---------------------------------------------------------------------------
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
