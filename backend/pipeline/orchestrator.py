"""
Pipeline orchestrator.

Runs the enrichment stages in order for a batch of raw rows.  Design points:

* **Per-row isolation.**  Every row is processed inside a try/except; a malformed
  product yields a FAILED record with its traceback captured, and the run continues.
* **Corpus-aware two-pass.**  Pass 1 resolves entities and classifies every row;
  the part-number prefix learner is then fitted on the confidently-branded rows and
  pass 2 revisits only the rows that are still missing a brand.  This is why the
  system improves with scale instead of degrading.
* **Stage toggles.**  Every stage reads ``settings.stages`` so the architecture is
  genuinely modular.
* **Caching and dedup.**  Identical (description, supplier) pairs -- extremely common
  in colour/length variant families -- reuse the classification and extraction result.
"""
from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend.agents.asset_agent import AssetAgent
from backend.agents.attribute_agent import AttributeExtractor, apply_series_and_model
from backend.agents.brand_inference import MpnBrandInference, Observation
from backend.agents.description_agent import DescriptionGenerator, DescriptionSpec
from backend.agents.brand_resolver import BrandResolver
from backend.agents.manufacturer_resolver import ManufacturerResolver, to_field_value
from backend.agents.product_parser import ProductParser
from backend.agents.research_agent import ResearchAgent
from backend.agents.taxonomy_agent import TaxonomyAgent
from backend.agents.validation_agent import ValidationAgent
from backend.llm.client import LlmClient
from backend.retrieval.collections import CollectionBuilder, CollectionSet
from backend.config import Settings, get_settings
from backend.models.product import (
    AttributeValue, EnrichedProduct, Evidence, FieldValue, ProcessingStatus, SourceTier,
)
from backend.normalization.text import clean_record, strip_leading_part_number
from backend.pipeline.confidence import apply_review_policy, score_product
from backend.pipeline.row_builder import RowBuilder
from backend.reference.bootstrap import match_key
from backend.reference.registry import ReferenceRegistry
from backend.reference.schema import ManufacturerRecord
from backend.validation.content_rules import ContentValidator
from backend.validation.schema import OutputSchema

INPUT_FIELDS = ("Mfg_Part_Num", "Part_Desc", "E1_Brand", "Unilog_Brand",
                "DIB_Brand", "Part_Manuf")

BRAND_CONFIDENT_ENOUGH_TO_TEACH = 0.65


@dataclass
class RunStats:
    total: int = 0
    success: int = 0
    partial: int = 0
    needs_review: int = 0
    failed: int = 0
    cache_hits: int = 0
    seconds: float = 0.0
    stage_timings: Dict[str, float] = field(default_factory=dict)
    agents: Dict[str, Any] = field(default_factory=dict)
    llm: Dict[str, Any] = field(default_factory=dict)
    retrieval: Dict[str, Any] = field(default_factory=dict)
    lov_rescues: int = 0
    workers: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total, "success": self.success, "partial": self.partial,
            "needs_review": self.needs_review, "failed": self.failed,
            "cache_hits": self.cache_hits, "seconds": round(self.seconds, 2),
            "stage_timings": {k: round(v, 3) for k, v in self.stage_timings.items()},
            "throughput_per_sec": round(self.total / self.seconds, 2) if self.seconds else 0.0,
            "agents": dict(self.agents),
            "llm": dict(self.llm),
            "retrieval": dict(self.retrieval),
            "lov_rescues": self.lov_rescues,
            "workers": self.workers,
        }


@dataclass
class RunResult:
    products: List[EnrichedProduct]
    rows: List[Dict[str, str]]
    stats: RunStats
    schema: OutputSchema
    reference_summary: Dict[str, Any]

    @property
    def review_queue(self) -> List[EnrichedProduct]:
        return [p for p in self.products if p.needs_review]


