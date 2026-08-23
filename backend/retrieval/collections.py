"""
The seven RAG collections from the brief (§8), built over the live reference data.

    A  manufacturer_brand   manufacturer + brand master
    B  lov                  global controlled vocabulary (classpath / label / value)
    C  fittings             fittings specification        (loaded when supplied)
    D  faucets              faucets specification         (loaded when supplied)
    E  uom                  approved UOM abbreviations and their aliases
    F  guidelines           content-guideline blocks      (loaded when supplied)
    G  examples             labelled delivery-format rows, for few-shot grounding

The design rule the brief calls out is enforced here: **never put the whole
vocabulary in a prompt.** ``adaptive_lov`` narrows by classpath *first* and only then
ranks, so a prompt sees a handful of candidate values rather than 161,000 rows.

Collections C, D, F and G are built only when their source data is present. An
absent collection reports ``documents: 0`` rather than being faked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from backend.reference.loader import csv_records, workbook_records
from backend.reference.registry import ReferenceRegistry
from backend.retrieval.vector_store import Document, Hit, RetrievalIndex

COLLECTION_NAMES = (
    "manufacturer_brand", "lov", "fittings", "faucets", "uom", "guidelines", "examples",
)


@dataclass
class CollectionSet:
    """All seven collections, each independently queryable."""
    indexes: Dict[str, RetrievalIndex] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def get(self, name: str) -> Optional[RetrievalIndex]:
        return self.indexes.get(name)

    def search(self, collection: str, query: str, k: int = 5,
               where: Optional[Dict[str, Any]] = None) -> List[Hit]:
        idx = self.indexes.get(collection)
        return idx.search(query, k=k, where=where) if idx else []

    # -- the adaptive-retrieval path the brief specifies --------------------
    def adaptive_lov(self, classpath: str, attribute_label: str,
                     raw_value: str, k: int = 8) -> List[Hit]:
        """
        Category → attribute → candidate values, in that order.

        This is the whole point of the collection: the filter runs before the
        ranking, so an LLM prompt receives a shortlist rather than the vocabulary.
        """
        idx = self.indexes.get("lov")
        if idx is None or not raw_value:
            return []
        where: Dict[str, Any] = {}
        if classpath:
            where["classpath"] = classpath
        if attribute_label:
            where["label"] = attribute_label
        hits = idx.search(raw_value, k=k, where=where)
        if hits:
            return hits
        # Relax to the category only -- a wider shortlist beats none. Relaxed hits are
        # marked, because a value retrieved outside its own attribute is a suggestion
        # for a human, never an automatic mapping.
        if classpath:
            relaxed = idx.search(raw_value, k=k, where={"classpath": classpath})
            for h in relaxed:
                h.relaxed = True
            return relaxed
        return []

    def stats(self) -> Dict[str, Any]:
        return {
            "collections": {n: (self.indexes[n].stats() if n in self.indexes
                                else {"name": n, "documents": 0, "backend": "absent"})
                            for n in COLLECTION_NAMES},
            "total_documents": sum(len(i) for i in self.indexes.values()),
            "notes": list(self.notes),
        }


class CollectionBuilder:
    """Builds the collection set from a populated :class:`ReferenceRegistry`."""

    def __init__(self, registry: ReferenceRegistry, reference_dir: Optional[Path] = None):
        self.reg = registry
        self.reference_dir = Path(reference_dir) if reference_dir else None

    def build(self, labelled_rows: Optional[Sequence[Dict[str, str]]] = None) -> CollectionSet:
        cs = CollectionSet()
        cs.indexes["manufacturer_brand"] = self._manufacturer_brand()
        cs.indexes["lov"] = self._lov()
        cs.indexes["uom"] = self._uom()

        guidelines = self._guidelines()
        if len(guidelines):
            cs.indexes["guidelines"] = guidelines
        else:
            cs.notes.append("collection F (guidelines) empty: no content-guidelines "
                            "document supplied")

        for name, pattern in (("fittings", "fitting"), ("faucets", "faucet")):
            idx = self._specialised(name, pattern)
            if len(idx):
                cs.indexes[name] = idx
            else:
                cs.notes.append(
                    f"collection {'C' if name == 'fittings' else 'D'} ({name}) empty: "
                    f"no {name} specification workbook supplied")

        examples = self._examples(labelled_rows or ())
        if len(examples):
            cs.indexes["examples"] = examples
        else:
            cs.notes.append("collection G (examples) empty: no labelled rows supplied")

        for idx in cs.indexes.values():
            idx.build()
        return cs

    # -- A ------------------------------------------------------------------
    def _manufacturer_brand(self) -> RetrievalIndex:
        idx = RetrievalIndex("manufacturer_brand")
        for m in self.reg.manufacturers:
            text = " ".join([m.name, m.code or "", *m.aliases])
            idx.add(Document(f"m:{m.name}", text, {
                "kind": "manufacturer", "name": m.name, "code": m.code,
                "is_distributor": m.is_distributor, "provenance": m.provenance,
            }, exact_terms=(m.name, *(m.aliases or ()), *( (m.code,) if m.code else () ))))

        seen = set()
        for b in self.reg.brands:
            seen.add(b.name.lower())
            text = " ".join([b.name, b.code or "", *b.aliases, *b.manufacturers])
            idx.add(Document(f"b:{b.name}", text, {
                "kind": "brand", "name": b.name, "code": b.code,
                "manufacturers": list(b.manufacturers), "provenance": b.provenance,
            }, exact_terms=(b.name, *(b.aliases or ()))))

        # The mined master only knows brands that appeared in the feed's brand
        # columns. Brands the lexicon detects inside descriptions (KitchenAid, LG,
        # Speed Queen ...) must be retrievable too, or collection A silently misses
        # exactly the co-op-supplied products that need it most.
        lex = self.reg.brand_lexicon
        if lex is not None:
            for entry in getattr(lex, "_entries", []):        # noqa: SLF001
                if entry.canonical.lower() in seen:
                    continue
                seen.add(entry.canonical.lower())
                idx.add(Document(
                    f"b:{entry.canonical}",
                    " ".join([entry.canonical, *entry.aliases]),
                    {"kind": "brand", "name": entry.canonical, "code": None,
                     "manufacturers": [], "provenance": entry.provenance},
                    exact_terms=(entry.canonical, *entry.aliases)))
        return idx

    # -- B ------------------------------------------------------------------
    def _lov(self) -> RetrievalIndex:
        idx = RetrievalIndex("lov")
        tax = self.reg.taxonomy
        if tax is None:
            return idx
        for leaf in tax.leaves:
            # The leaf itself is retrievable, so classification can use the index too.
            idx.add(Document(f"leaf:{leaf.id}",
                             " ".join([leaf.classpath, leaf.product_name, leaf.fine,
                                       *leaf.strong_keywords, *leaf.keywords]),
                             {"kind": "leaf", "leaf_id": leaf.id,
                              "classpath": leaf.classpath,
                              "product_name": leaf.product_name}))
            for attr in leaf.attributes:
                for value in attr.values:
                    text = " ".join([value.value, *value.synonyms, attr.label])
                    idx.add(Document(
                        f"lov:{leaf.id}:{attr.label}:{value.value}", text,
                        {"kind": "value", "leaf_id": leaf.id,
                         "classpath": leaf.classpath, "label": attr.label,
                         "value": value.value, "synonyms": list(value.synonyms),
                         "uom": attr.uom, "filtering": attr.filtering},
                        exact_terms=(value.value, *value.synonyms)))
                if not attr.values:
                    # Open-vocabulary attribute: index the label so the slot is findable.
                    idx.add(Document(
                        f"lov:{leaf.id}:{attr.label}:*", attr.label,
                        {"kind": "open_attribute", "leaf_id": leaf.id,
                         "classpath": leaf.classpath, "label": attr.label,
                         "uom": attr.uom}))
        return idx

    # -- E ------------------------------------------------------------------
    def _uom(self) -> RetrievalIndex:
        idx = RetrievalIndex("uom")
        reg = self.reg.uom
        if reg is None:
            return idx
        for abbrev in sorted({d for d in reg._by_abbrev}):      # noqa: SLF001
            entry = reg.entry(abbrev)
            if entry is None:
                continue
            text = " ".join([entry.abbreviation, entry.measurement_type, *entry.aliases])
            idx.add(Document(f"uom:{abbrev}", text, {
                "kind": "uom", "abbreviation": entry.abbreviation,
                "measurement_type": entry.measurement_type,
                "aliases": list(entry.aliases)},
                exact_terms=(entry.abbreviation, *entry.aliases)))
        return idx

    # -- F ------------------------------------------------------------------
    def _guidelines(self) -> RetrievalIndex:
        idx = RetrievalIndex("guidelines", use_char_ngrams=False)
        for i, block in enumerate(self.reg.guideline_blocks or ()):
            text = f"{block.get('heading', '')} {block.get('text', '')}".strip()
            if text:
                idx.add(Document(f"gl:{i}", text, {
                    "kind": "guideline", "heading": block.get("heading", "")}))
        return idx

    # -- C / D ---------------------------------------------------------------
    def _specialised(self, name: str, pattern: str) -> RetrievalIndex:
        """
        Fittings / Faucets specifications.

        Built only from a supplied workbook. Authoring these vocabularies without the
        source document would be inventing a controlled vocabulary, which is exactly
        what Golden Rule 1 forbids -- so an absent file yields an empty collection.
        """
        idx = RetrievalIndex(name)
        if self.reference_dir is None or not self.reference_dir.exists():
            return idx
        for path in sorted(self.reference_dir.iterdir()):
            if not path.is_file() or pattern not in path.stem.lower():
                continue
            book = (workbook_records(path) if path.suffix.lower() != ".csv"
                    else {"csv": csv_records(path)})
            for sheet, rows in book.items():
                for r_i, row in enumerate(rows):
                    text = " ".join(str(v) for v in row.values() if v)
                    if text.strip():
                        idx.add(Document(f"{name}:{sheet}:{r_i}", text, {
                            "kind": name, "sheet": sheet, "source": path.name,
                            **{k: v for k, v in row.items() if v}}))
        return idx

    # -- G -------------------------------------------------------------------
    def _examples(self, labelled_rows: Sequence[Dict[str, str]]) -> RetrievalIndex:
        """
        Labelled delivery-format rows, for few-shot grounding.

        Indexed for *retrieval of formatting patterns* only. These are never copied
        into output -- that is the hard-coding the brief forbids (§21).
        """
        idx = RetrievalIndex("examples")
        for i, row in enumerate(labelled_rows):
            key = " ".join(str(row.get(f, "")) for f in
                           ("Part_Desc", "Classpath", "Product Name", "MANUFACTURER_NAME"))
            if not key.strip():
                continue
            idx.add(Document(f"ex:{i}", key, {
                "kind": "example",
                "classpath": row.get("Classpath", ""),
                "product_name": row.get("Product Name", ""),
                "short_desc": row.get("SHORT_DESC", ""),
                "long_desc": row.get("LONG_DESC1", ""),
                "invoice_desc": row.get("INVOICE_DESC", ""),
                "mobile_desc": row.get("MOBILE_DESC", ""),
            }))
        return idx
