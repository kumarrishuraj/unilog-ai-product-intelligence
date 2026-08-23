# Demo & Presentation Guide

A 6-minute walkthrough plus the material behind the claims.

---

## Setup (once)

```bash
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
uvicorn backend.api.main:app --port 8000
```

Open <http://127.0.0.1:8000>. No API keys required — the system runs fully offline
and says so in the header.

---

## The 6-minute run

### 0:00 — The problem, in one row (30 s)

Show the raw input:

```
Mfg_Part_Num : WDTS7024RZ
Part_Desc    : WDTS7024RZ Dishwasher SS - Display Only
E1_Brand     : -- Unbranded --
Unilog_Brand : -- No Unilog Brand --
DIB_Brand    : -- No DIB Brand --
Part_Manuf   : Appliance Dealers Cooperative (APPDE)
```

> "Six fields. Three of them are placeholders pretending to be data. The fourth is
> a buying co-op, not a manufacturer. We need 252 validated columns out of this."

### 0:30 — Upload page (60 s)

Drop `data/raw/sample_1000_input.csv`. Before enriching anything, the profiler
reports:

- 1,000 rows, 6 columns
- `Unilog_Brand` is **100 % placeholder sentinels**
- `E1_Brand` **80 %**, `DIB_Brand` **76 %**
- 1 duplicated part number

> "Most pipelines would treat `-- Unbranded --` as a brand name. That single
> mistake corrupts 799 rows. We detect the sentinels first."

### 1:30 — Processing page (45 s)

Click enrich. Watch the twelve stages tick through.

> "1,000 products in about four seconds, zero failures. Every stage is individually
> toggleable — this is a modular pipeline, not one giant prompt."

### 2:15 — Dashboard (75 s)

Point at the numbers, then at the **Reference data in use** panel.

> "94.7 % classified. 100 % LOV compliance. 100 % evidence coverage. And here's the
> part I want you to notice — the provenance panel. The official UniCat reference
> pack wasn't supplied with this dataset, so the manufacturer master is marked
> `derived`, mined from the feed itself. We never present derived data as if it were
> official. Drop the real workbooks into `data/reference/` and these flip to
> `official` with no code change."

Then the review-reason chart:

> "46.7 % go to human review — and every one has a *specific* reason, not a generic
> low-confidence bucket."

### 3:30 — Product detail: the money slide (90 s)

Open a Milwaukee cut-off wheel: `49-94-0013 Milw 5"x.045"x7/8" Metal Cut Off Disc`.

**Attributes tab** — walk one row:

| | |
|---|---|
| Raw | `5"x.045"x7/8"` |
| Diameter | `5 in` |
| Thickness | `0.045 in` |
| Arbor Size | `7/8 in` |
| Transformation | dimension chain parsed in template order |

> "Three dimensions pulled out of one token, in the right order, each with its
> approved UOM. And note `0.045` stayed a decimal — it isn't an exact binary
> fraction, so we did **not** round it to 1/16 to make it look tidy. We don't invent
> precision."

Then `Material: SS → Stainless Steel`, transformation `LOV synonym`, LOV badge PASS.

**Evidence graph tab:**

> "Every value: what it is, where it came from, what transformed it, how confident
> we are. This is the audit trail. If a merchandiser asks 'why does this say
> Stainless Steel', the answer is one click."

### 5:00 — Human review (45 s)

Open the queue, find a `Brand unresolved` row, type a value, Apply.

> "The override is written back with provenance `human_override`, the row is
> re-projected onto the 252 columns immediately, and the flag clears. Every
> correction is a labelled training pair for later."

### 5:45 — Export (15 s)

Download the CSV. Open it. 252 columns, exact header order.

---

## The claim that lands hardest

Run this live:

```bash
python -m pytest tests/test_pipeline.py -k reproduces -v
```

