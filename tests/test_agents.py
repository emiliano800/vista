"""Tier 1: every agent phase on synthetic_data/ with a stubbed or canned model.
No DB, no network, no tokens. `make test-agents`."""

import json
import os
from types import SimpleNamespace

import pytest

from vista.agents import analyze, discover, execute, propose, synthetic
from vista.agents.eval import Prediction, score
from vista.agents.llm import STUB_MODEL, Cassette, ChatResult, Prompt, chat, strip_fences
from vista.agents.runtime import DEFAULT_PRICING, cost_usd, pricing, run_phase

RIDGEWAY_WB = "00_legacy_exports/RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx"


@pytest.fixture(scope="module")
def ridgeway() -> synthetic.Company:
    return synthetic.company("ridgeway")


@pytest.fixture(scope="module")
def ridgeway_invoices(ridgeway) -> synthetic.Table:
    return next(t for t in synthetic.tables_for(ridgeway, "11_billing_ar") if t.ref.endswith("customer_invoices.csv"))


@pytest.fixture(scope="module")
def ridgeway_items(ridgeway) -> synthetic.Table:
    return next(t for t in synthetic.tables_for(ridgeway, "00_legacy_exports") if t.ref.endswith("#items"))


# ---------- synthetic loader ----------


def test_manifest_lists_six_companies_in_two_sectors():
    cs = synthetic.companies()
    assert len(cs) == 6
    assert {c.sector for c in cs} == set(synthetic.SECTORS)
    assert {c.tier for c in cs} == {"high", "medium", "low"}
    assert synthetic.company("Castlebrook").tier == "low"


def test_csv_and_xlsx_tables_load(ridgeway, ridgeway_invoices, ridgeway_items):
    assert ridgeway_invoices.rows and ridgeway_invoices.columns
    assert ridgeway_items.ref == f"{RIDGEWAY_WB}#items"
    assert "MANUFACTURER_PART_" in ridgeway_items.columns  # truncated header, as planted
    assert "01_master_data" in synthetic.divisions(ridgeway)


def test_answer_key_is_only_read_by_eval():
    items = synthetic.answer_key()
    assert len(items) == 72
    assert any(i.is_false_positive_trap for i in items)
    # runtime prompts must never see it
    comp = synthetic.company("ridgeway")
    profile = discover.profile_table(synthetic.tables_for(comp, "11_billing_ar")[0])
    assert "answer_key" not in discover.prepare(comp, profile).user


# ---------- llm layer ----------


def test_live_chat_rejects_truncated_output(monkeypatch):
    from vista.agents.llm import live_chat
    from vista.config import settings

    def create(**kwargs):
        assert "max_tokens" not in kwargs
        assert kwargs["max_completion_tokens"] > 1200
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="length")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(type(settings), "openai_client", lambda self: client)
    with pytest.raises(RuntimeError, match="truncated agent output"):
        live_chat(Prompt(system="Return JSON", user="Inspect this table", max_tokens=1200), model="gpt-6-astra")


def test_stub_when_no_api_key():
    r = chat(Prompt(system="s", user="u"))
    assert r.model == STUB_MODEL and r.source == "stub" and r.input_tokens == 800


