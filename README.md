# Unilog Product Intelligence

An evidence-grounded enrichment pipeline that turns messy industrial catalogue rows
into a validated, commerce-ready 252-column delivery-format dataset.

```
Mfg_Part_Num  Part_Desc                                E1_Brand          Part_Manuf
WDTS7024RZ    WDTS7024RZ Dishwasher SS - Display Only  -- Unbranded --   Appliance Dealers Cooperative (APPDE)
```

becomes a fully classified, attributed, described, validated and confidence-scored
record — with every value traceable to the evidence that produced it.

---

## 1. What was built

A complete, running system. Not a mockup, and nothing hard-coded to the sample data.

| Layer | What it does | Status |
|---|---|---|
| **Deterministic core** | fraction ladder, UOM registry, placeholder detection, encoding repair, casing | working, 136 tests |
| **Reference layer** | loads official Unilog workbooks when present; **mines** masters from the working data when not | working |
| **Entity resolution** | 6-stage match cascade with margin tests and distributor detection | working |
| **Taxonomy** | 36 leaf nodes, 323 controlled values, explainable keyword classifier | working |
| **Attribute extraction** | LOV synonyms → regex → dimension chains → UOM scan | working |
| **Description engine** | template over a verified fact sheet; **reproduces the labelled rows character-exactly** | working |
| **Research agent** | manufacturer-source hierarchy enforced structurally | built; **needs a search key to run** |
| **Validation** | 8 check groups, self-correction loop | working |
| **Confidence** | 6 components, multiplicative validation gate | working |
| **Human review** | specific, actionable flags with in-UI override | working |
| **API** | 15 FastAPI endpoints | working |
| **Dashboard** | 7-page React/Tailwind SPA | working |
| **Evaluation** | field accuracy, semantic F1, LOV/char compliance, evidence coverage | working |
| **AI agents** | 8 agents incl. product parser, taxonomy tie-break, validation | working, model-optional |
| **LLM client** | strict JSON, repair, retry, prompt-hash cache, offline mode | working, **wired into 3 stages** |
| **RAG retrieval** | 7 collections, adaptive category-scoped lookup | working, 751 documents |
| **Submission deck** | built onto the UniHack prototype template | working |

### Headline results

**1,000-row scale run** (`data/raw/sample_1000_input.csv`, no API keys, no network):

| Metric | Result |
|---|---|
| Rows processed | 1,000 in **5.7 s** (174 rows/sec) |
| Failures | **0** |
| Classified to a real leaf | **94.7 %** |
| Brand resolved | **87.5 %** |
| Manufacturer resolved | **86.9 %** |
| Descriptions generated | **100 %** (5 fields each) |
| LOV compliance | **100 %** (1,558 / 1,558) |
| Attribute evidence coverage | **100 %** (2,743 / 2,743) |
| Rows with zero validation errors | **94.7 %** |
| Mean confidence | 0.727 (median 0.783) |
| Routed to human review | 46.7 % |
| RAG documents indexed | 751 across 7 collections |

**Against the labelled delivery-format rows**, the description engine reproduces
**all 10 derivable description fields character-for-character** — including the
390- and 405-character `LONG_DESC1` strings and both 40-character `INVOICE_DESC`
strings. See §7.

---

## 2. The core idea

Most LLM pipelines are `Input → LLM → Output`, which hallucinates and cannot be
audited. This one puts the model inside a deterministic cage:

```
Input
  ↓  deterministic  cleaning, placeholder detection, encoding repair
  ↓  deterministic  entity resolution against master data
  ↓  deterministic  product parsing (Agent 1; LLM only when the row is opaque)
  ↓  deterministic  taxonomy classification (LLM tie-break only when ambiguous)
  ↓  retrieval      category-scoped LOV shortlist from the RAG collections
  ↓  deterministic  attribute extraction (LOV → regex → dimension chain → UOM scan)
  ↓  retrieval      LOV rescue for values that missed the vocabulary
  ↓  deterministic  fraction / UOM / vocabulary normalisation
  ↓  evidence       manufacturer research under a source hierarchy
  ↓  template       description generation over a VERIFIED FACT SHEET
  ↓  deterministic  validation + self-correction
  ↓  deterministic  confidence scoring
  ↓  human          review queue
FINAL 252-COLUMN OUTPUT
```

