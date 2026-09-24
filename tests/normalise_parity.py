"""Generate tests/fixtures/normalise_parity.json — the cases both normalisers must agree on.

    uv run python tests/normalise_parity.py

The Python side is the reference. `tests/test_normalise_leakage.py` asserts the fixture is
current; `src/recorder/test/normalise.test.js` replays it against `src/recorder/src/normalise.js`.
"""

from __future__ import annotations

import json
from pathlib import Path

from taskmining.normalise import Vocabulary, classify, key_name, normalise, row_name

FIXTURE = Path(__file__).parent / "fixtures" / "normalise_parity.json"

SCREENS = [
    ["Amount", "Vendor", "Save", "Bill date", "Total 1,250.00"],
    ["Amount", "Vendor", "Save", "Due date", "Total 98.10", "ACME Corp"],
    ["Customers", "Search", "ACME Corp", "New customer"],
]
TEXTS = [
    "",
    "Amount",
    "Save",
    "Total 1,250.00",
    "INV-1042 ACME.pdf",
    "Bills - ACME Corp",
    "john.doe@example.com",
    "DE89 3704 0044 0532 0132 00",
    "4111 1111 1111 1111",
    "2026-09-24T10:15",
    "24/09/2026",
    "Sep 24, 2026",
    "+44 20 7946 0958",
    "$1,250.00",
    "1250 EUR",
    "Order #A1042 shipped",
    "Vendor: Müller & Söhne GmbH",
    "Zahlung 12 Rechnungen",
    "Save and new",
    "  Multiple   spaces\there ",
    "row 3",
    "customer's name",
    "日本語のテキスト 123",
    "Cmd+S",
    "Enter",
]
KEYS = ["Cmd+S", "ctrl + shift + p", "Enter", "F5", "a", "Save", "cmd+", "shift+customer", ""]


def build() -> dict:
    vocab = Vocabulary.build(SCREENS, extra=["Bill number"])
    return {
        "screens": SCREENS,
        "extra": ["Bill number"],
        "vocabulary": vocab.to_json(),
        "cases": [{"text": t, "classify": classify(t), "normalise": normalise(t), "normalise_vocab": normalise(t, vocab)} for t in TEXTS],
        "keys": [{"text": k, "key_name": key_name(k)} for k in KEYS],
        "rows": [
            {
                "headers": ["Vendor", "Amount", "Total 1,250.00", "Notes"],
                "position": 3,
                "row_name": row_name(["Vendor", "Amount", "Total 1,250.00", "Notes"], 3, vocab),
            },
            {"headers": [], "position": 1, "row_name": row_name([], 1, vocab)},
        ],
    }


if __name__ == "__main__":
    FIXTURE.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