class Orchestrator:
    """End-to-end enrichment for a batch of raw rows."""

    def __init__(self, settings: Optional[Settings] = None,
                 registry: Optional[ReferenceRegistry] = None,
                 schema: Optional[OutputSchema] = None,
                 asset_manifest: Optional[Sequence[str]] = None):
        self.settings = settings or get_settings()
        self.registry = registry
        self.schema = schema
        self.asset_manifest = list(asset_manifest or ())
        self._research: Optional[ResearchAgent] = None
        self._extract_cache: Dict[Tuple[str, str], List[AttributeValue]] = {}
        self.llm: Optional[LlmClient] = None
        self.collections: Optional[CollectionSet] = None
        self._lock = threading.Lock()
        self._lov_rescues = 0

    # ------------------------------------------------------------------
    def prepare(self, rows: Sequence[Dict[str, Any]]) -> None:
        """Build reference data (mining from this corpus when no official pack exists)."""
        if self.registry is None:
            self.registry = ReferenceRegistry.build(
                self.settings.reference_dir, self.settings.pack_dir, rows)
        if self.schema is None:
            template = self.settings.delivery_template
            if template is None:
                raise RuntimeError(
                    "no delivery-format template found; place the expected-output "
                    "CSV/XLSX in data/raw/ or set settings.delivery_template")
            self.schema = OutputSchema.from_file(Path(template))

        # The LLM is optional everywhere: when no key is present the client reports
        # available=False and every agent below stays on its deterministic path.
        self.llm = LlmClient(self.settings)

        # RAG collections (brief section 8). Built once per run and shared.
        self.collections = CollectionBuilder(
            self.registry, self.settings.reference_dir).build(self._labelled_examples())

        self.manufacturer_resolver = ManufacturerResolver(self.registry)
        self.brand_resolver = BrandResolver(self.registry)
        self.parser = ProductParser(self.registry.uom, self.llm)
        self.taxonomy_agent = TaxonomyAgent(self.registry.taxonomy, self.llm)
        self.extractor = AttributeExtractor(self.registry.uom)
        spec = (DescriptionSpec.from_guidelines(self.registry.guideline_blocks,
                                                self.settings.pack_dir)
                if self.registry.guideline_blocks
                else DescriptionSpec.load(self.settings.pack_dir))
        self.description_spec = spec
        self.generator = DescriptionGenerator(self.registry.uom, spec)
        self.validator = ContentValidator(self.schema, self.registry.uom, spec.limits)
        self.validation_agent = ValidationAgent(self.validator, spec.limits)
        self.row_builder = RowBuilder(self.schema)
        self.asset_agent = AssetAgent(self.asset_manifest)
        self._research = ResearchAgent(self.settings)

    # ------------------------------------------------------------------
    def run(self, rows: Sequence[Dict[str, Any]],
            progress: Optional[Callable[[int, int, str], None]] = None) -> RunResult:
        import time
        started = time.time()
        self.prepare(rows)
        stats = RunStats(total=len(rows))

        workers = self._worker_count(len(rows))
        stats.workers = workers

        # -- pass 1: clean, parse, resolve, classify --------------------
        products = self._map(list(enumerate(rows)), self._pass_one_safe, workers,
                             progress, "resolve+classify")

        # -- corpus learning: part-number prefix -> brand ----------------
        inference = self._fit_inference(products)

        # -- pass 2: enrich, describe, validate, score -------------------
        def enrich(item):
            idx, prod = item
            if prod.error:
                prod.status = ProcessingStatus.FAILED
                return prod
            try:
                self._pass_two(prod, inference)
            except Exception as exc:
                prod.error = f"{type(exc).__name__}: {exc}"
                prod.log_stage("error", traceback.format_exc(limit=3))
                prod.status = ProcessingStatus.FAILED
            return prod

        products = self._map(list(enumerate(products)), enrich, workers,
                             progress, "enrich+validate")

        for prod in products:
            if prod.status == ProcessingStatus.SUCCESS:
                stats.success += 1
            elif prod.status == ProcessingStatus.NEEDS_REVIEW:
                stats.needs_review += 1
            elif prod.status == ProcessingStatus.FAILED:
                stats.failed += 1
            else:
                stats.partial += 1

        out_rows = [self.row_builder.build(p) for p in products]
        stats.cache_hits = len(self._extract_cache)
        stats.lov_rescues = self._lov_rescues
        stats.agents = {"taxonomy": self.taxonomy_agent.stats()}
        stats.llm = self.llm.status() if self.llm else {}
        stats.retrieval = self.collections.stats() if self.collections else {}
        stats.seconds = time.time() - started

        assert self.registry is not None and self.schema is not None
        return RunResult(products, out_rows, stats, self.schema, self.registry.summary())

    # ------------------------------------------------------------------
    # Pass 1
    # ------------------------------------------------------------------
    def _pass_one(self, index: int, raw: Dict[str, Any]) -> EnrichedProduct:
        p = EnrichedProduct(row_index=index, raw=dict(raw))

        # -- cleaning ---------------------------------------------------
        if self.settings.stages.get("cleaning", True):
            cleaned = clean_record(raw, INPUT_FIELDS)
            p.cleaned = cleaned.values
            p.placeholders = cleaned.placeholders
            p.log_stage("cleaning",
                        f"{len(cleaned.placeholders)} placeholder field(s) neutralised",
                        placeholders=cleaned.placeholders)
        else:
            p.cleaned = {k: (str(raw.get(k)) if raw.get(k) is not None else None)
                         for k in INPUT_FIELDS}

        mpn = p.cleaned.get("Mfg_Part_Num")
        desc = p.cleaned.get("Part_Desc") or ""
        p.mpn = FieldValue(value=mpn, confidence=1.0 if mpn else 0.0,
                           method="input_passthrough", raw=mpn,
                           transformation="manufacturer part number taken from the feed",
                           evidence=[Evidence(source="input_feed:Mfg_Part_Num",
                                              tier=SourceTier.INPUT_FEED.value,
                                              snippet=mpn or "")] if mpn else [])

        # -- entity resolution ------------------------------------------
        if self.settings.stages.get("entity_resolution", True):
            supplier = self.manufacturer_resolver.resolve(p.cleaned.get("Part_Manuf"))
            p.supplier = to_field_value(supplier, p.cleaned.get("Part_Manuf"),
                                        "master_data:manufacturer")
            brand = self.brand_resolver.resolve(
                [p.cleaned.get("E1_Brand"), p.cleaned.get("DIB_Brand")],
                desc, supplier.record if isinstance(supplier.record, ManufacturerRecord) else None)
            p.brand = to_field_value(brand, p.cleaned.get("E1_Brand") or desc,
                                     "master_data:brand")
            mor = self.brand_resolver.manufacturer_of_record(brand, supplier)
            p.manufacturer = to_field_value(mor, p.cleaned.get("Part_Manuf"),
                                            "master_data:manufacturer")
            p.manufacturer_code = FieldValue(
                value=mor.code, confidence=p.manufacturer.confidence,
                method=mor.method, transformation="manufacturer code from master data")
            p._brand_resolution = brand          # type: ignore[attr-defined]
            p._supplier_resolution = supplier    # type: ignore[attr-defined]
            p.log_stage("entity_resolution",
                        f"supplier={supplier.value}; brand={brand.value}; "
                        f"manufacturer={mor.value}")

        # -- product parsing (Agent 1) ----------------------------------
        # Structured read of what the row literally says, before any resolution.
        parsed = self.parser.parse_with_llm(raw)
        p.parsed = parsed.as_dict()
        p.log_stage("product_parsing",
                    f"type={parsed.product_type!r} materials={parsed.materials} "
                    f"specs={list(parsed.technical_specs)} via {parsed.method}")

        # -- classification (Agent 4) -----------------------------------
        if self.settings.stages.get("classification", True):
            assert self.registry is not None and self.registry.taxonomy is not None
            text = strip_leading_part_number(desc, mpn)
            decision = self.taxonomy_agent.classify(desc, parsed.product_type)
            cls = decision.classification
            leaf = cls.leaf
            p.leaf_id = leaf.id if leaf else None
            p.classification_candidates = [
                {"leaf_id": c.leaf.id, "classpath": c.leaf.classpath,
                 "score": c.score, "matched": c.matched}
                for c in cls.candidates
            ]
            ev = [TaxonomyAgent.evidence_for(decision, text)]
            if leaf is not None:
                conf = cls.confidence
                p.classpath = FieldValue(leaf.classpath or None, conf, cls.method,
                                         cls.explanation, text, list(ev))
                p.dept = FieldValue(leaf.dept or None, conf, cls.method, cls.explanation)
                p.klass = FieldValue(leaf.klass or None, conf, cls.method, cls.explanation)
                p.fine = FieldValue(leaf.fine or None, conf, cls.method, cls.explanation)
                p.product_name = FieldValue(leaf.product_name or None, conf, cls.method,
                                            "product name from the taxonomy leaf", None, list(ev))
                p.unspsc = FieldValue(leaf.unspsc or None, conf, cls.method,
                                      "UNSPSC from the taxonomy leaf")
            p.log_stage("classification",
                        f"leaf={p.leaf_id} confidence={cls.confidence:.2f} "
                        f"({cls.explanation}); {decision.note}")
            if decision.escalated and not decision.llm_used:
                p.flag("Classification could not be settled automatically", "Classpath",
                       decision.note)
        return p

    # ------------------------------------------------------------------
    def _fit_inference(self, products: Sequence[EnrichedProduct]) -> MpnBrandInference:
        obs: List[Observation] = []
        for p in products:
            if p.error:
                continue
            obs.append(Observation(
                mpn=p.mpn.value or "",
                brand=p.brand.value if p.brand.confidence >= BRAND_CONFIDENT_ENOUGH_TO_TEACH
                else None,
                category=p.leaf_id,
                supplier=p.supplier.value,
            ))
        return MpnBrandInference().fit(obs)

    # ------------------------------------------------------------------
    # Pass 2
    # ------------------------------------------------------------------
    def _pass_two(self, p: EnrichedProduct, inference: MpnBrandInference) -> None:
        assert self.registry is not None and self.registry.taxonomy is not None
        desc = p.cleaned.get("Part_Desc") or ""
        mpn = p.mpn.value

        # -- brand recovery via corpus part-number prefixes --------------
        if not p.brand.present and mpn:
            rule = inference.infer(mpn, p.leaf_id, p.supplier.value)
            if rule is not None:
                p.brand = FieldValue(
                    value=rule.brand, confidence=rule.confidence,
                    method="mpn_prefix_inference",
                    transformation=rule.detail(p.leaf_id, p.supplier.value),
                    raw=mpn,
                    evidence=[Evidence(source="corpus:mpn_prefix",
                                       tier=SourceTier.INPUT_FEED.value,
                                       snippet=", ".join(rule.examples[:3]),
                                       locator=f"prefix {rule.prefix}")])
                p.flag("Brand inferred from part-number prefix", "BRAND_NAME",
                       rule.detail(p.leaf_id, p.supplier.value), rule.brand)
                p.log_stage("brand_inference", p.brand.transformation)

        leaf = self.registry.taxonomy.get(p.leaf_id) if p.leaf_id else None
        if leaf is None:
            leaf = self.registry.taxonomy.fallback
        if leaf is None:
            p.error = "no taxonomy leaf available, not even the fallback"
            p.status = ProcessingStatus.FAILED
            return

        # -- manufacturer research --------------------------------------
        research = None
        if self.settings.stages.get("manufacturer_research", True) and self._research:
            research = self._research.research(mpn or "", p.brand.value,
                                               p.manufacturer.value,
                                               p.product_name.value or "")
            if research.manufacturer_url:
                p.urls["mfr"] = research.manufacturer_url
            for i, u in enumerate(research.reference_urls[:5], start=1):
                p.urls[f"ref{i}"] = u
            p.log_stage("manufacturer_research",
                        research.skipped_reason or
                        f"{len(research.reference_urls)} approved source(s)")

        # -- attribute extraction (cached by description+leaf) -----------
        cache_key = (leaf.id, desc)
        if self.settings.stages.get("attribute_extraction", True):
            cached = self._extract_cache.get(cache_key)
            if cached is not None:
                attrs = [AttributeValue(**{**a.__dict__, "evidence": list(a.evidence)})
                         for a in cached]
            else:
                extra = [research.marketing_copy] if research and research.marketing_copy else []
                attrs = self.extractor.extract(leaf, desc, extra)
                self._extract_cache[cache_key] = attrs
            apply_series_and_model(attrs, desc, mpn)
            p.attributes = attrs
            p.log_stage("attribute_extraction",
                        f"{len(p.populated_attributes())}/{len(attrs)} slots populated")

        # -- 'With' clause ------------------------------------------------
        wc = self.generator.with_clause(desc)
        if wc:
            clause, snippet = wc
            p.with_clause = FieldValue(
                clause, 0.9, "regex",
                "'w/' shorthand in the supplier description expanded verbatim",
                snippet,
                [Evidence(source="input_feed:Part_Desc", tier=SourceTier.INPUT_FEED.value,
                          snippet=snippet)])

        # -- descriptions -------------------------------------------------
        if self.settings.stages.get("description_generation", True):
            p.descriptions = self.generator.generate(
                leaf,
                brand=self._brand_display(p),
                manufacturer=p.manufacturer.value,
                mpn=mpn,
                product_name=p.product_name.value or leaf.product_name or "",
                attributes=p.attributes,
                with_clause_text=p.with_clause.value,
                marketing=research.marketing_copy if research else None,
            )
            if research and research.features:
                p.features = research.features[:20]
            p.log_stage("description_generation",
                        f"{sum(1 for f in p.descriptions.values() if f.present)} fields composed")

        # -- digital assets -----------------------------------------------
        if self.settings.stages.get("digital_assets", True):
            plan = self.asset_agent.plan_names(
                p.brand.value, mpn,
                document_keys=list((research.documents if research else {}).keys()),
                alternate_count=0)
            plan = self.asset_agent.confirm(plan, research.documents if research else None)
            p.assets = plan.as_row_assets()
            unconfirmed = self.asset_agent.unconfirmed(plan)
            p.log_stage("digital_assets",
                        f"{len(p.assets)} confirmed; {len(unconfirmed)} predicted but "
                        f"unconfirmed (not published)")

        # -- LOV rescue (adaptive retrieval) --------------------------------
        # An extracted value that missed the controlled vocabulary gets one shot at a
        # retrieval-backed mapping before validation clears it. The shortlist is
        # category-scoped, so this stays cheap and never sees the whole vocabulary.
        if self.settings.stages.get("normalization", True):
            self._rescue_lov_values(p, leaf)

        # -- validation (Agent 7) -------------------------------------------
        row = self.row_builder.build(p)
        if self.settings.stages.get("validation", True):
            outcome = self.validation_agent.validate(
                p, row, leaf, rebuild_row=lambda: self.row_builder.build(p))
            p.log_stage("validation",
                        f"{outcome.checks_passed}/{outcome.checks_run} checks passed; "
                        f"{len(outcome.errors)} error(s) after {outcome.passes} pass(es)")
            for repair in outcome.repairs:
                p.log_stage("self_correction", repair)
            validation_failed = not outcome.valid
        else:
            validation_failed = False

        # -- confidence and review -----------------------------------------
        if self.settings.stages.get("confidence_scoring", True):
            result = score_product(p, validation_failed)
            p.confidence = result.score
            p.confidence_breakdown = result.breakdown
        if self.settings.stages.get("review_queue", True):
            p.status = apply_review_policy(p)
        else:
            p.status = ProcessingStatus.SUCCESS if not validation_failed \
                else ProcessingStatus.PARTIAL

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _worker_count(self, n_rows: int) -> int:
        """
        Threads to use for this batch -- decided by measurement, not by default.

        Benchmarked on the 1,000-row feed with every network stage off:

            workers= 1   5.42s   184.5 rows/s
            workers= 2   5.45s   183.4 rows/s
            workers= 4   4.97s   201.1 rows/s
            workers= 8   5.38s   185.9 rows/s
            workers=16   5.08s   196.9 rows/s

        i.e. no real speedup. Offline the per-row work is pure-Python regex and dict
        lookups, so the GIL serialises it and extra threads only add contention.
        Threads pay off only when there is I/O to overlap -- manufacturer research
        and LLM calls -- so the pool is engaged only when one of those is live.

        Threads rather than processes because the shared reference registry and the
        RAG indexes would have to be pickled to every worker otherwise, which costs
        far more than the enrichment itself.
        """
        configured = max(1, int(self.settings.max_workers or 1))
        if configured == 1 or n_rows < 64:
            return 1

        network_bound = bool(
            (self.settings.research_enabled
             and self.settings.stages.get("manufacturer_research", True))
            or (self.llm is not None and self.llm.available)
        )
        if not network_bound:
            return 1                       # CPU-bound: threads measurably do not help
        return min(configured, 32)

    def _map(self, items: Sequence[Any], fn: Callable[[Any], EnrichedProduct],
             workers: int, progress: Optional[Callable[[int, int, str], None]],
             phase: str) -> List[EnrichedProduct]:
        """
        Apply ``fn`` across items, preserving input order.

        Order preservation matters: row N of the export must correspond to row N of
        the input, so results are written back by index rather than appended as they
        complete.
        """
        total = len(items)
        results: List[Optional[EnrichedProduct]] = [None] * total
        done = 0

        def report() -> None:
            if progress and (done % 25 == 0 or done == total):
                progress(done, total, phase)

        if workers <= 1:
            for i, item in enumerate(items):
                results[i] = fn(item)
                done += 1
                report()
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        results[i] = fut.result()
                    except Exception as exc:              # belt and braces
                        prod = EnrichedProduct(row_index=i)
                        prod.error = f"{type(exc).__name__}: {exc}"
                        prod.status = ProcessingStatus.FAILED
                        results[i] = prod
                    done += 1
                    report()

        return [r for r in results if r is not None]

    def _pass_one_safe(self, item: Any) -> EnrichedProduct:
        """Index/row pair -> parsed product, isolating any per-row failure."""
        if isinstance(item, tuple):
            i, raw = item
        else:                                              # plain row, index unknown
            i, raw = 0, item
        try:
            return self._pass_one(i, raw)
        except Exception as exc:
            prod = EnrichedProduct(row_index=i, raw=dict(raw) if raw else {})
            prod.error = f"{type(exc).__name__}: {exc}"
            prod.log_stage("error", traceback.format_exc(limit=3))
            return prod

    # ------------------------------------------------------------------
    def _labelled_examples(self) -> List[Dict[str, str]]:
        """
        Labelled delivery-format rows for RAG collection G.

        Retrieved for *formatting patterns* only -- never copied into output, which
        is the hard-coding the brief forbids.
        """
        template = self.settings.delivery_template
        if template is None or not Path(template).exists():
            return []
        try:
            from backend.pipeline.io import read_table
            rows = read_table(Path(template))
        except Exception:
            return []
        return [r for r in rows
                if any(str(r.get(k, "")).strip() for k in ("MANUFACTURER_NAME", "Classpath"))]

    def _rescue_lov_values(self, p: EnrichedProduct, leaf) -> None:
        """
        Retrieval-backed second chance for values that missed the vocabulary.

        Runs only on attributes already marked ``lov_compliant is False``, so the
        common path costs nothing. A rescued value must come back as an *exact*
        alias hit within the same attribute -- a merely-similar or relaxed hit is
        recorded as a review suggestion instead of being applied, because a
        near-miss mapping is exactly the kind of silent corruption Golden Rule 2
        exists to prevent.
        """
        if self.collections is None or leaf is None:
            return
        classpath = leaf.classpath or ""
        for av in p.attributes:
            if not av.present or av.lov_compliant is not False:
                continue
            hits = self.collections.adaptive_lov(classpath, av.label, av.value or "", k=3)
            if not hits:
                continue
            top = hits[0]
            if top.exact and not top.relaxed and top.payload.get("label") == av.label:
                before = av.value
                av.value = top.payload.get("value")
                av.lov_compliant = True
                av.method = "lov_retrieval"
                av.confidence = min(0.92, av.confidence + 0.05)
                av.transformation = (f"retrieval mapped {before!r} onto approved value "
                                     f"{av.value!r} within {av.label}")
                av.evidence.append(Evidence(
                    source="rag:lov", tier=SourceTier.CONTROLLED_VOCAB.value,
                    snippet=str(before), locator=f"{classpath}|{av.label}"))
                with self._lock:
                    self._lov_rescues += 1
                p.log_stage("lov_rescue", av.transformation)
            else:
                p.flag("Attribute value outside the controlled vocabulary",
                       f"ATTRIBUTE:{av.label}",
                       f"nearest approved value: {top.payload.get('value')!r} "
                       f"(score {top.score:.2f}{', relaxed scope' if top.relaxed else ''})",
                       top.payload.get("value"))

    # ------------------------------------------------------------------
    def _brand_display(self, p: EnrichedProduct) -> Optional[str]:
        """Brand as it should appear in copy, including any trademark symbol."""
        if not p.brand.present:
            return None
        assert self.registry is not None
        symbol = ""
        if self.registry.brand_lexicon is not None:
            symbol = self.registry.brand_lexicon.symbol_for(p.brand.value or "")
        if not symbol:
            rec = self.registry.brand_by_key(match_key(p.brand.value or ""))
            symbol = rec.symbol if rec else ""
        return f"{p.brand.value}{symbol}" if symbol else p.brand.value

    def _self_correct(self, p: EnrichedProduct, leaf, row: Dict[str, str]) -> None:
        """
        Regenerate only the fields that failed, never the whole product.

        Character-limit overflow is fixed by re-composing the offending field with a
        tighter budget; an LOV violation clears the value rather than publishing an
        unapproved one.
        """
        for issue in list(p.errors):
            if issue.code == "char_limit_exceeded" and issue.field in p.descriptions:
                limit = self.description_spec.limits.get(issue.field)
                fv = p.descriptions[issue.field]
                if limit and fv.present:
                    from backend.normalization.text import truncate
                    trimmed = truncate(fv.value or "", limit)
                    fv.value = trimmed
                    fv.notes.append(f"self-corrected: trimmed to the {limit}-character limit")
                    fv.confidence = max(0.0, fv.confidence - 0.05)
                    p.log_stage("self_correction",
                                f"{issue.field} trimmed to {len(trimmed)} characters")
            elif issue.code == "lov_violation" and issue.field.startswith("ATTRIBUTE:"):
                label = issue.field.split(":", 1)[1]
                av = p.attribute(label)
                if av is not None:
                    av.value = None
                    av.confidence = 0.0
                    av.method = "cleared_lov_violation"
                    av.transformation = ("value was not in the controlled vocabulary and "
                                         "was cleared rather than published")
                    p.log_stage("self_correction", f"cleared unapproved value for {label}")

    def close(self) -> None:
        if self._research is not None:
            self._research.close()