> **The model is optional at every stage.** Each of the three AI call sites has a
> deterministic fallback, so every metric in this README is reproducible with no API
> key and no network — the figures below were produced exactly that way. With a key
> present the model is consulted only where the deterministic path is genuinely
> stuck: ~5 % of rows.

**The description generator never sees raw text.** It receives only resolved
entities and extracted attributes, so it is structurally incapable of introducing a
fact that is not already evidenced.

### The three golden rules, enforced in code

| Rule | How it is enforced |
|---|---|
| **Never hallucinate** | Values come only from the input, an approved vocabulary, manufacturer evidence, or exact arithmetic. Unknown → blank + flagged. `MARKETING_DESCRIPTION` is left empty unless real manufacturer copy was retrieved. Asset filenames are only published when something confirms the file exists. |
| **Controlled vocabulary wins** | `SS → Stainless Steel` only via a LOV synonym. A value outside the vocabulary is a validation **error**; the self-correction loop clears it rather than publishing it. |
| **Manufacturer source hierarchy** | `classify_source()` scores a URL *before* fetching. Amazon/eBay/social are rejected outright; distributors score 0.35 and are fallback-only; manufacturer product pages score 1.0. Every accepted claim stores URL + snippet + timestamp. |

---

## 3. Quick start

Verified on Python 3.13 / Node 20+ on Windows, macOS and Linux.

```bash
git clone https://github.com/kumarrishuraj/unilog-ai-product-intelligence.git
cd unilog-ai-product-intelligence

python -m venv .venv
source .venv/bin/activate           # macOS / Linux
# .venv\Scripts\activate            # Windows PowerShell / cmd

pip install -r requirements.txt
```

**No API key is required.** The pipeline runs fully offline; see §3.1.

### Enrich the bundled 1,000-row feed

```bash
python scripts/run_pipeline.py --input data/raw/sample_1000_input.csv --format both
```

Writes to `data/processed/`:
`*_delivery_format.csv` · `*_delivery_format.xlsx` · `*_review_queue.csv` ·
`*_run_report.json` · `*_products.json` · `input_profile.json`

A committed copy of exactly this output is in [`docs/sample_output/`](docs/sample_output)
so you can inspect it without running anything.

### Score against the labelled delivery-format rows

```bash
python evaluation/benchmark.py     --labelled data/raw/delivery_format_labelled.csv     --input    data/raw/sample_1000_input.csv
```

### Walk one product through all eleven stages, with its evidence

```bash
python scripts/demo.py --mpn 49-94-0013
```

### Run the dashboard

```bash
cd frontend && npm install && npm run build && cd ..
uvicorn backend.api.main:app --port 8000
# open http://127.0.0.1:8000
```

Frontend dev server with hot reload (API proxied to :8000):

```bash
cd frontend && npm run dev      # http://localhost:5173
```

Docker (builds the frontend and serves everything on one port):

```bash
docker compose up --build       # http://localhost:8000
```

### Tests

```bash
python -m pytest tests -q       # 136 passed
```

---

## 3.1 Offline mode, and the optional API integrations

**This project is offline-first by design, not by limitation.**

Every metric quoted in this README was produced with **no API key and no network
access**, which is what makes them reproducible by anyone who clones the repo. Each
AI-assisted stage has a deterministic fallback, so nothing breaks when a provider is
absent — the system simply resolves less and flags more, and says so in the UI.

