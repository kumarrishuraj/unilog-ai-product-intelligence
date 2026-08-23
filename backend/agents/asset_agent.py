"""
Digital-asset naming and discovery.

The labelled rows show assets are referenced by **filename**, not URL, and the naming
convention is deterministic:

    FRIGIDAIRE_PDSH4816AF.jpg                        product image
    FRIGIDAIRE_PDSH4816AF_1.jpg .. _4.jpg            alternates
    FRIGIDAIRE_PDSH4816AF_Specification_Sheet.pdf    document
    Whirlpool_WDTS7024RZ.jpg                         brand casing preserved

so the convention is ``{Brand}_{MPN}[_{n}|_{Doc_Type}].{ext}``, with the brand's own
casing kept and the trademark symbol dropped.

Important: a filename is only emitted when there is **evidence the asset exists** --
either research returned it, or the caller supplies an asset manifest listing files
actually present.  Emitting a name for a file nobody has is a fabricated reference,
which Golden Rule 1 forbids.  ``plan_names`` is therefore separate from ``assign``:
the first says what an asset *would* be called, the second commits only what exists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from backend.models.product import Evidence, SourceTier

IMAGE_EXT = ".jpg"
DOC_EXT = ".pdf"

# Asset key -> filename suffix used by the convention.
DOC_SUFFIX: Dict[str, str] = {
    "specification_sheet": "Specification_Sheet",
    "instruction_manual": "Instruction_Manual",
    "owners_manual": "Owners_Manual",
    "service_manual": "Service_Manual",
    "warranty_information": "Warranty_Information",
    "catalog": "Catalog",
    "line_drawing": "Line_Drawing",
    "submittal": "Submittal",
    "sds": "SDS",
}

MAX_ALTERNATE_IMAGES = 8


def _slug(text: str) -> str:
    """Filename-safe token: symbols dropped, spaces to underscores, casing kept."""
    s = re.sub(r"[®™©]", "", str(text or ""))
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s-]+", "_", s).strip("_")
    return s


@dataclass
class AssetPlan:
    """Names an asset *would* take, plus whether it is confirmed to exist."""
    product_image: str = ""
    alternate_images: List[str] = field(default_factory=list)
    documents: Dict[str, str] = field(default_factory=dict)
    confirmed: Set[str] = field(default_factory=set)

    def as_row_assets(self) -> Dict[str, str]:
        """Only confirmed assets are published."""
        out: Dict[str, str] = {}
        if self.product_image and self.product_image in self.confirmed:
            out["product_image"] = self.product_image
        for i, name in enumerate(self.alternate_images, start=1):
            if name in self.confirmed and i <= MAX_ALTERNATE_IMAGES:
                out[f"alternate_image_{i}"] = name
        for key, name in self.documents.items():
            if name in self.confirmed:
                out[key] = name
        return out


class AssetAgent:
    """Applies the naming convention and confirms assets against evidence."""

    def __init__(self, manifest: Optional[Iterable[str]] = None):
        # A manifest is the set of asset filenames known to exist (from a DAM
        # export, a media folder listing, or research output).
        self.manifest: Set[str] = {str(m).strip() for m in (manifest or ()) if str(m).strip()}

    # -- naming ------------------------------------------------------------
    @staticmethod
    def plan_names(brand: Optional[str], mpn: Optional[str],
                   document_keys: Sequence[str] = (),
                   alternate_count: int = 0) -> AssetPlan:
        plan = AssetPlan()
        if not mpn:
            return plan
        stem_parts = [p for p in (_slug(brand) if brand else "", _slug(mpn)) if p]
        if not stem_parts:
            return plan
        stem = "_".join(stem_parts)

        plan.product_image = f"{stem}{IMAGE_EXT}"
        plan.alternate_images = [f"{stem}_{i}{IMAGE_EXT}"
                                 for i in range(1, min(alternate_count,
                                                       MAX_ALTERNATE_IMAGES) + 1)]
        for key in document_keys:
            suffix = DOC_SUFFIX.get(key)
            if suffix:
                plan.documents[key] = f"{stem}_{suffix}{DOC_EXT}"
        return plan

    # -- confirmation ------------------------------------------------------
    def confirm(self, plan: AssetPlan,
                research_documents: Optional[Dict[str, str]] = None) -> AssetPlan:
        """
        Mark which planned assets are backed by evidence.

        Two evidence sources count: presence in the manifest, or a document URL
        returned by manufacturer research.
        """
        candidates = [plan.product_image, *plan.alternate_images, *plan.documents.values()]
        for name in candidates:
            if name and name in self.manifest:
                plan.confirmed.add(name)
        for key in (research_documents or {}):
            name = plan.documents.get(key)
            if name:
                plan.confirmed.add(name)
        return plan

    def evidence_for(self, plan: AssetPlan) -> List[Evidence]:
        out: List[Evidence] = []
        for name in sorted(plan.confirmed):
            tier = (SourceTier.MANUFACTURER_DOC.value if name.endswith(DOC_EXT)
                    else SourceTier.MASTER_DATA.value)
            out.append(Evidence(source="asset_manifest", tier=tier, snippet=name,
                                locator="digital asset naming convention"))
        return out

    def unconfirmed(self, plan: AssetPlan) -> List[str]:
        """Names the convention predicts but which nothing confirms exist."""
        predicted = [plan.product_image, *plan.alternate_images, *plan.documents.values()]
        return [n for n in predicted if n and n not in plan.confirmed]
