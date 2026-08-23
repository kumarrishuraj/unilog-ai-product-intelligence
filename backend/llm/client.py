"""
LLM abstraction with strict JSON output, caching, retry and an offline mode.

Design decisions
----------------
* **Structured output only.**  Every call declares a JSON schema and the response is
  parsed and validated.  Free-form prose is never consumed by the pipeline.
* **The system degrades, it does not break.**  With no API key the client reports
  ``available=False`` and callers fall back to their deterministic path.  That is why
  the whole pipeline runs, and produces honest output, offline.
* **Cache first.**  Prompts are hashed; identical products (very common in a
  catalogue of colour/length variants) cost one call, not N.
* **Repair, then retry.**  A malformed JSON response is repaired locally before a
  retry is spent, because truncated-brace failures are the common case.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.config import Settings


@dataclass
class LlmUsage:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0

    @property
    def cache_hit_rate(self) -> float:
        total = self.calls + self.cache_hits
        return self.cache_hits / total if total else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "calls": self.calls, "cache_hits": self.cache_hits,
            "failures": self.failures, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "seconds": round(self.seconds, 2),
        }


@dataclass
class LlmResponse:
    ok: bool
    data: Optional[Dict[str, Any]] = None
    error: str = ""
    cached: bool = False
    raw: str = ""


def repair_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Recover a JSON object from a noisy model response.

    Handles the three failure modes seen in practice: markdown fences, prose before
    or after the object, and truncation part-way through.
    """
    if not text:
        return None
    s = text.strip()

    fence = re.search(r"```(?:json)?\s*(.+?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()

    start = s.find("{")
    if start == -1:
        return None
    s = s[start:]

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Trim to the last balanced brace.
    depth, end, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end > 0:
        try:
            return json.loads(s[:end])
        except json.JSONDecodeError:
            pass

    # Truncated mid-object: close the open braces and retry.
    if depth > 0:
        candidate = s.rstrip().rstrip(",") + "}" * depth
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None


class PromptCache:
    """Content-addressed on-disk cache keyed by (model, prompt, schema)."""

    def __init__(self, directory: Path, enabled: bool = True):
        self.dir = Path(directory)
        self.enabled = enabled
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(model: str, system: str, prompt: str, schema: Optional[Dict]) -> str:
        blob = json.dumps({"m": model, "s": system, "p": prompt, "j": schema},
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        path = self.dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, value: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            (self.dir / f"{key}.json").write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class LlmClient:
    """Provider-agnostic structured-JSON client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.usage = LlmUsage()
        self.cache = PromptCache(settings.cache_dir / "llm", settings.cache_enabled)
        self._provider, self._handle = self._init_provider()

    # -- provider wiring ---------------------------------------------------
    def _init_provider(self):
        if not self.settings.llm_enabled or not self.settings.llm_api_key:
            return "offline", None
        want = (self.settings.llm_provider or "auto").lower()

        if want in ("auto", "anthropic"):
            try:
                import anthropic
                return "anthropic", anthropic.Anthropic(api_key=self.settings.llm_api_key)
            except Exception:
                if want == "anthropic":
                    return "offline", None
        if want in ("auto", "openai"):
            try:
                from openai import OpenAI
                return "openai", OpenAI(api_key=self.settings.llm_api_key)
            except Exception:
                pass
        return "offline", None

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def available(self) -> bool:
        return self._provider != "offline" and self._handle is not None

    # -- main entry point --------------------------------------------------
    def complete_json(self, system: str, prompt: str,
                      schema: Optional[Dict[str, Any]] = None,
                      max_tokens: Optional[int] = None) -> LlmResponse:
        """
        Request a JSON object.  Returns ``ok=False`` (never raises) so a single bad
        product can never take down a 1,000-row run.
        """
        key = PromptCache.key(self.settings.llm_model, system, prompt, schema)
        cached = self.cache.get(key)
        if cached is not None:
            self.usage.cache_hits += 1
            return LlmResponse(True, cached, cached=True)

        if not self.available:
            return LlmResponse(False, None,
                               error="LLM unavailable (no API key or SDK); "
                                     "deterministic fallback in use")

        instruction = (f"{prompt}\n\nRespond with a single JSON object only. "
                       f"No prose, no markdown fences.")
        if schema:
            instruction += f"\n\nIt must conform to this JSON schema:\n{json.dumps(schema)}"

        last_error = ""
        for attempt in range(self.settings.llm_max_retries):
            started = time.time()
            try:
                raw = self._invoke(system, instruction, max_tokens)
                self.usage.calls += 1
                self.usage.seconds += time.time() - started
                data = repair_json(raw)
                if data is not None:
                    self.cache.put(key, data)
                    return LlmResponse(True, data, raw=raw)
                last_error = "response was not parseable JSON"
            except Exception as exc:                    # network, rate limit, etc.
                self.usage.seconds += time.time() - started
                last_error = f"{type(exc).__name__}: {exc}"
                if "rate" in last_error.lower() or "429" in last_error:
                    time.sleep(min(8.0, 1.5 * (2 ** attempt)))
                    continue
            time.sleep(min(4.0, 0.5 * (2 ** attempt)))

        self.usage.failures += 1
        return LlmResponse(False, None, error=last_error)

    def _invoke(self, system: str, prompt: str, max_tokens: Optional[int]) -> str:
        tokens = max_tokens or self.settings.llm_max_tokens
        if self._provider == "anthropic":
            msg = self._handle.messages.create(
                model=self.settings.llm_model,
                max_tokens=tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            self.usage.input_tokens += getattr(msg.usage, "input_tokens", 0) or 0
            self.usage.output_tokens += getattr(msg.usage, "output_tokens", 0) or 0
            return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

        if self._provider == "openai":
            resp = self._handle.chat.completions.create(
                model=self.settings.llm_model,
                max_tokens=tokens,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
            )
            usage = getattr(resp, "usage", None)
            if usage:
                self.usage.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.usage.output_tokens += getattr(usage, "completion_tokens", 0) or 0
            return resp.choices[0].message.content or ""

        raise RuntimeError("no LLM provider configured")

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self._provider,
            "available": self.available,
            "model": self.settings.llm_model,
            "usage": self.usage.as_dict(),
        }