| | Offline (default) | With an LLM key | With a search key |
|---|---|---|---|
| Cleaning · placeholders · encoding | full | full | full |
| Entity resolution | full | full | full |
| Classification | keyword scoring; ambiguous rows flagged | + model tie-break on ~5% of rows | unchanged |
| Product parsing | deterministic | + grounded model assist on opaque rows | unchanged |
| Attribute extraction | LOV → regex → dimension chain → UOM | + synonym interpretation | + extraction from retrieved manufacturer prose |
| Manufacturer research | skipped, reason reported | unchanged | live, under the source hierarchy |
| Descriptions | full | full | + real `MARKETING_DESCRIPTION`, features, documents |
| Validation · confidence · review | full | full | full |

Nothing is silently degraded: `GET /api/config` and the dashboard header both show
whether the LLM and research are live, and the run report records
`stats.llm` and `stats.agents` so the model's actual contribution is measurable.

### Enabling the optional integrations

```bash
cp .env.example .env
```

Then set whichever you have. **`.env` is git-ignored — never commit it.**

```env
# LLM — optional. provider: auto | anthropic | openai
LLM_PROVIDER=auto
LLM_API_KEY=sk-...
LLM_MODEL=claude-sonnet-5

# Manufacturer research — optional. provider: none | tavily | serper
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=tvly-...
RESEARCH_ENABLED=true
```

The LLM is consulted at exactly **three guarded call sites** (§6), never as a
general-purpose generator. A search key is the higher-value addition of the two: it
is what populates `MFR URL`, the `Ref URL` family, `ITEM_FEATURES`,
`MARKETING_DESCRIPTION` and the document/image columns.

---

## 4. Reference data — read this

The brief describes a reference pack (UniCat manufacturer/brand list, Unicat LOV,
UOM standards, Decimal_Fraction, FAUCETS_LOV, Fittings_LOV, the content-guidelines
`.docx`). **Those files were not provided with this repository** — only the two
CSVs were. The system was therefore built with a four-tier reference layer that
works either way, and **reports which tier is live** so nothing is ever presented as
more authoritative than it is.

| Tier | Meaning | Currently live |
|---|---|---|
| `official` | loaded from a supplied Unilog workbook | — |
| `computed` | exact arithmetic | decimal ↔ fraction (63 entries, ladder to 1/64) |
| `derived` | **mined from the working data itself** | 75 manufacturers, 34 brands, 36 taxonomy leaves, 323 LOV values |
| `seed` | standard industry values shipped in `data/packs/` | 68 UOM abbreviations, 91-brand lexicon |

### To switch to official data — no code change

Drop the workbooks into `data/reference/`. They are discovered **by filename
pattern**, ingested with header sniffing and merged-cell handling, and they replace
the derived tier wholesale:

```
data/reference/
├── UniCat_Manufacturer_and_Brand_List.xlsx      → manufacturer + brand master
├── Unicat_Lov_v1_0_Updated_With_Remarks.xlsx    → taxonomy + controlled vocabulary
├── Unilog_Master_UOM_Standards_...xlsx          → UOM registry
├── Decimal_Fraction.xlsx                         → fraction override table
└── UNILOG_INTERNAL_CONTENT_GUIDELINES.docx      → description limits + rules
```

The Dashboard's "Reference data in use" panel shows the live tier for each.

### How the derived masters are mined (not invented)

* `Part_Manuf` carries an embedded code: `Freud Inc (2435)` → name + code.
* Co-occurrence of `(brand, Part_Manuf)` across the corpus yields evidence-backed
  ownership edges. On the supplied feed this recovers genuinely non-obvious real
  relationships: **Diablo → Freud Inc**, **Carlon → Thomas & Betts**,
  **BRK → First Alert**.
* A supplier seen against ≥3 distinct brands is a **distributor/co-op**, not the
  manufacturer. This matters: the labelled rows show `Appliance Dealers Cooperative
  (APPDE)` is a buying co-op while the true `MANUFACTURER_NAME` is the brand owner.
  The resolver refuses to publish a co-op as the manufacturer.

---

## 5. Things the data actually taught us

These are the non-obvious findings that shaped the design.

**The attribute label sequence belongs to the category, not to the extraction.**
Both labelled rows emit `ATTRIBUTE_LABEL n` even where `ATTRIBUTE_VALUE n` is blank
(row 1 has `Model` with no value; row 2 has `Number of Wash Cycles` with no value).
So the template is a property of the leaf node. The row builder emits every slot.

