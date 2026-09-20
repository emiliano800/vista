"""Tier 0: prove the configured model provider works before testing any agent.

    VISTA_OPENAI_API_KEY=... [VISTA_OPENAI_BASE_URL=...] [VISTA_OPENAI_MODEL=...] uv run python scripts/llm_smoke.py

Checks: plain answer, usage counts present, JSON mode parses, prices are known
for the model. Exit code 1 on any failure. Spends a few hundred tokens."""

import json
import sys

from vista.agents.llm import Prompt, live_chat
from vista.agents.runtime import DEFAULT_PRICING, cost_usd, pricing
from vista.config import settings


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
    return ok


def main() -> int:
    if not settings.openai_api_key:
        print("VISTA_OPENAI_API_KEY is not set; nothing to test (agents will use the stub).")
        return 1
    print(f"provider={settings.openai_base_url or 'api.openai.com'} model={settings.openai_model}")
    results = []

    plain = live_chat(Prompt(system="Answer in one word.", user="What colour is the sky on a clear day?", max_tokens=10, json_mode=False))
    results.append(check("plain completion", bool(plain.text.strip()), plain.text.strip()[:40]))
    results.append(
        check("usage reported", plain.input_tokens > 0 and plain.output_tokens > 0, f"{plain.input_tokens}+{plain.output_tokens} tokens")
    )
    results.append(check("model echoed", bool(plain.model), plain.model))

    js = live_chat(
        Prompt(
            system='Respond with JSON only: {"facts": [{"subject": str, "value": str}]}',
            user="Give one fact about bearings.",
            max_tokens=120,
        )
    )
    try:
        parsed = json.loads(js.text)
        results.append(check("json mode parses", isinstance(parsed.get("facts"), list), js.text[:60]))
    except json.JSONDecodeError:
        results.append(check("json mode parses", False, js.text[:60]))

    known = pricing(plain.model) != DEFAULT_PRICING
    check("pricing known (warning only)", known, f"{plain.model} -> {'table' if known else 'DEFAULT_PRICING (add to MODEL_PRICING)'}")
    print(f"cost of this smoke test: ${cost_usd(plain) + cost_usd(js):.6f}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
