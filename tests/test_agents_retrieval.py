"""
Tests for the agent layer (product parser, taxonomy agent, validation agent) and
the RAG collections.

The LLM-facing tests use a stub client rather than a live key, so they assert the
*contract* -- schema conformance, grounding, and the refusal to accept an answer
outside the offered candidate set -- deterministically and offline.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.agents.product_parser import ProductParser
from backend.agents.taxonomy_agent import TaxonomyAgent
from backend.agents.validation_agent import ValidationAgent
from backend.config import get_settings
from backend.llm.client import LlmResponse, repair_json
from backend.models.product import AttributeValue, EnrichedProduct, FieldValue
from backend.pipeline.io import read_table
from backend.pipeline.row_builder import RowBuilder
from backend.reference.registry import ReferenceRegistry
from backend.retrieval.collections import CollectionBuilder
from backend.retrieval.vector_store import Document, RetrievalIndex
from backend.validation.content_rules import ContentValidator
from backend.validation.schema import OutputSchema

PACKS = ROOT / "data" / "packs"
RAW = ROOT / "data" / "raw"
INPUT_CSV = RAW / "sample_1000_input.csv"
LABELLED_CSV = RAW / "delivery_format_labelled.csv"


class StubLlm:
    """Deterministic stand-in for LlmClient; records the prompts it was given."""

    def __init__(self, payload: Optional[Dict[str, Any]] = None, available: bool = True):
        self.payload = payload or {}
        self._available = available
        self.calls = []

    @property
    def available(self) -> bool:
        return self._available

    def complete_json(self, system: str, prompt: str, schema=None, max_tokens=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if not self._available:
            return LlmResponse(False, None, error="offline")
        return LlmResponse(True, dict(self.payload))


@pytest.fixture(scope="module")
def rows():
    return read_table(INPUT_CSV)


@pytest.fixture(scope="module")
def registry(rows):
    return ReferenceRegistry.build(ROOT / "data" / "reference", PACKS, rows)


@pytest.fixture(scope="module")
def collections(registry):
    gold = read_table(LABELLED_CSV)
    return CollectionBuilder(registry, ROOT / "data" / "reference").build(gold)


# ---------------------------------------------------------------------------
# Agent 1 -- Product Parser
# ---------------------------------------------------------------------------
def test_parser_extracts_dimensions_specs_and_materials(registry):
    p = ProductParser(registry.uom).parse({
        "Mfg_Part_Num": "49-94-0013",
        "Part_Desc": '49-94-0013 Milw 5"x.045"x7/8" Metal Cut Off Disc',
        "Part_Manuf": "Milwaukee Accessory (4031)",
    })
    assert p.mpn == "49-94-0013"
    assert p.model == "49-94-0013"
    assert p.manufacturer_raw == "Milwaukee Accessory (4031)"
    assert len(p.dimensions) >= 2
    assert p.confidence > 0


def test_parser_reads_series_and_pack_count(registry):
    p = ProductParser(registry.uom).parse({
        "Mfg_Part_Num": "X1",
        "Part_Desc": "X1 Professional Series Dishwasher SS - 6pc",
    })
    assert p.series == "Professional Series"
    assert p.technical_specs.get("Package Quantity") == "6"
    assert "Stainless Steel" in p.materials


def test_parser_strips_placeholder_brand_columns(registry):
    p = ProductParser(registry.uom).parse({
        "Mfg_Part_Num": "X1", "Part_Desc": "X1 Widget",
        "E1_Brand": "-- Unbranded --", "DIB_Brand": "-- No DIB Brand --",
    })
    assert p.brand_raw == ""


def test_parser_llm_output_must_be_grounded_in_the_source(registry):
    """A model claim absent from the text is dropped, not published."""
    stub = StubLlm({
        "product_type": "Dishwasher",
        "materials": ["Titanium"],                 # NOT in the text -> must be rejected
        "features": ["Wi-Fi Enabled"],             # NOT in the text -> must be rejected
        "technical_specs": {"Voltage": "9999 V"},  # NOT in the text -> must be rejected
        "reasoning_summary": "test",
    })
    parser = ProductParser(registry.uom, stub)
    p = parser.parse_with_llm({"Mfg_Part_Num": "ZZ1", "Part_Desc": "ZZ1 opaque code"},
                              force=True)
    assert "Titanium" not in p.materials
    assert "Wi-Fi Enabled" not in p.features
    assert "Voltage" not in p.technical_specs


def test_parser_llm_grounded_claim_is_accepted(registry):
    """
    A grounded field the deterministic pass missed is adopted and marked.

    Features are only read from ' - ' segments deterministically, so a description
    with no separator is exactly the gap the model pass exists to fill -- provided
    the words it returns are actually in the text.
    """
    stub = StubLlm({"product_type": "", "materials": [],
                    "features": ["folding tines"], "reasoning_summary": "stated"})
    parser = ProductParser(registry.uom, stub)
    p = parser.parse_with_llm(
        {"Mfg_Part_Num": "ZZ1", "Part_Desc": "ZZ1 opaque unit with folding tines"},
        force=True)
    assert "folding tines" in p.features
    assert p.method == "deterministic+llm"


def test_parser_llm_echo_of_a_known_field_is_not_counted_as_new(registry):
    """Re-stating what the deterministic pass already found changes nothing."""
    stub = StubLlm({"product_type": "Dishwasher", "materials": [], "features": [],
                    "reasoning_summary": "stated"})
    parser = ProductParser(registry.uom, stub)
    p = parser.parse_with_llm({"Mfg_Part_Num": "ZZ1", "Part_Desc": "ZZ1 Dishwasher"},
                              force=True)
    assert p.product_type == "Dishwasher"
    assert p.method == "deterministic"


def test_parser_never_calls_the_model_when_deterministic_pass_is_rich(registry):
    stub = StubLlm({"product_type": "Nope", "materials": [], "features": [],
                    "reasoning_summary": ""})
    parser = ProductParser(registry.uom, stub)
    parser.parse_with_llm({
        "Mfg_Part_Num": "49-94-0013",
        "Part_Desc": '49-94-0013 Milw 5"x.045"x7/8" Metal Cut Off Disc'})
    assert stub.calls == [], "model was called for a row the parser already resolved"


# ---------------------------------------------------------------------------
# Agent 4 -- Taxonomy Agent
# ---------------------------------------------------------------------------
def test_taxonomy_agent_does_not_escalate_a_clear_match(registry):
    stub = StubLlm()
    agent = TaxonomyAgent(registry.taxonomy, stub)
    d = agent.classify("PDSH4816AF Dishwasher SS - Display Only")
    assert d.leaf.id == "built_in_dishwashers"
    assert d.escalated is False
    assert stub.calls == []


def test_taxonomy_agent_escalates_only_when_unresolvable(registry):
    stub = StubLlm()
    agent = TaxonomyAgent(registry.taxonomy, stub)
    agent.classify("ZZZ opaque mystery item")
    assert agent.escalations == 1
    assert len(stub.calls) == 1


def test_taxonomy_agent_rejects_an_id_it_did_not_offer(registry):
    """A hallucinated leaf id must not become a classpath."""
    stub = StubLlm({"leaf_id": "totally_made_up_leaf", "confidence": 0.99,
                    "reasoning_summary": "invented"})
    agent = TaxonomyAgent(registry.taxonomy, stub)
    d = agent.classify("ZZZ opaque mystery item")
    assert d.leaf is None or d.leaf.id != "totally_made_up_leaf"
    assert agent.llm_resolutions == 0


def test_taxonomy_agent_accepts_none_as_a_valid_answer(registry):
    stub = StubLlm({"leaf_id": "none", "confidence": 0.0,
                    "reasoning_summary": "too opaque"})
    agent = TaxonomyAgent(registry.taxonomy, stub)
    d = agent.classify("ZZZ opaque mystery item")
    assert agent.llm_resolutions == 0
    assert d.llm_used is True


def test_taxonomy_agent_caps_model_confidence(registry):
    stub = StubLlm({"leaf_id": "built_in_dishwashers", "confidence": 1.0,
                    "reasoning_summary": "certain"})
    agent = TaxonomyAgent(registry.taxonomy, stub)
    d = agent.classify("ZZZ opaque mystery item")
    if d.llm_used and d.leaf is not None and d.classification.method == "llm_tiebreak":
        assert d.classification.confidence <= 0.72


def test_taxonomy_agent_degrades_cleanly_with_no_model(registry):
    agent = TaxonomyAgent(registry.taxonomy, StubLlm(available=False))
    d = agent.classify("ZZZ opaque mystery item")
    assert d.escalated is True and d.llm_used is False
    assert "human review" in d.note


# ---------------------------------------------------------------------------
# Agent 7 -- Validation Agent
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def schema():
    return OutputSchema.from_file(LABELLED_CSV)


def test_validation_agent_repairs_character_overflow(registry, schema):
    limits = {"SHORT_DESC": 40}
    agent = ValidationAgent(ContentValidator(schema, registry.uom, limits), limits)
    leaf = registry.taxonomy.get("built_in_dishwashers")

    p = EnrichedProduct(row_index=0, raw={"Mfg_Part_Num": "X1"})
    p.mpn = FieldValue("X1", 1.0, "input")
    p.product_name = FieldValue("Dishwasher", 0.9, "leaf")
    p.classpath = FieldValue(leaf.classpath, 0.9, "leaf")
    p.descriptions["SHORT_DESC"] = FieldValue("A" * 200, 0.9, "template")
    p.attributes = []

    builder = RowBuilder(schema)
    outcome = agent.validate(p, builder.build(p), leaf,
                             rebuild_row=lambda: builder.build(p))
    assert len(p.descriptions["SHORT_DESC"].value) <= 40
    assert outcome.repairs
    assert outcome.passes == 2


def test_validation_agent_clears_rather_than_publishes_an_unapproved_value(registry, schema):
    agent = ValidationAgent(ContentValidator(schema, registry.uom, {}), {})
    leaf = registry.taxonomy.get("built_in_dishwashers")

    p = EnrichedProduct(row_index=0, raw={"Mfg_Part_Num": "X1"})
    p.mpn = FieldValue("X1", 1.0, "input")
    p.product_name = FieldValue("Dishwasher", 0.9, "leaf")
    p.classpath = FieldValue(leaf.classpath, 0.9, "leaf")
    p.descriptions["SHORT_DESC"] = FieldValue("ok", 0.9, "template")
    p.attributes = [AttributeValue(label="Material", value="Unobtainium",
                                   confidence=0.8, method="regex",
                                   lov_compliant=False)]

    builder = RowBuilder(schema)
    agent.validate(p, builder.build(p), leaf, rebuild_row=lambda: builder.build(p))
    assert p.attribute("Material").value is None
    assert p.attribute("Material").method == "cleared_lov_violation"


def test_validation_agent_flags_unrepairable_failures_for_a_human(registry, schema):
    agent = ValidationAgent(ContentValidator(schema, registry.uom, {}), {})
    p = EnrichedProduct(row_index=0, raw={})          # no MPN, no classpath
    outcome = agent.validate(p, RowBuilder(schema).build(p), None)
    assert outcome.valid is False
    assert outcome.needs_human_review is True
    assert any(e["code"] == "required_field_missing" for e in outcome.errors)


def test_validation_agent_contract_shape(registry, schema):
    agent = ValidationAgent(ContentValidator(schema, registry.uom, {}), {})
    p = EnrichedProduct(row_index=0, raw={})
    d = agent.validate(p, RowBuilder(schema).build(p), None).as_dict()
    for key in ("valid", "errors", "warnings", "confidence", "needs_human_review"):
        assert key in d


# ---------------------------------------------------------------------------
# Retrieval / RAG collections
# ---------------------------------------------------------------------------
def test_all_seven_collections_are_addressable(collections):
    from backend.retrieval.collections import COLLECTION_NAMES
    stats = collections.stats()
    assert set(stats["collections"]) == set(COLLECTION_NAMES)


def test_populated_collections_have_documents(collections):
    stats = collections.stats()["collections"]
    for name in ("manufacturer_brand", "lov", "uom"):
        assert stats[name]["documents"] > 0


def test_absent_collections_report_zero_not_fabricated(collections):
    stats = collections.stats()["collections"]
    for name in ("fittings", "faucets"):
        assert stats[name]["documents"] == 0
    assert any("fittings" in n for n in collections.stats()["notes"])


@pytest.mark.parametrize("query,expected", [
    ("FREUD, INC.", "Freud Inc"),
    ("kitchen aid", "KitchenAid"),
])
def test_manufacturer_brand_recall(collections, query, expected):
    hits = collections.search("manufacturer_brand", query, k=3)
    assert hits and hits[0].payload["name"] == expected


@pytest.mark.parametrize("query,expected", [
    ("inches", "in"), ("kilowatt hour", "kW-hr"), ("decibel a", "dBA"),
])
def test_uom_collection_exact_alias_wins(collections, query, expected):
    hits = collections.search("uom", query, k=2)
    assert hits and hits[0].payload["abbreviation"] == expected


def test_adaptive_lov_filters_before_ranking(collections):
    """The prompt-sized shortlist is the whole point of the collection."""
    cp = ("Appliances & Consumer Electronics>Kitchen Appliances>"
          "Built-In Dishwashers")
    hits = collections.adaptive_lov(cp, "Material", "SST", k=5)
    assert hits and hits[0].payload["value"] == "Stainless Steel"
    assert len(hits) < len(collections.get("lov"))
    assert all(h.payload.get("label") == "Material" for h in hits)


def test_relaxed_lov_hits_are_labelled(collections):
    """A value found outside its own attribute is a suggestion, not a mapping."""
    cp = ("Appliances & Consumer Electronics>Kitchen Appliances>"
          "Built-In Dishwashers")
    hits = collections.adaptive_lov(cp, "Material", "bltln", k=3)
    assert hits and all(h.relaxed for h in hits)


def test_index_survives_without_sklearn_path():
    """The pure-Python fallback must rank sensibly too."""
    idx = RetrievalIndex("t")
    idx.extend([
        Document("a", "stainless steel sst", {"v": "Stainless Steel"},
                 exact_terms=("sst",)),
        Document("b", "black onyx bo", {"v": "Black Onyx"}),
    ])
    idx.build()
    hits = idx.search("sst", k=1)
    assert hits and hits[0].payload["v"] == "Stainless Steel"
    assert hits[0].exact is True


def test_where_filter_excludes_non_matching_payloads():
    idx = RetrievalIndex("t")
    idx.extend([
        Document("a", "widget", {"cat": "x"}),
        Document("b", "widget", {"cat": "y"}),
    ])
    idx.build()
    assert all(h.payload["cat"] == "y" for h in idx.search("widget", where={"cat": "y"}))


# ---------------------------------------------------------------------------
# LLM client contract
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Here is the answer:\n{"a": 1}\nHope that helps.',
    '{"a": 1',                        # truncated mid-object
])
def test_json_repair_recovers_common_malformations(raw):
    assert repair_json(raw) == {"a": 1}


def test_json_repair_returns_none_for_unrecoverable():
    assert repair_json("no json at all") is None


def test_llm_client_is_offline_without_a_key():
    from backend.llm.client import LlmClient
    s = get_settings()
    s.llm_api_key = ""
    client = LlmClient(s)
    assert client.available is False
    resp = client.complete_json("sys", "prompt")
    assert resp.ok is False and "unavailable" in resp.error
