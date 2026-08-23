"""
Configuration.

Secrets come from the environment (loaded from ``.env`` when present); nothing is
hard-coded.  Every pipeline stage can be toggled, which is what makes the
architecture modular in practice rather than just on the diagram.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency on python-dotenv)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


# Pipeline stages, in execution order.  Each maps to an orchestrator step.
STAGE_ORDER: List[str] = [
    "input_analysis",
    "cleaning",
    "entity_resolution",
    "classification",
    "attribute_extraction",
    "normalization",
    "manufacturer_research",
    "description_generation",
    "digital_assets",
    "validation",
    "confidence_scoring",
    "review_queue",
]


@dataclass
class Settings:
    # -- paths ---------------------------------------------------------
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    raw_dir: Path = BASE_DIR / "data" / "raw"
    reference_dir: Path = BASE_DIR / "data" / "reference"
    pack_dir: Path = BASE_DIR / "data" / "packs"
    processed_dir: Path = BASE_DIR / "data" / "processed"
    cache_dir: Path = BASE_DIR / "data" / "processed" / "cache"

    # -- delivery-format template --------------------------------------
    delivery_template: Optional[Path] = None

    # -- LLM -----------------------------------------------------------
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "auto"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-5"))
    llm_max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 2000))
    llm_timeout: float = field(default_factory=lambda: _float("LLM_TIMEOUT", 45.0))
    llm_max_retries: int = field(default_factory=lambda: _int("LLM_MAX_RETRIES", 3))
    llm_batch_size: int = field(default_factory=lambda: _int("LLM_BATCH_SIZE", 10))
    llm_enabled: bool = field(default_factory=lambda: _flag("LLM_ENABLED", True))

    # -- web research --------------------------------------------------
    search_api_key: str = field(default_factory=lambda: os.getenv("SEARCH_API_KEY", ""))
    search_provider: str = field(default_factory=lambda: os.getenv("SEARCH_PROVIDER", "none"))
    research_enabled: bool = field(default_factory=lambda: _flag("RESEARCH_ENABLED", False))
    research_timeout: float = field(default_factory=lambda: _float("RESEARCH_TIMEOUT", 20.0))
    research_max_pages: int = field(default_factory=lambda: _int("RESEARCH_MAX_PAGES", 3))

    # -- pipeline ------------------------------------------------------
    stages: Dict[str, bool] = field(default_factory=lambda: {s: True for s in STAGE_ORDER})
    max_workers: int = field(default_factory=lambda: _int("MAX_WORKERS", 8))
    cache_enabled: bool = field(default_factory=lambda: _flag("CACHE_ENABLED", True))

    # -- thresholds ----------------------------------------------------
    review_threshold: float = field(default_factory=lambda: _float("REVIEW_THRESHOLD", 0.62))

    def __post_init__(self) -> None:
        for d in (self.raw_dir, self.reference_dir, self.pack_dir,
                  self.processed_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        if self.delivery_template is None:
            self.delivery_template = self.discover_delivery_template()

    def discover_delivery_template(self) -> Optional[Path]:
        """Find whichever delivery-format template is available, by content not name."""
        candidates: List[Path] = []
        for d in (self.raw_dir, self.reference_dir, self.base_dir):
            if not d.exists():
                continue
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xlsm"):
                    stem = p.stem.lower()
                    if "delivery" in stem or "expected output" in stem or "output" in stem:
                        candidates.append(p)
        return candidates[0] if candidates else None

    @property
    def stage_list(self) -> List[str]:
        return [s for s in STAGE_ORDER if self.stages.get(s, True)]

    def enable(self, stage: str, on: bool = True) -> None:
        if stage in self.stages:
            self.stages[stage] = on

    def as_dict(self) -> Dict[str, object]:
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_enabled": self.llm_enabled,
            "llm_key_present": bool(self.llm_api_key),
            "search_provider": self.search_provider,
            "search_key_present": bool(self.search_api_key),
            "research_enabled": self.research_enabled,
            "cache_enabled": self.cache_enabled,
            "max_workers": self.max_workers,
            "review_threshold": self.review_threshold,
            "stages": dict(self.stages),
            "delivery_template": str(self.delivery_template) if self.delivery_template else None,
            "reference_dir": str(self.reference_dir),
        }


_SETTINGS: Optional[Settings] = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings()
    return _SETTINGS