**`MOBILE_DESC` has an 80-character cap.** Row 1 stops at 75 characters and omits
`Leg Mounting`; row 2 includes `Built-in Mounting` and reaches 64. An 80-char budget
is the only value that explains both. Implemented as greedy packing.

**`INVOICE_DESC` has a 40-character cap with house abbreviations.** Both rows land
at 38 and 39. `Stainless Steel → SST`, `Built-in → BLTLN`. Attributes are packed in
a priority order and **skipped when they don't fit** — which is exactly why row 1
carries `50-1/4IN` (Depth) and row 2 carries `41DBA` (Sound Level) instead.

**`RETAIL_DESC` drops the `With` clause.** Row 1's `SHORT_DESC` contains
`With CleanBoost™` but its `RETAIL_DESC` does not.

**Naive brand matching silently corrupts data.** Substring matching turned
`Square Drive Bit` into the Square D brand, `Phillips Drive Bit` into Philips, and
`Heated Glove Blk LG` into LG. Fixed deterministically with word boundaries plus
per-alias context guards (`reject_after`, `require_after`) — zero false positives on
the 1,000-row feed.

**Cross-category part-number inference is dangerous.** An unguarded prefix learner
inferred `SQ Washer → Edge Eyewear` because both part numbers begin `TC`. The
learner now requires ≥3-character prefixes, ≥2 supporting rows, single-brand purity,
**and a shared category or supplier**.

---

## 6. Architecture

```
backend/
├── config.py                  settings, .env, stage toggles
├── normalization/
│   ├── fractions.py           exact decimal ↔ fraction ladder (LLM-free)
│   ├── uom.py                 approved-UOM registry + house spacing
│   └── text.py                placeholder detection, mojibake repair, casing
├── reference/
│   ├── loader.py              robust XLSX/DOCX ingestion (header sniffing, merges)
│   ├── bootstrap.py           mines masters from the working data
│   ├── brand_lexicon.py       guarded brand-mention matcher
│   ├── taxonomy.py            leaf catalogue + explainable classifier
│   └── registry.py            four-tier reference assembly with provenance
├── agents/
│   ├── product_parser.py          Agent 1: structured read, grounded LLM assist
│   ├── manufacturer_resolver.py   Agent 2: 6-stage cascade, margin tests
│   ├── brand_resolver.py          Agent 3: brand + manufacturer of record
│   ├── resolution.py              shared Resolution / fuzzy primitives
│   ├── taxonomy_agent.py          Agent 4: LLM tie-break on ambiguity only
│   ├── validation_agent.py        Agent 7: verdict + self-correction loop
│   ├── brand_inference.py         corpus part-number prefix learner
│   ├── attribute_agent.py         LOV → regex → dimension chain → UOM scan
│   ├── description_agent.py       template over a verified fact sheet
│   ├── research_agent.py          source hierarchy enforcement
│   └── asset_agent.py             naming convention + existence confirmation
├── pipeline/
│   ├── orchestrator.py        two-pass corpus-aware run loop
│   ├── row_builder.py         projection onto the 252 columns
│   ├── confidence.py          scoring + review policy
│   └── io.py                  profiling, CSV/XLSX export
├── retrieval/
│   ├── vector_store.py        lexical index (+ EmbeddingIndex seam)
│   └── collections.py         the 7 RAG collections, adaptive LOV lookup
├── validation/
│   ├── schema.py              252 headers read from the template, never literals
│   └── content_rules.py       8 check groups
├── llm/client.py              provider-agnostic strict-JSON client + cache
├── models/product.py          FieldValue = value + confidence + evidence
└── api/main.py                15 endpoints

data/packs/                    seed packs (JSON, human-editable)
evaluation/                    metrics + benchmark CLI
frontend/src/                  React + Tailwind dashboard
tests/                         136 tests
```

### What is and is not in the repository

