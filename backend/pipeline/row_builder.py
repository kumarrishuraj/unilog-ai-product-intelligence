"""
Project an ``EnrichedProduct`` onto the delivery-format columns.

Rules enforced here:

  * Column names, order and count come from the loaded ``OutputSchema`` -- never
    from literals in this file.
  * Unknown values are written as an **empty string**, matching the labelled rows
    (which leave gaps blank rather than writing 'Not Found').
  * Attribute label slots are emitted even when the value is empty, because the
    labelled rows do exactly that: the label sequence belongs to the category.
  * Input columns present in both the feed and the delivery format are passed
    through verbatim -- ``Mfg_Part_Num``, ``Part_Desc``, ``E1_Brand``,
    ``Unilog_Brand``, ``DIB_Brand``, ``Part_Manuf`` -- including their placeholder
    sentinels, because the delivery format preserves them.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from backend.models.product import AttributeValue, EnrichedProduct
from backend.validation.schema import OutputSchema

# Feed columns copied through untouched when the template also defines them.
PASSTHROUGH_COLUMNS = (
    "Mfg_Part_Num", "Part_Desc", "E1_Brand", "Unilog_Brand", "DIB_Brand", "Part_Manuf",
)

# Enriched scalar fields -> delivery-format column names.
SCALAR_MAP = {
    "MANUFACTURER_NAME": "manufacturer",
    "BRAND_NAME": "brand",
    "MANUFACTURER_PART_NUMBER": "mpn",
    "Classpath": "classpath",
    "Dept": "dept",
    "Class": "klass",
    "Fine": "fine",
    "Product Name": "product_name",
    "UNSPSC": "unspsc",
}


def _safe(value: Optional[str]) -> str:
    return "" if value is None else str(value)


class RowBuilder:
    """Builds one delivery-format record per enriched product."""

    def __init__(self, schema: OutputSchema):
        self.schema = schema

    def build(self, product: EnrichedProduct) -> Dict[str, str]:
        row = self.schema.blank_row()

        # -- 1. passthrough input columns -------------------------------
        for col in PASSTHROUGH_COLUMNS:
            if self.schema.has(col) and col in product.raw:
                row[col] = _safe(product.raw.get(col))

        # -- 2. resolved scalars ----------------------------------------
        for col, attr in SCALAR_MAP.items():
            if not self.schema.has(col):
                continue
            fv = getattr(product, attr, None)
            if fv is not None and getattr(fv, "present", False):
                row[col] = _safe(fv.value)

        # BRAND_NAME carries the trademark symbol; TRADE_NAME mirrors the brand.
        if self.schema.has("TRADE_NAME") and product.brand.present:
            row["TRADE_NAME"] = ""      # only populated from manufacturer evidence

        # -- 3. descriptions --------------------------------------------
        for col in ("MOBILE_DESC", "INVOICE_DESC", "SHORT_DESC", "LONG_DESC1",
                    "RETAIL_DESC", "MARKETING_DESCRIPTION"):
            if self.schema.has(col):
                row[col] = _safe(product.description(col))

        # -- 4. attribute triples ---------------------------------------
        self._write_attributes(row, product.attributes)

        # -- 5. item features -------------------------------------------
        feature_cols = self.schema.family_columns("item_features")
        for col, text in zip(feature_cols, product.features):
            row[col] = _safe(text)

        # -- 6. clause / approvals --------------------------------------
        if self.schema.has("With") and product.with_clause.present:
            row["With"] = _safe(product.with_clause.value)
        if self.schema.has("Standard/Approvals") and product.approvals.present:
            row["Standard/Approvals"] = _safe(product.approvals.value)

        # -- 7. URLs -----------------------------------------------------
        if self.schema.has("MFR URL") and product.urls.get("mfr"):
            row["MFR URL"] = product.urls["mfr"]
        ref_cols = self.schema.family_columns("ref_url")
        refs = [u for k, u in sorted(product.urls.items()) if k.startswith("ref") and u]
        for col, url in zip(ref_cols, refs):
            row[col] = url

        # -- 8. digital assets -------------------------------------------
        self._write_assets(row, product)

        return row

    # ------------------------------------------------------------------
    def _write_attributes(self, row: Dict[str, str],
                          attributes: Sequence[AttributeValue]) -> None:
        """
        Fill ATTRIBUTE_LABEL/VALUE/UOM triples in template order.

        Labels are written for every slot the leaf defines, even when the value is
        empty -- that is the behaviour the labelled rows exhibit.  Excess attributes
        beyond the template's slot count are dropped (and flagged by the validator).
        """
        labels = self.schema.family_columns("attribute_label")
        values = self.schema.family_columns("attribute_value")
        uoms = self.schema.family_columns("attribute_uom")
        n = min(len(labels), len(values), len(uoms))

        for i in range(n):
            if i >= len(attributes):
                break
            av = attributes[i]
            row[labels[i]] = _safe(av.label)
            row[values[i]] = _safe(av.value) if av.present else ""
            row[uoms[i]] = _safe(av.uom) if (av.present and av.uom) else ""

    def _write_assets(self, row: Dict[str, str], product: EnrichedProduct) -> None:
        assets = product.assets
        if self.schema.has("Product Image") and assets.get("product_image"):
            row["Product Image"] = assets["product_image"]

        alt_cols = self.schema.family_columns("alternate_image")
        alts = [v for k, v in sorted(assets.items())
                if k.startswith("alternate_image") and v]
        for col, val in zip(alt_cols, alts):
            row[col] = val

        for key, col in (("specification_sheet", "Specification Sheet"),
                         ("instruction_manual", "Instruction/Installation Manual"),
                         ("owners_manual", "Owners/User Manual"),
                         ("service_manual", "Service Manual"),
                         ("warranty_information", "Warranty Information"),
                         ("catalog", "Catalog"),
                         ("line_drawing", "Line Drawing"),
                         ("submittal", "Submittal"),
                         ("sds", "SDS")):
            if self.schema.has(col) and assets.get(key):
                row[col] = assets[key]

        if self.schema.has("Actual Image (Yes/No)"):
            row["Actual Image (Yes/No)"] = "Yes" if assets.get("product_image") else ""


def build_rows(products: Sequence[EnrichedProduct], schema: OutputSchema) -> List[Dict[str, str]]:
    builder = RowBuilder(schema)
    return [builder.build(p) for p in products]
