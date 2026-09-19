"""Industrial-goods back office generator (manufacturer / distributor).

One call builds a complete environment for a single company: master data,
quote-to-order, procure-to-pay, inventory, warehouse, planning, manufacturing,
quality, maintenance, order-to-cash, returns, engineering/trade compliance,
finance, HR/EHS, documents and a workflow event log. Output quality is then
degraded according to the company's tier.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from common import (CITIES, TODAY, AnswerKey, Emitter, Rng, Row, email_for, eml, gl_chart, iso,
                    month_ends)
from profiles import SHARED_VENDORS, SOFTWARE, TRAP_VENDORS

# ---------------------------------------------------------------------------
# Portfolio-wide item catalogue. Every company buys a subset of these under its
# OWN internal SKU at its OWN negotiated price -> purchasing price-gap analysis
# has to match on manufacturer + manufacturer part number, not on SKU.
# ---------------------------------------------------------------------------
SHARED_ITEMS = [
    # mfr, mfr_pn, description, uom, pack, {company: (supplier, unit_cost)}
    ("SKF", "6205-2RS1", "Deep groove ball bearing 25x52x15, sealed", "EA", 1,
     {"Northfield": ("Motion Industries", 4.10), "Keystone": ("Applied Industrial Technologies", 3.55), "Ridgeway": ("Fastenal", 5.20)}),
    ("SKF", "6308-2Z", "Deep groove ball bearing 40x90x23, shielded", "EA", 1,
     {"Northfield": ("Motion Industries", 11.80), "Keystone": ("Applied Industrial Technologies", 10.25), "Ridgeway": ("Fastenal", 14.90)}),
    ("Timken", "LM11949/LM11910", "Tapered roller bearing set", "EA", 1,
     {"Northfield": ("Motion Industries", 8.95), "Keystone": ("Applied Industrial Technologies", 7.60)}),
    ("Gates", "B62", "Hi-Power II V-belt B62", "EA", 1,
     {"Northfield": ("Motion Industries", 14.20), "Keystone": ("Kaman Industrial Technologies", 12.70), "Ridgeway": ("Grainger Industrial Supply", 17.85)}),
    ("Dodge", "TXT315", "Torque-Arm II shaft-mount reducer 15:1", "EA", 1,
     {"Keystone": ("Applied Industrial Technologies", 1180.00), "Northfield": ("Motion Industries", 1265.00)}),
    ("Henkel Loctite", "243", "Threadlocker 243 medium strength blue, 50 mL", "EA", 1,
     {"Northfield": ("W.W. Grainger, Inc.", 21.40), "Keystone": ("GRAINGER", 22.10), "Ridgeway": ("Fastenal", 24.95)}),
    ("3M", "7447", "Scotch-Brite hand pad 6x9 maroon, 20/bx", "BX", 20,
     {"Northfield": ("MSC Industrial Supply", 28.50), "Ridgeway": ("Grainger Industrial Supply", 33.20)}),
    ("Kimberly-Clark", "G10", "KleenGuard G10 nitrile gloves, large, 100/bx", "BX", 100,
     {"Northfield": ("Cintas Corporation", 9.85), "Keystone": ("GRAINGER", 11.40), "Ridgeway": ("Grainger Industrial Supply", 12.95)}),
    ("Sigma Stretch Film", "SF18-80", "Machine stretch wrap 18in x 1500ft 80ga", "RL", 1,
     {"Northfield": ("Uline", 24.60), "Keystone": ("Uline", 24.60), "Ridgeway": ("Uline", 27.10)}),
    ("Uline", "S-4123", "Corrugated box 12x12x12, 25/bd", "BD", 25,
     {"Northfield": ("Uline", 31.00), "Keystone": ("Uline", 31.00), "Ridgeway": ("Uline", 31.00)}),
    ("3M", "SF401AF", "SecureFit 400 safety glasses clear anti-fog", "EA", 1,
     {"Northfield": ("Cintas Corporation", 3.15), "Keystone": ("GRAINGER", 4.05), "Ridgeway": ("Fastenal", 4.60)}),
    ("WD-40 Company", "49012", "WD-40 Multi-Use 1 gal", "GA", 1,
     {"Northfield": ("W.W. Grainger, Inc.", 27.80), "Ridgeway": ("Grainger Industrial Supply", 29.95)}),
    ("Brighton-Best", "HHCS-0500-13-200-G8", "Hex head cap screw 1/2-13 x 2 Gr 8 yellow zinc, 50/bx", "BX", 50,
     {"Northfield": ("Apex Fastener Corp", 38.40), "Ridgeway": ("Brighton-Best International", 29.10), "Keystone": ("Fastenal", 44.75)}),
    ("Brighton-Best", "HN-0500-13-G8", "Hex nut 1/2-13 Gr 8 yellow zinc, 100/bx", "BX", 100,
     {"Northfield": ("Apex Fastener Corp", 19.20), "Ridgeway": ("Brighton-Best International", 14.60)}),
    ("Ryerson", "A36-PL-0.500", "A36 hot-rolled steel plate 1/2in", "LB", 1,
     {"Northfield": ("Ryerson", 0.68)}),
    ("Ryerson", "1045-RB-2.000", "1045 cold-finished round bar 2.000in", "LB", 1,
     {"Northfield": ("Ryerson", 0.94)}),
]

INDUSTRIAL_SUPPLIERS = {
    # name: (category, city, state, payment_terms)
    "Motion Industries": ("Bearings & PT Distributor", "Birmingham", "AL", "Net 30"),
    "Applied Industrial Technologies": ("Bearings & PT Distributor", "Cleveland", "OH", "Net 30"),
    "Kaman Industrial Technologies": ("Bearings & PT Distributor", "Bloomfield", "CT", "Net 30"),
    "Fastenal": ("Fasteners / MRO", "Winona", "MN", "Net 30"),
    "MSC Industrial Supply": ("Cutting Tools / MRO", "Melville", "NY", "Net 30"),
    "McMaster-Carr": ("MRO Catalog", "Elmhurst", "IL", "Net 30"),
    "Uline": ("Packaging", "Pleasant Prairie", "WI", "Net 30"),
    "Ryerson": ("Metals Service Center", "Chicago", "IL", "Net 45"),
    "Alro Steel": ("Metals Service Center", "Jackson", "MI", "Net 30"),
    "Apex Fastener Corp": ("Fastener Manufacturer", "Elgin", "IL", "Net 30"),
    "Brighton-Best International": ("Fastener Master Distributor", "Long Beach", "CA", "Net 30"),
    "Baldor-Reliance (ABB)": ("Motors", "Fort Smith", "AR", "Net 45"),
    "Regal Rexnord": ("Gearing / Couplings", "Milwaukee", "WI", "Net 45"),
    "Parker Hannifin": ("Seals / Hydraulics", "Cleveland", "OH", "Net 45"),
    "Garlock Sealing Technologies": ("Seals / Gaskets", "Palmyra", "NY", "Net 30"),
    "Haas Automation": ("Machine Tools", "Oxnard", "CA", "Net 30"),
    "Sandvik Coromant": ("Cutting Tools", "Mebane", "NC", "Net 30"),
}

CUSTOMER_SEGMENTS = [
    ("Food & Beverage Processing", "311", "Foods"), ("Pulp & Paper", "322", "Paper"),
    ("Aggregates / Cement", "327", "Materials"), ("Steel Processing", "331", "Steel"),
    ("Automotive Tier 2", "336", "Automotive"), ("Packaging Machinery OEM", "333", "Packaging Systems"),
    ("Conveyor / Material Handling OEM", "333", "Conveyor"), ("Water / Wastewater Utility", "221", "Water Authority"),
    ("Poultry Processing", "311", "Poultry"), ("Lumber / Wood Products", "321", "Lumber"),
    ("Chemical Blending", "325", "Chemical"), ("Agricultural Equipment", "333", "Ag Equipment"),
    ("Plastics Extrusion", "326", "Plastics"), ("HVAC Equipment OEM", "333", "Climate"),
]
CUST_WORDS = ["Cardinal", "Lehigh Valley", "Ohio Valley", "Great Lakes", "Blue Ridge", "Tennessee Valley", "Fox River",
              "Susquehanna", "Cumberland", "Allegheny", "Prairie", "Pioneer", "Midwest", "Tri-County", "Riverbend",
              "Hickory", "Iron City", "Summit", "Keystone", "Northstar", "Rock River", "Appalachian", "Piedmont"]

# National multi-plant customer that appears at two portfolio companies under
# different plant names -> cross-sell opportunity.
NATIONAL_ACCOUNT = "Cardinal Foods Group"


def generate_industrial(profile: dict, root: str, key: AnswerKey) -> dict:
    rng = Rng(profile["seed"])
    tier = profile["tier"]
    short = profile["short"]
    em = Emitter(root, profile["slug"], tier, rng)
    hq_city, hq_state, hq_zip3 = profile["hq"]
    nearby = {"IL": {"IL", "WI", "IN", "IA"}, "PA": {"PA", "NJ", "OH", "NY"}, "TN": {"TN", "GA", "AL", "KY"}}[hq_state]
    region = [c for c in CITIES if c[1] in nearby] or [profile["hq"]]
    domain = profile["domain"]
    manufactures = short in ("Northfield", "Keystone")
    is_mfr = short == "Northfield"
    pre = {"Northfield": "NIC", "Keystone": "KBD", "Ridgeway": "RFS"}[short]

    # ---------------- employees ----------------
    roles = []
    if is_mfr:
        roles += [("President", "Executive", 1), ("Controller", "Finance", 1), ("AP/AR Specialist", "Finance", 2),
                  ("Plant Manager", "Operations", 1), ("Production Supervisor", "Operations", 3), ("CNC Machinist", "Operations", 28),
                  ("Assembler", "Operations", 18), ("Quality Manager", "Quality", 1), ("Quality Inspector", "Quality", 4),
                  ("Buyer", "Purchasing", 3), ("Purchasing Manager", "Purchasing", 1), ("Inside Sales Rep", "Sales", 5),
                  ("Outside Sales Rep", "Sales", 4), ("Customer Service Rep", "Sales", 3), ("Shipping/Receiving Clerk", "Warehouse", 6),
                  ("Warehouse Lead", "Warehouse", 2), ("Maintenance Technician", "Maintenance", 4), ("Manufacturing Engineer", "Engineering", 3),
                  ("Design Engineer", "Engineering", 3), ("Production Planner", "Operations", 2), ("HR Manager", "HR", 1),
                  ("EHS Coordinator", "HR", 1), ("IT Administrator", "IT", 1), ("Material Handler", "Warehouse", 8)]
    elif short == "Keystone":
        roles += [("President", "Executive", 1), ("Controller", "Finance", 1), ("Accounting Clerk", "Finance", 1),
                  ("Operations Manager", "Operations", 1), ("Assembly Technician", "Operations", 6), ("Buyer", "Purchasing", 2),
                  ("Inside Sales Rep", "Sales", 6), ("Outside Sales Rep", "Sales", 5), ("Customer Service Rep", "Sales", 2),
                  ("Warehouse Associate", "Warehouse", 10), ("Warehouse Manager", "Warehouse", 1), ("Delivery Driver", "Warehouse", 3),
                  ("Applications Engineer", "Engineering", 2), ("Branch Manager", "Sales", 2), ("HR/Office Manager", "HR", 1),
                  ("Quality Coordinator", "Quality", 1)]
    else:
        roles += [("Owner", "Executive", 1), ("Bookkeeper", "Finance", 1), ("Purchasing", "Purchasing", 1),
                  ("Inside Sales", "Sales", 3), ("Outside Sales", "Sales", 3), ("Counter Sales", "Sales", 2),
                  ("Warehouse", "Warehouse", 6), ("Warehouse Mgr", "Warehouse", 1), ("Driver", "Warehouse", 3), ("VMI Tech", "Sales", 3),
                  ("Office Admin", "HR", 1)]
    employees: list[Row] = []
    eid = 0
    for title, dept, n in roles:
        for _ in range(n):
            eid += 1
            name = rng.person()
            hire = rng.date_between(date(profile["founded"] + 3, 1, 1), date(2025, 12, 1))
            hourly = dept in ("Operations", "Warehouse", "Maintenance", "Quality") and "Manager" not in title and "Supervisor" not in title
            employees.append({
                "employee_id": f"E{eid:03d}", "full_name": name, "email": email_for(name, domain), "title": title,
                "department": dept, "hire_date": iso(hire), "employment_type": "Full-Time" if rng.chance(0.9) else "Part-Time",
                "pay_type": "Hourly" if hourly else "Salary",
                "pay_rate": rng.money(17, 34, 0.25) if hourly else rng.money(52000, 165000, 500),
                "shift": rng.choice(["1st", "1st", "1st", "2nd"]) if hourly else "1st",
                "manager_id": "E001" if eid > 1 else "", "location": hq_city,
                "status": "Active" if rng.chance(0.94) else "Terminated",
                "termination_date": "" if rng.chance(0.94) else iso(rng.date_between(date(2025, 6, 1), TODAY)),
            })
    employees = employees[: profile["employees"]]
    sales_reps = [e for e in employees if "Sales" in e["title"] and "Service" not in e["title"] and "Counter" not in e["title"]]
    buyers = [e for e in employees if e["department"] == "Purchasing"]
    csrs = [e for e in employees if "Customer Service" in e["title"] or "Inside" in e["title"]]
    whs = [e for e in employees if e["department"] == "Warehouse"]
    ops = [e for e in employees if e["department"] == "Operations" and e["pay_type"] == "Hourly"] or whs
    qa = [e for e in employees if e["department"] == "Quality"] or ops
    maint = [e for e in employees if e["department"] == "Maintenance"] or ops
    finance = [e for e in employees if e["department"] == "Finance"]

    # ---------------- customers ----------------
    customers: list[Row] = []
    contacts: list[Row] = []
    ship_tos: list[Row] = []
    used: set[str] = set()
    for i in range(1, profile["n_customers"] + 1):
        seg, naics3, noun = rng.choice(CUSTOMER_SEGMENTS)
        for _ in range(40):
            nm = f"{rng.choice(CUST_WORDS)} {noun} {rng.choice(['Inc.', 'LLC', 'Corp.', 'Co.', 'LP'])}"
            if nm not in used:
                used.add(nm)
                break
        addr = rng.address(region)
        cid = f"{pre}-CUST{i:04d}"
        terms = rng.choice(["Net 30", "Net 30", "Net 30", "Net 45", "Net 60", "2% 10 Net 30", "COD"])
        customers.append({
            "customer_id": cid, "customer_name": nm, "segment": seg, "naics_prefix": naics3,
            "bill_to_street": addr["street"], "bill_to_city": addr["city"], "bill_to_state": addr["state"], "bill_to_zip": addr["zip"],
            "payment_terms": terms, "credit_limit": rng.choice([25000, 50000, 75000, 100000, 150000, 250000]),
            "credit_hold": "N", "tax_exempt": "Y" if rng.chance(0.7) else "N",
            "resale_cert_on_file": "Y" if rng.chance(0.65) else "N",
            "sales_rep_id": rng.choice(sales_reps)["employee_id"], "price_level": rng.choice(["A", "B", "B", "C", "C", "D"]),
            "customer_since": iso(rng.date_between(date(profile["founded"] + 2, 1, 1), date(2025, 6, 1))),
            "preferred_carrier": rng.choice(["UPS Ground", "FedEx Ground", "Customer Pickup", "LTL - Estes", "LTL - XPO", "Company Truck"]),
            "status": "Active" if rng.chance(0.92) else "Inactive",
        })
        for _ in range(rng.int(1, 3)):
            p = rng.person()
            contacts.append({"contact_id": f"{pre}-CT{len(contacts) + 1:04d}", "customer_id": cid, "full_name": p,
                             "title": rng.choice(["Purchasing Manager", "Buyer", "Maintenance Manager", "Plant Engineer", "Storeroom Lead", "AP Clerk", "Reliability Engineer"]),
                             "email": email_for(p, nm.split(" ")[0].lower().replace("-", "") + "-" + noun.lower().replace(" ", "") + ".com"),
                             "phone": rng.phone(), "role": rng.choice(["Buyer", "Buyer", "Technical", "AP", "Decision Maker"]), "primary": "Y" if _ == 0 else "N"})
        for j in range(1 if rng.chance(0.7) else 2):
            sa = rng.address(region) if j else addr
            ship_tos.append({"ship_to_id": f"{cid}-S{j + 1}", "customer_id": cid, "name": f"{nm} - {'Plant' if j == 0 else 'Warehouse'} {rng.int(1, 12)}",
                             "street": sa["street"], "city": sa["city"], "state": sa["state"], "zip": sa["zip"],
                             "dock_hours": "07:00-15:30", "appointment_required": "Y" if rng.chance(0.3) else "N"})
    # national account planted for cross-sell
    plant = {"Northfield": "Plant 07 - Rochelle, IL", "Keystone": "Plant 12 - Hazleton, PA", "Ridgeway": "Plant 21 - Cleveland, TN"}[short]
    nat = dict(customers[3])
    nat.update({"customer_name": f"{NATIONAL_ACCOUNT} - {plant}" if short != "Ridgeway" else f"Cardinal Foods {plant.split(' - ')[0]}",
                "segment": "Food & Beverage Processing", "naics_prefix": "311", "payment_terms": "Net 60", "tax_exempt": "Y"})
    customers[3] = nat
    ship_tos = [s for s in ship_tos if s["customer_id"] != nat["customer_id"]] + [
        {"ship_to_id": f"{nat['customer_id']}-S1", "customer_id": nat["customer_id"], "name": nat["customer_name"],
         "street": nat["bill_to_street"], "city": nat["bill_to_city"], "state": nat["bill_to_state"], "zip": nat["bill_to_zip"],
         "dock_hours": "06:00-14:00", "appointment_required": "Y"}]
    if short == "Ridgeway":  # duplicate customer records
        dup = dict(customers[7])
        dup["customer_id"] = f"{pre}-CUST{len(customers) + 1:04d}"
        dup["customer_name"] = customers[7]["customer_name"].replace("Inc.", "INC").replace("LLC", "L.L.C.").upper()
        dup["payment_terms"] = "COD"
        customers.append(dup)
        ship_tos.append({**next(s for s in ship_tos if s["customer_id"] == customers[7]["customer_id"]), "ship_to_id": f"{dup['customer_id']}-S1", "customer_id": dup["customer_id"]})
        contacts.append({**next(x for x in contacts if x["customer_id"] == customers[7]["customer_id"]), "contact_id": f"{pre}-CT{len(contacts) + 1:04d}", "customer_id": dup["customer_id"]})
        key.add("IND-CRM-01", "data_quality", [short], "Duplicate customer record (same company, different casing/suffix, different terms)",
                f"{customers[7]['customer_id']} and {dup['customer_id']} are the same customer; open AR is split across both and terms disagree (Net 30 vs COD).",
                ["01_master_data/customers.csv", "11_billing_ar/customer_invoices.csv"],
                "Merge records, pick surviving terms, consolidate AR aging.")
    active_customers = [c for c in customers if c["status"] == "Active"]

    # ---------------- items ----------------
    items: list[Row] = []
    uoms = [{"uom": "EA", "description": "Each", "base": "EA", "factor": 1}, {"uom": "BX", "description": "Box", "base": "EA", "factor": "varies by item (see item pack_qty)"},
            {"uom": "BD", "description": "Bundle", "base": "EA", "factor": "varies by item"}, {"uom": "RL", "description": "Roll", "base": "RL", "factor": 1},
            {"uom": "GA", "description": "Gallon", "base": "GA", "factor": 1}, {"uom": "LB", "description": "Pound", "base": "LB", "factor": 1},
            {"uom": "FT", "description": "Foot", "base": "FT", "factor": 1}, {"uom": "C", "description": "Hundred", "base": "EA", "factor": 100},
            {"uom": "M", "description": "Thousand", "base": "EA", "factor": 1000}]
    classes = [{"class_code": "RAW", "description": "Raw material", "gl_inventory_account": "1200"},
               {"class_code": "COMP", "description": "Purchased component", "gl_inventory_account": "1200"},
               {"class_code": "FG", "description": "Finished good - manufactured", "gl_inventory_account": "1220"},
               {"class_code": "RESALE", "description": "Purchased for resale", "gl_inventory_account": "1230"},
               {"class_code": "MRO", "description": "Maintenance / consumables (expensed)", "gl_inventory_account": "6700"},
               {"class_code": "PKG", "description": "Packaging", "gl_inventory_account": "1200"}]
    shared_map: dict[str, Row] = {}
    for idx, (mfr, pn, desc, uom, pack, buys) in enumerate(SHARED_ITEMS):
        if short not in buys:
            continue
        sup, cost = buys[short]
        cls = "MRO" if mfr in ("Henkel Loctite", "3M", "Kimberly-Clark", "WD-40 Company") else ("PKG" if mfr in ("Sigma Stretch Film", "Uline") else ("RAW" if mfr == "Ryerson" else ("RESALE" if not is_mfr else "COMP")))
        sku = {"Northfield": f"P-{10000 + idx * 7}", "Keystone": f"{mfr[:3].upper()}-{pn.replace('/', '-')}", "Ridgeway": f"{pn.split('-')[0].split('/')[0]}-{rng.int(100, 999)}"}[short]
        if short == "Ridgeway" and pn == "6205-2RS1":
            sku = "BRG-6205"
        if short == "Northfield" and pn == "6205-2RS1":
            sku = "P-10000"
        row = {"item_id": sku, "description": desc, "item_class": cls, "manufacturer": mfr, "manufacturer_part_number": pn,
               "stock_uom": uom, "pack_qty": pack, "purchase_uom": uom, "primary_supplier": sup, "unit_cost": cost,
               "list_price": round(cost * rng.money(1.28, 1.65), 2), "abc_class": rng.choice(["A", "B", "B", "C"]),
               "reorder_point": rng.int(10, 120), "reorder_qty": rng.int(50, 400), "lead_time_days": rng.int(3, 21),
               "make_or_buy": "Buy", "weight_lb": round(rng.money(0.1, 12, 0.05), 2), "hts_code": "", "country_of_origin": rng.choice(["US", "US", "CN", "MX", "DE", "IN"]),
               "status": "Active", "item_revision": ""}
        items.append(row)
        shared_map[pn] = row
    if short == "Ridgeway":  # TRAP: same internal SKU code as Northfield's P-10000/BRG-6205 lookalike but a different generic import bearing
        items.append({"item_id": "BRG-6205-IMP", "description": "6205 2RS ball bearing (import, generic)", "item_class": "RESALE", "manufacturer": "Unbranded (import)",
                      "manufacturer_part_number": "6205-2RS", "stock_uom": "EA", "pack_qty": 1, "purchase_uom": "EA", "primary_supplier": "Brighton-Best International",
                      "unit_cost": 1.85, "list_price": 3.49, "abc_class": "B", "reorder_point": 50, "reorder_qty": 200, "lead_time_days": 21, "make_or_buy": "Buy",
                      "weight_lb": 0.28, "hts_code": "8482.10.5044", "country_of_origin": "CN", "status": "Active", "item_revision": ""})
        key.add("IND-PRICE-TRAP-01", "purchasing_price_gap", ["Ridgeway", "Northfield", "Keystone"],
                "Generic import 6205 bearing is NOT the same item as SKF 6205-2RS1", "Ridgeway's BRG-6205-IMP ($1.85, unbranded CN import) looks like the SKF 6205-2RS1 the others buy. "
                "A naive 'same size bearing' match would report a huge price gap; manufacturer and part number differ, so it is not a like-for-like comparison.",
                ["01_master_data/items.csv"], "Do not include in price-gap findings; at most flag as a possible spec substitution question.", is_false_positive_trap=True)
    # company-specific items
    n_own = {"Northfield": 120, "Keystone": 90, "Ridgeway": 140}[short]
    fam = {"Northfield": [("Shaft Coupling", "FG", "Make", 85, 420), ("Machined Housing", "FG", "Make", 140, 900), ("Gearbox Sub-Assembly", "FG", "Make", 600, 3200),
                          ("Coupling Hub - Raw Casting", "COMP", "Buy", 22, 90), ("Elastomer Insert", "COMP", "Buy", 4, 30), ("Retaining Ring", "COMP", "Buy", 0.3, 2),
                          ("Set Screw", "COMP", "Buy", 0.1, 0.8), ("Oil Seal", "COMP", "Buy", 3, 18), ("Carbide Insert", "MRO", "Buy", 9, 24)],
           "Keystone": [("Mounted Ball Bearing Unit", "RESALE", "Buy", 18, 140), ("Roller Chain ANSI", "RESALE", "Buy", 30, 160), ("Sheave / Pulley", "RESALE", "Buy", 25, 300),
                        ("AC Motor TEFC", "RESALE", "Buy", 240, 2400), ("Gear Reducer", "RESALE", "Buy", 400, 3800), ("Conveyor Drive Package", "FG", "Make", 1800, 7500),
                        ("Timing Belt", "RESALE", "Buy", 12, 90), ("Sprocket", "RESALE", "Buy", 8, 120)],
           "Ridgeway": [("Hex Bolt Gr 5 Zinc", "RESALE", "Buy", 5, 60), ("Socket Head Cap Screw Alloy", "RESALE", "Buy", 8, 90), ("Flat Washer SAE", "RESALE", "Buy", 2, 20),
                        ("Lock Nut Nylon Insert", "RESALE", "Buy", 3, 30), ("Anchor Wedge", "RESALE", "Buy", 15, 120), ("Cutting Wheel 4.5in", "RESALE", "Buy", 20, 60),
                        ("Abrasive Flap Disc", "RESALE", "Buy", 25, 75), ("Nitrile Gloves", "RESALE", "Buy", 7, 14), ("Hose Clamp SS", "RESALE", "Buy", 6, 40),
                        ("Cable Tie UV Black", "RESALE", "Buy", 4, 30), ("Drill Bit Cobalt", "RESALE", "Buy", 10, 70)]}[short]
    sizes = ["1/4-20", "5/16-18", "3/8-16", "1/2-13", "5/8-11", "3/4-10", "M6", "M8", "M10", "M12", "1.000", "1.250", "1.375", "1.500", "2.000", "size 4", "size 6", "size 8", "#50", "#60", "#80"]
    for i in range(n_own):
        name, cls, mob, lo, hi = rng.choice(fam)
        cost = rng.money(lo, hi, 0.01)
        sku = {"Northfield": f"{'A' if mob == 'Make' else 'P'}-{20000 + i}", "Keystone": f"{name.split(' ')[0][:4].upper()}-{1000 + i}",
               "Ridgeway": f"{name.split(' ')[0][:3].upper()}{rng.choice(sizes).replace('/', '').replace('-', '').replace(' ', '').replace('.', '').replace('#', '')}-{i:03d}"}[short]
        items.append({"item_id": sku, "description": f"{name} {rng.choice(sizes)}" + (f" x {rng.int(1, 6)}in" if short == "Ridgeway" and cls == "RESALE" else ""),
                      "item_class": cls, "manufacturer": profile["short"] if mob == "Make" else rng.choice(list(INDUSTRIAL_SUPPLIERS)),
                      "manufacturer_part_number": "" if mob == "Make" else f"{rng.choice('ABCDEFGHKLMNPRST')}{rng.int(1000, 99999)}",
                      "stock_uom": rng.choice(["EA", "EA", "EA", "BX", "C"]) if short == "Ridgeway" else "EA", "pack_qty": rng.choice([1, 25, 50, 100]) if short == "Ridgeway" else 1,
                      "purchase_uom": "EA", "primary_supplier": "" if mob == "Make" else rng.choice(list(INDUSTRIAL_SUPPLIERS)),
                      "unit_cost": cost, "list_price": round(cost * rng.money(1.3, 1.9), 2), "abc_class": rng.choice(["A", "B", "B", "C", "C"]),
                      "reorder_point": rng.int(2, 60), "reorder_qty": rng.int(10, 200), "lead_time_days": rng.int(5, 45) if mob == "Buy" else rng.int(10, 30),
                      "make_or_buy": mob, "weight_lb": round(rng.money(0.05, 90, 0.05), 2),
                      "hts_code": rng.choice(["7318.15.2095", "8483.30.8090", "8482.10.5068", "8483.60.8000", "8483.40.5010", ""]) if is_mfr or rng.chance(0.4) else "",
                      "country_of_origin": "US" if mob == "Make" else rng.choice(["US", "US", "CN", "TW", "MX", "DE", "IT"]),
                      "status": "Active" if rng.chance(0.93) else "Obsolete", "item_revision": rng.choice(["A", "A", "B", "C"]) if mob == "Make" else ""})
    if short == "Keystone":  # UoM mismatch anomaly: pack of 100 recorded with stock_uom EA
        it = shared_map["G10"]
        it["stock_uom"] = "EA"
        it["unit_cost"] = 11.40  # actually the box price
        key.add("IND-UOM-01", "data_quality", [short], "Unit-of-measure mismatch: box price stored against EA stock UoM",
                f"Item {it['item_id']} (nitrile gloves, 100/bx) has stock_uom EA but unit_cost 11.40 is the per-box price; on-hand quantities in inventory_balances are in boxes while sales order lines sell EA.",
                ["01_master_data/items.csv", "05_inventory/inventory_balances.xlsx", "02_sales_quote_to_order/sales_order_lines.csv"],
                "Correct stock_uom to BX (pack 100) or restate cost per EA before margin analysis.")
    make_items = [i for i in items if i["make_or_buy"] == "Make"]
    buy_items = [i for i in items if i["make_or_buy"] == "Buy" and i["status"] == "Active"]
    sell_items = [i for i in items if i["item_class"] in ("FG", "RESALE") and i["status"] == "Active"]
    item_by_id = {i["item_id"]: i for i in items}

    price_lists: list[Row] = []
    for it in sell_items:
        for lvl, disc in (("A", 0.0), ("B", 0.05), ("C", 0.10), ("D", 0.18)):
            price_lists.append({"price_list_id": f"PL-{lvl}", "price_level": lvl, "item_id": it["item_id"], "unit_price": round(it["list_price"] * (1 - disc), 2),
                                "currency": "USD", "effective_date": "2025-01-01", "expiration_date": "2026-12-31"})
    cust_price_agreements: list[Row] = []
    for c in rng.sample(active_customers, min(8, len(active_customers))):
        for it in rng.sample(sell_items, 3):
            exp = rng.choice(["2026-12-31", "2026-06-30", "2025-12-31"])
            cust_price_agreements.append({"agreement_id": f"{pre}-CPA{len(cust_price_agreements) + 1:03d}", "customer_id": c["customer_id"], "item_id": it["item_id"],
                                          "contract_price": round(it["list_price"] * rng.money(0.7, 0.88), 2), "min_qty": rng.choice([1, 10, 25, 100]),
                                          "effective_date": "2025-01-01", "expiration_date": exp, "approved_by": "E001", "source_document": f"quote_{rng.int(2400, 2600)}.pdf"})
    expired_cpa = [a for a in cust_price_agreements if a["expiration_date"] == "2025-12-31"]

    # BOM / routings / work centers
    work_centers = [{"work_center_id": "WC-SAW", "name": "Bar Saw", "hourly_rate": 48.0, "capacity_hours_per_day": 16}, {"work_center_id": "WC-CNC1", "name": "CNC Lathe Cell 1", "hourly_rate": 95.0, "capacity_hours_per_day": 20},
                    {"work_center_id": "WC-CNC2", "name": "CNC Mill Cell 2", "hourly_rate": 105.0, "capacity_hours_per_day": 20}, {"work_center_id": "WC-DEBUR", "name": "Deburr / Finish", "hourly_rate": 42.0, "capacity_hours_per_day": 16},
                    {"work_center_id": "WC-ASSY", "name": "Assembly", "hourly_rate": 55.0, "capacity_hours_per_day": 16}, {"work_center_id": "WC-INSP", "name": "Final Inspection", "hourly_rate": 60.0, "capacity_hours_per_day": 8},
                    {"work_center_id": "WC-PACK", "name": "Packaging", "hourly_rate": 38.0, "capacity_hours_per_day": 16}]
    boms: list[Row] = []
    routings: list[Row] = []
    comp_pool = [i for i in items if i["item_class"] in ("COMP", "RAW", "RESALE")]
    for fg in make_items:
        for line, comp in enumerate(rng.sample(comp_pool, rng.int(3, 6)), 1):
            boms.append({"bom_id": f"BOM-{fg['item_id']}", "parent_item_id": fg["item_id"], "parent_revision": fg["item_revision"], "line": line * 10,
                         "component_item_id": comp["item_id"], "component_revision": comp["item_revision"], "qty_per": rng.choice([1, 1, 2, 4, 0.75, 2.5]) if comp["item_class"] != "RAW" else round(rng.money(1.5, 22, 0.1), 2),
                         "uom": comp["stock_uom"], "scrap_pct": rng.choice([0, 0, 2, 3, 5]), "effective_date": "2025-01-01", "status": "Released"})
        if is_mfr:
            for op, wc in enumerate(rng.sample(work_centers[:5], rng.int(2, 4)) + [work_centers[5], work_centers[6]], 1):
                routings.append({"routing_id": f"RTG-{fg['item_id']}", "item_id": fg["item_id"], "operation": op * 10, "work_center_id": wc["work_center_id"],
                                 "description": wc["name"], "setup_hours": round(rng.money(0.25, 2.0, 0.25), 2), "run_hours_per_unit": round(rng.money(0.05, 1.2, 0.01), 3), "queue_days": rng.int(0, 2)})
    if is_mfr and boms:  # ECO anomaly: FG at rev C but BOM still calls rev A component that was superseded
        target = next(b for b in boms if b["component_revision"] == "A") if any(b["component_revision"] == "A" for b in boms) else boms[0]
        target["component_revision"] = "A"
        item_by_id[target["parent_item_id"]]["item_revision"] = "C"
        eco_item = target

    # ---------------- suppliers ----------------
    my_sups = sorted({i["primary_supplier"] for i in items if i["primary_supplier"]})
    suppliers: list[Row] = []
    sup_contacts: list[Row] = []
    sup_id: dict[str, str] = {}
    for n, s in enumerate(my_sups, 1):
        cat, city, st, terms = INDUSTRIAL_SUPPLIERS.get(s, ("Distributor", hq_city, hq_state, "Net 30"))
        if s in ("W.W. Grainger, Inc.", "GRAINGER", "Grainger Industrial Supply"):
            cat, city, st = "MRO Supplies", "Lake Forest", "IL"
        if s == "Cintas Corporation":
            cat, city, st = "Uniforms / Facility Services / PPE", "Cincinnati", "OH"
        sid = f"{pre}-V{n:03d}"
        sup_id[s] = sid
        suppliers.append({"supplier_id": sid, "supplier_name": s, "category": cat, "city": city, "state": st, "payment_terms": terms,
                          "tax_id": rng.fein(), "approved_supplier": "Y", "w9_on_file": "Y" if rng.chance(0.85) else "N", "iso_9001_certified": "Y" if rng.chance(0.6) else "N",
                          "supplier_since": iso(rng.date_between(date(profile["founded"] + 1, 1, 1), date(2024, 1, 1))), "remit_to": f"PO Box {rng.int(1000, 99999)}, {city}, {st}",
                          "ach_enabled": "Y" if rng.chance(0.6) else "N", "status": "Active"})
        p = rng.person()
        sup_contacts.append({"supplier_id": sid, "full_name": p, "title": rng.choice(["Account Manager", "Inside Sales", "Customer Service", "Territory Manager"]),
                             "email": email_for(p, s.split(" ")[0].lower().replace(".", "").replace(",", "") + ".com"), "phone": rng.phone()})
    if short == "Keystone":  # duplicate supplier
        dupsid = f"{pre}-V{len(suppliers) + 1:03d}"
        suppliers.append({**suppliers[[s["supplier_name"] for s in suppliers].index("GRAINGER")], "supplier_id": dupsid, "supplier_name": "W W Grainger Inc", "payment_terms": "Net 45", "ach_enabled": "N"})
        sup_id["W W Grainger Inc"] = dupsid
        key.add("IND-VEND-01", "data_quality", [short], "Duplicate supplier master records for Grainger",
                f"{sup_id['GRAINGER']} 'GRAINGER' and {dupsid} 'W W Grainger Inc' are the same vendor with different terms; POs and AP invoices are split between them.",
                ["03_procurement/suppliers.csv", "03_procurement/purchase_orders.csv", "04_receiving_ap/supplier_invoices.csv"], "Merge vendor records; consolidate spend for negotiation.")
    for tv in TRAP_VENDORS:
        if tv["company"] == short:
            sid = f"{pre}-V{len(suppliers) + 1:03d}"
            suppliers.append({"supplier_id": sid, "supplier_name": tv["name"], "category": tv["category"], "city": hq_city, "state": hq_state, "payment_terms": "Net 30", "tax_id": rng.fein(),
                              "approved_supplier": "Y", "w9_on_file": "Y", "iso_9001_certified": "N", "supplier_since": "2019-04-02", "remit_to": f"{rng.int(100, 9999)} Industrial Blvd, {hq_city}, {hq_state}", "ach_enabled": "N", "status": "Active"})
            sup_id[tv["name"]] = sid
            key.add("IND-VEND-TRAP-01", "vendor_consolidation", [short, "Northfield"], "Apex Fastening Systems LLC is not Apex Fastener Corp",
                    "Ridgeway buys from 'Apex Fastening Systems LLC' (Chattanooga distributor); Northfield buys from 'Apex Fastener Corp' (Elgin IL manufacturer). Similar names, different companies.",
                    ["03_procurement/suppliers.csv"], "Do not merge; do not report as shared vendor.", is_false_positive_trap=True)
    supplier_quotes: list[Row] = []
    for it in rng.sample(buy_items, min(25, len(buy_items))):
        for s in rng.sample(my_sups, min(2, len(my_sups))):
            supplier_quotes.append({"supplier_quote_id": f"{pre}-SQ{len(supplier_quotes) + 1:04d}", "supplier_id": sup_id[s], "item_id": it["item_id"], "quote_date": iso(rng.date_between(date(2025, 6, 1), TODAY)),
                                    "quoted_unit_cost": round(it["unit_cost"] * rng.money(0.92, 1.18), 2), "min_order_qty": rng.choice([1, 10, 25, 50, 100]), "lead_time_days": rng.int(3, 45), "valid_until": iso(TODAY + timedelta(days=rng.int(10, 120))),
                                    "awarded": "Y" if s == it["primary_supplier"] else "N"})

    # ---------------- quote-to-order / O2C ----------------
    start = date(2025, 4, 1)
    rfqs: list[Row] = []
    quotes: list[Row] = []
    quote_lines: list[Row] = []
    orders: list[Row] = []
    order_lines: list[Row] = []
    acks: list[Row] = []
    order_changes: list[Row] = []
    credit_checks: list[Row] = []
    n_orders = {"Northfield": 420, "Keystone": 520, "Ridgeway": 380}[short]
    cpa_by = {(a["customer_id"], a["item_id"]): a for a in cust_price_agreements}
    for i in range(1, n_orders + 1):
        c = rng.choice(active_customers)
        odate = rng.date_between(start, TODAY)
        so = f"SO-{26000 + i}" if short != "Ridgeway" else f"{rng.int(10000, 19999)}"
        cust_po = f"{rng.choice(['PO', 'P', '4500', 'PUR-'])}{rng.int(100000, 999999)}"
        rep = c["sales_rep_id"]
        via_quote = rng.chance(0.45 if is_mfr else 0.25)
        qid = ""
        if via_quote:
            qd = odate - timedelta(days=rng.int(2, 21))
            rfq_id = f"{pre}-RFQ{len(rfqs) + 1:04d}"
            rfqs.append({"rfq_id": rfq_id, "customer_id": c["customer_id"], "received_date": iso(qd - timedelta(days=rng.int(0, 3))), "channel": rng.choice(["Email", "Email", "Portal", "Phone", "EDI 840"]),
                         "requested_by": rng.choice([x for x in contacts if x["customer_id"] == c["customer_id"]])["full_name"], "due_date": iso(qd + timedelta(days=rng.int(1, 5))), "assigned_to": rng.choice(csrs)["employee_id"], "status": "Quoted"})
            qid = f"Q-{26000 + len(quotes) + 1}"
            quotes.append({"quote_id": qid, "rfq_id": rfq_id, "customer_id": c["customer_id"], "quote_date": iso(qd), "valid_until": iso(qd + timedelta(days=30)), "sales_rep_id": rep, "status": "Won", "converted_order_id": so, "total": 0.0})
        n_lines = rng.int(1, 5 if short != "Ridgeway" else 8)
        total = 0.0
        shipped_lines = 0
        for ln in range(1, n_lines + 1):
            it = rng.choice(sell_items)
            qty = rng.choice([1, 2, 4, 6, 10, 12, 25, 50, 100, 200]) if it["unit_cost"] < 100 else rng.choice([1, 1, 2, 3, 5])
            base = next(p["unit_price"] for p in price_lists if p["item_id"] == it["item_id"] and p["price_level"] == c["price_level"])
            cpa = cpa_by.get((c["customer_id"], it["item_id"]))
            price_src = "Price List " + c["price_level"]
            if cpa and (cpa["expiration_date"] >= iso(odate) or short == "Keystone"):
                base = cpa["contract_price"]
                price_src = cpa["agreement_id"]
            if rng.chance(0.08):
                base = round(base * rng.money(0.85, 0.97), 2)
                price_src = "Manual override"
            req = odate + timedelta(days=rng.int(2, 28))
            promised = req + timedelta(days=rng.choice([0, 0, 0, 1, 3, 7]))
            line_status = "Shipped" if promised < TODAY - timedelta(days=3) and rng.chance(0.93) else ("Backordered" if rng.chance(0.15) else "Open")
            if line_status == "Shipped":
                shipped_lines += 1
            ext = round(base * qty, 2)
            total += ext
            order_lines.append({"so_number": so, "line": ln, "item_id": it["item_id"], "description": it["description"], "ordered_qty": qty, "uom": it["stock_uom"],
                                "unit_price": base, "extended_price": ext, "unit_cost_at_order": it["unit_cost"], "price_source": price_src,
                                "requested_date": iso(req), "promised_date": iso(promised), "shipped_qty": qty if line_status == "Shipped" else 0, "line_status": line_status,
                                "ship_to_id": next(s["ship_to_id"] for s in ship_tos if s["customer_id"] == c["customer_id"])})
            if via_quote:
                quote_lines.append({"quote_id": qid, "line": ln, "item_id": it["item_id"], "qty": qty, "unit_price": base, "lead_time_days": rng.int(3, 30)})
        if via_quote:
            quotes[-1]["total"] = round(total, 2)
        status = "Closed" if shipped_lines == n_lines else ("Partially Shipped" if shipped_lines else "Open")
        hold = "Y" if (c["credit_hold"] == "Y" or rng.chance(0.02)) else "N"
        orders.append({"so_number": so, "customer_id": c["customer_id"], "customer_po_number": cust_po, "order_date": iso(odate), "quote_id": qid, "sales_rep_id": rep,
                       "entered_by": rng.choice(csrs)["employee_id"], "order_source": rng.choice(["Email", "Email", "Phone", "EDI 850", "Portal", "Counter"] if short == "Ridgeway" else ["Email", "Email", "Phone", "EDI 850", "Portal"]),
                       "payment_terms": c["payment_terms"], "ship_via": c["preferred_carrier"], "freight_terms": rng.choice(["Prepaid & Add", "Prepaid & Add", "Collect", "Prepaid"]),
                       "order_total": round(total, 2), "credit_hold": hold, "order_status": status, "invoiced": "N"})
        if c["credit_limit"] < 60000 or rng.chance(0.2):
            credit_checks.append({"so_number": so, "check_date": iso(odate), "customer_id": c["customer_id"], "credit_limit": c["credit_limit"], "open_ar_at_check": round(rng.money(0, c["credit_limit"] * 1.1), 2),
                                  "order_total": round(total, 2), "result": "Hold" if hold == "Y" else "Approved", "reviewed_by": finance[0]["employee_id"] if finance else "E001"})
        acks.append({"ack_id": f"ACK-{so}", "so_number": so, "sent_date": iso(odate + timedelta(days=rng.int(0, 1))), "sent_to": rng.choice([x for x in contacts if x["customer_id"] == c["customer_id"]])["email"],
                     "method": rng.choice(["Email PDF", "Email PDF", "EDI 855"]), "acknowledged_total": round(total, 2)})
        if rng.chance(0.12):
            order_changes.append({"change_id": f"{pre}-CHG{len(order_changes) + 1:04d}", "so_number": so, "change_date": iso(odate + timedelta(days=rng.int(1, 10))), "change_type": rng.choice(["Qty change", "Date change", "Add line", "Cancel line", "Ship-to change"]),
                                  "requested_by": "Customer", "old_value": str(rng.int(1, 50)), "new_value": str(rng.int(1, 50)), "approved_by": rep, "source": rng.choice(["Email", "Phone", "EDI 860"])})
    # Keystone: expired contract pricing still applied
    if short == "Keystone" and expired_cpa:
        used_lines = [ol for ol in order_lines if ol["price_source"] in {a["agreement_id"] for a in expired_cpa} and ol["so_number"] in {o["so_number"] for o in orders if o["order_date"] > "2025-12-31"}]
        if used_lines:
            key.add("IND-PRICE-02", "margin_leakage", [short], "Expired customer price agreement still applied on 2026 orders",
                    f"{len(used_lines)} order lines dated after 2025-12-31 still price at contract agreements that expired 2025-12-31 (e.g. {used_lines[0]['so_number']} line {used_lines[0]['line']} via {used_lines[0]['price_source']}).",
                    ["01_master_data/customer_price_agreements.csv", "02_sales_quote_to_order/sales_order_lines.csv"], "Re-price or renew agreements; quantify margin given up.")
    so_by = {o["so_number"]: o for o in orders}
    lines_by_so: dict[str, list[Row]] = {}
    for ol in order_lines:
        lines_by_so.setdefault(ol["so_number"], []).append(ol)

    # ---------------- procurement (P2P) ----------------
    reqs: list[Row] = []
    pos: list[Row] = []
    po_lines: list[Row] = []
    po_acks: list[Row] = []
    asns: list[Row] = []
    receipts: list[Row] = []
    receipt_lines: list[Row] = []
    sup_invoices: list[Row] = []
    sup_invoice_lines: list[Row] = []
    match_results: list[Row] = []
    n_pos = {"Northfield": 260, "Keystone": 300, "Ridgeway": 210}[short]
    items_by_sup: dict[str, list[Row]] = {}
    for it in buy_items:
        items_by_sup.setdefault(it["primary_supplier"], []).append(it)
    planted_ppv = None
    planted_short = None
    for i in range(1, n_pos + 1):
        s = rng.choice(my_sups)
        sname = s
        if short == "Keystone" and s == "GRAINGER" and rng.chance(0.4):
            sname = "W W Grainger Inc"
        pdate = rng.date_between(start, TODAY)
        po = f"PO-{31000 + i}" if short != "Ridgeway" else f"{rng.int(5000, 8999)}"
        buyer = rng.choice(buyers)["employee_id"] if buyers else "E001"
        if rng.chance(0.5):
            reqs.append({"requisition_id": f"{pre}-REQ{len(reqs) + 1:04d}", "requested_by": rng.choice(employees)["employee_id"], "request_date": iso(pdate - timedelta(days=rng.int(1, 5))),
                         "department": rng.choice(["Operations", "Maintenance", "Warehouse", "Purchasing"]), "reason": rng.choice(["Below reorder point", "Customer order", "MRP", "Maintenance", "Stock-out"]),
                         "approved_by": buyers[0]["employee_id"] if buyers else "E001", "approval_date": iso(pdate), "converted_po": po, "status": "Ordered"})
        n_lines = rng.int(1, 4)
        total = 0.0
        pool = items_by_sup.get(s, buy_items)
        for ln in range(1, n_lines + 1):
            it = rng.choice(pool)
            qty = it["reorder_qty"] if rng.chance(0.6) else rng.choice([10, 25, 50, 100, 250, 500])
            cost = it["unit_cost"] if rng.chance(0.85) else round(it["unit_cost"] * rng.money(0.97, 1.06), 2)
            due = pdate + timedelta(days=it["lead_time_days"])
            recv_status = "Received" if due < TODAY - timedelta(days=2) and rng.chance(0.9) else ("Partial" if rng.chance(0.2) else "Open")
            po_lines.append({"po_number": po, "line": ln, "item_id": it["item_id"], "description": it["description"], "manufacturer_part_number": it["manufacturer_part_number"],
                             "ordered_qty": qty, "uom": it["purchase_uom"], "unit_cost": cost, "extended_cost": round(qty * cost, 2), "need_by_date": iso(due), "promised_date": iso(due + timedelta(days=rng.choice([0, 0, 0, 2, 5]))),
                             "received_qty": qty if recv_status == "Received" else (qty // 2 if recv_status == "Partial" else 0), "line_status": recv_status, "gl_account": next(c["gl_inventory_account"] for c in classes if c["class_code"] == it["item_class"])})
            total += qty * cost
        pos.append({"po_number": po, "supplier_id": sup_id[sname], "supplier_name": sname, "po_date": iso(pdate), "buyer_id": buyer, "payment_terms": INDUSTRIAL_SUPPLIERS.get(s, ("", "", "", "Net 30"))[3],
                    "ship_via": rng.choice(["UPS Ground", "FedEx Ground", "LTL - Estes", "Supplier Truck", "Will Call"]), "freight_terms": rng.choice(["Prepaid & Add", "Collect", "FOB Origin", "FOB Destination"]),
                    "po_total": round(total, 2), "po_status": "Closed" if all(p["line_status"] == "Received" for p in po_lines if p["po_number"] == po) else "Open",
                    "approved_by": "E001" if total > 10000 else buyer, "sent_method": rng.choice(["Email PDF", "Email PDF", "Supplier Portal", "EDI 850", "Phone"] if short != "Ridgeway" else ["Email PDF", "Phone", "Phone", "Fax"])})
        if rng.chance(0.75):
            po_acks.append({"po_number": po, "ack_date": iso(pdate + timedelta(days=rng.int(0, 3))), "confirmed_total": round(total, 2) if rng.chance(0.93) else round(total * rng.money(1.0, 1.04), 2),
                            "confirmed_ship_date": iso(pdate + timedelta(days=rng.int(3, 30))), "method": rng.choice(["Email", "EDI 855", "Portal"])})
        my_lines = [p for p in po_lines if p["po_number"] == po and p["received_qty"] > 0]
        if not my_lines:
            continue
        rdate = date.fromisoformat(my_lines[0]["need_by_date"]) + timedelta(days=rng.int(-2, 6))
        if rdate > TODAY:
            rdate = TODAY
        rid = f"RCV-{40000 + len(receipts) + 1}"
        if rng.chance(0.55):
            asns.append({"asn_id": f"ASN-{po}", "po_number": po, "supplier_id": sup_id[sname], "ship_date": iso(rdate - timedelta(days=rng.int(1, 4))), "carrier": pos[-1]["ship_via"],
                         "tracking_number": f"1Z{rng.int(10**9, 10**10 - 1)}" if "UPS" in pos[-1]["ship_via"] else f"{rng.int(10**11, 10**12 - 1)}", "cartons": rng.int(1, 12), "weight_lb": rng.int(5, 900)})
        receipts.append({"receipt_id": rid, "po_number": po, "supplier_id": sup_id[sname], "receipt_date": iso(rdate), "received_by": rng.choice(whs)["employee_id"], "packing_slip_number": f"PS{rng.int(100000, 999999)}",
                         "carrier": pos[-1]["ship_via"], "condition": rng.choice(["OK", "OK", "OK", "OK", "Damaged carton - contents OK", "Short vs packing slip"]), "inspection_required": "Y" if is_mfr and rng.chance(0.3) else "N"})
        for p in my_lines:
            rq = p["received_qty"]
            receipt_lines.append({"receipt_id": rid, "po_number": po, "po_line": p["line"], "item_id": p["item_id"], "received_qty": rq, "uom": p["uom"], "lot_number": f"L{rdate.strftime('%y%m%d')}-{rng.int(100, 999)}" if is_mfr else "",
                                  "bin_location": f"{rng.choice('ABCDEF')}-{rng.int(1, 40):02d}-{rng.int(1, 5)}", "putaway_by": rng.choice(whs)["employee_id"], "qc_status": "Accepted" if rng.chance(0.96) else "Hold"})
        # supplier invoice
        inv_date = rdate + timedelta(days=rng.int(0, 7))
        inv_no = f"{rng.choice(['INV', 'SI', '', '9'])}{rng.int(100000, 9999999)}"
        inv_total = 0.0
        exc = "Matched"
        for p in my_lines:
            price = p["unit_cost"]
            qty = p["received_qty"]
            if planted_ppv is None and short == "Northfield" and i > 30:
                price = round(p["unit_cost"] * 1.06, 2)
                planted_ppv = (po, inv_no, p, price)
                exc = "Price variance"
            elif planted_short is None and short == "Keystone" and i > 40 and qty > 10:
                qty = qty + 10
                planted_short = (po, inv_no, p, qty)
                exc = "Qty variance"
            elif rng.chance(0.04):
                price = round(price * rng.money(1.01, 1.05), 2)
                exc = "Price variance"
            sup_invoice_lines.append({"supplier_invoice_number": inv_no, "po_number": po, "po_line": p["line"], "item_id": p["item_id"], "invoiced_qty": qty, "unit_price": price, "extended": round(qty * price, 2)})
            inv_total += qty * price
        freight = round(rng.money(0, 180), 2) if pos[-1]["freight_terms"] == "Prepaid & Add" else 0.0
        tax = 0.0
        if short == "Ridgeway" and rng.chance(0.15):
            tax = round(inv_total * 0.0925, 2)
        sup_invoices.append({"supplier_invoice_number": inv_no, "supplier_id": sup_id[sname], "supplier_name": sname, "po_number": po, "invoice_date": iso(inv_date), "received_date": iso(inv_date + timedelta(days=rng.int(0, 5))),
                             "due_date": iso(inv_date + timedelta(days=int(pos[-1]["payment_terms"].split()[-1]) if pos[-1]["payment_terms"].startswith("Net") else 30)),
                             "merchandise_total": round(inv_total, 2), "freight": freight, "sales_tax": tax, "invoice_total": round(inv_total + freight + tax, 2),
                             "gl_account": "2000", "status": "Paid" if inv_date < TODAY - timedelta(days=45) else ("Approved" if exc == "Matched" else "On Hold"), "match_status": exc, "entered_by": finance[0]["employee_id"] if finance else "E002"})
        match_results.append({"supplier_invoice_number": inv_no, "po_number": po, "receipt_id": rid, "po_amount": round(sum(p["extended_cost"] for p in my_lines), 2), "received_amount": round(sum(p["received_qty"] * p["unit_cost"] for p in my_lines), 2),
                              "invoiced_amount": round(inv_total, 2), "variance": round(inv_total - sum(p["received_qty"] * p["unit_cost"] for p in my_lines), 2), "result": exc, "tolerance_pct": 2.0,
                              "resolved": "N" if exc != "Matched" and inv_date > TODAY - timedelta(days=60) else "Y"})
    if planted_ppv:
        po, inv_no, p, price = planted_ppv
        key.add("IND-P2P-01", "three_way_match", [short], "Supplier invoice priced 6% above PO unit cost",
                f"Invoice {inv_no} bills {p['item_id']} at {price} vs PO {po} line {p['line']} at {p['unit_cost']}; outside 2% tolerance, unresolved.",
                ["04_receiving_ap/supplier_invoices.csv", "04_receiving_ap/three_way_match.csv", "03_procurement/purchase_order_lines.csv"], "Short-pay or request credit memo; record PPV.")
    if planted_short:
        po, inv_no, p, qty = planted_short
        key.add("IND-P2P-02", "three_way_match", [short], "Supplier invoiced more units than received",
                f"Invoice {inv_no} bills {qty} of {p['item_id']} against PO {po}; receipt shows {p['received_qty']}. Invoice is on hold.",
                ["04_receiving_ap/supplier_invoices.csv", "04_receiving_ap/receipt_lines.csv", "04_receiving_ap/three_way_match.csv"], "Dispute 10 units with supplier; do not pay full amount.")
    if short == "Ridgeway":
        taxed = [s for s in sup_invoices if s["sales_tax"]]
        key.add("IND-AP-01", "overpayment", [short], "Sales tax paid on resale inventory despite resale certificate",
                f"{len(taxed)} supplier invoices include TN sales tax (9.25%) on items purchased for resale, totalling ${sum(s['sales_tax'] for s in taxed):,.2f}.",
                ["04_receiving_ap/supplier_invoices.csv"], "File for refund / provide resale certificate to suppliers.")
    ap_aging: list[Row] = []
    ap_payments: list[Row] = []
    for si in sup_invoices:
        if si["status"] == "Paid":
            ap_payments.append({"payment_id": f"{pre}-APP{len(ap_payments) + 1:05d}", "supplier_id": si["supplier_id"], "supplier_invoice_number": si["supplier_invoice_number"], "payment_date": iso(min(TODAY, date.fromisoformat(si["due_date"]) + timedelta(days=rng.int(-5, 12)))),
                                "amount": si["invoice_total"], "method": rng.choice(["ACH", "ACH", "Check", "Card"]), "check_number": f"{rng.int(10000, 29999)}" if rng.chance(0.4) else "", "bank_account": "1000"})
        else:
            age = (TODAY - date.fromisoformat(si["due_date"])).days
            bucket = "Current" if age <= 0 else ("1-30" if age <= 30 else ("31-60" if age <= 60 else ("61-90" if age <= 90 else "90+")))
            ap_aging.append({"as_of_date": iso(TODAY), "supplier_id": si["supplier_id"], "supplier_invoice_number": si["supplier_invoice_number"], "invoice_date": si["invoice_date"], "due_date": si["due_date"], "open_amount": si["invoice_total"], "aging_bucket": bucket, "hold_reason": si["match_status"] if si["status"] == "On Hold" else ""})
    scorecards: list[Row] = []
    for s in my_sups:
        sid = sup_id[s]
        sp = [p for p in pos if p["supplier_id"] == sid]
        for q in ("2025-Q3", "2025-Q4", "2026-Q1"):
            scorecards.append({"supplier_id": sid, "supplier_name": s, "period": q, "po_count": max(1, len(sp) // 3), "on_time_delivery_pct": round(rng.money(78, 99.5, 0.1), 1), "quality_ppm": rng.int(0, 8000),
                               "invoice_accuracy_pct": round(rng.money(88, 100, 0.1), 1), "responsiveness_score": rng.int(2, 5), "overall_rating": rng.choice(["Preferred", "Approved", "Approved", "Conditional"])})
    sup_contracts: list[Row] = []
    for s in rng.sample(my_sups, min(5, len(my_sups))):
        sup_contracts.append({"contract_id": f"{pre}-SC{len(sup_contracts) + 1:03d}", "supplier_id": sup_id[s], "supplier_name": s, "type": rng.choice(["Pricing Agreement", "Blanket PO", "Consignment", "Rebate Agreement"]),
                              "start_date": "2025-01-01", "end_date": rng.choice(["2026-12-31", "2026-06-30", "2025-12-31"]), "annual_commitment": rng.choice([25000, 50000, 100000, 250000]), "rebate_pct": rng.choice([0, 0, 1.0, 2.0, 3.0]), "auto_renew": rng.choice(["Y", "N"]),
                              "document": f"{s.split(' ')[0].lower()}_agreement_2025.pdf"})

    # ---------------- inventory ----------------
    balances: list[Row] = []
    for it in items:
        if it["status"] != "Active":
            continue
        onhand = rng.int(0, it["reorder_qty"] * 3)
        alloc = min(onhand, rng.int(0, 40))
        bal = {"item_id": it["item_id"], "warehouse": "MAIN", "bin_location": f"{rng.choice('ABCDEF')}-{rng.int(1, 40):02d}-{rng.int(1, 5)}", "on_hand_qty": onhand, "allocated_qty": alloc, "available_qty": onhand - alloc,
               "on_order_qty": sum(p["ordered_qty"] - p["received_qty"] for p in po_lines if p["item_id"] == it["item_id"]), "uom": it["stock_uom"], "unit_cost": it["unit_cost"], "extended_value": round(onhand * it["unit_cost"], 2),
               "last_count_date": iso(rng.date_between(date(2025, 9, 1), TODAY)), "last_receipt_date": iso(rng.date_between(date(2025, 6, 1), TODAY)), "last_issue_date": iso(rng.date_between(date(2025, 10, 1), TODAY)), "as_of_date": iso(TODAY)}
        balances.append(bal)
    if short == "Keystone":
        neg = balances[11]
        neg["on_hand_qty"] = -6
        neg["available_qty"] = -6
        neg["extended_value"] = round(-6 * neg["unit_cost"], 2)
        key.add("IND-INV-01", "inventory_integrity", [short], "Negative on-hand quantity", f"Item {neg['item_id']} shows on_hand -6 in MAIN; shipments were confirmed before receipts were posted.",
                ["05_inventory/inventory_balances.xlsx", "05_inventory/inventory_transactions.csv"], "Post the missing receipt / investigate backflush timing.")
    inv_txns: list[Row] = []
    for rl in receipt_lines:
        inv_txns.append({"transaction_id": f"IT-{len(inv_txns) + 1:06d}", "transaction_date": next(r["receipt_date"] for r in receipts if r["receipt_id"] == rl["receipt_id"]), "type": "PO Receipt", "item_id": rl["item_id"], "qty": rl["received_qty"], "uom": rl["uom"],
                         "from_location": "RECEIVING", "to_location": rl["bin_location"], "reference": rl["po_number"], "lot_number": rl["lot_number"], "user_id": rl["putaway_by"], "unit_cost": item_by_id[rl["item_id"]]["unit_cost"] if rl["item_id"] in item_by_id else ""})
    for ol in order_lines:
        if ol["shipped_qty"]:
            inv_txns.append({"transaction_id": f"IT-{len(inv_txns) + 1:06d}", "transaction_date": ol["promised_date"], "type": "SO Issue", "item_id": ol["item_id"], "qty": -ol["shipped_qty"], "uom": ol["uom"], "from_location": rng.choice(balances)["bin_location"], "to_location": "SHIPPING",
                             "reference": ol["so_number"], "lot_number": "", "user_id": rng.choice(whs)["employee_id"], "unit_cost": ol["unit_cost_at_order"]})
    for _ in range(40):
        it = rng.choice(items)
        inv_txns.append({"transaction_id": f"IT-{len(inv_txns) + 1:06d}", "transaction_date": iso(rng.date_between(start, TODAY)), "type": rng.choice(["Adjustment", "Transfer", "Cycle Count Adj", "Scrap"]), "item_id": it["item_id"], "qty": rng.int(-12, 12), "uom": it["stock_uom"],
                         "from_location": f"{rng.choice('ABCDEF')}-{rng.int(1, 40):02d}-{rng.int(1, 5)}", "to_location": f"{rng.choice('ABCDEF')}-{rng.int(1, 40):02d}-{rng.int(1, 5)}", "reference": rng.choice(["Count", "Bin move", "Damage", ""]), "lot_number": "", "user_id": rng.choice(whs)["employee_id"], "unit_cost": it["unit_cost"]})
    inv_txns.sort(key=lambda r: r["transaction_date"])
    cycle_counts: list[Row] = []
    for b in rng.sample(balances, min(60, len(balances))):
        counted = b["on_hand_qty"] + (rng.int(-8, 8) if rng.chance(0.25) else 0)
        cycle_counts.append({"count_id": f"CC-{len(cycle_counts) + 1:04d}", "count_date": b["last_count_date"], "item_id": b["item_id"], "bin_location": b["bin_location"], "system_qty": b["on_hand_qty"], "counted_qty": counted, "variance_qty": counted - b["on_hand_qty"],
                             "variance_value": round((counted - b["on_hand_qty"]) * b["unit_cost"], 2), "counted_by": rng.choice(whs)["employee_id"], "approved_by": whs[0]["employee_id"], "posted": "Y" if rng.chance(0.9) else "N"})
    if is_mfr:
        big = max(cycle_counts, key=lambda c: abs(c["variance_value"]))
        big["variance_qty"] = -34
        big["counted_qty"] = big["system_qty"] - 34
        big["variance_value"] = round(-34 * item_by_id[big["item_id"]]["unit_cost"], 2)
        big["posted"] = "N"
        key.add("IND-INV-02", "inventory_integrity", [short], "Large unposted cycle-count shrink", f"Count {big['count_id']} for {big['item_id']} found 34 fewer than system (${abs(big['variance_value']):,.2f}); variance not posted for 30+ days.",
                ["05_inventory/cycle_counts.csv", "05_inventory/inventory_balances.csv"], "Investigate and post adjustment; inventory valuation is overstated.")
    reservations = [{"reservation_id": f"RSV-{n:04d}", "so_number": ol["so_number"], "so_line": ol["line"], "item_id": ol["item_id"], "reserved_qty": ol["ordered_qty"], "reserved_date": so_by[ol["so_number"]]["order_date"], "status": "Open"}
                    for n, ol in enumerate([x for x in order_lines if x["line_status"] == "Open"][:80], 1)]
    reorder_params = [{"item_id": it["item_id"], "warehouse": "MAIN", "planning_method": rng.choice(["Min/Max", "Min/Max", "ROP", "MRP"] if is_mfr else ["Min/Max", "Min/Max", "ROP"]), "reorder_point": it["reorder_point"], "reorder_qty": it["reorder_qty"], "safety_stock": it["reorder_point"] // 3,
                       "lead_time_days": it["lead_time_days"], "abc_class": it["abc_class"], "last_reviewed": iso(rng.date_between(date(2024, 6, 1), TODAY))} for it in buy_items]
    transfers = [{"transfer_id": f"TR-{n:04d}", "transfer_date": iso(rng.date_between(start, TODAY)), "item_id": rng.choice(items)["item_id"], "qty": rng.int(1, 60), "from_location": "MAIN", "to_location": rng.choice(["VMI-CUST0007", "VMI-CUST0012", "TRUCK-2", "BRANCH-2"] if short != "Northfield" else ["WIP-CNC1", "WIP-ASSY", "FG-STAGE"]),
                  "requested_by": rng.choice(employees)["employee_id"], "status": rng.choice(["Complete", "Complete", "In Transit"])} for n in range(1, 45)]
    vmi_bins: list[Row] = []
    if short == "Ridgeway":
        for c in rng.sample(active_customers, 5):
            for it in rng.sample(sell_items, 12):
                vmi_bins.append({"customer": c["customer_name"], "bin": f"BIN-{rng.int(1, 80):03d}", "item": it["item_id"], "min": rng.int(50, 200), "max": rng.int(300, 800), "last count": iso(rng.date_between(date(2026, 1, 1), TODAY)), "count": rng.int(0, 700), "tech": rng.choice([e for e in employees if e['title'] == 'VMI Tech'])["full_name"], "notes": rng.choice(["", "", "replenished", "bin moved", "cust says wrong part", "label missing"])})
    lot_trace: list[Row] = []
    if is_mfr:
        for rl in [r for r in receipt_lines if r["lot_number"]][:120]:
            lot_trace.append({"lot_number": rl["lot_number"], "item_id": rl["item_id"], "supplier_id": next(r["supplier_id"] for r in receipts if r["receipt_id"] == rl["receipt_id"]), "receipt_id": rl["receipt_id"], "heat_number": f"H{rng.int(100000, 999999)}" if item_by_id.get(rl["item_id"], {}).get("item_class") == "RAW" else "",
                              "cert_on_file": "Y" if rng.chance(0.9) else "N", "consumed_in_work_orders": ";".join(f"WO-{rng.int(70000, 70400)}" for _ in range(rng.int(0, 3))), "remaining_qty": rng.int(0, rl["received_qty"])})

    # ---------------- demand planning ----------------
    forecasts: list[Row] = []
    mrp: list[Row] = []
    if short != "Ridgeway":
        for it in rng.sample(sell_items, min(40, len(sell_items))):
            hist = [ol["ordered_qty"] for ol in order_lines if ol["item_id"] == it["item_id"]]
            base = (sum(hist) / 12) if hist else rng.int(1, 20)
            for m in range(1, 7):
                md = date(2026, 3, 1) + timedelta(days=31 * m)
                forecasts.append({"item_id": it["item_id"], "forecast_month": md.strftime("%Y-%m"), "forecast_qty": max(1, round(base * rng.money(0.7, 1.3))), "method": rng.choice(["12-mo moving avg", "Exp smoothing", "Sales input"]), "planner_id": rng.choice(employees)["employee_id"], "customer_id": "" if rng.chance(0.7) else rng.choice(active_customers)["customer_id"]})
        for b in balances:
            it = item_by_id[b["item_id"]]
            if b["available_qty"] + b["on_order_qty"] < it["reorder_point"]:
                mrp.append({"run_date": iso(TODAY), "item_id": it["item_id"], "action": "Buy" if it["make_or_buy"] == "Buy" else "Make", "available": b["available_qty"], "on_order": b["on_order_qty"], "reorder_point": it["reorder_point"], "suggested_qty": it["reorder_qty"],
                            "suggested_due": iso(TODAY + timedelta(days=it["lead_time_days"])), "supplier_id": sup_id.get(it["primary_supplier"], ""), "status": "Open"})

    # ---------------- manufacturing ----------------
    work_orders: list[Row] = []
    wo_ops: list[Row] = []
    labor: list[Row] = []
    mat_issues: list[Row] = []
    scrap: list[Row] = []
    downtime: list[Row] = []
    completions: list[Row] = []
    wip: list[Row] = []
    if manufactures and make_items:
        n_wo = 220 if is_mfr else 60
        for i in range(1, n_wo + 1):
            fg = rng.choice(make_items)
            rel = rng.date_between(start, TODAY)
            qty = rng.choice([5, 10, 20, 25, 50, 100]) if is_mfr else rng.choice([1, 1, 2, 4])
            due = rel + timedelta(days=rng.int(7, 30))
            st = "Closed" if due < TODAY - timedelta(days=5) and rng.chance(0.9) else rng.choice(["Released", "In Process", "In Process"])
            wo = f"WO-{70000 + i}"
            demand_so = rng.choice([o["so_number"] for o in orders]) if rng.chance(0.6) else "Stock"
            work_orders.append({"work_order_id": wo, "item_id": fg["item_id"], "item_revision": fg["item_revision"], "order_qty": qty, "release_date": iso(rel), "due_date": iso(due), "demand_source": demand_so, "planner_id": rng.choice(employees)["employee_id"],
                                "status": st, "completed_qty": qty if st == "Closed" else (rng.int(0, qty) if st == "In Process" else 0), "scrap_qty": 0, "std_cost_per_unit": fg["unit_cost"], "actual_cost": ""})
            comp = [b for b in boms if b["parent_item_id"] == fg["item_id"]]
            for b in comp:
                mat_issues.append({"work_order_id": wo, "issue_date": iso(rel + timedelta(days=rng.int(0, 2))), "component_item_id": b["component_item_id"], "required_qty": round(b["qty_per"] * qty, 2), "issued_qty": round(b["qty_per"] * qty * rng.money(0.98, 1.05), 2), "uom": b["uom"], "lot_number": f"L{rel.strftime('%y%m%d')}-{rng.int(100, 999)}" if is_mfr else "", "issued_by": rng.choice(whs)["employee_id"]})
            act_cost = 0.0
            for r in [r for r in routings if r["item_id"] == fg["item_id"]]:
                setup = r["setup_hours"] * rng.money(0.8, 1.4)
                run = r["run_hours_per_unit"] * qty * rng.money(0.85, 1.35)
                wc = next(w for w in work_centers if w["work_center_id"] == r["work_center_id"])
                st_op = "Complete" if st == "Closed" or rng.chance(0.5) else "Open"
                wo_ops.append({"work_order_id": wo, "operation": r["operation"], "work_center_id": r["work_center_id"], "planned_setup_hours": r["setup_hours"], "planned_run_hours": round(r["run_hours_per_unit"] * qty, 2), "actual_setup_hours": round(setup, 2) if st_op == "Complete" else "", "actual_run_hours": round(run, 2) if st_op == "Complete" else "", "status": st_op})
                if st_op == "Complete":
                    act_cost += (setup + run) * wc["hourly_rate"]
                    for _ in range(rng.int(1, 3)):
                        labor.append({"labor_txn_id": f"LT-{len(labor) + 1:06d}", "work_order_id": wo, "operation": r["operation"], "employee_id": rng.choice(ops)["employee_id"], "clock_in": f"{iso(rel + timedelta(days=rng.int(0, 10)))}T{rng.int(6, 14):02d}:{rng.choice(['00', '15', '30', '45'])}:00", "hours": round((setup + run) / rng.int(1, 3), 2), "labor_type": rng.choice(["Run", "Run", "Setup"]), "work_center_id": r["work_center_id"]})
                if rng.chance(0.08):
                    downtime.append({"downtime_id": f"DT-{len(downtime) + 1:04d}", "date": iso(rel + timedelta(days=rng.int(0, 10))), "work_center_id": r["work_center_id"], "work_order_id": wo, "minutes": rng.int(10, 240), "reason_code": rng.choice(["Tool change", "Machine fault", "Waiting material", "Waiting inspection", "No operator", "Program edit"]), "reported_by": rng.choice(ops)["employee_id"]})
            if rng.chance(0.18):
                sq = rng.int(1, max(1, qty // 10))
                work_orders[-1]["scrap_qty"] = sq
                scrap.append({"scrap_id": f"SC-{len(scrap) + 1:04d}", "work_order_id": wo, "item_id": fg["item_id"], "date": iso(rel + timedelta(days=rng.int(1, 12))), "qty": sq, "reason_code": rng.choice(["Dimensional", "Surface finish", "Material defect", "Setup scrap", "Operator error"]), "cost": round(sq * fg["unit_cost"], 2), "disposition": rng.choice(["Scrap", "Rework", "Use-as-is (MRB)"]), "reported_by": rng.choice(ops)["employee_id"]})
            if st == "Closed":
                mat_cost = sum(m["issued_qty"] * item_by_id[m["component_item_id"]]["unit_cost"] for m in mat_issues if m["work_order_id"] == wo)
                work_orders[-1]["actual_cost"] = round(act_cost + mat_cost, 2)
                completions.append({"completion_id": f"CMP-{len(completions) + 1:05d}", "work_order_id": wo, "completion_date": iso(min(TODAY, due + timedelta(days=rng.int(-3, 6)))), "item_id": fg["item_id"], "completed_qty": qty, "to_location": "FG-STAGE", "std_cost": round(qty * fg["unit_cost"], 2), "actual_cost": work_orders[-1]["actual_cost"], "variance": round(work_orders[-1]["actual_cost"] - qty * fg["unit_cost"], 2), "received_by": rng.choice(whs)["employee_id"]})
            elif st == "In Process":
                wip.append({"as_of_date": iso(TODAY), "work_order_id": wo, "item_id": fg["item_id"], "order_qty": qty, "completed_qty": work_orders[-1]["completed_qty"], "material_cost_to_date": round(sum(m["issued_qty"] * item_by_id[m["component_item_id"]]["unit_cost"] for m in mat_issues if m["work_order_id"] == wo), 2), "labor_cost_to_date": round(act_cost, 2), "overhead_applied": round(act_cost * 0.6, 2)})

    # ---------------- quality ----------------
    inspections: list[Row] = []
    ncrs: list[Row] = []
    capas: list[Row] = []
    cocs: list[Row] = []
    calibrations: list[Row] = []
    if short != "Ridgeway":
        for r in [x for x in receipts if x["inspection_required"] == "Y"] + rng.sample(receipts, min(40, len(receipts))):
            res = "Accept" if rng.chance(0.9) else "Reject"
            inspections.append({"inspection_id": f"INS-{len(inspections) + 1:05d}", "type": "Receiving", "reference": r["receipt_id"], "item_id": next(l["item_id"] for l in receipt_lines if l["receipt_id"] == r["receipt_id"]), "inspection_date": iso(date.fromisoformat(r["receipt_date"]) + timedelta(days=rng.int(0, 2))),
                                "inspector_id": rng.choice(qa)["employee_id"], "sample_size": rng.choice([2, 5, 8, 13]), "defects_found": 0 if res == "Accept" else rng.int(1, 4), "result": res, "plan": "AQL 1.0 Level II"})
        for w in [x for x in work_orders if x["status"] == "Closed"][:80]:
            res = "Accept" if rng.chance(0.92) else "Reject"
            inspections.append({"inspection_id": f"INS-{len(inspections) + 1:05d}", "type": "Final", "reference": w["work_order_id"], "item_id": w["item_id"], "inspection_date": iso(date.fromisoformat(w["due_date"]) if date.fromisoformat(w["due_date"]) <= TODAY else TODAY), "inspector_id": rng.choice(qa)["employee_id"], "sample_size": min(w["order_qty"], 5), "defects_found": 0 if res == "Accept" else rng.int(1, 3), "result": res, "plan": "100% dimensional on criticals"})
        for ins in [i for i in inspections if i["result"] == "Reject"]:
            opened = date.fromisoformat(ins["inspection_date"])
            closed = opened + timedelta(days=rng.int(5, 40))
            status = "Closed" if closed < TODAY and rng.chance(0.8) else "Open"
            ncrs.append({"ncr_id": f"NCR-{len(ncrs) + 1:04d}", "opened_date": iso(opened), "source": ins["type"], "reference": ins["reference"], "item_id": ins["item_id"], "supplier_id": next((r["supplier_id"] for r in receipts if r["receipt_id"] == ins["reference"]), ""), "qty_affected": rng.int(1, 40),
                         "defect_code": rng.choice(["DIM-OOT", "SURFACE", "WRONG-PART", "MISSING-CERT", "DAMAGE", "THREAD"]), "description": rng.choice(["Bore diameter oversize 0.003in", "Rust on incoming bar stock", "Wrong revision received", "No material cert with shipment", "Chipped teeth on gear", "Thread gauge no-go accepted"]),
                         "disposition": rng.choice(["Return to vendor", "Rework", "Scrap", "Use-as-is", "Pending MRB"]), "owner_id": rng.choice(qa)["employee_id"], "status": status, "closed_date": iso(closed) if status == "Closed" else "", "capa_required": "Y" if rng.chance(0.35) else "N"})
        for n in [x for x in ncrs if x["capa_required"] == "Y"]:
            capas.append({"capa_id": f"CAPA-{len(capas) + 1:03d}", "ncr_id": n["ncr_id"], "opened_date": n["opened_date"], "root_cause": rng.choice(["Tool wear not tracked", "Supplier process change not communicated", "Operator training gap", "Drawing revision control", "Packaging inadequate"]),
                          "corrective_action": rng.choice(["Add tool-life counter", "Require PPAP on process change", "Retrain and re-certify operator", "Lock BOM revisions in ERP", "Change to foam-lined cartons"]), "owner_id": n["owner_id"], "due_date": iso(date.fromisoformat(n["opened_date"]) + timedelta(days=45)),
                          "effectiveness_check": rng.choice(["Pending", "Verified", "Verified", ""]), "status": "Closed" if n["status"] == "Closed" and rng.chance(0.7) else "Open"})
        if is_mfr and ncrs:
            stale = next((n for n in ncrs if n["status"] == "Open" and (TODAY - date.fromisoformat(n["opened_date"])).days > 45), None)
            if stale is None:
                stale = ncrs[0]
                stale["status"] = "Open"
                stale["closed_date"] = ""
                stale["opened_date"] = iso(TODAY - timedelta(days=71))
            stale["capa_required"] = "Y"
            key.add("IND-QA-01", "quality", [short], "NCR open 45+ days with CAPA required but no CAPA record", f"{stale['ncr_id']} opened {stale['opened_date']} ({stale['defect_code']}) is still open and flagged capa_required=Y; no matching row in corrective_actions.",
                    ["09_quality/nonconformance_reports.csv", "09_quality/corrective_actions.csv"], "Open CAPA; escalate to supplier if receiving-related.")
            capas = [c for c in capas if c["ncr_id"] != stale["ncr_id"]]
        for w in [x for x in work_orders if x["status"] == "Closed"][:60]:
            cocs.append({"coc_id": f"COC-{len(cocs) + 1:05d}", "work_order_id": w["work_order_id"], "item_id": w["item_id"], "revision": w["item_revision"], "qty": w["order_qty"], "issue_date": w["due_date"], "customer_so": w["demand_source"], "signed_by": qa[0]["employee_id"], "material_certs_attached": "Y" if rng.chance(0.9) else "N", "file": f"COC-{w['work_order_id']}.pdf"})
        for n in range(1, 26 if is_mfr else 9):
            last = rng.date_between(date(2025, 3, 1), TODAY)
            calibrations.append({"gauge_id": f"G-{n:03d}", "description": rng.choice(["Digital caliper 0-6in", "Micrometer 1-2in", "Bore gauge set", "Thread plug gauge 1/2-13", "Surface plate", "Torque wrench 20-100 ft-lb", "CMM probe", "Height gauge 12in"]), "location": rng.choice(["QC Lab", "CNC1", "CNC2", "Assembly", "Receiving"]),
                                 "calibration_interval_months": rng.choice([6, 12, 12]), "last_calibration_date": iso(last), "next_due_date": iso(last + timedelta(days=365)), "calibrated_by": rng.choice(["Internal", "Transcat", "Cross Precision"]), "status": "Overdue" if last + timedelta(days=365) < TODAY else "In Cal"})

    # ---------------- maintenance ----------------
    equipment: list[Row] = []
    maint_wos: list[Row] = []
    pm_sched: list[Row] = []
    if short != "Ridgeway":
        eq_names = ["Haas ST-30 CNC Lathe", "Haas VF-4 VMC", "Okuma LB3000 Lathe", "Amada Band Saw", "Timesaver Deburr", "Overhead Crane 5T", "Toyota Forklift 8FGU25", "Toyota Forklift 8FGU25", "Air Compressor Quincy QGS-30", "Parts Washer", "Baldor Motor Test Stand", "Stretch Wrapper Lantech Q300", "Dock Leveler 1", "Dock Leveler 2", "Hyster Reach Truck"] if is_mfr else ["Toyota Forklift 8FGU25", "Crown Reach Truck", "Order Picker", "Stretch Wrapper", "Dock Leveler 1", "Dock Leveler 2", "Assembly Press 10T", "Air Compressor", "Delivery Truck 1 (Isuzu NPR)", "Delivery Truck 2 (Isuzu NPR)", "Delivery Truck 3 (Ford Transit)"]
        for n, nm in enumerate(eq_names, 1):
            equipment.append({"asset_id": f"A-{n:03d}", "description": nm, "location": rng.choice(["Machine Shop", "Assembly", "Warehouse", "Dock", "Yard"]), "manufacturer": nm.split(" ")[0], "serial_number": f"{rng.int(100000, 999999)}", "install_date": iso(rng.date_between(date(2008, 1, 1), date(2024, 12, 1))),
                              "criticality": rng.choice(["High", "Medium", "Low"]), "pm_interval_days": rng.choice([30, 90, 180, 365]), "status": "In Service" if rng.chance(0.93) else "Down"})
            pm_sched.append({"asset_id": equipment[-1]["asset_id"], "pm_task": rng.choice(["Lubricate & inspect", "Filter change", "Hydraulic fluid check", "Chain/belt inspection", "Safety inspection", "Annual load test"]), "interval_days": equipment[-1]["pm_interval_days"], "last_done": iso(rng.date_between(date(2025, 6, 1), TODAY)), "next_due": iso(TODAY + timedelta(days=rng.int(-30, 120))), "assigned_to": rng.choice(maint)["employee_id"]})
        for i in range(1, 70 if is_mfr else 30):
            a = rng.choice(equipment)
            od = rng.date_between(start, TODAY)
            typ = rng.choice(["PM", "PM", "Corrective", "Breakdown"])
            maint_wos.append({"maint_wo_id": f"MWO-{i:04d}", "asset_id": a["asset_id"], "type": typ, "opened_date": iso(od), "description": rng.choice(["Scheduled PM", "Coolant leak", "Spindle vibration", "Hydraulic hose burst", "Forklift brake inspection", "Dock leveler not lifting", "Replace worn belt", "Annual crane inspection"]),
                              "priority": rng.choice(["Low", "Normal", "Normal", "High", "Emergency"]) if typ != "PM" else "Normal", "assigned_to": rng.choice(maint)["employee_id"], "labor_hours": round(rng.money(0.5, 16, 0.5), 1), "parts_cost": round(rng.money(0, 2400), 2), "external_vendor": rng.choice(["", "", "", "Haas Factory Outlet", "Sunbelt Rentals", "Toyota Material Handling"]),
                              "downtime_hours": round(rng.money(0, 48, 0.5), 1) if typ == "Breakdown" else 0, "closed_date": iso(od + timedelta(days=rng.int(0, 14))) if od + timedelta(days=14) < TODAY else "", "status": "Closed" if od + timedelta(days=14) < TODAY else "Open"})

    # ---------------- fulfillment / shipping ----------------
    pick_lists: list[Row] = []
    shipments: list[Row] = []
    bols: list[Row] = []
    pods: list[Row] = []
    freight_inv: list[Row] = []
    for so, lines in lines_by_so.items():
        shipped = [l for l in lines if l["shipped_qty"]]
        if not shipped:
            continue
        o = so_by[so]
        sdate = date.fromisoformat(max(l["promised_date"] for l in shipped))
        if sdate > TODAY:
            sdate = TODAY
        ship_id = f"SH-{50000 + len(shipments) + 1}"
        for l in shipped:
            pick_lists.append({"pick_id": f"PK-{len(pick_lists) + 1:05d}", "so_number": so, "so_line": l["line"], "item_id": l["item_id"], "qty_to_pick": l["shipped_qty"], "qty_picked": l["shipped_qty"] - (1 if short == "Ridgeway" and rng.chance(0.04) else 0), "bin_location": f"{rng.choice('ABCDEF')}-{rng.int(1, 40):02d}-{rng.int(1, 5)}", "picker_id": rng.choice(whs)["employee_id"], "pick_date": iso(sdate - timedelta(days=rng.int(0, 1))), "shipment_id": ship_id})
        carrier = o["ship_via"]
        wt = round(sum(item_by_id[l["item_id"]]["weight_lb"] * l["shipped_qty"] for l in shipped), 1)
        mode = "Parcel" if ("UPS" in carrier or "FedEx" in carrier) and wt < 150 else ("LTL" if wt >= 150 else ("Pickup" if "Pickup" in carrier else "Company Truck"))
        cost = round((12 + wt * 0.42) * (1.0 if short == "Northfield" else (1.12 if short == "Keystone" else 1.31)), 2) if mode in ("Parcel", "LTL") else 0.0
        billed = round(cost * rng.money(1.0, 1.35), 2) if o["freight_terms"] == "Prepaid & Add" else 0.0
        shipments.append({"shipment_id": ship_id, "so_number": so, "customer_id": o["customer_id"], "ship_to_id": shipped[0]["ship_to_id"], "ship_date": iso(sdate), "carrier": carrier, "mode": mode, "service": rng.choice(["Ground", "Ground", "3 Day", "Next Day Air"]) if mode == "Parcel" else "",
                          "tracking_number": f"1Z{rng.int(10**9, 10**10 - 1)}" if "UPS" in carrier else (f"{rng.int(10**11, 10**12 - 1)}" if "FedEx" in carrier else f"PRO{rng.int(10**8, 10**9 - 1)}" if mode == "LTL" else ""), "cartons": max(1, int(wt // 40) + 1), "weight_lb": wt,
                          "freight_cost": cost, "freight_billed_to_customer": billed, "shipped_by": rng.choice(whs)["employee_id"], "packing_slip": f"PS-{ship_id}.pdf", "status": "Delivered" if sdate < TODAY - timedelta(days=5) else "In Transit"})
        if mode == "LTL":
            bols.append({"bol_number": f"BOL-{ship_id}", "shipment_id": ship_id, "carrier": carrier, "ship_date": iso(sdate), "shipper": profile["name"], "consignee": next(s["name"] for s in ship_tos if s["ship_to_id"] == shipped[0]["ship_to_id"]), "nmfc_class": rng.choice(["50", "55", "60", "70", "85"]), "pallets": max(1, int(wt // 800) + 1), "weight_lb": wt, "freight_terms": o["freight_terms"], "hazmat": "N", "signed": "Y"})
            freight_inv.append({"freight_invoice_id": f"FI-{len(freight_inv) + 1:05d}", "carrier": carrier, "shipment_id": ship_id, "invoice_date": iso(sdate + timedelta(days=rng.int(5, 20))), "quoted_amount": cost, "billed_amount": round(cost * rng.money(0.98, 1.22), 2), "accessorials": rng.choice(["", "", "Liftgate", "Reweigh", "Residential", "Redelivery"]), "audit_status": rng.choice(["Approved", "Approved", "Disputed", "Pending"])})
        if shipments[-1]["status"] == "Delivered" and rng.chance(0.8):
            pods.append({"shipment_id": ship_id, "delivered_date": iso(sdate + timedelta(days=rng.int(1, 6))), "signed_by": rng.person().split()[0][0] + ". " + rng.person().split()[1], "exceptions": rng.choice(["", "", "", "1 carton damaged", "Short 1 carton"]), "source": rng.choice(["Carrier API", "Carrier API", "Scanned POD", "Driver app"])})

    # ---------------- AR ----------------
    cust_invoices: list[Row] = []
    cust_invoice_lines: list[Row] = []
    payments: list[Row] = []
    disputes: list[Row] = []
    credit_holds: list[Row] = []
    unbilled = None
    for sh in shipments:
        o = so_by[sh["so_number"]]
        lines = [l for l in lines_by_so[sh["so_number"]] if l["shipped_qty"]]
        if short == "Keystone" and unbilled is None and sh["ship_date"] < iso(TODAY - timedelta(days=40)) and o["order_total"] > 4000:
            unbilled = sh
            continue
        idate = date.fromisoformat(sh["ship_date"])
        inv = f"INV-{60000 + len(cust_invoices) + 1}" if short != "Ridgeway" else f"{rng.int(20000, 29999)}"
        merch = round(sum(l["extended_price"] for l in lines), 2)
        tax = 0.0 if next(c for c in customers if c["customer_id"] == o["customer_id"])["tax_exempt"] == "Y" else round(merch * 0.0725, 2)
        total = round(merch + sh["freight_billed_to_customer"] + tax, 2)
        days = int(o["payment_terms"].split()[-1]) if o["payment_terms"].split()[-1].isdigit() else 0
        due = idate + timedelta(days=days)
        age = (TODAY - due).days
        paid = idate < TODAY - timedelta(days=rng.int(25, 75))
        cust_invoices.append({"invoice_number": inv, "customer_id": o["customer_id"], "so_number": sh["so_number"], "shipment_id": sh["shipment_id"], "customer_po_number": o["customer_po_number"], "invoice_date": iso(idate), "due_date": iso(due), "payment_terms": o["payment_terms"],
                              "merchandise_total": merch, "freight": sh["freight_billed_to_customer"], "sales_tax": tax, "invoice_total": total, "amount_paid": total if paid else 0.0, "balance": 0.0 if paid else total, "status": "Paid" if paid else ("Past Due" if age > 0 else "Open"), "delivery_method": rng.choice(["Email PDF", "Email PDF", "EDI 810", "Portal upload", "Mail"]), "gl_revenue_account": "4000" if is_mfr else "4010"})
        for l in lines:
            cust_invoice_lines.append({"invoice_number": inv, "line": l["line"], "item_id": l["item_id"], "qty": l["shipped_qty"], "unit_price": l["unit_price"], "extended": l["extended_price"], "unit_cost": l["unit_cost_at_order"], "margin": round(l["extended_price"] - l["unit_cost_at_order"] * l["shipped_qty"], 2)})
        if paid:
            short_pay = rng.chance(0.06)
            amt = total if not short_pay else round(total - rng.money(20, 400), 2)
            payments.append({"payment_id": f"{pre}-CR{len(payments) + 1:05d}", "customer_id": o["customer_id"], "payment_date": iso(min(TODAY, due + timedelta(days=rng.int(-10, 25)))), "method": rng.choice(["ACH", "ACH", "Check", "Card", "Wire"]), "reference": f"{rng.int(100000, 999999)}", "amount": amt, "applied_to_invoice": inv, "unapplied": 0.0, "remittance_source": rng.choice(["Lockbox", "Email remittance", "Bank feed", "Portal"])})
            if short_pay:
                cust_invoices[-1]["amount_paid"] = amt
                cust_invoices[-1]["balance"] = round(total - amt, 2)
                cust_invoices[-1]["status"] = "Short Paid"
                disputes.append({"dispute_id": f"{pre}-DSP{len(disputes) + 1:03d}", "invoice_number": inv, "customer_id": o["customer_id"], "opened_date": payments[-1]["payment_date"], "amount": round(total - amt, 2), "reason_code": rng.choice(["Pricing", "Freight", "Short shipment", "Damaged", "Tax charged", "Unknown deduction"]), "owner_id": finance[0]["employee_id"] if finance else "E002", "status": rng.choice(["Open", "Open", "Credit issued", "Collected"])})
    if unbilled:
        key.add("IND-O2C-01", "revenue_leakage", [short], "Order shipped and delivered but never invoiced", f"Shipment {unbilled['shipment_id']} for {unbilled['so_number']} (${so_by[unbilled['so_number']]['order_total']:,.2f}) shipped {unbilled['ship_date']} with POD on file; no customer invoice exists.",
                ["06_warehouse_fulfillment/shipments.csv", "06_warehouse_fulfillment/proof_of_delivery.csv", "11_billing_ar/customer_invoices.csv"], "Invoice immediately; add ship-not-invoiced control.")
    for o in orders:
        o["invoiced"] = "Y" if any(i["so_number"] == o["so_number"] for i in cust_invoices) else "N"
    ar_aging: list[Row] = []
    for ci in cust_invoices:
        if ci["balance"] > 0:
            age = (TODAY - date.fromisoformat(ci["due_date"])).days
            bucket = "Current" if age <= 0 else ("1-30" if age <= 30 else ("31-60" if age <= 60 else ("61-90" if age <= 90 else "90+")))
            ar_aging.append({"as_of_date": iso(TODAY), "customer_id": ci["customer_id"], "invoice_number": ci["invoice_number"], "invoice_date": ci["invoice_date"], "due_date": ci["due_date"], "days_past_due": max(0, age), "balance": ci["balance"], "aging_bucket": bucket, "collector_id": finance[-1]["employee_id"] if finance else "E002", "last_contact": iso(rng.date_between(date(2026, 1, 1), TODAY)) if age > 15 else "", "promise_to_pay": iso(TODAY + timedelta(days=rng.int(3, 20))) if age > 30 and rng.chance(0.5) else ""})
    if is_mfr:
        over90 = {}
        for a in ar_aging:
            if a["aging_bucket"] == "90+":
                over90.setdefault(a["customer_id"], 0)
                over90[a["customer_id"]] += a["balance"]
        if over90:
            worst = max(over90, key=over90.get)
            new_orders = [o for o in orders if o["customer_id"] == worst and o["order_date"] > iso(TODAY - timedelta(days=45)) and o["credit_hold"] == "N"]
            if new_orders:
                key.add("IND-AR-01", "credit_control", [short], "Customer with 90+ day balance still receiving new orders without credit hold", f"{worst} has ${over90[worst]:,.2f} over 90 days past due; {len(new_orders)} orders in the last 45 days (e.g. {new_orders[0]['so_number']}) were released with credit_hold=N.",
                        ["11_billing_ar/ar_aging.csv", "02_sales_quote_to_order/sales_orders.csv", "02_sales_quote_to_order/credit_checks.csv"], "Apply credit hold; review credit policy enforcement.")
    for c in customers:
        if c["credit_hold"] == "Y" or rng.chance(0.05):
            credit_holds.append({"customer_id": c["customer_id"], "hold_date": iso(rng.date_between(date(2025, 10, 1), TODAY)), "reason": rng.choice(["Over credit limit", "90+ past due", "NSF check", "Bankruptcy watch"]), "placed_by": finance[0]["employee_id"] if finance else "E001", "released_date": "" if rng.chance(0.6) else iso(rng.date_between(date(2026, 1, 1), TODAY)), "status": "Active" if rng.chance(0.6) else "Released"})

    # ---------------- returns / service ----------------
    rmas: list[Row] = []
    warranty: list[Row] = []
    field_service: list[Row] = []
    for ci in rng.sample(cust_invoices, min(28, len(cust_invoices))):
        l = rng.choice([x for x in cust_invoice_lines if x["invoice_number"] == ci["invoice_number"]])
        rd = date.fromisoformat(ci["invoice_date"]) + timedelta(days=rng.int(3, 60))
        if rd > TODAY:
            continue
        rmas.append({"rma_number": f"RMA-{len(rmas) + 1:04d}", "customer_id": ci["customer_id"], "invoice_number": ci["invoice_number"], "item_id": l["item_id"], "qty": min(l["qty"], rng.choice([1, 1, 2, 5])), "request_date": iso(rd), "reason_code": rng.choice(["Wrong part ordered", "Wrong part shipped", "Defective", "Damaged in transit", "Overshipment", "No longer needed"]),
                     "disposition": rng.choice(["Restock", "Restock w/ 15% fee", "Scrap", "Return to vendor", "Repair"]), "credit_memo": f"CM-{rng.int(7000, 7999)}" if rng.chance(0.7) else "", "credit_amount": round(l["unit_price"] * min(l["qty"], 2), 2), "received_date": iso(rd + timedelta(days=rng.int(2, 14))) if rng.chance(0.8) else "", "status": rng.choice(["Closed", "Closed", "Awaiting return", "Open"]), "approved_by": rng.choice(csrs)["employee_id"]})
    if manufactures:
        for r in rng.sample(rmas, min(6, len(rmas))):
            if r["reason_code"] == "Defective":
                warranty.append({"claim_id": f"WC-{len(warranty) + 1:03d}", "rma_number": r["rma_number"], "customer_id": r["customer_id"], "item_id": r["item_id"], "failure_mode": rng.choice(["Bearing seizure", "Seal leak", "Keyway wear", "Coupling insert failure", "Gear tooth fracture"]), "hours_in_service": rng.int(100, 6000), "warranty_period_months": 12, "in_warranty": rng.choice(["Y", "Y", "N"]), "claim_amount": r["credit_amount"], "root_cause": rng.choice(["Misalignment at install", "Material defect", "Overload", "Under investigation"]), "status": rng.choice(["Approved", "Denied", "Under review"])})
        for n in range(1, 14 if short == "Keystone" else 8):
            field_service.append({"service_order_id": f"FSO-{n:03d}", "customer_id": rng.choice(active_customers)["customer_id"], "request_date": iso(rng.date_between(start, TODAY)), "type": rng.choice(["Install support", "Laser alignment", "Vibration analysis", "Drive package commissioning", "Warranty inspection"]), "technician_id": rng.choice([e for e in employees if "Engineer" in e["title"]] or employees)["employee_id"], "hours": round(rng.money(2, 16, 0.5), 1), "billable": rng.choice(["Y", "Y", "N"]), "rate": 145.0, "parts_used": rng.choice(["", "", "Coupling insert x2", "Shim kit", "Bearing 6205"]), "status": rng.choice(["Complete", "Complete", "Scheduled"])})

    # ---------------- engineering / trade compliance ----------------
    ecos: list[Row] = []
    revisions: list[Row] = []
    trade: list[Row] = []
    customs: list[Row] = []
    if is_mfr:
        for n, fg in enumerate(rng.sample(make_items, min(14, len(make_items))), 1):
            ed = rng.date_between(date(2025, 2, 1), TODAY)
            ecos.append({"eco_number": f"ECO-{2500 + n}", "item_id": fg["item_id"], "from_revision": "A" if fg["item_revision"] in ("B", "C") else "-", "to_revision": fg["item_revision"], "reason": rng.choice(["Customer request", "Cost reduction", "Supplier obsolescence", "Quality improvement", "Tolerance relief"]),
                         "description": rng.choice(["Change insert material to urethane", "Add 0.5mm chamfer to bore", "Replace set screw with clamp collar", "Update surface finish callout to 63 Ra", "Consolidate two housing variants"]), "requested_by": rng.choice([e for e in employees if "Engineer" in e["title"]])["employee_id"], "approved_by": "E001", "effectivity": rng.choice(["Immediate", "Use up stock", "Next WO"]), "release_date": iso(ed), "bom_updated": "Y" if rng.chance(0.85) else "N", "status": "Released"})
            revisions.append({"item_id": fg["item_id"], "revision": fg["item_revision"], "eco_number": ecos[-1]["eco_number"], "effective_date": iso(ed), "drawing_number": f"D-{fg['item_id']}-{fg['item_revision']}", "drawing_file": f"D-{fg['item_id']}-{fg['item_revision']}.pdf", "cad_model": f"{fg['item_id']}.SLDPRT", "status": "Current"})
        if boms:
            e = eco_item
            ecos.append({"eco_number": "ECO-2599", "item_id": e["parent_item_id"], "from_revision": "B", "to_revision": "C", "reason": "Supplier obsolescence", "description": f"Component {e['component_item_id']} rev A obsolete; use rev B", "requested_by": ecos[0]["requested_by"], "approved_by": "E001", "effectivity": "Immediate", "release_date": "2026-01-15", "bom_updated": "N", "status": "Released"})
            key.add("IND-ENG-01", "engineering_change", [short], "Released ECO not reflected in BOM (BOM still calls obsolete component revision)", f"ECO-2599 released 2026-01-15 moves {e['parent_item_id']} to rev C and replaces component {e['component_item_id']} rev A; bills_of_material still lists rev A and work orders since then issued the old rev.",
                    ["13_engineering_compliance/engineering_change_orders.csv", "01_master_data/bills_of_material.csv", "08_manufacturing/work_orders.csv"], "Update BOM; check WIP/FG built since 2026-01-15 for containment.")
    if short != "Ridgeway":
        for it in [i for i in items if i["country_of_origin"] != "US"][:40]:
            trade.append({"item_id": it["item_id"], "hts_code": it["hts_code"] or rng.choice(["8482.10.5068", "7318.15.2095", "8483.30.8090"]), "country_of_origin": it["country_of_origin"], "duty_rate_pct": rng.choice([0, 2.5, 5.7, 9.0]), "section_301_applicable": "Y" if it["country_of_origin"] == "CN" else "N", "eccn": "EAR99", "usmca_qualified": "Y" if it["country_of_origin"] == "MX" and rng.chance(0.7) else "N", "classified_by": rng.choice(buyers or employees)["employee_id"], "classification_date": iso(rng.date_between(date(2024, 1, 1), TODAY))})
        for n in range(1, 12):
            customs.append({"entry_number": f"{rng.int(100, 999)}-{rng.int(1000000, 9999999)}-{rng.int(0, 9)}", "entry_date": iso(rng.date_between(start, TODAY)), "broker": rng.choice(["Expeditors", "C.H. Robinson", "Livingston"]), "supplier_id": rng.choice(suppliers)["supplier_id"], "po_number": rng.choice(pos)["po_number"], "country_of_origin": rng.choice(["CN", "TW", "DE", "IT", "IN"]), "entered_value": round(rng.money(2000, 60000), 2), "duty_paid": round(rng.money(0, 5000), 2), "mpf_hmf": round(rng.money(30, 600), 2), "brokerage_fee": 125.0, "isf_filed": "Y", "documents": "commercial_invoice.pdf;packing_list.pdf;7501.pdf"})

    # ---------------- finance ----------------
    coa = [{"account_number": a, "account_name": b, "account_type": c} for a, b, c in gl_chart("industrial")]
    gl: list[Row] = []
    jn = 0

    def je(d: date, acct: str, debit: float, credit: float, memo: str, src: str, ref: str):
        nonlocal jn
        jn += 1
        gl.append({"journal_id": f"JE-{jn:06d}", "posting_date": iso(d), "period": d.strftime("%Y-%m"), "account_number": acct, "account_name": next(a["account_name"] for a in coa if a["account_number"] == acct), "debit": round(debit, 2), "credit": round(credit, 2), "memo": memo, "source_module": src, "reference": ref})

    for ci in cust_invoices:
        d = date.fromisoformat(ci["invoice_date"])
        je(d, "1100", ci["invoice_total"], 0, f"Invoice {ci['invoice_number']}", "AR", ci["invoice_number"])
        je(d, ci["gl_revenue_account"], 0, ci["merchandise_total"], f"Invoice {ci['invoice_number']}", "AR", ci["invoice_number"])
        if ci["freight"]:
            je(d, "4100", 0, ci["freight"], "Freight billed", "AR", ci["invoice_number"])
        if ci["sales_tax"]:
            je(d, "2100", 0, ci["sales_tax"], "Sales tax payable", "AR", ci["invoice_number"])
        cogs = round(sum(l["unit_cost"] * l["qty"] for l in cust_invoice_lines if l["invoice_number"] == ci["invoice_number"]), 2)
        je(d, "5000", cogs, 0, "COGS", "AR", ci["invoice_number"])
        je(d, "1220" if is_mfr else "1230", 0, cogs, "Inventory relief", "AR", ci["invoice_number"])
    for p in payments:
        je(date.fromisoformat(p["payment_date"]), "1000", p["amount"], 0, "Customer payment", "AR", p["payment_id"])
        je(date.fromisoformat(p["payment_date"]), "1100", 0, p["amount"], "Customer payment", "AR", p["payment_id"])
    for si in sup_invoices:
        d = date.fromisoformat(si["invoice_date"])
        je(d, "2050", si["merchandise_total"], 0, f"Supplier inv {si['supplier_invoice_number']}", "AP", si["supplier_invoice_number"])
        if si["freight"]:
            je(d, "5300", si["freight"], 0, "Freight in", "AP", si["supplier_invoice_number"])
        je(d, "2000", 0, si["invoice_total"], f"Supplier inv {si['supplier_invoice_number']}", "AP", si["supplier_invoice_number"])
        if si["sales_tax"]:
            je(d, "6700", si["sales_tax"], 0, "Sales tax on purchase", "AP", si["supplier_invoice_number"])
    for r in receipts:
        val = round(sum(l["received_qty"] * item_by_id[l["item_id"]]["unit_cost"] for l in receipt_lines if l["receipt_id"] == r["receipt_id"] and l["item_id"] in item_by_id), 2)
        je(date.fromisoformat(r["receipt_date"]), "1200" if is_mfr else "1230", val, 0, f"PO receipt {r['po_number']}", "INV", r["receipt_id"])
        je(date.fromisoformat(r["receipt_date"]), "2050", 0, val, f"PO receipt {r['po_number']}", "INV", r["receipt_id"])
    # non-inventory vendors + AP + software + payroll + rent
    corp_vendors: list[Row] = []
    corp_ap: list[Row] = []
    for k, v in SHARED_VENDORS.items():
        if short in v["variants"]:
            corp_vendors.append({"vendor_id": f"{pre}-CV{len(corp_vendors) + 1:03d}", "vendor_name": v["variants"][short], "category": v["category"], "gl_account": {"Records Storage / Shredding": "6700", "MRO Supplies": "6700", "Uniforms / Facility Services": "6700", "Office Supplies": "6700", "Waste / Recycling": "6300", "Parcel Freight": "5300", "Equipment Rental": "6700"}[v["category"]], "payment_terms": "Net 30", "tax_id": rng.fein(), "w9_on_file": "Y", "shared_key": k})
    landlord = f"{hq_city} Industrial Park LLC"
    corp_vendors.append({"vendor_id": f"{pre}-CV{len(corp_vendors) + 1:03d}", "vendor_name": landlord, "category": "Rent", "gl_account": "6300", "payment_terms": "Due on 1st", "tax_id": rng.fein(), "w9_on_file": "Y", "shared_key": ""})
    for tv in TRAP_VENDORS:
        if tv["company"] == short and tv["name"] not in sup_id:
            corp_vendors.append({"vendor_id": f"{pre}-CV{len(corp_vendors) + 1:03d}", "vendor_name": tv["name"], "category": tv["category"], "gl_account": "6500", "payment_terms": "Net 30", "tax_id": rng.fein(), "w9_on_file": "Y", "shared_key": ""})
    for cv in corp_vendors:
        for me in month_ends(start, TODAY):
            if cv["category"] == "Rent":
                amt = {"Northfield": 38500, "Keystone": 14200, "Ridgeway": 7800}[short]
            elif cv["category"] == "Parcel Freight":
                amt = round(sum(s["freight_cost"] for s in shipments if s["mode"] == "Parcel" and cv["vendor_name"].split()[0].upper()[:3] in s["carrier"].upper() and s["ship_date"][:7] == me.strftime("%Y-%m")), 2)
                if not amt:
                    continue
            elif rng.chance(0.75):
                amt = round(rng.money(180, 4200), 2)
            else:
                continue
            corp_ap.append({"ap_invoice_id": f"{pre}-AP{len(corp_ap) + 1:05d}", "vendor_id": cv["vendor_id"], "vendor_name": cv["vendor_name"], "invoice_number": f"{rng.int(100000, 9999999)}", "invoice_date": iso(me - timedelta(days=rng.int(0, 20))), "due_date": iso(me + timedelta(days=rng.int(10, 30))), "amount": amt, "gl_account": cv["gl_account"], "category": cv["category"], "status": "Paid" if me < TODAY - timedelta(days=30) else "Open", "approved_by": finance[0]["employee_id"] if finance else "E001"})
            je(me, cv["gl_account"], amt, 0, cv["vendor_name"], "AP", corp_ap[-1]["ap_invoice_id"])
            je(me, "2000", 0, amt, cv["vendor_name"], "AP", corp_ap[-1]["ap_invoice_id"])
    stack = {"Northfield": ["Epicor Kinetic", "SolidWorks Standard", "MasterControl", "Microsoft 365 Business Standard", "ADP Workforce Now", "UPS WorldShip", "Salesforce Sales Cloud", "Bill.com", "Box Business", "Zoom Workplace", "RingCentral MVP", "KnowBe4"],
             "Keystone": ["NetSuite", "Autodesk Inventor", "uniPoint QMS", "Microsoft 365 Business Standard", "Paycom", "ShipStation", "HubSpot Sales Hub", "Expensify", "Dropbox Business", "Zoom Workplace", "8x8 Work"],
             "Ridgeway": ["QuickBooks Desktop Enterprise", "Fishbowl Inventory", "Google Workspace Business", "Gusto", "UPS WorldShip", "FedEx Ship Manager", "Dropbox Business", "Zoom Workplace"]}[short]
    seats = {"Northfield": 142, "Keystone": 63, "Ridgeway": 27}[short]
    software: list[Row] = []
    for s in stack:
        vendor, func, per = SOFTWARE[s]
        n_seats = seats if "365" in s or "Workspace" in s or "KnowBe4" in s else (rng.int(3, 12) if per > 1000 else rng.int(5, seats))
        if s in ("Epicor Kinetic", "NetSuite"):
            n_seats = {"Northfield": 45, "Keystone": 30}[short]
        annual = per * n_seats if per else 0
        if s in ("MasterControl", "uniPoint QMS", "Sage Intacct"):
            annual = per
        software.append({"product": s, "vendor": vendor, "function": func, "seats": n_seats, "annual_cost": annual, "renewal_date": iso(rng.date_between(date(2026, 4, 1), date(2027, 3, 31))), "owner_id": rng.choice(employees)["employee_id"], "contract_term": rng.choice(["Annual", "Annual", "Monthly", "3-year"]), "auto_renew": rng.choice(["Y", "Y", "N"]), "payment_method": rng.choice(["ACH", "Corporate card", "Invoice"]), "gl_account": "6400"})
        for me in month_ends(start, TODAY):
            if annual:
                je(me, "6400", round(annual / 12, 2), 0, s, "AP", f"SW-{s[:6].upper().replace(' ', '')}")
                je(me, "2000", 0, round(annual / 12, 2), s, "AP", f"SW-{s[:6].upper().replace(' ', '')}")
    if short == "Keystone":
        software.append({"product": "Zoom Workplace", "vendor": "Zoom", "function": "Video Conferencing", "seats": 8, "annual_cost": 8 * 180, "renewal_date": "2026-09-01", "owner_id": sales_reps[0]["employee_id"], "contract_term": "Annual", "auto_renew": "Y", "payment_method": "Corporate card", "gl_account": "6800"})
        key.add("IND-SW-01", "software_overlap", [short], "Two separate Zoom subscriptions (one expensed to T&E)", "Keystone pays for Zoom Workplace twice: an IT-owned subscription on GL 6400 and a sales-team one on GL 6800 (corporate card).",
                ["14_finance_gl/software_subscriptions.csv", "14_finance_gl/general_ledger.csv"], "Consolidate to one account.")
    payroll: list[Row] = []
    for me in month_ends(start, TODAY):
        for e in employees:
            if e["status"] == "Terminated" and e["termination_date"] and e["termination_date"] < iso(me):
                continue
            if e["hire_date"] > iso(me):
                continue
            gross = round(e["pay_rate"] * 173.3 * rng.money(0.95, 1.12) if e["pay_type"] == "Hourly" else e["pay_rate"] / 12, 2)
            ot = round(e["pay_rate"] * 1.5 * rng.int(0, 22), 2) if e["pay_type"] == "Hourly" else 0
            payroll.append({"pay_period_end": iso(me), "employee_id": e["employee_id"], "department": e["department"], "regular_hours": 173.3 if e["pay_type"] == "Hourly" else "", "overtime_hours": round(ot / (e["pay_rate"] * 1.5), 1) if ot else 0, "gross_pay": round(gross + ot, 2), "overtime_pay": ot, "federal_tax": round((gross + ot) * 0.12, 2), "state_tax": round((gross + ot) * {"IL": 0.0495, "PA": 0.0307, "TN": 0.0}[hq_state], 2), "fica": round((gross + ot) * 0.0765, 2), "401k": round((gross + ot) * rng.choice([0, 0.03, 0.05, 0.06]), 2), "health_deduction": rng.choice([0, 142.5, 285.0, 410.0]), "net_pay": ""})
            payroll[-1]["net_pay"] = round(payroll[-1]["gross_pay"] - payroll[-1]["federal_tax"] - payroll[-1]["state_tax"] - payroll[-1]["fica"] - payroll[-1]["401k"] - payroll[-1]["health_deduction"], 2)
        tot = round(sum(p["gross_pay"] for p in payroll if p["pay_period_end"] == iso(me)), 2)
        je(me, "6000", tot, 0, "Monthly payroll", "PR", f"PR-{me.strftime('%Y%m')}")
        je(me, "2300", 0, tot, "Monthly payroll", "PR", f"PR-{me.strftime('%Y%m')}")
    gl.sort(key=lambda r: (r["posting_date"], r["journal_id"]))
    inv_valuation = [{"as_of_date": iso(me), "item_class": c["class_code"], "gl_account": c["gl_inventory_account"], "qty_on_hand": sum(b["on_hand_qty"] for b in balances if item_by_id[b["item_id"]]["item_class"] == c["class_code"]), "extended_cost": round(sum(b["extended_value"] for b in balances if item_by_id[b["item_id"]]["item_class"] == c["class_code"]) * rng.money(0.85, 1.1), 2), "costing_method": "Standard" if is_mfr else ("Average" if short == "Keystone" else "FIFO (QB)"), "reserve_for_obsolescence": round(rng.money(0, 12000), 2)}
                     for me in month_ends(date(2025, 12, 31), TODAY) for c in classes if c["class_code"] != "MRO"]
    cost_roll = [{"item_id": fg["item_id"], "revision": fg["item_revision"], "roll_date": "2026-01-01", "material_cost": round(fg["unit_cost"] * 0.55, 2), "labor_cost": round(fg["unit_cost"] * 0.25, 2), "overhead_cost": round(fg["unit_cost"] * 0.20, 2), "standard_cost": fg["unit_cost"], "previous_standard": round(fg["unit_cost"] * rng.money(0.9, 1.02), 2), "approved_by": finance[0]["employee_id"] if finance else "E001"} for fg in make_items]
    variances = [{"period": me.strftime("%Y-%m"), "variance_type": vt, "amount": round(rng.money(-8000, 12000), 2), "gl_account": "5500" if vt == "Purchase Price Variance" else "5200", "explanation": rng.choice(["", "", "Steel surcharge", "Overtime on rush order", "Scrap on WO batch", "Supplier price increase"])}
                 for me in month_ends(start, TODAY) for vt in (["Purchase Price Variance", "Labor Efficiency", "Overhead Absorption", "Material Usage"] if is_mfr else ["Purchase Price Variance"])]
    budget = [{"period": me.strftime("%Y-%m"), "account_number": a["account_number"], "account_name": a["account_name"], "budget": (b := round(rng.money(5000, 400000, 100), 2)), "actual": round(b * rng.money(0.82, 1.18), 2), "variance": ""} for me in month_ends(date(2026, 1, 1), TODAY) for a in coa if a["account_type"] in ("Revenue", "Expense")]
    for b in budget:
        b["variance"] = round(b["actual"] - b["budget"], 2)
    bank: list[Row] = []
    bal_ = 850000.0 if is_mfr else (410000.0 if short == "Keystone" else 96000.0)
    for p in sorted(payments, key=lambda x: x["payment_date"]):
        bal_ += p["amount"]
        bank.append({"date": p["payment_date"], "description": f"{p['method']} DEPOSIT {p['reference']}", "debit": "", "credit": p["amount"], "balance": round(bal_, 2)})
    for p in sorted(ap_payments, key=lambda x: x["payment_date"]):
        bal_ -= p["amount"]
        bank.append({"date": p["payment_date"], "description": f"{p['method']} {p['check_number'] or 'EFT'} {next(s['supplier_name'] for s in suppliers if s['supplier_id'] == p['supplier_id']).upper()[:22]}", "debit": p["amount"], "credit": "", "balance": round(bal_, 2)})
    for me in month_ends(start, TODAY):
        tot = round(sum(p["net_pay"] for p in payroll if p["pay_period_end"] == iso(me)), 2)
        bal_ -= tot
        bank.append({"date": iso(me), "description": f"{'ADP' if short == 'Northfield' else ('PAYCOM' if short == 'Keystone' else 'GUSTO')} PAYROLL", "debit": tot, "credit": "", "balance": round(bal_, 2)})
    bank.sort(key=lambda r: r["date"])

    # ---------------- HR / EHS ----------------
    pto = [{"employee_id": e["employee_id"], "as_of_date": iso(TODAY), "pto_accrued_hours": round(rng.money(40, 160, 0.5), 1), "pto_used_hours": round(rng.money(0, 100, 0.5), 1), "sick_accrued_hours": 40.0, "sick_used_hours": round(rng.money(0, 40, 0.5), 1), "carryover_hours": round(rng.money(0, 40, 0.5), 1)} for e in employees if e["status"] == "Active"]
    training: list[Row] = []
    courses = ["Forklift Certification", "Lockout/Tagout", "Hazard Communication", "Crane & Rigging", "GD&T Fundamentals", "ISO 9001 Internal Auditor", "First Aid/CPR", "Fall Protection", "Hearing Conservation", "DOT Medical Card"]
    for e in employees:
        for c in rng.sample(courses, rng.int(1, 4)):
            comp = rng.date_between(date(2023, 1, 1), TODAY)
            exp = comp + timedelta(days=365 * (3 if "Forklift" in c else 2))
            training.append({"employee_id": e["employee_id"], "course": c, "completed_date": iso(comp), "expiration_date": iso(exp), "provider": rng.choice(["Internal", "OSHA Outreach", "Toyota Material Handling", "Red Cross", "ASQ"]), "status": "Expired" if exp < TODAY else "Current", "certificate_file": f"{e['employee_id']}_{c.split()[0].lower()}.pdf" if rng.chance(0.7) else ""})
    if short != "Ridgeway":
        fl_drivers = [t for t in training if t["course"] == "Forklift Certification" and t["status"] == "Expired"]
        if fl_drivers:
            key.add(f"IND-EHS-{'01' if is_mfr else '02'}", "compliance", [short], "Employees operating forklifts with expired certification", f"{len(fl_drivers)} employees have expired Forklift Certification but appear as putaway/shipping users in receipt_lines / pick_lists after expiry (e.g. {fl_drivers[0]['employee_id']}, expired {fl_drivers[0]['expiration_date']}).",
                    ["15_hr_ehs/training_certifications.csv", "04_receiving_ap/receipt_lines.csv", "06_warehouse_fulfillment/pick_lists.csv"], "Schedule recertification; OSHA 1910.178 exposure.")
    incidents: list[Row] = []
    for n in range(1, (18 if is_mfr else 7)):
        d = rng.date_between(start, TODAY)
        typ = rng.choice(["Near miss", "Near miss", "First aid", "Recordable", "Property damage"])
        incidents.append({"incident_id": f"EHS-{n:03d}", "date": iso(d), "type": typ, "employee_id": rng.choice(ops)["employee_id"], "location": rng.choice(["Machine Shop", "Warehouse", "Dock", "Assembly", "Yard"]), "description": rng.choice(["Forklift near-miss at aisle intersection", "Laceration from burr on housing", "Pallet fell from rack", "Slip on coolant spill", "Strain lifting motor", "Chip in eye - no glasses"]), "days_away": rng.int(0, 5) if typ == "Recordable" else 0, "osha_300_logged": "Y" if typ == "Recordable" else "N", "root_cause": rng.choice(["Housekeeping", "PPE not worn", "Procedure not followed", "Equipment", "Training"]), "corrective_action": rng.choice(["Toolbox talk", "Mirror installed", "Re-train", "Guard added", "Spill kit relocated"]), "status": "Closed" if d < TODAY - timedelta(days=20) else "Open"})
    safety_obs = [{"observation_id": f"SO-{n:03d}", "date": iso(rng.date_between(start, TODAY)), "observer_id": rng.choice(employees)["employee_id"], "area": rng.choice(["Machine Shop", "Warehouse", "Dock", "Assembly"]), "category": rng.choice(["Safe", "Safe", "At-Risk"]), "note": rng.choice(["Eyewear worn", "Blocked fire extinguisher", "Good housekeeping", "Speeding forklift", "Missing guard", "Proper lifting"])} for n in range(1, 40 if is_mfr else 15)]

    # ---------------- documents & emails ----------------
    docs: list[Row] = []
    for po in pos[:60]:
        docs.append({"document_id": f"DOC-{len(docs) + 1:05d}", "type": "Purchase Order", "reference": po["po_number"], "file_name": f"{po['po_number']}.pdf", "folder": f"Purchasing/{po['po_date'][:4]}/{po['supplier_name'][:12]}", "uploaded": po["po_date"], "uploaded_by": po["buyer_id"], "source": "ERP print" if short != "Ridgeway" else "QB print"})
    for ci in cust_invoices[:80]:
        docs.append({"document_id": f"DOC-{len(docs) + 1:05d}", "type": "Customer Invoice", "reference": ci["invoice_number"], "file_name": f"{ci['invoice_number']}.pdf", "folder": f"AR/{ci['invoice_date'][:7]}", "uploaded": ci["invoice_date"], "uploaded_by": finance[0]["employee_id"] if finance else "E002", "source": "ERP print" if short != "Ridgeway" else "QB print"})
    for sh in shipments[:60]:
        docs.append({"document_id": f"DOC-{len(docs) + 1:05d}", "type": "Packing Slip", "reference": sh["shipment_id"], "file_name": sh["packing_slip"], "folder": f"Shipping/{sh['ship_date'][:7]}", "uploaded": sh["ship_date"], "uploaded_by": sh["shipped_by"], "source": "Scanned"})
    for r in receipts[:40]:
        docs.append({"document_id": f"DOC-{len(docs) + 1:05d}", "type": "Supplier Packing Slip", "reference": r["receipt_id"], "file_name": f"{r['packing_slip_number']}.pdf", "folder": f"Receiving/{r['receipt_date'][:7]}", "uploaded": r["receipt_date"], "uploaded_by": r["received_by"], "source": "Scanned"})
    for c in cocs[:30]:
        docs.append({"document_id": f"DOC-{len(docs) + 1:05d}", "type": "Certificate of Conformance", "reference": c["work_order_id"], "file_name": c["file"], "folder": f"Quality/COC/{c['issue_date'][:4]}", "uploaded": c["issue_date"], "uploaded_by": c["signed_by"], "source": "Generated"})
    for sc in sup_contracts:
        docs.append({"document_id": f"DOC-{len(docs) + 1:05d}", "type": "Supplier Contract", "reference": sc["contract_id"], "file_name": sc["document"], "folder": "Purchasing/Contracts", "uploaded": sc["start_date"], "uploaded_by": buyers[0]["employee_id"] if buyers else "E001", "source": "Scanned"})

    # customer PO as structured doc (EDI-like / scanned PO json)
    sample_o = orders[5]
    sample_lines = lines_by_so[sample_o["so_number"]]
    cust = next(c for c in customers if c["customer_id"] == sample_o["customer_id"])
    po_doc = {"document_type": "Customer Purchase Order", "source": "Email attachment (PDF, OCR)", "customer_po_number": sample_o["customer_po_number"], "po_date": sample_o["order_date"], "buyer": cust["customer_name"],
              "ship_to": next(s for s in ship_tos if s["customer_id"] == cust["customer_id"])["name"], "payment_terms": cust["payment_terms"], "lines": [{"line": l["line"], "customer_part_number": f"CP-{rng.int(10000, 99999)}", "our_item_id": l["item_id"] if rng.chance(0.7) else "", "description": l["description"], "qty": l["ordered_qty"] + (2 if l["line"] == 1 and short != "Northfield" else 0), "unit_price": l["unit_price"], "requested_date": l["requested_date"]} for l in sample_lines],
              "notes": "Please reference our PO number on all packages and invoices."}
    em.add("16_documents_emails", f"customer_po_{sample_o['customer_po_number']}", [po_doc], fmt="json")
    if short != "Northfield":
        key.add(f"IND-O2C-{'02' if short == 'Keystone' else '03'}", "order_entry", [short], "Customer PO quantity does not match entered sales order", f"Customer PO {sample_o['customer_po_number']} line 1 shows qty {sample_lines[0]['ordered_qty'] + 2}; sales order {sample_o['so_number']} line 1 was entered as {sample_lines[0]['ordered_qty']}.",
                [f"16_documents_emails/customer_po_{sample_o['customer_po_number']}.json", "02_sales_quote_to_order/sales_order_lines.csv"], "Confirm with customer; correct SO before shipment.")
    # emails
    buyer_e = buyers[0] if buyers else employees[0]
    core_line = {"Northfield": "machined components", "Keystone": "bearings & drives", "Ridgeway": "fasteners"}[short]
    sup0 = suppliers[0]
    em.add_text("16_documents_emails", f"supplier_price_increase_{sup0['supplier_id']}", eml(sup_contacts[0]["email"], buyer_e["email"], f"Price adjustment effective April 1 - {sup0['supplier_name']}",
                f"Hi {buyer_e['full_name'].split()[0]},\n\nDue to continued increases in raw material and freight costs, {sup0['supplier_name']} will be implementing a 4.5% price adjustment across {sup0['category'].lower()} products effective April 1, 2026. Open POs received before that date will be honored at current pricing.\n\nUpdated price file attached (Price_File_Q2_2026.xlsx).\n\nRegards,\n{sup_contacts[0]['full_name']}\n{sup_contacts[0]['title']}, {sup0['supplier_name']}", datetime(2026, 3, 12, 9, 41)), ext="eml")
    csr0 = csrs[0]
    c_contact = next(x for x in contacts if x["customer_id"] == sample_o["customer_id"])
    em.add_text("16_documents_emails", f"customer_expedite_{sample_o['so_number']}", eml(c_contact["email"], csr0["email"], f"RE: PO {sample_o['customer_po_number']} - need ship date",
                f"{csr0['full_name'].split()[0]},\n\nOur line is down waiting on this. Can you confirm when PO {sample_o['customer_po_number']} ships? Original request date was {sample_lines[0]['requested_date']}. If you can't ship complete, ship what you have and backorder the rest.\n\nAlso - we're being told by corporate to consolidate MRO suppliers. Do you carry fasteners/safety supplies or just {core_line}?\n\nThanks,\n{c_contact['full_name']}\n{c_contact['title']}", datetime(2026, 3, 18, 14, 2), cc=sales_reps[0]["email"]), ext="eml")
    if short == "Ridgeway":
        em.add_text("16_documents_emails", "counter_sale_pricing_question", eml(email_for(employees[3]["full_name"], domain), email_for(employees[0]["full_name"], domain), "pricing for cardinal foods",
                    "Hey,\n\nCardinal Foods (the Cleveland TN plant) wants a quote on 1/2-13 gr8 bolts, 5000 pcs a month, plus 6205 bearings for their conveyor line - they said they buy the bearings from someone in PA right now (Keystone something?). Do we stock the SKF ones or just the import? What price do I give them - price level B or the contract sheet from last year (the one in the shared drive, 'Cardinal pricing FINAL v3.xlsx')? Not sure if that expired.\n\nAlso the QuickBooks item list has two entries for the 6205 - BRG-6205 and BRG-6205-IMP - which one is which??\n\nthx", datetime(2026, 3, 20, 16, 25)), ext="eml")
        em.add_text("16_documents_emails", "vmi_count_sheet_photo_transcription", "TRANSCRIBED FROM PHOTO OF PAPER COUNT SHEET (03/24/26) - customer site bins\n\nBIN 012  HHCS 1/2-13x2 G8   min 100 max 400   count: 60   -> refill 340\nBIN 013  HN 1/2-13 G8       min 200 max 600   count 410\nBIN 014  FW 1/2 SAE         min 200 max 600   count ~150?? (bag torn)\nBIN 020  6205 brg           min 6 max 24      count 4    -> refill (which one? SKF or import - cust wants SKF)\nBIN 021  nitrile L          min 5 max 20      count 2    refill\n\ntech: Danny   truck 2   left invoice copy w/ receiving", ext="txt")
        # QuickBooks-style exports
        qb_items = [{"Item": i["item_id"], "Description": i["description"], "Type": "Inventory Part", "Cost": i["unit_cost"], "Price": i["list_price"], "Sales Tax Code": "Non" if rng.chance(0.8) else "Tax", "Quantity On Hand": next((b["on_hand_qty"] for b in balances if b["item_id"] == i["item_id"]), ""), "Preferred Vendor": i["primary_supplier"], "Reorder Pt (Min)": i["reorder_point"], "Max": i["reorder_qty"], "MPN": i["manufacturer_part_number"], "U/M": i["stock_uom"]} for i in items]
        em.add("01_master_data", "QuickBooks_Item_List_export", qb_items)
        key.add("IND-DQ-02", "data_quality", [short], "Mislabeled 'Max' column actually holds reorder quantity", "In the QuickBooks item list export the 'Max' column is the reorder quantity (order-up-to is not tracked), and 'Reorder Pt (Min)' is the reorder point. Stock UoM mixes EA / BX / C (hundreds) without a consistent pack factor.",
                ["01_master_data/QuickBooks_Item_List_export.csv", "01_master_data/items.csv"], "Map Max->reorder_qty; normalize UoM before comparing prices per each.")

    # ---------------- workflow events ----------------
    events: list[Row] = []

    def ev(ts: str, wf: str, obj_type: str, obj_id: str, actor: str, action: str, prev: str, new: str, src: str, conf: float = 1.0, review: str = "N"):
        events.append({"event_id": f"EV-{len(events) + 1:06d}", "company_id": profile["slug"], "workflow_type": wf, "object_type": obj_type, "object_id": obj_id, "timestamp": ts if "T" in ts else f"{ts}T{rng.int(7, 17):02d}:{rng.int(0, 59):02d}:00", "actor": actor, "action": action, "previous_state": prev, "new_state": new, "source_document": src, "confidence": conf, "requires_review": review})

    for q in quotes:
        ev(q["quote_date"], "quote_to_order", "quote", q["quote_id"], q["sales_rep_id"], "quote_sent", "draft", "sent", f"{q['quote_id']}.pdf")
    for o in orders:
        ev(o["order_date"], "quote_to_order", "sales_order", o["so_number"], o["entered_by"], "order_entered", "", "open", f"customer_po_{o['customer_po_number']}", 0.9 if o["order_source"] in ("Email", "Phone") else 1.0, "Y" if o["credit_hold"] == "Y" else "N")
        if o["credit_hold"] == "Y":
            ev(o["order_date"], "credit_control", "sales_order", o["so_number"], finance[0]["employee_id"] if finance else "E001", "credit_hold_applied", "open", "credit_hold", "credit_checks")
    for sh in shipments:
        ev(sh["ship_date"], "fulfillment", "shipment", sh["shipment_id"], sh["shipped_by"], "shipment_confirmed", "picked", "shipped", sh["packing_slip"])
    for ci in cust_invoices:
        ev(ci["invoice_date"], "order_to_cash", "customer_invoice", ci["invoice_number"], finance[0]["employee_id"] if finance else "E002", "invoice_issued", "", "open", f"{ci['invoice_number']}.pdf")
    for p in payments:
        ev(p["payment_date"], "order_to_cash", "customer_invoice", p["applied_to_invoice"], "system", "payment_applied", "open", "paid", p["remittance_source"], 0.95 if p["remittance_source"] == "Email remittance" else 1.0)
    for po in pos:
        ev(po["po_date"], "procure_to_pay", "purchase_order", po["po_number"], po["buyer_id"], "po_issued", "draft", "open", f"{po['po_number']}.pdf")
    for r in receipts:
        ev(r["receipt_date"], "procure_to_pay", "receipt", r["receipt_id"], r["received_by"], "goods_received", "open", "received", r["packing_slip_number"], 0.85 if r["condition"] != "OK" else 1.0, "Y" if r["condition"] != "OK" else "N")
    for si in sup_invoices:
        ev(si["received_date"], "procure_to_pay", "supplier_invoice", si["supplier_invoice_number"], si["entered_by"], "invoice_matched" if si["match_status"] == "Matched" else "match_exception", "received", si["status"].lower(), si["supplier_invoice_number"], 1.0 if si["match_status"] == "Matched" else 0.7, "N" if si["match_status"] == "Matched" else "Y")
    for w in work_orders:
        ev(w["release_date"], "manufacturing", "work_order", w["work_order_id"], w["planner_id"], "wo_released", "planned", "released", "MRP")
        if w["status"] == "Closed":
            ev(w["due_date"], "manufacturing", "work_order", w["work_order_id"], qa[0]["employee_id"], "wo_closed", "in_process", "closed", f"COC-{w['work_order_id']}.pdf")
    for n in ncrs:
        ev(n["opened_date"], "quality", "ncr", n["ncr_id"], n["owner_id"], "ncr_opened", "", "open", n["reference"], 1.0, "Y")
    for r in rmas:
        ev(r["request_date"], "returns", "rma", r["rma_number"], r["approved_by"], "rma_approved", "requested", "approved", r["invoice_number"])
    events.sort(key=lambda e: e["timestamp"])

    # ---------------- company profile ----------------
    prof = {**{k: v for k, v in profile.items() if k not in ("seed",)}, "as_of_date": iso(TODAY), "hq": {"city": hq_city, "state": hq_state}, "business_model": "Make-to-order + make-to-stock manufacturer with distribution" if is_mfr else ("Distributor with light assembly" if short == "Keystone" else "Distributor / VMI supply house"),
            "systems": {"erp": profile["erp"], "cad": "SolidWorks" if is_mfr else ("Autodesk Inventor" if short == "Keystone" else "none"), "qms": "MasterControl" if is_mfr else ("uniPoint" if short == "Keystone" else "none (paper)"), "shipping": "UPS WorldShip" if short != "Keystone" else "ShipStation", "payroll": "ADP" if is_mfr else ("Paycom" if short == "Keystone" else "Gusto")},
            "employee_count": len(employees), "active_customers": len(active_customers), "active_items": len([i for i in items if i["status"] == "Active"]), "fiscal_year_end": "12-31", "costing_method": "Standard" if is_mfr else ("Average" if short == "Keystone" else "FIFO"),
            "data_quality_tier": tier, "data_quality_notes": {"high": "Complete ERP export; ISO dates; snake_case headers; strong referential integrity.", "medium": "Most datasets present; some Excel exports; Title Case headers; some mixed date formats; a few duplicate supplier/pricing issues.",
                                                              "low": "QuickBooks + Excel shop. Master data lives in one multi-sheet workbook; no perpetual inventory transactions, quality, maintenance, MRP or workflow logs. Mislabeled columns, mixed date/currency formats, duplicate rows, free-text notes."}[tier]}
    em.add_text("00_company", "company_profile", json.dumps(prof, indent=2), ext="json")
    sops = {"Northfield": "# Northfield Industrial Components — Operating Procedures (excerpt)\n\n1. **Quote-to-order.** RFQs logged in Epicor within 1 business day; quotes valid 30 days; engineering review for any non-catalog part.\n2. **Credit.** Orders over credit limit or with any 90+ balance route to Controller before release. Credit hold is a hard stop in the ERP.\n3. **Purchasing.** POs > $10k need President approval. Three-way match tolerance 2% / $50.\n4. **Receiving.** Raw material requires cert check and lot tag; inspection_required items go to QC hold bin.\n5. **Quality.** NCRs with capa_required must have a CAPA opened within 10 days. Gauges out of cal are quarantined.\n6. **Engineering changes.** ECO release must update BOM the same day (bom_updated = Y). Planner verifies before next WO release.\n7. **Month-end.** Inventory valuation, PPV and cycle-count adjustments posted by WD+3.\n",
            "Keystone": "# Keystone Bearing & Drive — How we do things\n\n- Inside sales enters orders in NetSuite from email/phone. EDI customers come in automatically.\n- Customer contract pricing is maintained in the price agreements list; check expiration before quoting (this is often missed).\n- Buyers may use either Grainger vendor record; we've been meaning to clean this up.\n- Warehouse ships from pick list; invoicing runs nightly from shipped orders. If a shipment is marked 'delivered' manually it can skip the invoice batch.\n- Assembly cell builds conveyor drive packages from a simple BOM; no routings or labor tracking beyond timesheets.\n- Quality: receiving inspection on motors/reducers only; NCRs tracked in uniPoint.\n",
            "Ridgeway": "# Ridgeway — notes for whoever takes over the office (Linda, Jan 2026)\n\n- Everything is in QuickBooks Desktop + the MASTER WORKBOOK on the shared drive. Don't rename the tabs, Fishbowl sync breaks.\n- Sales orders: SO number is the QB invoice number. Counter sales use the customer's PO in the SO field sometimes, sorry.\n- Customer 'Cardinal Foods' = Cardinal Foods Group (Cleveland TN plant). Their corporate is in Chicago.\n- VMI: techs count bins on paper, take a photo, and Danny types it into the VMI tab on Fridays.\n- We pay TN sales tax on some Fastenal/Grainger orders — Ray says we have a resale cert but nobody sent it to them.\n- No cycle counts, we do a full physical at year end.\n- Items: BRG-6205 is the SKF one, BRG-6205-IMP is the cheap import. Some customers will only take SKF.\n- 'Max' column in item list = how many we order, not a max.\n"}[short]
    em.add_text("00_company", "operating_procedures", sops, ext="md")

    if short == "Ridgeway":
        for o in orders:  # some orders were keyed with customer PO in the SO field
            if rng.chance(0.07):
                o["so_number"], o["customer_po_number"] = o["customer_po_number"], o["so_number"]

    # ---------------- register datasets ----------------
    A = em.add
    A("01_master_data", "customers", customers)
    A("01_master_data", "customer_contacts", contacts)
    A("01_master_data", "ship_to_locations", ship_tos)
    A("01_master_data", "items", items)
    A("01_master_data", "units_of_measure", uoms)
    A("01_master_data", "item_classifications", classes)
    A("01_master_data", "price_lists", price_lists)
    A("01_master_data", "customer_price_agreements", cust_price_agreements)
    A("01_master_data", "bills_of_material", boms)
    A("01_master_data", "routings", routings)
    if manufactures:
        A("01_master_data", "work_centers", work_centers)
    A("02_sales_quote_to_order", "rfqs", rfqs)
    A("02_sales_quote_to_order", "sales_quotes", quotes)
    A("02_sales_quote_to_order", "sales_quote_lines", quote_lines)
    A("02_sales_quote_to_order", "sales_orders", orders)
    A("02_sales_quote_to_order", "sales_order_lines", order_lines)
    A("02_sales_quote_to_order", "order_acknowledgments", acks)
    A("02_sales_quote_to_order", "order_changes", order_changes)
    A("02_sales_quote_to_order", "credit_checks", credit_checks)
    A("03_procurement", "suppliers", suppliers)
    A("03_procurement", "supplier_contacts", sup_contacts)
    A("03_procurement", "supplier_quotes", supplier_quotes)
    A("03_procurement", "purchase_requisitions", reqs)
    A("03_procurement", "purchase_orders", pos)
    A("03_procurement", "purchase_order_lines", po_lines)
    A("03_procurement", "po_acknowledgments", po_acks)
    A("03_procurement", "supplier_scorecards", scorecards)
    A("03_procurement", "supplier_contracts", sup_contracts)
    A("04_receiving_ap", "advance_ship_notices", asns)
    A("04_receiving_ap", "receipts", receipts)
    A("04_receiving_ap", "receipt_lines", receipt_lines)
    A("04_receiving_ap", "supplier_invoices", sup_invoices)
    A("04_receiving_ap", "supplier_invoice_lines", sup_invoice_lines)
    A("04_receiving_ap", "three_way_match", match_results)
    A("04_receiving_ap", "ap_aging", ap_aging)
    A("04_receiving_ap", "ap_payments", ap_payments)
    A("05_inventory", "inventory_balances", balances, fmt="xlsx" if tier == "medium" else "csv")
    A("05_inventory", "inventory_transactions", inv_txns)
    A("05_inventory", "cycle_counts", cycle_counts)
    A("05_inventory", "lot_traceability", lot_trace)
    A("05_inventory", "reservations", reservations)
    A("05_inventory", "reorder_parameters", reorder_params)
    A("05_inventory", "transfers", transfers)
    A("05_inventory", "vmi_bin_counts", vmi_bins)
    A("06_warehouse_fulfillment", "pick_lists", pick_lists)
    A("06_warehouse_fulfillment", "shipments", shipments)
    A("06_warehouse_fulfillment", "bills_of_lading", bols)
    A("06_warehouse_fulfillment", "proof_of_delivery", pods)
    A("06_warehouse_fulfillment", "freight_invoices", freight_inv)
    A("07_demand_planning", "demand_forecast", forecasts)
    A("07_demand_planning", "mrp_recommendations", mrp)
    A("08_manufacturing", "work_orders", work_orders)
    A("08_manufacturing", "work_order_operations", wo_ops)
    A("08_manufacturing", "labor_transactions", labor)
    A("08_manufacturing", "material_issues", mat_issues)
    A("08_manufacturing", "scrap", scrap)
    A("08_manufacturing", "downtime", downtime)
    A("08_manufacturing", "wip_balances", wip)
    A("08_manufacturing", "production_completions", completions)
    A("09_quality", "inspections", inspections)
    A("09_quality", "nonconformance_reports", ncrs)
    A("09_quality", "corrective_actions", capas)
    A("09_quality", "certificates_of_conformance", cocs)
    A("09_quality", "calibration_records", calibrations)
    A("10_maintenance", "equipment", equipment)
    A("10_maintenance", "maintenance_work_orders", maint_wos)
    A("10_maintenance", "pm_schedule", pm_sched)
    A("11_billing_ar", "customer_invoices", cust_invoices)
    A("11_billing_ar", "customer_invoice_lines", cust_invoice_lines)
    A("11_billing_ar", "customer_payments", payments)
    A("11_billing_ar", "ar_aging", ar_aging, fmt="xlsx" if tier == "medium" else "csv")
    A("11_billing_ar", "disputes_deductions", disputes)
    A("11_billing_ar", "credit_holds", credit_holds)
    A("12_returns_service", "rmas", rmas)
    A("12_returns_service", "warranty_claims", warranty)
    A("12_returns_service", "field_service_orders", field_service)
    A("13_engineering_compliance", "engineering_change_orders", ecos)
    A("13_engineering_compliance", "item_revisions", revisions)
    A("13_engineering_compliance", "trade_compliance", trade)
    A("13_engineering_compliance", "customs_entries", customs)
    A("14_finance_gl", "chart_of_accounts", coa)
    A("14_finance_gl", "general_ledger", gl)
    A("14_finance_gl", "inventory_valuation", inv_valuation)
    A("14_finance_gl", "standard_cost_roll", cost_roll)
    A("14_finance_gl", "cost_variances", variances)
    A("14_finance_gl", "budget_vs_actual", budget)
    A("14_finance_gl", "bank_statement_operating", bank)
    A("14_finance_gl", "corporate_vendors", corp_vendors)
    A("14_finance_gl", "ap_vendor_invoices_indirect", corp_ap)
    A("14_finance_gl", "software_subscriptions", software)
    A("15_hr_ehs", "employees", employees)
    A("15_hr_ehs", "payroll_register", payroll)
    A("15_hr_ehs", "pto_balances", pto)
    A("15_hr_ehs", "training_certifications", training)
    A("15_hr_ehs", "ehs_incidents", incidents)
    A("15_hr_ehs", "safety_observations", safety_obs)
    A("16_documents_emails", "document_index", docs)
    A("17_workflow_events", "workflow_events", events)

    # Remove datasets that are empty because the business model doesn't have them (not a data-quality issue).
    em.datasets = [d for d in em.datasets if d.rows or d.text is not None]

    if tier == "high":
        manifest = em.write_all()
    elif tier == "medium":
        manifest = em.write_all(
            drop=["routings", "work_centers", "labor_transactions", "downtime", "calibration_records", "lot_traceability", "standard_cost_roll", "purchase_requisitions", "safety_observations", "reservations"],
            renames={"sales_order_lines": {"unit_price": "Price", "ordered_qty": "Qty", "unit_cost_at_order": "Cost"}, "purchase_order_lines": {"unit_cost": "Price"}},
            header_style="title",
        )
    else:
        manifest = em.write_all(
            drop=["workflow_events", "inventory_transactions", "cycle_counts", "three_way_match", "ap_aging", "supplier_scorecards", "supplier_contracts", "supplier_quotes", "purchase_requisitions",
                  "order_acknowledgments", "order_changes", "credit_checks", "rfqs", "sales_quotes", "sales_quote_lines", "advance_ship_notices", "receipt_lines", "reservations", "reorder_parameters",
                  "transfers", "bills_of_lading", "proof_of_delivery", "freight_invoices", "credit_holds", "general_ledger", "inventory_valuation", "cost_variances", "budget_vs_actual", "pto_balances",
                  "training_certifications", "safety_observations", "ehs_incidents", "units_of_measure", "item_classifications", "price_lists", "customer_price_agreements", "supplier_contacts"],
            merge_to_workbook=["customers", "customer_contacts", "ship_to_locations", "items", "suppliers", "purchase_orders", "purchase_order_lines", "sales_orders", "sales_order_lines",
                               "inventory_balances", "vmi_bin_counts", "employees", "corporate_vendors"],
            renames={"sales_orders": {"so_number": "PO_NUM", "customer_po_number": "SO_NUM"}, "items": {"reorder_point": "MIN", "reorder_qty": "MAX", "unit_cost": "COST", "list_price": "PRICE"},
                     "sales_order_lines": {"so_number": "INV_NO", "unit_price": "RATE", "ordered_qty": "QTY"}, "customer_invoices": {"invoice_number": "NUM", "invoice_total": "AMOUNT", "balance": "OPEN_BAL"},
                     "shipments": {"freight_cost": "FRT", "freight_billed_to_customer": "FRT_BILLED"}},
            header_style="legacy",
            workbook_name="RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx",
        )
        key.add("IND-DQ-01", "data_quality", [short], "Sales order export has SO and customer-PO columns swapped (and 7% of rows entered swapped in the source)",
                "In the legacy workbook 'sales_orders' sheet the column labelled PO_NUM holds Ridgeway's own SO/invoice number and SO_NUM holds the customer PO. Independently, ~7% of rows were keyed with the two values reversed at data entry, so the label swap does not fix every row.",
                ["00_legacy_exports/RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx#sales_orders", "11_billing_ar/customer_invoices.csv"], "Infer semantics from value patterns (5-digit numeric = SO; PO/P/4500/PUR- prefixes = customer PO); flag rows that do not fit either pattern.")
    em.resolve_evidence(key, short)

    return {"slug": profile["slug"], "name": profile["name"], "short": short, "tier": tier, "files": manifest, "dropped_datasets": em.dropped, "merged_into_legacy_workbook": em.merged_into_workbook,
            "stats": {"customers": len(customers), "items": len(items), "sales_orders": len(orders), "purchase_orders": len(pos), "shipments": len(shipments), "customer_invoices": len(cust_invoices), "work_orders": len(work_orders), "gl_lines": len(gl), "employees": len(employees)},
            "shared_item_prices": {pn: {"item_id": r["item_id"], "supplier": r["primary_supplier"], "unit_cost": r["unit_cost"], "annual_qty": sum(p["ordered_qty"] for p in po_lines if p["item_id"] == r["item_id"])} for pn, r in shared_map.items()},
            "parcel_freight_cost_per_lb": round(sum(s["freight_cost"] for s in shipments if s["mode"] == "Parcel") / max(1, sum(s["weight_lb"] for s in shipments if s["mode"] == "Parcel")), 3)}
