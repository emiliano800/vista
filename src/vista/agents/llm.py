"""The single place agents call a model.

Three modes, chosen without code changes:
  stub    — no VISTA_OPENAI_API_KEY: deterministic empty answer, fixed token counts (unit tests)
  cassette— VISTA_LLM_CASSETTE=<file>: replay recorded answers by prompt hash, record misses when live
  live    — API key set: OpenAI-compatible chat completion
"""

import hashlib
import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from vista.config import settings

STUB_MODEL = "stub-model-v0"


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    max_tokens: int = 800
    json_mode: bool = True

    def key(self, model: str) -> str:
        h = hashlib.sha256()
        for part in (model, self.system, self.user, str(self.max_tokens), str(self.json_mode)):
            h.update(part.encode())
            h.update(b"\0")
        return h.hexdigest()


@dataclass(frozen=True)
class ChatResult:
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    source: str = "live"  # live|stub|cassette


class Cassette:
    """JSON file of {prompt_key: ChatResult}. Replays hits; records misses when a key is configured."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> ChatResult | None:
        entry = self.entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        known = {f.name for f in fields(ChatResult)}
        return ChatResult(**{**{k: v for k, v in entry.items() if k in known}, "source": "cassette"})

    def put(self, key: str, result: ChatResult) -> None:
        self.entries[key] = {k: v for k, v in asdict(result).items() if k != "source"}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=1, sort_keys=True))


def _cassette() -> Cassette | None:
    path = os.environ.get("VISTA_LLM_CASSETTE")
    return Cassette(Path(path)) if path else None


def live_chat(prompt: Prompt, model: str | None = None) -> ChatResult:
    model = model or settings.openai_model
    kwargs: dict = {"temperature": 0}
    if prompt.json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = settings.openai_client().chat.completions.create(
        model=model,
        max_tokens=prompt.max_tokens,
        messages=[{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}],
        extra_body=settings.openai_extra_body(),
        **kwargs,
    )
    return ChatResult(
        model=resp.model,
        text=resp.choices[0].message.content or "",
        input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
        output_tokens=resp.usage.completion_tokens if resp.usage else 0,
    )


def chat(prompt: Prompt, model: str | None = None, cassette: Cassette | None = None) -> ChatResult:
    model = model or settings.openai_model
    cassette = cassette or _cassette()
    if cassette is not None:
        hit = cassette.get(prompt.key(model))
        if hit is not None:
            return hit
    if not settings.openai_api_key:
        return ChatResult(model=STUB_MODEL, text="", input_tokens=800, output_tokens=200, source="stub")
    result = live_chat(prompt, model)
    if cassette is not None:
        cassette.put(prompt.key(model), result)
    return result


def strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        cleaned = cleaned.removeprefix("json").strip()
    return cleaned