def test_cassette_replays_by_prompt_hash(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    p = Prompt(system="s", user="u")
    Cassette(path).put(p.key("m"), ChatResult(model="m", text='{"facts": []}', input_tokens=5, output_tokens=6))
    monkeypatch.setenv("VISTA_LLM_CASSETTE", str(path))
    r = chat(p, model="m")
    assert r.source == "cassette" and r.text == '{"facts": []}'
    assert chat(Prompt(system="s", user="other"), model="m").source == "stub"
    monkeypatch.delenv("VISTA_LLM_CASSETTE")


def test_strip_fences():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('{"a": 1}') == '{"a": 1}'


def test_pricing_and_cost():
    assert pricing("gpt-4o-mini-2024") != pricing("gpt-4o")
    assert pricing("unknown") == DEFAULT_PRICING
    assert cost_usd(ChatResult(model="gpt-4o", text="", input_tokens=1000, output_tokens=100)) > 0


# ---------- Discover ----------


def test_profile_finds_planted_low_tier_problems(ridgeway_invoices, ridgeway_items):
    inv = discover.profile_table(ridgeway_invoices)
    by_name = {c.name: c for c in inv.columns}
    assert "mixed_date_formats" in by_name["INVOICE_DATE"].flags
    assert "money_as_text" in by_name["AMOUNT"].flags
    assert any(f.startswith("duplicate_rows:") for f in inv.flags)
    assert inv.header_style == "upper"

    items = discover.profile_table(ridgeway_items)
    assert "truncated_header" in {f for c in items.columns for f in c.flags}


def test_profile_is_clean_for_high_tier():
    nf = synthetic.company("northfield")
    t = next(t for t in synthetic.tables_for(nf, "01_master_data") if t.ref.endswith("items.csv"))
    p = discover.profile_table(t)
    assert not [f for c in p.columns for f in c.flags if f != "empty"]
    assert p.header_style == "snake"


def test_discover_prompt_is_json_and_bounded(ridgeway, ridgeway_invoices):
    profile = discover.profile_table(ridgeway_invoices)
    prompt = discover.prepare(ridgeway, profile)
    body = json.loads(prompt.user)
    assert body["company"]["data_quality_tier"] == "low"
    assert len(body["table"]["sample"]) <= discover.SAMPLE_ROWS
    assert prompt.json_mode


def test_discover_parse_tolerates_fences_and_garbage():
    fact = {"subject": "AMOUNT", "predicate": "holds", "value": "money as text", "confidence": 0.9, "columns": ["AMOUNT"]}
    good = discover.parse(f"```json\n{json.dumps({'facts': [fact]})}\n```")
    assert good.facts[0].subject == "AMOUNT"
    assert discover.parse("not json").facts == []
    assert discover.parse('{"facts": [{"subject": "x", "confidence": 7}]}').facts == []


def test_discover_apply_merges_deterministic_and_model_facts(ridgeway_invoices):
    profile = discover.profile_table(ridgeway_invoices)
    out = discover.parse(
        '{"facts": [{"subject": "AMOUNT", "predicate": "holds", "value": "money", "confidence": 0.8, "columns": ["AMOUNT", "NOPE"]}]}'
    )
    rows = discover.apply(out, profile)
    assert all(r["source_ref"]["file"] == profile.ref for r in rows)
    model_row = next(r for r in rows if r["predicate"] == "holds")
    assert model_row["columns"] == ["AMOUNT"]  # unknown columns dropped
    assert {r["predicate"] for r in rows} >= {"mixed_date_formats", "money_as_text", "duplicate_rows"}


def test_discover_runs_end_to_end_with_stub(ridgeway, ridgeway_invoices):
    profile = discover.profile_table(ridgeway_invoices)
    run = run_phase(discover.prepare(ridgeway, profile), discover.parse, lambda o: discover.apply(o, profile))
    assert run.result.source == "stub" and run.rows and run.cost_usd > 0


# ---------- Propose ----------


def canned(text: str):
    return lambda prompt: ChatResult(model="canned", text=text, input_tokens=1, output_tokens=1, source="cassette")


def test_propose_requires_evidence_and_opens_for_review(ridgeway, ridgeway_invoices):
    facts = discover.apply(discover.DiscoverOutput(), discover.profile_table(ridgeway_invoices))
    text = json.dumps(
        {
            "proposals": [
                {
                    "kind": "column_mapping",
                    "title": "Parse AMOUNT as money",
                    "diff": {"AMOUNT": {"type": "money"}},
                    "evidence": [0],
                    "rationale": "r",
                },
                {"kind": "rule", "title": "no evidence", "diff": {}, "evidence": [], "rationale": ""},
                {"kind": "rule", "title": "bad evidence", "diff": {}, "evidence": [999], "rationale": ""},
            ]
        }
    )
    run = run_phase(propose.prepare(ridgeway, facts), propose.parse, lambda o: propose.apply(o, facts), llm=canned(text))
    assert [p["title"] for p in run.rows] == ["Parse AMOUNT as money"]
    p = run.rows[0]
    assert p["status"] == "open" and p["diff"] == {"AMOUNT": {"type": "money"}}
    assert p["evidence_refs"][0]["file"] == ridgeway_invoices.ref


def test_propose_rejects_unknown_kind():
    assert propose.parse('{"proposals": [{"kind": "delete_everything", "title": "x", "evidence": [0]}]}').proposals == []


# ---------- Execute ----------


def exec_output(*actions) -> execute.ExecuteOutput:
    return execute.parse(json.dumps({"actions": list(actions)}))


def test_execute_creates_findings_with_undo_when_scoped():
    ref = "11_billing_ar/customer_invoices.csv"
    out = exec_output({"tool": "create_finding", "args": {"kind": "receivables", "title": "Overdue", "detail": "d"}, "evidence": [ref]})
    rows = execute.apply(out, scopes={"findings:write"}, approved_proposal_ids=set(), known_refs={ref})
    assert rows[0]["status"] == "done" and rows[0]["undo"] == {"tool": "undo_create_finding", "args": rows[0]["args"]}
    assert rows[0]["evidence_refs"] == [ref]


def test_execute_denies_out_of_scope_tools():
    out = exec_output({"tool": "create_task", "args": {"title": "t"}, "evidence": ["x"]})
    with pytest.raises(execute.PermissionDenied):
        execute.apply(out, scopes={"findings:write"}, approved_proposal_ids=set(), known_refs={"x"})


def test_execute_routes_ambiguity_to_exceptions():
    ref = "03_procurement/suppliers.csv"
    out = exec_output(
        {"tool": "create_finding", "args": {"kind": "quality", "title": "no evidence"}, "evidence": ["made_up.csv"]},
        {"tool": "normalise_records", "args": {"proposal_id": "p9", "table": ref, "changes": []}, "evidence": [ref]},
        {"tool": "normalise_records", "args": {"proposal_id": "p1", "table": ref, "changes": []}, "evidence": [ref]},
        {"tool": "create_finding", "args": {"kind": "not_a_kind", "title": "k"}, "evidence": [ref]},
    )
    rows = execute.apply(out, scopes={"findings:write", "records:write"}, approved_proposal_ids={"p1"}, known_refs={ref})
    assert [r["tool"] for r in rows] == ["raise_exception", "raise_exception", "normalise_records", "create_finding"]
    assert rows[0]["status"] == "open" and rows[0]["undo"] is None
    assert "approved proposal" in rows[1]["args"]["reason"]
    assert rows[3]["args"]["kind"] == "data_quality"


def test_execute_raise_exception_never_needs_scope():
    out = exec_output({"tool": "raise_exception", "args": {"reason": "unsure"}, "evidence": ["r"]})
    rows = execute.apply(out, scopes=set(), approved_proposal_ids=set(), known_refs={"r"})
    assert rows[0]["status"] == "open"


def test_execute_prompt_trims_tables(ridgeway):
    tables = synthetic.tables_for(ridgeway, "11_billing_ar")
    body = json.loads(execute.prepare(ridgeway, "11_billing_ar", tables, []).user)
    assert all(len(t["rows"]) <= execute.MAX_TABLE_ROWS for t in body["tables"])
    assert body["division"] == "11_billing_ar"


# ---------- Analyze ----------


def test_analyze_keeps_only_cross_company_opportunities():
    text = json.dumps(
        {
            "opportunities": [
                {
                    "kind": "purchasing_price_gap",
                    "title": "SKF 6205-2RS1: Ridgeway pays 46% more",
                    "companies": ["Ridgeway", "Keystone"],
                    "shared_key": "SKF 6205-2RS1",
                    "evidence": ["Ridgeway:01_master_data/items.csv"],
                    "confidence": 0.9,
                },
                {
                    "kind": "purchasing_price_gap",
                    "title": "one company",
                    "companies": ["Ridgeway"],
                    "shared_key": "x",
                    "evidence": [],
                    "confidence": 0.5,
                },
                {
                    "kind": "software_overlap",
                    "title": "unknown co",
                    "companies": ["Ridgeway", "Acme"],
                    "shared_key": "Slack",
                    "evidence": [],
                    "confidence": 0.5,
                },
                {
                    "kind": "vendor_consolidation",
                    "title": "no key",
                    "companies": ["Ridgeway", "Northfield"],
                    "shared_key": " ",
                    "evidence": [],
                    "confidence": 0.5,
                },
            ],
            "rejected": ["generic 6205 vs SKF 6205-2RS1"],
        }
    )
    out = analyze.parse(text)
    rows = analyze.apply(out, known_shorts={"Ridgeway", "Keystone", "Northfield"})
    assert [r["title"] for r in rows] == ["SKF 6205-2RS1: Ridgeway pays 46% more"]
    assert rows[0]["companies"] == ["Keystone", "Ridgeway"] and rows[0]["status"] == "open"


def test_analyze_prompt_groups_by_company():
    cs = [c for c in synthetic.companies() if c.sector == "industrial_goods"]
    by = {c: [t for ds in synthetic.datasets(c) if ds.file == "items.csv" for t in synthetic.read_tables(ds)] for c in cs}
    body = json.loads(analyze.prepare("industrial_goods", by).user)
    assert {c["short"] for c in body["companies"]} == {"Northfield", "Keystone", "Ridgeway"}
    assert all(len(t["rows"]) <= analyze.MAX_ROWS_PER_TABLE for c in body["companies"] for t in c["tables"])


def test_analyze_parse_is_item_tolerant_and_apply_normalises_evidence():
    ref = "14_finance_gl/software_subscriptions.csv"
    text = json.dumps(
        {
            "opportunities": [
                {
                    "kind": "policy_overlap",
                    "title": "bad kind",
                    "companies": ["Meridian", "Harborline"],
                    "shared_key": "x",
                    "confidence": 1,
                },
                {
                    "kind": "software_overlap",
                    "title": "Zoom",
                    "companies": ["Northfield", "Keystone"],
                    "shared_key": "Zoom Workplace",
                    "evidence": [f"Northfield:{ref}", "Keystone has Zoom too"],
                    "confidence": 0.8,
                },
            ],
            "rejected": ["Salesforce vs Slack", 3],
        }
    )
    out = analyze.parse(text)
    assert [o.title for o in out.opportunities] == ["Zoom"] and out.rejected == ["Salesforce vs Slack", "3"]
    rows = analyze.apply(out, known_shorts={"Northfield", "Keystone"}, known_refs={ref})
    assert rows[0]["evidence"] == [ref] and rows[0]["notes"] == ["Keystone has Zoom too"]
    # free-text evidence only: fall back to every table the analyst was shown
    out2 = analyze.parse(text.replace(f"Northfield:{ref}", "see above"))
    assert analyze.apply(out2, {"Northfield", "Keystone"}, known_refs={ref})[0]["evidence"] == [ref]


def test_analyze_prepare_per_kind_selects_tables_and_compacts_rows():
    cs = [c for c in synthetic.companies() if c.sector == "industrial_goods"]
    by = {c: synthetic.tables_for(c) for c in cs}
    body = json.loads(analyze.prepare("industrial_goods", by, "purchasing_price_gap").user)
    names = {synthetic.Table(ref=t["ref"], columns=[]).name for c in body["companies"] for t in c["tables"]}
    assert names == {"items", "quickbooks_item_list_export"}  # csv for two companies, legacy xlsx sheet for Ridgeway
    assert body["look_for"] == "purchasing_price_gap"
    for c in body["companies"]:
        for t in c["tables"]:
            assert len(t["rows"]) <= analyze.MAX_ROWS_PER_TABLE and set(t["columns"]) <= set(t["rows"][0])
    assert synthetic.Table(ref="00_legacy_exports/X.xlsx#Items", columns=[]).name == "items"


def test_analyze_compact_drops_duplicate_projections():
    t = synthetic.Table(ref="a/items.csv", columns=["item_id", "manufacturer_part_number", "unit_cost", "noise_col"])
    t.rows = [{"item_id": "1", "manufacturer_part_number": "6205", "unit_cost": 4, "noise_col": i} for i in range(5)]
    c = analyze.compact(t)
    assert c["columns"] == ["item_id", "manufacturer_part_number", "unit_cost"] and len(c["rows"]) == 1 and c["total_rows"] == 5


def _industrial():
    cs = [c for c in synthetic.companies() if c.sector == "industrial_goods"]
    return {c: synthetic.tables_for(c) for c in cs}


def test_analyze_screen_finds_planted_shared_keys_and_flags_traps():
    by = _industrial()
    price = {
        c["shared_key"]: c
        for c in analyze.screen("purchasing_price_gap", {c: analyze.tables_for_kind("purchasing_price_gap", t) for c, t in by.items()})
    }
    assert {"SKF 6205-2RS1", "Gates B62", "Timken LM11949/LM11910"} <= set(price)
    assert price["SKF 6205-2RS1"]["spread_pct"] > 0 and len(price["SKF 6205-2RS1"]["companies"]) == 3
    assert price["Uline S-4123"]["spread_pct"] == 0 and "no gap" in price["Uline S-4123"]["note"]  # equal-price trap
    assert not any(k.endswith(" 6205") for k in price)  # generic 6205 never joins SKF 6205-2RS1

    vendors = {
        c["shared_key"]: c
        for c in analyze.screen("vendor_consolidation", {c: analyze.tables_for_kind("vendor_consolidation", t) for c, t in by.items()})
    }
    assert {"grainger", "cintas", "ups", "fedex", "staples", "sunbelt"} <= set(vendors)
    assert len(vendors["grainger"]["companies"]) == 3 and "W.W. Grainger, Inc." in vendors["grainger"]["names_by_company"]["Northfield"]
    assert "iron" not in vendors  # AP-file key folded into the vendor master's shared_key
    assert "alro" not in vendors  # direct-material suppliers are out of scope
    assert (
        "iron_mountain" in vendors
        and "Iron Mountain Information Management, LLC" in vendors["iron_mountain"]["names_by_company"]["Keystone"]
    )

    sw = {
        c["shared_key"]: c
        for c in analyze.screen("software_overlap", {c: analyze.tables_for_kind("software_overlap", t) for c, t in by.items()})
    }
    assert {"crm", "erp", "cad", "payroll / hris", "file storage"} <= set(sw)
    assert sw["shipping / parcel"]["products_by_company"]["Ridgeway"] == ["FedEx Ship Manager", "UPS WorldShip"]

    (freight,) = analyze.screen("freight_rate_gap", {c: analyze.tables_for_kind("freight_rate_gap", t) for c, t in by.items()})
    assert set(freight["cost_per_lb"]) == {"Keystone", "Northfield", "Ridgeway"} and freight["spread_pct"] > 0

    cust = {c["shared_key"]: c for c in analyze.screen("cross_sell", {c: analyze.tables_for_kind("cross_sell", t) for c, t in by.items()})}
    assert len(cust["cardinal foods"]["companies"]) == 3
    assert "keystone chemical" not in cust and not any(k.startswith("keystone") for k in cust)  # look-alike trap dropped
    assert "fox river" not in cust  # 'Fox River Paper' and 'Fox River Climate' are different customers
    assert analyze.screen("carrier_consolidation", by) == [] and analyze.screen("nope", by) == []


def test_analyze_vendor_key_normalises_legal_forms_and_aliases():
    assert analyze.vendor_key("W.W. Grainger, Inc.") == analyze.vendor_key("GRAINGER") == "grainger"
    assert analyze.vendor_key("United Parcel Service") == analyze.vendor_key("UPS Freight / UPS") == "ups"
    assert analyze.vendor_key("Staples Business Advantage") == "staples"
    assert analyze.vendor_key("Apex Fastener Corp") == analyze.vendor_key("Apex Fastening Systems LLC")  # same key: the model decides


def test_analyze_customer_key_strips_sites_and_legal_forms():
    assert (
        analyze.customer_key("Cardinal Foods Group - Plant 12 - Hazleton, PA")
        == analyze.customer_key("Cardinal Foods Corp.")
        == analyze.customer_key("Cardinal Foods Plant 21")
        == "cardinal foods"
    )
    assert analyze.customer_key("Fox River Paper Co.") != analyze.customer_key("Fox River Climate Inc.")


def test_analyze_parse_coerces_kind_for_per_kind_calls():
    text = json.dumps(
        {
            "opportunities": [
                {
                    "kind": "vendor consolidation",
                    "title": "SKF 6205-2RS1",
                    "companies": ["Keystone", "Ridgeway"],
                    "shared_key": "SKF 6205-2RS1",
                    "evidence": [],
                    "detail": "",
                    "confidence": 0.8,
                }
            ]
        }
    )
    assert analyze.parse(text).opportunities[0].kind == "vendor_consolidation"  # label normalised
    assert analyze.parse(text, "purchasing_price_gap").opportunities[0].kind == "purchasing_price_gap"  # per-kind call wins


def test_analyze_prompt_carries_candidates_per_kind():
    by = _industrial()
    body = json.loads(analyze.prepare("industrial_goods", by, "software_overlap").user)
    assert body["candidates"] and all(len(c["companies"]) >= 2 for c in body["candidates"])
    assert all("software_subscriptions" in ref for c in body["candidates"] for ref in c["evidence"])  # screened on the kind's tables only
    xsell = json.loads(analyze.prepare("industrial_goods", by, "cross_sell").user)["candidates"]
    assert "accounts payable" not in {c["shared_key"] for c in xsell}
    assert len(body["candidates"]) <= analyze.MAX_CANDIDATES
    assert json.loads(analyze.prepare("industrial_goods", by).user)["candidates"] == []
    assert "candidates" in analyze.SYSTEM and "rejected" in analyze.SYSTEM


# ---------- Eval scorer (tier 3 logic, deterministic) ----------


def test_scorer_matches_items_and_penalises_traps():
    items = synthetic.answer_key()
    ind = {"Northfield", "Keystone", "Ridgeway"}
    good = Prediction.from_opportunity(
        {
            "kind": "purchasing_price_gap",
            "title": "SKF 6205-2RS1 gap",
            "companies": ["Ridgeway", "Keystone"],
            "shared_key": "SKF 6205-2RS1",
            "evidence": ["Ridgeway:01_master_data/items.csv"],
        }
    )
    trap = Prediction.from_opportunity(
        {
            "kind": "purchasing_price_gap",
            "title": "Uline S-4123 price gap",
            "companies": ["Ridgeway", "Keystone"],
            "shared_key": "Uline S-4123",
            "evidence": ["Ridgeway:01_master_data/items.csv"],
        }
    )
    s = score([good, trap], items, companies=ind, kinds={"purchasing_price_gap"})
    assert s.tp == 1 and s.trap_hits == 1 and s.fp == 1
    assert ("PORT-PRICE-6205-2RS1", "SKF 6205-2RS1 gap") in s.matched
    assert "PORT-PRICE-S-4123" not in s.missed  # traps never count as misses
    assert 0 < s.recall < 1


def test_scorer_ambiguous_prediction_is_a_false_positive():
    items = synthetic.answer_key()
    vague = Prediction.from_opportunity(
        {
            "kind": "purchasing_price_gap",
            "title": "some gap",
            "companies": ["Ridgeway", "Keystone"],
            "shared_key": "zzz",
            "evidence": ["Ridgeway:01_master_data/items.csv"],
        }
    )
    s = score([vague], items, companies={"Ridgeway", "Keystone"}, kinds={"purchasing_price_gap"})
    assert s.tp == 0 and s.fp == 1


def test_scorer_finding_matches_xlsx_sheet_evidence():
    items = synthetic.answer_key()
    pred = Prediction.from_finding(
        "Ridgeway",
        {"kind": "data_quality", "title": "SO/PO columns swapped", "source_ref": {"file": f"{RIDGEWAY_WB}#sales_orders", "columns": []}},
    )
    s = score([pred], items, companies={"Ridgeway"}, kinds={"data_quality"})
    assert ("IND-DQ-01", "SO/PO columns swapped") in s.matched


# ---------- Tier 0 (live, opt-in) ----------


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("VISTA_OPENAI_API_KEY"), reason="needs VISTA_OPENAI_API_KEY")
def test_live_provider_smoke(monkeypatch):
    from vista.agents.llm import live_chat
    from vista.config import settings

    monkeypatch.setattr(settings, "openai_api_key", os.environ["VISTA_OPENAI_API_KEY"])
    r = live_chat(Prompt(system='Respond with JSON only: {"ok": true}', user="ping", max_tokens=20))
    assert json.loads(r.text)["ok"] is True
    assert r.input_tokens > 0 and r.output_tokens > 0 and r.model