> "We reverse-engineered the description formulas from the two labelled rows. Given
> the same verified facts a human analyst had, our generator reproduces all ten
> description fields **character-for-character** — including a 390-character
> long description and both 40-character invoice descriptions."

```
FRIGIDAIRE® Dishwasher With CleanBoost™, Professional Series, 5 Wash Cycles,
120 V, 15 A, Leg Mounting, 24 in W x 24-1/4 in D, 50-1/4 in Depth With Door
Open, 8-1/2 in Upper Rack, 11-1/4 in Lower Rack Minimum Height, ... 47 dBA
Sound Level, Stainless Steel, Additional Information: 240 kW-hr Annual Energy,
1 to 12 hr Delay Start Hours
```

---

## Anticipated questions

**"Why is brand accuracy 0 % on the labelled rows?"**

Because the brand is genuinely not in the input. `WDTS7024RZ Dishwasher SS -
Display Only` contains no brand token; the supplier is a co-op. The gold row says
Whirlpool because a human opened whirlpool.com. Offline we flag it rather than
guess — that is the system working as specified. Turn on `RESEARCH_ENABLED` with a
search key and the research agent supplies it under the source hierarchy.

We could trivially have hard-coded `PDSH4816AF → FRIGIDAIRE` and shown 100 %. That
number would have meant nothing on unseen data.

**"Isn't the taxonomy just hard-coded for the sample?"**

It is keyed on *product type*, never on a part number or row index — the same way
the real LOV is. `grep -r "PDSH4816AF" backend/` returns nothing. The classifier is
scored against all 1,000 rows and reaches 94.7 % coverage across 36 categories.

**"Where does AI actually get used?"**

Deliberately narrowly. Deterministic code does parsing, matching, normalisation,
arithmetic, validation and formatting — anything a model would only make less
reliable. The LLM layer handles ambiguous classification, extraction from retrieved
manufacturer prose, and synonym interpretation, always behind strict JSON schemas
with deterministic validation after. Every AI stage has a fallback, which is why the
whole system runs offline.

**"How does it scale?"**

387 rows/sec offline. Extraction is cached by (leaf, description) — variant families
like 24 Trex decking colours cost one extraction. LLM calls are cached by prompt
hash. The two-pass design means the corpus *teaches* the pipeline: part-number
prefix rules are learned from confidently-branded rows and applied to unbranded
siblings, so accuracy improves with scale rather than degrading.

---

## Failure modes we found and fixed (worth telling)

These make the engineering credible.

| Bug | Symptom | Fix |
|---|---|---|
| Substring brand matching | `Square Drive Bit` → Square D brand | word-boundary matching |
| Homograph aliases | `Phillips Drive Bit` → Philips brand | per-alias `reject_after` guards |
| Short ambiguous aliases | `Heated Glove Blk LG` → LG brand | `require_after` noun guard |
| Unscoped prefix inference | `SQ Washer` → Edge Eyewear | category/supplier scope requirement |
| Greedy regex | `5"x.045"` parsed as `45` | leading-dot decimals in the token pattern |
| Over-eager validator | supplier `Wera Tools NA Inc` flagged as placeholder | whole-field vs substring check split |

Each is covered by a regression test.

---

## Slide skeleton

1. **Problem** — six messy fields → 252 validated columns, at catalogue scale.
2. **Why LLM-only fails** — hallucination, no audit trail, no vocabulary control.
3. **Our architecture** — deterministic cage, AI where it helps, evidence throughout.
4. **Golden rules enforced in code** — with the `classify_source` and
   `MARKETING_DESCRIPTION` examples.
5. **Live demo** — the 6 minutes above.
6. **Results** — 1,000 rows / 4.1 s / 0 failures / 94.7 % classified / 100 % LOV /
   100 % evidence / 10-of-10 character-exact descriptions.
7. **Honesty slide** — what scores 0 % and why that is correct.
8. **What's next** — official reference pack, research on, embeddings, learn from
   reviewer overrides.
