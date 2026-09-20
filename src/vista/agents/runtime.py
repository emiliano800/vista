"""Generic phase runner: prompt -> chat -> parse -> apply, returning every stage."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from vista.agents.llm import ChatResult, Prompt, chat

# USD per token: (input, output). Extend as models are adopted.
MODEL_PRICING = {
    "gpt-4o-mini": (Decimal("0.00000015"), Decimal("0.00000060")),
    "gpt-4o": (Decimal("0.0000025"), Decimal("0.00001")),
    # OpenAI list price Sep 2026, standard tier <=272K input: $10/M in, $50/M out.
    "gpt-6-astra": (Decimal("0.00001"), Decimal("0.00005")),
}
DEFAULT_PRICING = (Decimal("0.000003"), Decimal("0.000015"))


def pricing(model: str) -> tuple[Decimal, Decimal]:
    for prefix, prices in sorted(MODEL_PRICING.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(prefix):
            return prices
    return DEFAULT_PRICING


def cost_usd(result: ChatResult) -> Decimal:
    in_price, out_price = pricing(result.model)
    return Decimal(result.input_tokens) * in_price + Decimal(result.output_tokens) * out_price


@dataclass
class PhaseRun:
    prompt: Prompt
    result: ChatResult
    output: Any
    rows: list[dict]

    @property
    def cost_usd(self) -> Decimal:
        return cost_usd(self.result)


def run_phase(
    prompt: Prompt, parse: Callable[[str], Any], apply: Callable[[Any], list[dict]], llm: Callable[[Prompt], ChatResult] = chat
) -> PhaseRun:
    result = llm(prompt)
    output = parse(result.text)
    return PhaseRun(prompt=prompt, result=result, output=output, rows=apply(output))
