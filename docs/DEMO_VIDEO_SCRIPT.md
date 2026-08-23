# Demo Video Script — 4:00

Screen recording of the deployed app at
**https://unilog-product-intelligence.onrender.com**

Every number below was read off that deployment. Every click exists.

---

## ⚠️ Read this before you record

**1. Wake the server first.** Render's free tier sleeps after 15 minutes idle. Open
the URL **2 minutes before** you hit record and click through a page. A cold start
takes ~50 seconds and would kill your opening.

**2. Do not say "5.6 seconds".** That is the *local* benchmark. On Render's free
tier the same 1,000 rows takes **~25 seconds**, and that is what your screen will
show. The script below says 25 seconds. Saying 5.6 while 25 is on screen is the
kind of thing a judge catches.

**3. Do not call the vocabulary "the official Unilog LOV".** The official reference
pack was never supplied. The dashboard literally prints `DERIVED`. The script says
"controlled vocabulary" and "derived from the feed" — keep it that way.

**4. Have `sample_1000_input.csv` on your desktop.** Get it from
`data/raw/sample_1000_input.csv` in the repo.

**5. The 751 RAG documents figure is not on any screen.** For that 30 seconds,
switch to **slide 10 of the PPT** (*AI · RAG · enrichment pipeline*). The script
tells you when.

---

## Recommended demo products

| Product | Why |
|---|---|
| **`49-94-0013`** — Milwaukee cut-off disc | The success story. `5"x.045"x7/8"` is parsed into three separate dimensions, `Cut Off Disc` maps to the controlled value `Cut-Off Wheel`, brand is recovered from the abbreviation "Milw". **81.7% MEDIUM, SUCCESS, 5 of 7 attributes.** |
| **`WDTS7024RZ`** — dishwasher | The honesty story. Brand comes back **unresolved and flagged**, not guessed. **47.0% LOW, NEEDS REVIEW, 4 flags.** This is your strongest trust moment. |

---

# THE SCRIPT

---

## 0:00 – 0:20 · Hook / Problem  *(20s)*

### 🖥️ WHAT I SHOW
```
Browser already open on the Dashboard
→ Switch to the Upload page
→ Drag sample_1000_input.csv onto the drop zone
→ The column profile appears instantly — leave it on screen
```

### 🎙️ WHAT I SAY

> "This is a real industrial product feed — a thousand products, six columns each.
>
> And look what the profiler just told us. `Unilog_Brand` is a hundred percent
> placeholder. `E1_Brand` is eighty percent placeholder. Those aren't brands —
> they're the word 'Unbranded' pretending to be data.
>
> Commerce needs two hundred and fifty-two validated columns. We have six, and
> three of them are lying."

---

## 0:20 – 0:45 · The Solution  *(25s)*

### 🖥️ WHAT I SHOW
```
→ Type 1000 into the "Row limit" box
→ Click "Enrich uploaded file"
→ The app jumps to the Processing page — leave it running
```

### 🎙️ WHAT I SAY

> "So we built an evidence-grounded product intelligence pipeline, and it's running
> now on all thousand products.
>
> It cleans the data, resolves the manufacturer and brand, classifies the product,
> retrieves a controlled vocabulary, extracts and normalises attributes, generates
> the commerce copy, validates it, scores confidence — and sends anything uncertain
> to a human.
>
> The important part is the order. Deterministic first. AI only where it actually
> helps."

---

## 0:45 – 1:30 · Live Processing  *(45s)*

> ⏱️ The run takes about 25 seconds. Narrate over it — the timing lines up.

### 🖥️ WHAT I SHOW
```
→ Stay on Processing. Point at the twelve stages ticking green
→ When it finishes, click "View dashboard"
→ Then click Products
→ Search "49-94-0013" and click the row
→ Open the "Attributes (5/7)" tab
```

### 🎙️ WHAT I SAY

