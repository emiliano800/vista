"""Cross-company (portfolio-level) planted findings.

These are the things a PE operating partner would want the agent to surface
once all six companies are loaded: shared vendors under different names,
duplicate software spend, purchasing price gaps on identical items, freight
rate gaps and a cross-sell opportunity. False-positive traps are registered too.
"""
from __future__ import annotations

from common import AnswerKey
from industrial import SHARED_ITEMS
from profiles import SHARED_VENDORS, SOFTWARE


def register_portfolio_findings(key: AnswerKey, manifests: dict[str, list[dict]]) -> None:
    ind = {m["short"]: m for m in manifests.get("industrial_goods", [])}
    ins_names = [m["name"] for m in manifests.get("insurance_broking", [])]

    # --- shared corporate vendors -----------------------------------------
    for k, v in SHARED_VENDORS.items():
        cos = list(v["variants"].keys())
        if len(cos) < 2:
            continue
        key.add(f"PORT-VEND-{k.upper()}", "vendor_consolidation", cos,
                f"{v['canonical']} used by {len(cos)} portfolio companies under {len(set(v['variants'].values()))} different names",
                "Variants: " + "; ".join(f"{c}: '{n}'" for c, n in v["variants"].items()) + f". Category: {v['category']}.",
                ["*/11_finance_gl/vendors.csv", "*/11_finance_gl/ap_vendor_invoices.csv", "*/14_finance_gl/corporate_vendors.csv", "*/14_finance_gl/ap_vendor_invoices_indirect.csv"],
                "Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.")

    # --- software overlap ----------------------------------------------
    by_function: dict[str, set[str]] = {}
    for prod, (_vendor, func, _cost) in SOFTWARE.items():
        by_function.setdefault(func, set()).add(prod)
    overlaps = {
        "Agency Management System": ("Meridian: Applied Epic; Harborline: Vertafore AMS360; Castlebrook: HawkSoft CMS", ["Meridian", "Harborline", "Castlebrook"]),
        "ERP": ("Northfield: Epicor Kinetic; Keystone: NetSuite; Ridgeway: QuickBooks Desktop + Fishbowl", ["Northfield", "Keystone", "Ridgeway"]),
        "Quality Management System": ("Northfield: MasterControl ($12k flat); Keystone: uniPoint ($4.8k flat)", ["Northfield", "Keystone"]),
        "CAD": ("Northfield: SolidWorks; Keystone: Autodesk Inventor", ["Northfield", "Keystone"]),
        "e-Signature": ("Meridian: DocuSign; Harborline: Adobe Acrobat Sign", ["Meridian", "Harborline"]),
        "CRM": ("Meridian: Salesforce; Northfield: Salesforce; Keystone: HubSpot", ["Meridian", "Northfield", "Keystone"]),
        "VoIP Phone System": ("Meridian: RingCentral; Northfield: RingCentral; Keystone: 8x8; Harborline: 8x8", ["Meridian", "Northfield", "Keystone", "Harborline"]),
        "Payroll / HRIS": ("ADP (Meridian, Northfield), Paychex (Harborline), Gusto (Castlebrook, Ridgeway), Paycom (Keystone)", ["Meridian", "Northfield", "Harborline", "Castlebrook", "Ridgeway", "Keystone"]),
        "Productivity Suite / Email": ("Microsoft 365 at 4 companies, Google Workspace at Castlebrook and Ridgeway", ["Meridian", "Harborline", "Northfield", "Keystone", "Castlebrook", "Ridgeway"]),
        "File Storage": ("Box (Meridian, Northfield), Dropbox (Harborline, Keystone, Ridgeway)", ["Meridian", "Northfield", "Harborline", "Keystone", "Ridgeway"]),
    }
    for func, (desc, cos) in overlaps.items():
        key.add(f"PORT-SW-{func.split(' ')[0].upper().replace('/', '')}", "software_overlap", cos,
                f"Multiple products serving the same function: {func}", desc + " Same-function tools at different vendors; consolidation or a portfolio agreement is the play.",
                ["*/11_finance_gl/software_subscriptions.csv", "*/14_finance_gl/software_subscriptions.csv"],
                "Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.")
    key.add("PORT-SW-TRAP-01", "software_overlap", ["Northfield", "Keystone"], "Salesforce Sales Cloud vs Slack Business+ are both billed by Salesforce but are NOT overlapping tools",
            "A vendor-name match would group CRM and team messaging together; functions differ.", ["*/11_finance_gl/software_subscriptions.csv", "*/14_finance_gl/software_subscriptions.csv"], "Group by function, not vendor.", is_false_positive_trap=True)

    # --- purchasing price gaps on identical items -------------------------
    for mfr, pn, desc, uom, pack, buys in SHARED_ITEMS:
        if len(buys) < 2:
            continue
        prices = {c: cost for c, (_s, cost) in buys.items()}
        lo_c = min(prices, key=prices.get)
        hi_c = max(prices, key=prices.get)
        if prices[hi_c] == prices[lo_c]:
            key.add(f"PORT-PRICE-{pn.replace('/', '-')}", "purchasing_price_gap", list(buys), f"{mfr} {pn}: same price at every company (no gap)",
                    f"{desc}: all companies pay {prices[lo_c]} from {buys[lo_c][0]}. Correctly report as 'no opportunity'.",
                    ["*/01_master_data/items.csv", "*/03_procurement/purchase_order_lines.csv"], "No action.", is_false_positive_trap=True)
            continue
        gap = prices[hi_c] - prices[lo_c]
        vols = {c: (ind.get(c, {}).get("shared_item_prices", {}).get(pn, {}).get("annual_qty", 0)) for c in buys}
        savings = sum((prices[c] - prices[lo_c]) * vols.get(c, 0) for c in buys)
        key.add(f"PORT-PRICE-{pn.replace('/', '-')}", "purchasing_price_gap", list(buys), f"{mfr} {pn}: {hi_c} pays {gap / prices[lo_c]:.0%} more than {lo_c}",
                f"{desc} ({uom}, pack {pack}). " + "; ".join(f"{c}: {p:.2f} via {buys[c][0]} (SKU {ind.get(c, {}).get('shared_item_prices', {}).get(pn, {}).get('item_id', '?')}, ~{vols.get(c, 0)} {uom}/yr on PO lines)" for c, p in prices.items())
                + f". Moving everyone to {lo_c}'s price saves ~${savings:,.0f}/yr on PO-line volume.",
                ["*/01_master_data/items.csv", "*/03_procurement/purchase_order_lines.csv", "*/03_procurement/supplier_quotes.csv"],
                "Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.")

    # --- freight ----------------------------------------------------------
    rates = {c: m.get("parcel_freight_cost_per_lb") for c, m in ind.items()}
    key.add("PORT-FREIGHT-01", "freight_rate_gap", list(rates), "Parcel freight cost per lb varies ~30% across industrial companies",
            "Effective parcel cost/lb from shipments.csv: " + ", ".join(f"{c}: ${r}" for c, r in rates.items()) + ". Northfield has a negotiated UPS agreement; Ridgeway pays near list via WorldShip/FedEx Ship Manager.",
            ["*/06_warehouse_fulfillment/shipments.csv", "*/14_finance_gl/ap_vendor_invoices_indirect.csv"], "Extend Northfield's carrier agreement to the other two; audit accessorials in freight_invoices.")

    # --- cross-sell -------------------------------------------------------
    key.add("PORT-XSELL-01", "cross_sell", ["Keystone", "Ridgeway", "Northfield"], "Cardinal Foods Group buys from three portfolio companies as three unrelated plants",
            "Northfield sells to 'Cardinal Foods Group - Plant 07 - Rochelle, IL', Keystone to 'Cardinal Foods Group - Plant 12 - Hazleton, PA', Ridgeway to 'Cardinal Foods Plant 21'. Ridgeway's email notes the TN plant buys bearings from 'someone in PA' (Keystone) and wants to consolidate MRO suppliers; corporate is in Chicago.",
            ["*/01_master_data/customers.csv", "ridgeway_fasteners_and_supply/16_documents_emails/counter_sale_pricing_question.eml", "*/02_sales_quote_to_order/sales_order_lines.csv"],
            "Treat as one national account; propose a corporate agreement covering fasteners (Ridgeway), bearings/drives (Keystone) and machined components (Northfield).")
    key.add("PORT-XSELL-TRAP-01", "cross_sell", ["Keystone", "Ridgeway", "Northfield"], "Customers named 'Keystone ...' at Ridgeway/Northfield are not Keystone Bearing & Drive",
            "Customer-name word 'Keystone' (a common Pennsylvania brand word) appears in customer names at other companies; not an intercompany relationship.", ["*/01_master_data/customers.csv"], "Do not flag as intercompany sale.", is_false_positive_trap=True)

    # --- insurance cross-company ------------------------------------------
    key.add("PORT-INS-01", "carrier_consolidation", ["Meridian", "Harborline", "Castlebrook"], "Same carriers appointed at all three agencies at different commission rates",
            f"Travelers, Hartford, Liberty Mutual and CNA appear in carriers.csv for {', '.join(ins_names)}; contracted commission_pct differs by agency for the same line (see carriers.csv commission column). Combined premium volume could support a single higher-tier agreement / contingent bonus.",
            ["*/03_marketing_submissions/carriers.csv", "*/02_policies_exposures/policies.csv", "*/10_compliance_licensing/carrier_appointments.csv"],
            "Aggregate written premium by carrier across agencies; compare commission schedules; identify carriers where volume tiers would be reached combined.")
    key.add("PORT-INS-02", "cross_sell", ["Meridian", "Harborline", "Castlebrook", "Northfield", "Keystone", "Ridgeway"], "Industrial portfolio companies are prospects for the insurance agencies",
            "Northfield, Keystone and Ridgeway each carry WC/GL/property/auto (see 12_hr_ehs incidents, fleets in shipments 'Company Truck'); none of the three agencies write them today.",
            ["industrial_goods/*/00_company/company_profile.json", "insurance_broking/*/01_clients_crm/clients.csv"], "Intercompany placement is a real but sensitive opportunity; surface as a suggestion requiring human review (related-party).")