| Committed | Not committed (and why) |
|---|---|
| all source: backend, frontend, agents, retrieval, evaluation, tests | `node_modules/`, `frontend/dist/` — reproduce with `npm install && npm run build` |
| `data/raw/` — the two supplied CSVs, so the pipeline runs on clone | `data/processed/` — regenerable, and the products dump is ~15 MB |
| `data/packs/` — seed vocabulary packs | `.env` — secrets; use `.env.example` |
| `docs/sample_output/` — the real 1,000-row output, review queue and reports | `data/reference/` — the official Unilog pack is licensed material, not ours to redistribute |
| `docs/screenshots/` — real UI captures | `docs/deck_render/` — PNG renders used only to validate the deck |
| `UniHack_Unilog_Product_Intelligence.pptx` — the completed submission deck | `__pycache__/`, `.pytest_cache/` |

### Submission deck

[`UniHack_Unilog_Product_Intelligence.pptx`](UniHack_Unilog_Product_Intelligence.pptx)
— 19 slides built on the official UniHack template, with three native diagrams and
real screenshots of this build. Rebuild it after a new run with:

```bash
python scripts/capture_screenshots.py     # real UI captures (needs playwright)
python scripts/complete_deck.py           # fills the template
python scripts/validate_deck.py           # no empty sections / stray placeholders
```


### Where the LLM sits — the three call sites

`backend/llm/client.py` is a provider-agnostic client: strict JSON schemas, local
repair of truncated responses before a retry is spent, exponential backoff on rate
limits, on-disk prompt-hash caching, and a hard offline mode.

It is called in exactly three places, each narrowly scoped and each with a
deterministic fallback:

| Stage | Fires when | Guard |
|---|---|---|
| **Product parser** (Agent 1) | the deterministic parse is *thin* — an opaque, code-only description | every returned token must occur in the source text, or it is dropped |
| **Taxonomy tie-break** (Agent 4) | top-2 leaves score within 80 %, or nothing matched — ~5 % of rows | the answer must be one of the candidate ids offered, or `none`; confidence capped at 0.72 |
| **LOV rescue** | an extracted value missed the controlled vocabulary | retrieval-first; only an *exact* alias hit inside the same attribute is applied, anything looser becomes a review suggestion |

Run telemetry (`stats.llm`, `stats.agents`) reports calls, cache hits, escalations
and resolutions, so the model's actual contribution is measurable rather than
asserted. On the 1,000-row feed with no key: 53 escalations, 0 resolutions, and the
53 affected rows go to review instead of being guessed.

---

## 7. Evaluation

```bash
python evaluation/benchmark.py \
    --labelled data/raw/delivery_format_labelled.csv \
    --input    data/raw/sample_1000_input.csv
```

Accuracy is computed **only over fields the labelled data actually populates**, so a
pipeline that stays silent cannot inflate its score. Coverage is reported separately.

### Result against the labelled rows

| Field group | Accuracy |
|---|---|
| `Dept`, `Class`, `Fine` | **100 %** |
| `Classpath` | **100 %** |
| `Product Name` | **100 %** |
| `MANUFACTURER_PART_NUMBER` | **100 %** |
| All `ATTRIBUTE_LABEL n` slots | **100 %** |
| `MANUFACTURER_NAME`, `BRAND_NAME` | 0 % |
| `ATTRIBUTE_VALUE n` | 0 % |
| LOV compliance / char compliance / evidence coverage | **100 %** |
| Overall quality score | **82.7 %** |

**Why brand and attribute values score 0 %, honestly:** those facts are not in the
input. `WDTS7024RZ Dishwasher SS - Display Only` contains no brand token, no
voltage, no amperage, no dimensions and no sound level. The gold row has them
because a human opened `whirlpool.com`. With `RESEARCH_ENABLED=true` and a search
key the research agent supplies exactly these fields; offline, the system correctly
**refuses to invent them** and routes the record to review. That is Golden Rule 1
working, not a defect — and it is why the review rate is 46.7 % rather than a
suspiciously low number.

### Description-engine fidelity