def test_scorer_tie_with_trap_goes_to_real_item():
    items = synthetic.answer_key()
    pred = Prediction.from_opportunity(
        {
            "kind": "software_overlap",
            "title": "Dropbox Business",
            "companies": ["Northfield", "Keystone"],
            "shared_key": "File Storage",
            "detail": "Both companies are using Dropbox Business for file storage.",
            "evidence": ["14_finance_gl/software_subscriptions.csv"],
        }
    )
    s = score([pred], items, companies={"Northfield", "Keystone"}, kinds={"software_overlap"})
    assert s.trap_hits == 0 and s.tp == 1 and ("PORT-SW-FILE", "Dropbox Business") in s.matched


def test_scorer_title_match_beats_trap_that_only_shares_detail_words():
    """A correct 'CRM: HubSpot vs Salesforce' must not land on the trap whose title mentions Salesforce;
    an unrelated cross-sell look-alike naming Keystone in its detail must not count as the Keystone trap."""
    items = synthetic.answer_key()
    crm = Prediction.from_opportunity(
        {
            "kind": "software_overlap",
            "title": "CRM Systems",
            "companies": ["Northfield", "Keystone"],
            "shared_key": "crm",
            "detail": "Keystone has HubSpot Sales Hub, Northfield uses Salesforce Sales Cloud.",
            "evidence": ["14_finance_gl/software_subscriptions.csv"],
        }
    )
    s = score([crm], items, companies={"Northfield", "Keystone"}, kinds={"software_overlap"})
    assert s.trap_hits == 0 and ("PORT-SW-CRM", "CRM Systems") in s.matched
    xsell = Prediction.from_opportunity(
        {
            "kind": "cross_sell",
            "title": "Cross-sell opportunity for Allegheny Paper",
            "companies": ["Northfield", "Keystone"],
            "shared_key": "allegheny paper",
            "detail": "Allegheny Paper Inc. buys from Keystone and Allegheny Paper LP from Northfield.",
            "evidence": ["01_master_data/customers.csv"],
        }
    )
    s = score([xsell], items, companies={"Northfield", "Keystone"}, kinds={"cross_sell"})
    assert s.trap_hits == 0 and s.fp == 1


