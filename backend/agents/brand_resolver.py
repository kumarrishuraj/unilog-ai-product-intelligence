"""
Agent 3 -- Brand resolution.

Distinguishes the *supplier* (``Part_Manuf``, which for a buying co-op is a
distributor) from the *manufacturer of record* (the brand owner).  The labelled rows
confirm this matters: ``Appliance Dealers Cooperative (APPDE)`` is a buying co-op,
while ``MANUFACTURER_NAME`` is the brand's parent company.

Signal order is strictest-evidence-first, and the resolver refuses rather than
guesses when nothing supports a brand.
"""
from __future__ import annotations

from typing import Optional, Sequence

from backend.agents.resolution import METHOD_CONFIDENCE, Resolution
from backend.normalization.text import clean_value
from backend.reference.bootstrap import match_key
from backend.reference.registry import ReferenceRegistry
from backend.reference.schema import PROVENANCE_OFFICIAL, BrandRecord, ManufacturerRecord


class BrandResolver:
    """
    Resolves the brand, then the manufacturer of record implied by that brand.

    Signal order:
      1. explicit brand columns (E1_Brand / DIB_Brand), once placeholders are removed
      2. a brand mention inside Part_Desc, via the guarded lexicon
      3. the supplier itself, when the supplier is *not* a distributor
    """

    def __init__(self, registry: ReferenceRegistry):
        self.reg = registry
        self.lexicon = registry.brand_lexicon

    def resolve(self, brand_fields: Sequence[Optional[str]], description: Optional[str],
                supplier_record: Optional[ManufacturerRecord]) -> Resolution:
        # 1. explicit brand columns
        for raw in brand_fields:
            cleaned = clean_value(raw)
            if not cleaned:
                continue
            rec = self.reg.brand_by_key(match_key(cleaned))
            if rec is not None:
                conf = METHOD_CONFIDENCE["exact"]
                if rec.provenance != PROVENANCE_OFFICIAL:
                    conf *= 0.94
                return Resolution(rec.name, rec.code, "exact", round(conf, 4),
                                  [(rec.name, 100.0)], record=rec,
                                  detail="brand supplied explicitly in the input feed")
            # Present but unknown to the master: keep it, flag it.
            return Resolution(cleaned, None, "input_unverified", 0.55, [(cleaned, 100.0)],
                              detail="brand present in feed but absent from the brand master")

        # 2. brand mentioned in the description
        if description and self.lexicon is not None:
            mention = self.lexicon.best(description)
            if mention is not None:
                rec = self.reg.brand_by_key(match_key(mention.canonical))
                conf = 0.80 if rec is not None else 0.68
                return Resolution(mention.canonical, rec.code if rec else None,
                                  "description_mention", conf,
                                  [(mention.canonical, 100.0)], record=rec,
                                  detail=(f"brand name '{mention.alias}' found in the "
                                          f"product description"))

        # 3. supplier is itself the brand owner (only when not a distributor)
        if supplier_record is not None and not supplier_record.is_distributor:
            rec = self.reg.brand_by_key(match_key(supplier_record.name))
            if rec is not None:
                return Resolution(rec.name, rec.code, "supplier_is_brand", 0.72,
                                  [(rec.name, 100.0)], record=rec,
                                  detail=(f"supplier '{supplier_record.name}' is a "
                                          f"single-brand manufacturer"))

        return Resolution(None, None, "unresolved", 0.0, [],
                          detail="no brand evidence in feed columns, description or supplier")

    def manufacturer_of_record(self, brand: Resolution,
                               supplier: Resolution) -> Resolution:
        """
        Decide MANUFACTURER_NAME.

        A brand's owning manufacturer wins when known.  Otherwise the supplier is
        used only if it is not a distributor -- a co-op must never be published as
        the manufacturer.
        """
        rec: Optional[BrandRecord] = brand.record if isinstance(brand.record, BrandRecord) else None
        if rec is not None and rec.manufacturers:
            owner = rec.manufacturers[0]
            m = self.reg.manufacturer_by_key(match_key(owner))
            conf = min(0.95, brand.confidence + 0.05) if m else 0.60
            return Resolution(m.name if m else owner, m.code if m else None,
                              "brand_owner", round(conf, 4), [], record=m,
                              detail=f"'{brand.value}' is owned by '{owner}' per the brand master")

        supplier_rec = supplier.record if isinstance(supplier.record, ManufacturerRecord) else None
        if supplier_rec is not None and not supplier_rec.is_distributor:
            return Resolution(supplier_rec.name, supplier_rec.code, "supplier_direct",
                              round(supplier.confidence * 0.95, 4), [], record=supplier_rec,
                              detail="supplier is a direct manufacturer (not a distributor)")

        if supplier_rec is not None and supplier_rec.is_distributor:
            return Resolution(None, None, "distributor_only", 0.0, [],
                              detail=(f"'{supplier_rec.name}' is a distributor/co-op "
                                      f"(carries {supplier_rec.brand_fanout} brands); the "
                                      f"manufacturer of record needs manufacturer evidence"))

        return Resolution(None, None, "unresolved", 0.0, [],
                          detail="no brand owner and no direct-manufacturer supplier")