Given the same verified fact sheet a human analyst had, the generator reproduces the
labelled strings **exactly**:

| Field | Row 1 | Row 2 |
|---|---|---|
| `LONG_DESC1` | exact (390 ch) | exact (405 ch) |
| `SHORT_DESC` | exact (115 ch) | exact (96 ch) |
| `RETAIL_DESC` | exact (75 ch) | exact (74 ch) |
| `MOBILE_DESC` | exact (75 ch) | exact (64 ch) |
| `INVOICE_DESC` | exact (38 ch) | exact (39 ch) |

Asserted in `tests/test_pipeline.py::test_generator_reproduces_labelled_row_one`
and `..._two`.

---

## 8. Using the system

### CLI

```bash
# profile a feed without enriching it
python scripts/run_pipeline.py --input feed.xlsx --profile-only

# enrich, limit rows, choose format
python scripts/run_pipeline.py --input feed.csv --limit 200 --format both

# disable stages (the architecture is genuinely modular)
python scripts/run_pipeline.py --input feed.csv \
    --disable manufacturer_research --disable digital_assets

# scale report from a finished run
python scripts/scale_report.py data/processed/feed_products.json

# build the UniHack submission deck onto the supplied template
python scripts/build_deck.py
```

Outputs land in `data/processed/`:
`*_delivery_format.csv` · `*_delivery_format.xlsx` · `*_review_queue.csv` ·
`*_run_report.json` · `*_products.json` · `input_profile.json`

### Dashboard

| Page | Shows |
|---|---|
| **Upload** | drag-drop CSV/XLSX, column profile, placeholder density, duplicates, encoding damage |
| **Processing** | live stage-by-stage progress, throughput, cache stats |
| **Dashboard** | processed / enriched / confidence / LOV / validation / review / evidence, category and flag breakdowns, live reference provenance |
| **Products** | searchable, filterable table with per-row confidence bands |
| **Product detail** | Raw vs Enriched vs Evidence vs Confidence, attribute table with transformations, **evidence graph**, generated copy, delivery row, stage log |
| **Human review** | one row per specific issue, with suggested value and inline override |
| **Evaluation** | full metric breakdown against labelled data |

### API

`GET /api/health` · `GET /api/config` · `POST /api/config/stage` ·
`POST /api/upload` · `POST /api/process` · `POST /api/process/sample` ·
`GET /api/jobs` · `GET /api/jobs/{id}` · `GET /api/jobs/{id}/dashboard` ·
`GET /api/jobs/{id}/products` · `GET /api/jobs/{id}/products/{row}` ·
`GET|POST /api/jobs/{id}/review` · `GET /api/jobs/{id}/export?fmt=csv|xlsx|review` ·
`GET /api/jobs/{id}/evaluation`

Interactive docs at `/docs`.

---

## 9. Environment variables

| Variable | Default | Effect |
|---|---|---|
| `LLM_PROVIDER` | `auto` | `auto` / `anthropic` / `openai` |
| `LLM_API_KEY` | *(empty)* | absent → deterministic fallbacks, reported in the UI |
| `LLM_MODEL` | `claude-sonnet-5` | |
| `LLM_ENABLED` | `true` | hard off-switch |
| `SEARCH_PROVIDER` | `none` | `none` / `tavily` / `serper` |
| `SEARCH_API_KEY` | *(empty)* | |
| `RESEARCH_ENABLED` | `false` | enables manufacturer research |
| `MAX_WORKERS` | `8` | |
| `CACHE_ENABLED` | `true` | on-disk prompt cache |
| `REVIEW_THRESHOLD` | `0.62` | product confidence floor |

Secrets are read from `.env` (git-ignored). Nothing is hard-coded.

---

## 10. Known limitations

Stated plainly, because inflated claims are worse than gaps.

1. **The official reference pack was not supplied.** Manufacturer/brand masters,
   taxonomy and LOV are mined or seeded. They are correct for this corpus but are
   not the real UniCat vocabulary. Provenance is reported everywhere; drop the
   workbooks in to switch.
