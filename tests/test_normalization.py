"""Deterministic-core tests: fractions, UOM, text cleaning."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.normalization.fractions import (
    build_reference_table, decimal_to_fraction, fraction_to_decimal,
    normalize_decimals_in_text, normalize_measure,
)
from backend.normalization.text import (
    clean_record, clean_value, is_placeholder, repair_mojibake,
    split_segments, strip_leading_part_number, title_case, truncate,
)
from backend.normalization.uom import UomRegistry, split_measure


# ---------------------------------------------------------------------------
# Fractions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (0.5, "1/2"),
    (0.25, "1/4"),
    (0.015625, "1/64"),
    (50.25, "50-1/4"),          # observed in the labelled delivery-format rows
    (33.4375, "33-7/16"),
    (22.625, "22-5/8"),
    (23.875, "23-7/8"),
    (50.1875, "50-3/16"),
    (10.375, "10-3/8"),
    (7.0, "7"),
    (0, "0"),
])
def test_decimal_to_fraction(value, expected):
    assert decimal_to_fraction(value) == expected


def test_off_ladder_values_are_not_invented():
    """A value that is not an exact binary fraction must not be forced onto one."""
    assert decimal_to_fraction(3.14159) is None
    assert decimal_to_fraction(0.333) is None


def test_fraction_roundtrip():
    for text, expected in [("50-1/4", 50.25), ("1/2", 0.5), ("33 7/16", 33.4375), ("7", 7.0)]:
        assert fraction_to_decimal(text) == pytest.approx(expected)


def test_normalize_measure_recanonicalises_spacing():
    assert normalize_measure("33 7/16") == "33-7/16"
    assert normalize_measure("Leg") is None


def test_text_decimals_converted_but_others_left_alone():
    out = normalize_decimals_in_text("Depth 50.25 in, clearance 0.5 in, pi 3.14159")
    assert "50-1/4" in out and "1/2" in out and "3.14159" in out


def test_reference_table_is_lowest_terms():
    table = build_reference_table(64)
    assert table["0.5"] == "1/2"
    assert table["0.015625"] == "1/64"
    assert "2/4" not in table.values()


# ---------------------------------------------------------------------------
# UOM
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def uom():
    return UomRegistry()


@pytest.mark.parametrize("raw,expected", [
    ("inches", "in"), ("inch", "in"), ("IN.", "in"), ("in", "in"),
    ("VOLTS", "V"), ("amp", "A"), ("dba", "dBA"), ("kwh", "kW-hr"),
    ("Hours", "hr"), ("lumens", "lm"),
])
def test_uom_alias_resolution(uom, raw, expected):
    assert uom.resolve(raw).abbreviation == expected


def test_unapproved_uom_is_not_invented(uom):
    res = uom.resolve("flurbles")
    assert res.abbreviation is None and not res.approved


def test_uom_house_spacing(uom):
    """'24 in' is correct; '24in' is not."""
    assert uom.format_measure("24", "inches") == "24 in"
    assert uom.format_measure("120", "v") == "120 V"
    assert uom.format_measure("47", "dba") == "47 dBA"


def test_uom_compact_form_for_invoice(uom):
    assert uom.format_measure("50-1/4", "in", compact=True) == "50-1/4IN"
    assert uom.format_measure("120", "V", compact=True) == "120V"


@pytest.mark.parametrize("token,expected", [
    ("24in", ("24", "in")),
    ("50-1/4 in", ("50-1/4", "in")),
    ("120V", ("120", "V")),
    ("Leg", None),
])
def test_split_measure(token, expected):
    assert split_measure(token) == expected


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", [
    "-- Unbranded --", "-- No Unilog Brand --", "-- No DIB Brand --",
    "-", "N/A", "", "  ", "COMMODITY - UNBRANDED", "TBD", "null",
])
def test_placeholders_detected(value):
    assert is_placeholder(value)


@pytest.mark.parametrize("value", ["TREX", "Freud Inc (2435)", "Philips", "Stainless Steel"])
def test_real_values_not_flagged_as_placeholders(value):
    assert not is_placeholder(value)


def test_clean_value_returns_none_for_sentinels():
    assert clean_value("-- Unbranded --") is None
    assert clean_value("  TREX  ") == "TREX"


def test_mojibake_repair_is_safe():
    # utf-8 read as cp1252 produces these sequences
    assert repair_mojibake("Caf" + chr(0xC3) + chr(0xA9)) == "Caf" + chr(0xE9)
    assert repair_mojibake("BRAND" + chr(0xC2) + chr(0xAE)) == "BRAND" + chr(0xAE)
    # already-correct text must be untouched
    clean = "BRAND" + chr(0xAE)
    assert repair_mojibake(clean) == clean
    assert repair_mojibake("Whirlpool") == "Whirlpool"


def test_title_case_preserves_acronyms_and_internal_caps():
    assert title_case("led wall light") == "LED Wall Light"
    assert title_case("gfci outlet") == "GFCI Outlet"
    assert title_case("stainless steel") == "Stainless Steel"
    assert title_case("CleanBoost dishwasher") == "CleanBoost Dishwasher"


def test_strip_leading_part_number():
    assert strip_leading_part_number(
        "PDSH4816AF Dishwasher SS - Display Only", "PDSH4816AF"
    ) == "Dishwasher SS - Display Only"


def test_split_segments():
    assert split_segments("3M 775L Stikit Film P150 - Cubitron II 50 Disc/Box") == [
        "3M 775L Stikit Film P150", "Cubitron II 50 Disc/Box"]


def test_truncate_respects_word_boundary():
    assert len(truncate("DISHWASHER LEG 5 SST 120V 15A 50-1/4IN AND MORE", 40)) <= 40


def test_clean_record_reports_placeholders():
    res = clean_record(
        {"E1_Brand": "-- Unbranded --", "Part_Manuf": "Freud Inc (2435)"},
        ["E1_Brand", "Part_Manuf"])
    assert res.values["E1_Brand"] is None
    assert res.values["Part_Manuf"] == "Freud Inc (2435)"
    assert "E1_Brand" in res.placeholders