> "Twelve stages, every one independently switchable. Watch them complete.
>
> …Done. A thousand products in twenty-five seconds on a free-tier container. Zero
> failures.
>
> Now let's see this working on a real product."
>
> *(open 49-94-0013 → Attributes tab)*
>
> "This Milwaukee cut-off disc came in as one messy string: five inch, point-oh-four-five,
> seven-eighths.
>
> The system split that into three separate dimensions — Diameter five inches,
> Thickness point-oh-four-five, Arbor seven-eighths — each with an approved unit.
>
> 'Cut Off Disc' became the controlled value 'Cut-Off Wheel'. 'Metal' became the
> Application.
>
> And notice — point-oh-four-five stayed a decimal. It isn't an exact binary
> fraction, so we did **not** round it to look tidy. We don't invent precision."

---

## 1:30 – 2:15 · Explainability & Trust  *(45s)*  ⭐ strongest section

### 🖥️ WHAT I SHOW
```
→ Click the "Evidence graph" tab
→ Then the "Confidence" tab
→ Click "← Back to products"
→ Search "WDTS7024RZ" and click the row
→ Show the red "unresolved" brand and the flags
→ Open "Generated copy" — point at MARKETING_DESCRIPTION
```

### 🎙️ WHAT I SAY

> "This is where our approach differs from a normal LLM pipeline.
>
> Every single value carries its evidence. Brand — Milwaukee — found in the product
> description. Manufacturer — resolved through the brand master. Classpath — matched
> on these keywords. Source, transformation, confidence, all of it. Fully auditable.
>
> And confidence is broken down by component, so you can see exactly which part of
> the pipeline limited the score."
>
> *(open WDTS7024RZ)*
>
> "Now here's the honest case. This dishwasher — the brand is **unresolved**.
>
> The description says 'Dishwasher SS'. There's no brand token anywhere, and the
> supplier is a buying co-op, not a manufacturer.
>
> A normal LLM would confidently invent 'Whirlpool' here. **Instead of guessing, the
> system flags it for review** — and look at the marketing description: blank, with
> the reason written out. *Left blank rather than invented.*
>
> We only publish what comes from the input, an approved vocabulary, real evidence,
> or a deterministic transformation. Everything else gets flagged."

---

## 2:15 – 2:45 · AI & RAG Architecture  *(30s)*

### 🖥️ WHAT I SHOW
```
→ Alt-Tab to the PowerPoint, slide 10 — "AI · RAG · enrichment pipeline"
   (this is the only figure with the 751-document number on it)
```

### 🎙️ WHAT I SAY

> "Briefly, how the AI fits.
>
> We index seven retrieval collections — seven hundred and fifty-one documents:
> manufacturers, brands, the vocabulary, units, examples. Retrieval filters by
> category *before* it ranks, so a prompt sees a handful of candidates, never the
> whole vocabulary.
>
> The model is called at exactly three places, each with a hard guard. It can only
> pick from options we supply, and every token it returns must already appear in the
> source text.
>
> And every one of those has a deterministic fallback — which is why everything you
> just watched ran with no API key at all."

---

## 2:45 – 3:15 · Results  *(30s)*

### 🖥️ WHAT I SHOW
```
→ Back to the browser, click Dashboard
→ Point at the four tiles, then the Quality gates panel
→ Then the "Reference data in use" panel on the right
→ Click Evaluation
```

### 🎙️ WHAT I SAY

> "The results, measured on this run — not estimated.
>
> A thousand products. Nine hundred and forty-seven enriched — ninety-four point
> seven percent. Zero failures.
>
> A hundred percent controlled-vocabulary compliance. A hundred percent evidence
> coverage. A hundred percent character-limit compliance. Ninety-four point seven
> percent of rows pass validation with zero errors.
>
> And this panel is my favourite — it reports where our reference data actually came
> from. `DERIVED`. `SEED`. `COMPUTED`. We mined the manufacturer master from the feed
> itself. We never claim it's more authoritative than it is."

---

## 3:15 – 3:40 · Export  *(25s)*

### 🖥️ WHAT I SHOW
```
→ Click Products → open any product → "Delivery row" tab
→ Then click Processing → click the "CSV" button
→ Open the downloaded file (have Excel ready) OR just show the download bar
```

### 🎙️ WHAT I SAY

