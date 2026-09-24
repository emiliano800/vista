"""The one normaliser and the leakage test it makes possible (docs/computer_use_system.md §2, §4).
Fixtures carry seeded sensitive strings; every test asserts on what leaves, never on a model."""

import json
from pathlib import Path

from taskmining import leakage
from taskmining.normalise import TOKENS, Vocabulary, classify, normalise, row_name

PLAN = json.loads((Path(__file__).parent / "fixtures" / "plan_invoice.json").read_text())

# Three screens of one accounting app: labels recur, values do not.
SCREENS = [
    ["Vendor", "Amount", "Due date", "Save", "Bills", "ACME Corp", "1,250.00", "2026-03-01"],
    ["Vendor", "Amount", "Due date", "Save", "Bills", "Northfield Ltd", "980.50", "2026-03-14"],
    ["Vendor", "Amount", "Memo", "Save and close", "Bills", "Castlebrook", "12.00"],
]
VOCAB = Vocabulary.build(SCREENS)
SEEDED = {
    "email": "maria.lopez@acme-corp.com",
    "iban": "DE89 3704 0044 0532 0130 00",
    "card": "4111 1111 1111 1111",
    "phone": "+1 (415) 555-0142",
    "date": "2026-03-01",
    "amount": "$1,250.00",
    "id": "INV-1042",
    "number": "4521",
}


def test_every_seeded_value_becomes_its_class_token_and_nothing_else_survives():
    for cls, value in SEEDED.items():
        out = classify(value)
        assert out == f"{{{cls}}}", (cls, out)
        assert not any(ch.isdigit() for ch in out)


def test_class_tokens_are_exactly_the_ones_the_leakage_test_accepts():
    assert TOKENS == {"{email}", "{iban}", "{card}", "{phone}", "{date}", "{amount}", "{id}", "{number}", "{text}"}


def test_vocabulary_is_names_recurring_across_screens_never_data():
    assert {"vendor", "amount", "due", "date", "save", "bills"} <= VOCAB.words
    for data in ("acme", "corp", "northfield", "castlebrook", "memo", "close"):
        assert data not in VOCAB
    assert VOCAB.screens == 3 and VOCAB.min_screens == 2
    assert VOCAB.to_json()["method"] == "recurring-ax-names/1"


def test_normalise_keeps_vocabulary_words_and_folds_everything_else():
    assert normalise("Amount", VOCAB) == "amount"
    assert normalise("Vendor: ACME Corp", VOCAB) == "vendor {text}"
    assert normalise("Bill INV-1042 for $1,250.00 due 2026-03-01", VOCAB) == "{text} {id} {text} {amount} due {date}"
    assert normalise("Save and close", VOCAB) == "save {text}"
    # A run of data words collapses to one token: nothing about its length leaks.
    assert normalise("Maria Lopez Fernandez", VOCAB) == "{text}"
    # No vocabulary: everything is data.
    assert normalise("Amount 12", None) == "{text} {number}"


def test_rows_are_named_from_headers_and_position_never_cells():
    name = row_name(["Vendor", "Amount", "Due date"], 3, VOCAB)
    assert name == "row 3 of (vendor, amount, due date)"
    assert "ACME" not in name


def test_the_recorder_fixture_passes_the_leakage_test():
    ctx = leakage.RecordingContext.build(
        values=["INV-1042", "ACME", "1,250", "412", "233"],
        titles=["INV-1042.pdf — Preview", "QuickBooks — Bills"],
        vocab=Vocabulary.build([["Total", "Amount", "Enter", "Cmd+S"], ["Total", "Amount", "Enter", "Cmd+S"]]),
    )
    report = leakage.check(PLAN, ctx)
    assert report.ok, [f.to_json() for f in report.failures]
    assert report.strings_checked > 20
    assert report.known_limits  # limits travel with the result


def test_each_leak_kind_fails_with_a_path_and_no_value():
    ctx = leakage.RecordingContext.build(values=["INV-1042", "Maria Lopez"], titles=["QuickBooks — Bills"], vocab=VOCAB)
    payload = {
        "nodes": [{"activity": "accounting", "signature": ["field:Amount"]}],
        "edges": [
            {"control": "Amount", "slot": "input_1"},
            {"control": "Bill INV-1042"},
            {"control": "Vendor Castlebrook"},
            {"label": "{secret}"},
        ],
        "facts": {"title": "QuickBooks — Bills"},
    }
    report = leakage.check(payload, ctx)
    assert not report.ok
    reasons = {(f.path, f.reason) for f in report.failures}
    assert ("$.edges[1].control", "recorded_value") in reasons
    assert ("$.edges[2].control", "non_vocab_name") in reasons
    assert ("$.edges[3].label", "unknown_token") in reasons
    assert ("$.facts.title", "window_title") in reasons
    assert ("$.edges[0].control", "non_vocab_name") not in reasons
    dumped = json.dumps(report.to_json())
    for secret in ("INV-1042", "Castlebrook", "QuickBooks", "Maria"):
        assert secret not in dumped


def test_hashes_slot_tokens_and_roles_are_exempt_from_the_vocabulary_rule():
    ctx = leakage.RecordingContext.build(vocab=Vocabulary())
    payload = {"name": "a1b2c3d4e5f60718", "control": "field:Amount", "activity": "accounting"}
    assert leakage.check(payload, ctx).ok


def test_ocr_survivors_finds_values_still_legible_on_screen():
    ctx = leakage.RecordingContext.build(values=["INV-1042"], titles=["Bills — QuickBooks"])
    assert not leakage.ocr_survivors("Bill inv-1042 saved", ctx).ok
    assert leakage.ocr_survivors("Bill {id} saved", ctx).ok
