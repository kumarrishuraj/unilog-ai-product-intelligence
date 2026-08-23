"""
Resolution, classification, description and end-to-end pipeline tests.

The description tests are the important ones: they assert the generator reproduces
the labelled delivery-format strings character-for-character from a verified fact
sheet, which is what proves the derived formulas are correct rather than plausible.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.agents.attribute_agent import AttributeExtractor, apply_series_and_model
from backend.agents.brand_inference import MpnBrandInference, Observation
from backend.agents.description_agent import DescriptionGenerator, DescriptionSpec
from backend.agents.brand_resolver import BrandResolver
from backend.agents.manufacturer_resolver import ManufacturerResolver
from backend.agents.research_agent import classify_source
from backend.config import get_settings
from backend.models.product import AttributeValue, SourceTier
from backend.pipeline.io import profile_input, read_table
from backend.pipeline.orchestrator import Orchestrator
from backend.reference.brand_lexicon import BrandLexicon
from backend.reference.registry import ReferenceRegistry
from backend.validation.schema import OutputSchema

PACKS = ROOT / "data" / "packs"
RAW = ROOT / "data" / "raw"
INPUT_CSV = RAW / "sample_1000_input.csv"
LABELLED_CSV = RAW / "delivery_format_labelled.csv"


@pytest.fixture(scope="module")
def rows():
    return read_table(INPUT_CSV)


@pytest.fixture(scope="module")
def registry(rows):
    return ReferenceRegistry.build(ROOT / "data" / "reference", PACKS, rows)


@pytest.fixture(scope="module")
def schema():
    return OutputSchema.from_file(LABELLED_CSV)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------
def test_schema_has_252_columns_read_from_the_template(schema):
    assert len(schema) == 252
    assert schema.headers[0] == "MFR URL"
    assert schema.headers[-1] == "Actual Image (Yes/No)"


def test_schema_detects_column_families(schema):
    assert schema.attribute_slots == 50
    assert schema.family_size("item_features") == 20
    assert schema.family_size("ref_url") == 5


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    "Freud Inc (2435)", "FREUD, INC.", "Freud Inc", "freud",
])
def test_manufacturer_variants_resolve_to_one_record(registry, raw):
    res = ManufacturerResolver(registry).resolve(raw)
    assert res.value == "Freud Inc"
    assert res.confidence > 0.85


def test_unknown_manufacturer_is_not_guessed(registry):
    res = ManufacturerResolver(registry).resolve("Totally Unknown Trading Co")
    assert res.value is None
    assert res.method == "unresolved"


def test_placeholder_supplier_yields_nothing(registry):
    assert ManufacturerResolver(registry).resolve("-").value is None


def test_brand_from_explicit_column_wins(registry):
    res = BrandResolver(registry).resolve(["TREX", None], "some decking", None)
    assert res.value == "TREX"
    assert res.method == "exact"


def test_distributor_is_never_the_manufacturer_of_record(registry):
    mr, br = ManufacturerResolver(registry), BrandResolver(registry)
    supplier = mr.resolve("Appliance Dealers Cooperative (APPDE)")
    assert supplier.record is not None and supplier.record.is_distributor
    brand = br.resolve([None, None], "PDSH4816AF Dishwasher SS", supplier.record)
    mor = br.manufacturer_of_record(brand, supplier)
    assert mor.value != "Appliance Dealers Cooperative"


# ---------------------------------------------------------------------------
# Brand lexicon guards
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def lexicon():
    return BrandLexicon.from_pack(PACKS / "brand_lexicon.json")


@pytest.mark.parametrize("text", [
    'DPH12B 2" #1 Phillips Drive - Bit',     # screw recess, not Philips
    'DSQ12B 2" #1 Square Drive Bit',         # not Square D
    "MWUG42010424 UTW Pro Heated Glove Blk LG",   # size Large, not LG
])
def test_homograph_guards_block_false_brands(lexicon, text):
    assert lexicon.find(text) == []


@pytest.mark.parametrize("text,expected", [
    ('49-94-0013 Milw 5"x.045"x7/8" Metal Cut Off Disc', "Milwaukee"),
    ("KDTS424SBE Kitchen Aid Dishwasher Bk", "KitchenAid"),
    ("LDPH5554D LG Dishwasher BSS", "LG"),
    ("DF7004WE Speed Queen Elect Dryer Wh", "Speed Queen"),
])
def test_real_brand_mentions_detected(lexicon, text, expected):
    best = lexicon.best(text)
    assert best is not None and best.canonical == expected


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("desc,leaf", [
    ("PDSH4816AF Dishwasher SS - Display Only", "built_in_dishwashers"),
    ('49-94-0013 Milw 5"x.045"x7/8" Metal Cut Off Disc', "cutting_grinding_wheels"),
    ("R92-GFWT1-0KW 15A GFCI Outlet Wh", "wiring_devices"),
    ("DF7004WE Speed Queen Elect Dryer Wh", "laundry_dryers"),
    ("1x6-16' Coastline Sq Edge - Vintage Azek PVC Decking", "composite_decking"),
    ("576355 60/100/150 Led Med 50k", "lamps"),
])
def test_classification(registry, desc, leaf):
    cls = registry.taxonomy.classify(desc)
    assert cls.leaf is not None and cls.leaf.id == leaf


def test_unclassifiable_falls_back_without_crashing(registry):
    cls = registry.taxonomy.classify("ZZZ mystery widget thing")
    assert cls.method == "fallback"
    assert cls.confidence == 0.0


# ---------------------------------------------------------------------------
# Attribute extraction
# ---------------------------------------------------------------------------
def test_dimension_chain_parsed_in_order(registry):
    ex = AttributeExtractor(registry.uom)
    leaf = registry.taxonomy.get("cutting_grinding_wheels")
    attrs = {a.label: a for a in ex.extract(leaf, '49-94-0013 Milw 5"x.045"x7/8" Metal Cut Off Disc')}
    assert attrs["Diameter"].value == "5"
    assert attrs["Thickness"].value == "0.045"     # off-ladder: stays decimal
    assert attrs["Arbor Size"].value == "7/8"
    assert attrs["Application"].value == "Metal"


def test_lov_synonym_mapping(registry):
    ex = AttributeExtractor(registry.uom)
    leaf = registry.taxonomy.get("built_in_dishwashers")
    attrs = {a.label: a for a in ex.extract(leaf, "PDSH4816AF Dishwasher SS")}
    assert attrs["Material"].value == "Stainless Steel"      # 'SS' -> LOV value
    assert attrs["Material"].lov_compliant is True


def test_every_template_slot_is_emitted_even_when_empty(registry):
    ex = AttributeExtractor(registry.uom)
    leaf = registry.taxonomy.get("built_in_dishwashers")
    attrs = ex.extract(leaf, "PDSH4816AF Dishwasher SS")
    assert len(attrs) == len(leaf.attributes)
    assert [a.label for a in attrs] == list(leaf.labels)


# ---------------------------------------------------------------------------
# Description generation -- reproduces the labelled rows exactly
# ---------------------------------------------------------------------------
def _facts(leaf, values):
    out = []
    for a in leaf.attributes:
        v, u = values.get(a.label, (None, None))
        out.append(AttributeValue(label=a.label, value=v, uom=u,
                                  confidence=0.95 if v else 0.0,
                                  method="fixture" if v else "not_found"))
    return out


REG = chr(0xAE)   # (R)
TM = chr(0x2122)  # (TM)


def test_generator_reproduces_labelled_row_one(registry):
    leaf = registry.taxonomy.get("built_in_dishwashers")
    gen = DescriptionGenerator(registry.uom, DescriptionSpec.load(PACKS))
    attrs = _facts(leaf, {
        "Series": ("Professional Series", None),
        "Number of Wash Cycles": ("5", None),
        "Voltage Rating": ("120", "V"), "Amperage Rating": ("15", "A"),
        "Mounting Type": ("Leg", None),
        "Size": ("24 in W x 24-1/4 in D", None),
        "Depth With Door Open": ("50-1/4", "in"),
        "Minimum Height": ("8-1/2 in Upper Rack, 11-1/4 in Lower Rack", None),
        "Maximum Height": ("10-3/8 in Upper Rack, 13-1/4 in Lower Rack", None),
        "Sound Level": ("47", "dBA"), "Material": ("Stainless Steel", None),
        "Additional Information": (
            "240 kW-hr Annual Energy, 1 to 12 hr Delay Start Hours", None),
    })
    out = gen.generate(leaf, brand="FRIGIDAIRE" + REG,
                       manufacturer="Rheem Manufacturing", mpn="PDSH4816AF",
                       product_name="Dishwasher", attributes=attrs,
                       with_clause_text="With CleanBoost" + TM)

    assert out["LONG_DESC1"].value == (
        "FRIGIDAIRE" + REG + " Dishwasher With CleanBoost" + TM + ", Professional Series, "
        "5 Wash Cycles, 120 V, 15 A, Leg Mounting, 24 in W x 24-1/4 in D, "
        "50-1/4 in Depth With Door Open, 8-1/2 in Upper Rack, 11-1/4 in Lower Rack "
        "Minimum Height, 10-3/8 in Upper Rack, 13-1/4 in Lower Rack Maximum Height, "
        "47 dBA Sound Level, Stainless Steel, Additional Information: 240 kW-hr "
        "Annual Energy, 1 to 12 hr Delay Start Hours")
    assert out["SHORT_DESC"].value == (
        "FRIGIDAIRE" + REG + " Professional Series PDSH4816AF Dishwasher With "
        "CleanBoost" + TM + ", Leg Mounting, 5-Wash Cycle, Stainless Steel")
    assert out["RETAIL_DESC"].value == (
        "Professional Series Dishwasher, Leg Mounting, 5-Wash Cycle, Stainless Steel")
    assert out["MOBILE_DESC"].value == (
        "Rheem Manufacturing FRIGIDAIRE, Dishwasher, Professional Series, PDSH4816AF")
    assert out["INVOICE_DESC"].value == "DISHWASHER LEG 5 SST 120V 15A 50-1/4IN"


def test_generator_reproduces_labelled_row_two(registry):
    leaf = registry.taxonomy.get("built_in_dishwashers")
    gen = DescriptionGenerator(registry.uom, DescriptionSpec.load(PACKS))
    addl = ("Folding Tines, Leak Detection System, Moisture Repellent Silverware "
            "Basket, Normal Cycle, Quick Wash Cycle, Sani Rinse Option, Sensor "
            "Cycle, Triple Wash Spray")
    attrs = _facts(leaf, {
        "Series": ("Eco Series", None),
        "Voltage Rating": ("120", "V"), "Amperage Rating": ("10", "A"),
        "Mounting Type": ("Built-in", None),
        "Size": ("33-7/16 in H x 23-7/8 in W x 22-5/8 in D", None),
        "Depth With Door Open": ("50-3/16", "in"),
        "Minimum Height": ("33-7/16", "in"),
        "Sound Level": ("41", "dBA"),
        "Material": ("Stainless Steel", None), "Color": ("Stainless Steel", None),
        "Additional Information": (addl, None),
    })
    out = gen.generate(leaf, brand="Whirlpool" + REG,
                       manufacturer="Whirlpool Corporation", mpn="WDTS7024RZ",
                       product_name="Dishwasher", attributes=attrs)

    assert out["LONG_DESC1"].value == (
        "Whirlpool" + REG + " Dishwasher, Eco Series, 120 V, 10 A, Built-in Mounting, "
        "33-7/16 in H x 23-7/8 in W x 22-5/8 in D, 50-3/16 in Depth With Door Open, "
        "33-7/16 in Minimum Height, 41 dBA Sound Level, Stainless Steel, "
        "Stainless Steel, Additional Information: " + addl)
    assert out["SHORT_DESC"].value == (
        "Whirlpool" + REG + " Eco Series WDTS7024RZ Dishwasher, Built-in Mounting, "
        "Stainless Steel, Stainless Steel")
    assert out["MOBILE_DESC"].value == (
        "Whirlpool, Dishwasher, Eco Series, WDTS7024RZ, Built-in Mounting")
    assert out["INVOICE_DESC"].value == "DISHWASHER BLTLN SST SST 120V 10A 41DBA"


def test_marketing_copy_is_never_invented(registry):
    leaf = registry.taxonomy.get("built_in_dishwashers")
    gen = DescriptionGenerator(registry.uom, DescriptionSpec.load(PACKS))
    out = gen.generate(leaf, brand="Whirlpool", manufacturer=None, mpn="X1",
                       product_name="Dishwasher", attributes=_facts(leaf, {}))
    assert out["MARKETING_DESCRIPTION"].value is None


def test_with_clause_taken_verbatim_from_feed():
    gen = DescriptionGenerator.with_clause.__func__ if False else DescriptionGenerator.with_clause
    clause, snippet = gen("6' Wh Select T-Rail Kit Horiz - w/Sq Composite Balusters")
    assert clause == "With Sq Composite Balusters"
    assert "w/" in snippet


# ---------------------------------------------------------------------------
# Source hierarchy (Golden Rule 3)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://www.amazon.com/dp/B01ABC",
    "https://www.ebay.com/itm/12345",
    "https://reddit.com/r/tools/comments/x",
])
def test_marketplaces_are_rejected_outright(url):
    assert classify_source(url, "Whirlpool", "Whirlpool Corporation").score == 0.0


def test_manufacturer_product_page_scores_highest():
    c = classify_source("https://www.whirlpool.com/p/WDTS7024RZ", "Whirlpool",
                        "Whirlpool Corporation")
    assert c.tier == SourceTier.MANUFACTURER_PRODUCT_PAGE.value
    assert c.score == 1.0


def test_distributor_is_downranked_not_rejected():
    c = classify_source("https://www.grainger.com/product/abc", "Milwaukee", "Milwaukee")
    assert c.tier == SourceTier.DISTRIBUTOR.value
    assert 0 < c.score < 0.5


# ---------------------------------------------------------------------------
# Corpus brand inference guards
# ---------------------------------------------------------------------------
def test_prefix_inference_requires_shared_scope():
    """
    Real case from the supplied feed: KitchenAid dishwashers whose brand is visible
    teach the 'KDTS' prefix, which then recovers the sibling rows whose brand is not.
    An unrelated category sharing the prefix must NOT inherit it -- an unguarded
    version of this produced 'SQ Washer -> Edge Eyewear'.
    """
    inf = MpnBrandInference().fit([
        Observation("KDTS424SBE", "KitchenAid", "built_in_dishwashers", "APPDE"),
        Observation("KDTS324SPS", "KitchenAid", "built_in_dishwashers", "APPDE"),
    ])
    same = inf.infer("KDTS624SBE", "built_in_dishwashers", "APPDE")
    assert same is not None and same.brand == "KitchenAid"
    assert same.confidence <= 0.55           # inference never reaches the HIGH band

    # Same prefix, unrelated category and supplier: refuse.
    assert inf.infer("KDTS900ZZ", "laundry_washers", "Some Other Co") is None


def test_two_letter_prefixes_are_too_weak_to_learn():
    """Short prefixes collide across manufacturers, so they are never learned."""
    inf = MpnBrandInference().fit([
        Observation("TC121VS", "Edge Eyewear", "safety_ppe", "Edge"),
        Observation("TC126VS", "Edge Eyewear", "safety_ppe", "Edge"),
    ])
    assert inf.infer("TC5003BN", "safety_ppe", "Edge") is None


def test_product_is_never_evidence_for_its_own_brand():
    inf = MpnBrandInference().fit([
        Observation("ABCD1", "BrandX", "cat", "sup"),
        Observation("ABCD2", "BrandX", "cat", "sup"),
    ])
    assert inf.infer("ABCD1", "cat", "sup") is None      # support collapses to 1


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def small_run(rows):
    settings = get_settings()
    settings.delivery_template = LABELLED_CSV
    orch = Orchestrator(settings)
    result = orch.run(rows[:60])
    orch.close()
    return result


def test_end_to_end_produces_exact_schema(small_run):
    assert len(small_run.rows) == 60
    for row in small_run.rows:
        assert list(row.keys()) == small_run.schema.headers
        assert len(row) == 252


def test_no_row_fails(small_run):
    assert all(p.status.value != "FAILED" for p in small_run.products)


def test_input_columns_pass_through_verbatim(small_run):
    for prod, row in zip(small_run.products, small_run.rows):
        assert row["Mfg_Part_Num"] == prod.raw["Mfg_Part_Num"]
        assert row["Part_Desc"] == prod.raw["Part_Desc"]
        assert row["Part_Manuf"] == prod.raw["Part_Manuf"]


def test_every_populated_attribute_has_evidence(small_run):
    for p in small_run.products:
        for av in p.populated_attributes():
            assert av.evidence, f"{p.mpn.value}/{av.label} has no evidence"


def test_lov_values_are_all_approved(small_run):
    for p in small_run.products:
        for av in p.attributes:
            if av.present and av.lov_compliant is not None:
                assert av.lov_compliant, f"{p.mpn.value}/{av.label}={av.value}"


def test_uom_columns_only_contain_approved_abbreviations(small_run, registry):
    uom_cols = small_run.schema.family_columns("attribute_uom")
    for row in small_run.rows:
        for c in uom_cols:
            v = (row.get(c) or "").strip()
            if v:
                assert registry.uom.is_approved(v), f"{c}={v}"


def test_malformed_row_does_not_abort_the_batch():
    settings = get_settings()
    settings.delivery_template = LABELLED_CSV
    bad = [
        {"Mfg_Part_Num": "OK1", "Part_Desc": "GFCI Outlet Wh", "Part_Manuf": "Leviton Mfg Co"},
        {"Mfg_Part_Num": None, "Part_Desc": None, "Part_Manuf": None},
        {},
        {"Mfg_Part_Num": "OK2", "Part_Desc": "Dishwasher SS", "Part_Manuf": "-"},
    ]
    orch = Orchestrator(settings)
    result = orch.run(bad)
    orch.close()
    assert len(result.rows) == 4
    assert result.stats.total == 4


def test_profile_detects_placeholder_columns(rows):
    profile = profile_input(rows[:200], "test")
    unilog = next(c for c in profile.columns if c.name == "Unilog_Brand")
    assert unilog.placeholder == 200
    assert any("placeholder" in w for w in profile.warnings)