> "And here's the output — the delivery row, mapped straight onto the two hundred
> and fifty-two column schema.
>
> One click exports the whole run as CSV or XLSX, with the exact header order the
> commerce system expects.
>
> Six messy columns in. A validated, traceable, commerce-ready catalogue record out."

---

## 3:40 – 4:00 · Closing  *(20s)*

### 🖥️ WHAT I SHOW
```
→ Back to Dashboard (leave it on screen)
```

### 🎙️ WHAT I SAY

> "So — why not just send the raw data to an LLM?
>
> Because an LLM will fill every field, confidently, including the ones it made up.
> And you cannot tell which is which.
>
> Ours is evidence-grounded, offline-first, AI-optional, fully auditable, and it
> keeps a human in the loop exactly where a human is needed.
>
> **We'd rather leave a field blank and tell you why, than fill it in and be wrong.**"

---

# Recording sequence — click-by-click

1. **Before recording:** open the site, click around, let it wake up (~1 min).
2. Put `sample_1000_input.csv` on the desktop. Have PowerPoint open on slide 10.
3. **Record.** Start on **Dashboard**.
4. Click **Upload**.
5. Drag `sample_1000_input.csv` onto the drop zone → profile appears.
6. Type **`1000`** into **Row limit**. *(It defaults to 200 — you must change it.)*
7. Click **Enrich uploaded file** → lands on **Processing**.
8. Narrate the twelve stages while it runs (~25 s).
9. Click **View dashboard**.
10. Click **Products** → search **`49-94-0013`** → click the row.
11. Tab: **Attributes (5/7)**.
12. Tab: **Evidence graph**.
13. Tab: **Confidence**.
14. Click **← Back to products** → search **`WDTS7024RZ`** → click the row.
15. Tab: **Generated copy** → point at the blank `MARKETING_DESCRIPTION`.
16. **Alt-Tab to PowerPoint, slide 10.** Talk for 30 s. Alt-Tab back.
17. Click **Dashboard** → tiles → Quality gates → Reference data in use.
18. Click **Evaluation**.
19. Click **Products** → any product → **Delivery row** tab.
20. Click **Processing** → click **CSV**.
21. Click **Dashboard**. Deliver the closing line. Stop.

---

# Timing

| Section | Duration | Running |
|---|---|---|
| Hook / problem | 0:20 | 0:20 |
| Solution | 0:25 | 0:45 |
| Live processing | 0:45 | 1:30 |
| Explainability & trust | 0:45 | 2:15 |
| AI & RAG | 0:30 | 2:45 |
| Results | 0:30 | 3:15 |
| Export | 0:25 | 3:40 |
| Closing | 0:20 | **4:00** |

~600 spoken words. If you run long, cut the **Export** section to a single sentence —
never cut the trust section.

---

# Final line

> **"We'd rather leave a field blank and tell you why, than fill it in and be wrong."**

---

# Numbers you may safely quote

All read from the deployed instance on a 1,000-row run.

| Claim | Value |
|---|---|
| Products processed | 1,000 |
| Successfully enriched | 947 (94.7%) |
| Processing failures | 0 |
| Run time (Render free tier) | ~25 s |
| Run time (local benchmark) | 5.6 s — *say "on our own machine"* |
| Mean confidence | 72.7% |
| Needs human review | 467 (46.7%) |
| Controlled-vocabulary compliance | 100% |
| Evidence coverage | 100% |
| Character-limit compliance | 100% |
| Validation pass rate | 94.7% |
| Confidence bands | 181 HIGH / 565 MEDIUM / 238 LOW / 16 UNKNOWN |
| RAG collections / documents | 7 / 751 *(PPT slide 10 only)* |
| Reference data mined | 75 manufacturers, 34 brands, 36 categories, 323 vocabulary values |
| Evaluation vs labelled rows | 77.8% micro accuracy, 82.9% overall quality |
| Tests | 136 / 136 passing |

**Do not say:** "official Unilog LOV" · "5.6 seconds" while Render is on screen ·
any accuracy figure above 94.7% · that research or the LLM is running (both are off).