2. **`FAUCETS_LOV` / `Fittings_LOV` are not implemented as content.** The loader
   and pack format support them, but authoring a faucets or fittings specification
   without the source documents would be inventing a vocabulary — precisely what
   Golden Rule 1 forbids. The demo therefore centres on **Dishwashers/Appliances**,
   the only category with real ground truth, plus the largest real categories in
   the feed (lighting, decking, power tools).
3. **Attribute values are limited to what the input states.** Offline, roughly 41 %
   of template slots fill. Manufacturer research lifts this substantially but needs
   a search key.
4. **`INVOICE_DESC` ordering is derived from two examples.** Both reproduce
   exactly, but the priority list for categories with no labelled example is a
   documented convention, not a confirmed rule.
5. **53 of 1,000 rows cannot be classified** (flashlights, drink bottles, driveway
   alerts). They receive the generic template, no classpath, and a validation error
   — correctly unpublishable rather than wrongly categorised.
6. **`PART_NUMBER` / `SKU - MY_PART_NUMBER` are never populated.** These are the
   client's internal identifiers; there is no source for them in the input.
7. **Jobs are in-memory.** Restarting the server loses history. `JobStore` is the
   seam for Celery/RQ + a database.
8. **The mined distributor heuristic needs corpus scale.** With a small feed a real
   distributor carrying one brand looks like a manufacturer (e.g. Jam Industrial
   Supply for 3M). The official master resolves this.
9. **The LLM layer is built but not wired in.** `LlmClient` is complete and tested;
   no stage calls it. The three intended call sites are: (a) a tie-breaker when
   `Classification.ambiguous` is true or classification falls back, (b) attribute
   extraction from retrieved manufacturer prose once research is enabled, and
   (c) synonym interpretation for values that miss the LOV. Until those are
   implemented, treat this as a deterministic pipeline with an AI-ready seam.
10. **No vector/RAG retrieval layer.** The brief's §8 collections (FAISS/Chroma over
   the 161k-row LOV, manufacturer master, guidelines) are not built. Category-scoped
   LOV lookup is currently a direct index on the taxonomy leaf, which is exact and
   fast at the present 323-value scale but will not carry the full vocabulary.
11. **Research and official-reference loaders are unexercised.** Both are implemented
   and unit-tested in isolation, but neither has been run against a live search API
   or a real Unilog workbook, because neither was available here.

---

## 11. Recommended next steps

1. **Load the official reference pack** — the single highest-value change. Removes
   limitations 1, 2 and 8 with no code change.
2. **Enable manufacturer research** with a Tavily/Serper key; this is what closes
   the brand and attribute-value gap measured in §7.
3. **Replace TF-IDF retrieval with embeddings** (FAISS/Chroma) once the 161k-row LOV
   is available — the retrieval interface is already isolated.
4. **Persist jobs** in Postgres and move execution to a worker queue.
5. **Learn from reviewer overrides**: every human correction is already captured
   with provenance, so it is a labelled training pair for tuning thresholds and
   mining new synonyms.
6. **Batch LLM extraction** across variant families (already deduplicated by the
   extraction cache) to cut per-row cost further.

---

## 12. Tests

```bash
python -m pytest tests -q      # 136 passed
```

The suite runs offline in ~6 seconds and needs no API key: the LLM-facing tests use a stub client, so they assert the *contract* (schema conformance, grounding, refusal to accept an answer outside the offered candidate set) deterministically.

Covering: exact fraction conversion and refusal to invent off-ladder precision;
UOM alias resolution and house spacing; placeholder and mojibake handling;
manufacturer-variant resolution and refusal to guess; distributor exclusion;
brand-lexicon homograph guards; classification; dimension-chain parsing; LOV synonym
mapping; empty-slot emission; **character-exact reproduction of both labelled rows**;
marketing copy never invented; source-hierarchy rejection of marketplaces;
prefix-inference scope guards; 252-column schema conformance; verbatim input
passthrough; evidence presence on every populated attribute; and batch survival of
malformed rows.
