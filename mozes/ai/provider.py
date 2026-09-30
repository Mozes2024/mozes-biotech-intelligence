"""Optional AI layer. The system is fully functional without it (NullProvider).
AI is for interpretation (design comparison, inconsistencies, Hebrew summaries) — never for arithmetic."""
from __future__ import annotations

import json
import urllib.request

from ..config import ANTHROPIC_API_KEY, MOZES_AI_MODEL
from ..pit import OUTCOME_KEYS, LeakageError, all_keys, thaw


class NullProvider:
    name = "none"

    def complete(self, prompt: str):
        return None


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    def complete(self, prompt: str):
        body = json.dumps({"model": self.model, "max_tokens": 1500,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
            return "".join(b.get("text", "") for b in data.get("content", []))
        except Exception:
            return None  # graceful degradation: callers must handle None


def get_provider():
    if ANTHROPIC_API_KEY and MOZES_AI_MODEL:
        return AnthropicProvider(ANTHROPIC_API_KEY, MOZES_AI_MODEL)
    return NullProvider()


def build_evidence_prompt(snapshot) -> str:
    """Builds a prompt strictly from a point-in-time snapshot. Refuses outcome fields."""
    snap = thaw(snapshot)
    if all_keys(snap) & OUTCOME_KEYS:
        raise LeakageError("outcome fields present in prompt input")
    docs = "\n".join(f"- [{d.get('published')}] {d.get('title', '')}: {d.get('text', '')}" for d in snap.get("documents", []))
    return (
        f"You are assessing a biotech catalyst strictly as of {snap['as_of']}.\n"
        "Use ONLY the information below. Do not use knowledge of anything after this date. "
        "If information is missing, say so instead of inferring.\n\n"
        f"Program: {snap.get('program')} | Indication: {snap.get('indication')} | Type: {snap.get('type')}\n"
        f"Structured features: {json.dumps(snap.get('features', {}), ensure_ascii=False)}\n"
        f"Documents available as of {snap['as_of']}:\n{docs or '- (none ingested)'}\n\n"
        "Tasks: (1) compare prior-phase vs current design (population, dose, endpoint, duration, comparator); "
        "(2) list inconsistencies; (3) list safety signals. Return JSON with keys: "
        "design_changes, inconsistencies, safety_signals, missing_information."
    )