def test_scorer_description_variant_resolves_vendor_alias():
    items = synthetic.answer_key()
    ups = Prediction.from_opportunity(
        {
            "kind": "vendor_consolidation",
            "title": "Consolidation Opportunity for UPS",
            "companies": ["Northfield", "Keystone", "Ridgeway"],
            "shared_key": "ups",
            "detail": "UPS appears in every vendor master.",
            "evidence": ["14_finance_gl/corporate_vendors.csv"],
        }
    )
    s = score([ups], items, companies={"Northfield", "Keystone", "Ridgeway"}, kinds={"vendor_consolidation"})
    assert ("PORT-VEND-UPS", "Consolidation Opportunity for UPS") in s.matched


# ---------- Summary provenance (no DB) ----------


def test_summary_provenance_tags_observed_vs_hypothesis():
    from vista.jobs.handlers import _provenance

    assert _provenance({"source": "role-hypothesis", "confidence": "low"}) == "hypothesis"
    assert _provenance(None) == "hypothesis"
    assert _provenance({"file": "14_finance_gl/ap.csv", "columns": ["x"]}) == "observed: 14_finance_gl/ap.csv"
    assert _provenance({"refs": ["Keystone:a.csv", "Ridgeway:b.csv"]}) == "observed: Keystone:a.csv, Ridgeway:b.csv"
