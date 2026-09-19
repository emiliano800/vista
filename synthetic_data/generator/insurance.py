"""Synthetic back-office data for a commercial P&C insurance brokerage.

Lifecycle modelled: client -> exposure -> submission -> quote -> bind ->
policy -> service (certificates, endorsements, claims) -> renewal ->
premium billing / carrier payables / commission accounting, plus the
internal finance, HR and compliance functions every agency has.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from common import (CITIES, TODAY, AnswerKey, Emitter, Rng, Row, email_for, eml, gl_chart, iso,
                    month_ends)
from profiles import CARRIERS, LINES, SHARED_VENDORS, SOFTWARE, TRAP_VENDORS

CLIENT_INDUSTRIES = [
    ("Contractors - Electrical", "238210", "GL,WC,AUTO,UMB"),
    ("Contractors - HVAC/Plumbing", "238220", "GL,WC,AUTO,UMB"),
    ("Contractors - General Building", "236220", "GL,WC,AUTO,UMB,PROP"),
    ("Real Estate - Lessors of Commercial Buildings", "531120", "PROP,GL,UMB"),
    ("Manufacturing - Fabricated Metal", "332312", "PROP,GL,WC,AUTO,UMB"),
    ("Manufacturing - Plastics", "326199", "PROP,GL,WC"),
    ("Wholesale - Industrial Supplies", "423840", "PROP,GL,AUTO,WC"),
    ("Trucking - General Freight", "484121", "AUTO,GL,WC,UMB"),
    ("Restaurants - Full Service", "722511", "BOP,WC"),
    ("Professional Services - Engineering", "541330", "PL,GL,CYB,WC"),
    ("Professional Services - Accounting", "541211", "PL,CYB,BOP"),
    ("Healthcare - Outpatient Clinic", "621498", "PL,GL,PROP,CYB,WC"),
    ("Technology - Software", "541511", "CYB,PL,GL,DO"),
    ("Hospitality - Hotels", "721110", "PROP,GL,WC,UMB"),
    ("Marine - Boat Repair & Fabrication", "336612", "GL,PROP,WC,AUTO"),
    ("Auto Repair", "811111", "GL,PROP,WC"),
    ("Retail - Hardware Store", "444130", "BOP,WC"),
    ("Nonprofit - Social Services", "624190", "GL,PL,DO,WC"),
]
BIZ_SUFFIX = ["LLC", "Inc.", "Corp.", "Co.", "LLC", "Inc.", "LP"]
BIZ_WORDS = ["Allied", "Summit", "Pioneer", "Granite", "Blue Ridge", "Riverside", "Northgate", "Evergreen",
             "Lakeshore", "Ironclad", "Sterling", "Beacon", "Cornerstone", "Heritage", "Metro", "Coastal",
             "Highland", "Valley", "Tri-State", "Patriot", "Liberty", "Anchor", "Bayside", "Keystone State"]
BIZ_NOUNS = {"Contractors": "Electric", "Real": "Properties", "Manufacturing": "Manufacturing", "Wholesale": "Supply",
             "Trucking": "Transport", "Restaurants": "Hospitality Group", "Professional": "Associates",
             "Healthcare": "Medical Group", "Technology": "Software", "Hospitality": "Hotels", "Marine": "Marine",
             "Auto": "Automotive", "Retail": "Hardware", "Nonprofit": "Community Services"}


def _biz_name(rng: Rng, industry: str, used: set[str]) -> str:
    noun = BIZ_NOUNS[industry.split(" ")[0]]
    for _ in range(50):
        n = f"{rng.choice(BIZ_WORDS)} {noun} {rng.choice(BIZ_SUFFIX)}"
        if n not in used:
            used.add(n)
            return n
    n = f"{rng.choice(BIZ_WORDS)} {noun} {len(used)} {rng.choice(BIZ_SUFFIX)}"
    used.add(n)
    return n


def generate_insurance(profile: dict, root: str, key: AnswerKey) -> dict:
    rng = Rng(profile["seed"])
    tier = profile["tier"]
    short = profile["short"]
    em = Emitter(root, profile["slug"], tier, rng)
    hq_city, hq_state, hq_zip3 = profile["hq"]
    nearby = {"CT": {"CT", "MA", "RI", "NY"}, "FL": {"FL", "GA"}, "PA": {"PA", "NY", "OH"}}[hq_state]
    region = [c for c in CITIES if c[1] in nearby] or [profile["hq"]]
    domain = profile["domain"]
    my_carriers = [c for c in CARRIERS if short in c["commission"]]

    # ---------------- staff / producers ----------------
    n_prod = {"high": 8, "medium": 5, "low": 3}[tier]
    n_am = {"high": 10, "medium": 6, "low": 3}[tier]
    employees: list[Row] = []
    producers: list[Row] = []
    account_managers: list[Row] = []
    roles = [("Producer", n_prod), ("Account Manager", n_am), ("Account Executive", max(1, n_am // 3)),
             ("Claims Advocate", 1 if tier != "low" else 0), ("Accounting Specialist", 2 if tier != "low" else 1),
             ("Certificate Specialist", 1 if tier == "high" else 0), ("Marketing Specialist", 1 if tier == "high" else 0),
             ("Office Manager", 1), ("President", 1), ("CFO" if tier != "low" else "Bookkeeper", 1), ("IT Administrator", 1 if tier == "high" else 0),
             ("Receptionist", 1)]
    eid = 1
    for role, n in roles:
        for _ in range(n):
            name = rng.person()
            hire = rng.date_between(date(profile["founded"], 1, 1), date(2025, 12, 31))
            emp = {
                "employee_id": f"E{eid:03d}", "full_name": name, "role": role,
                "department": {"Producer": "Sales", "Account Manager": "Service", "Account Executive": "Service",
                               "Claims Advocate": "Service", "Accounting Specialist": "Accounting", "Certificate Specialist": "Service",
                               "Marketing Specialist": "Marketing", "Office Manager": "Admin", "President": "Executive", "CFO": "Accounting",
                               "Bookkeeper": "Accounting", "IT Administrator": "IT", "Receptionist": "Admin"}[role],
                "email": email_for(name, domain), "hire_date": iso(hire), "employment_type": "Full-Time" if rng.chance(0.9) else "Part-Time",
                "annual_salary": {"Producer": rng.int(65, 140), "Account Manager": rng.int(52, 78), "Account Executive": rng.int(75, 110),
                                  "Claims Advocate": rng.int(60, 80), "Accounting Specialist": rng.int(50, 70), "Certificate Specialist": rng.int(42, 55),
                                  "Marketing Specialist": rng.int(55, 72), "Office Manager": rng.int(58, 75), "President": rng.int(180, 260),
                                  "CFO": rng.int(150, 210), "Bookkeeper": rng.int(48, 62), "IT Administrator": rng.int(70, 95),
                                  "Receptionist": rng.int(36, 45)}[role] * 1000,
                "state": hq_state, "status": "Active",
            }
            employees.append(emp)
            if role == "Producer":
                producers.append(emp)
            if role in ("Account Manager", "Account Executive"):
                account_managers.append(emp)
            eid += 1
    # one terminated employee
    t = dict(employees[-1]); t["employee_id"] = f"E{eid:03d}"; t["full_name"] = rng.person(); t["role"] = "Account Manager"
    t["status"] = "Terminated"; t["termination_date"] = iso(date(2025, 11, 14)); t["email"] = email_for(t["full_name"], domain)
    employees.append(t)

    # ---------------- clients / contacts / locations ----------------
    clients: list[Row] = []
    contacts: list[Row] = []
    locations: list[Row] = []
    used_names: set[str] = set()
    for i in range(profile["n_clients"]):
        ind, naics, lines = rng.choice(CLIENT_INDUSTRIES)
        name = _biz_name(rng, ind, used_names)
        addr = rng.address(region)
        producer = rng.choice(producers)
        am = rng.choice(account_managers)
        since = rng.date_between(date(max(profile["founded"], 2008), 1, 1), date(2025, 6, 30))
        revenue = rng.int(8, 900) * 100000
        c = {
            "client_id": f"{short[:3].upper()}-C{i + 1:04d}", "client_name": name, "dba_name": "",
            "fein": rng.fein(), "naics_code": naics, "industry": ind, "annual_revenue": revenue,
            "employee_count": max(3, revenue // rng.int(90000, 220000)),
            "billing_street": addr["street"], "billing_city": addr["city"], "billing_state": addr["state"], "billing_zip": addr["zip"],
            "producer_id": producer["employee_id"], "producer_name": producer["full_name"],
            "account_manager_id": am["employee_id"], "account_manager_name": am["full_name"],
            "client_since": iso(since), "status": "Active", "lines_of_business": lines, "source": rng.choice(["Referral", "Cold Outreach", "Walk-in", "Web", "Association", "BOR"]),
        }
        clients.append(c)
        n_contacts = rng.int(1, 3)
        for j in range(n_contacts):
            pn = rng.person()
            contacts.append({
                "contact_id": f"{c['client_id']}-P{j + 1}", "client_id": c["client_id"], "full_name": pn,
                "title": rng.choice(["Owner", "CFO", "Controller", "Office Manager", "HR Director", "Risk Manager", "President", "VP Operations"]),
                "email": email_for(pn, name.lower().split(" ")[0].replace("-", "") + ".com"), "phone": rng.phone(),
                "is_primary": "Y" if j == 0 else "N", "role": "Decision Maker" if j == 0 else rng.choice(["Billing", "Certificates", "Claims", "HR"]),
            })
        n_loc = 1 if "PROP" not in lines and rng.chance(0.6) else rng.int(1, 4)
        for j in range(n_loc):
            la = addr if j == 0 else rng.address(region)
            locations.append({
                "location_id": f"{c['client_id']}-L{j + 1}", "client_id": c["client_id"], "location_number": j + 1,
                "street": la["street"], "city": la["city"], "state": la["state"], "zip": la["zip"],
                "occupancy": rng.choice(["Office", "Warehouse", "Manufacturing", "Retail", "Mixed Use", "Yard/Storage"]),
                "construction": rng.choice(["Frame", "Joisted Masonry", "Non-Combustible", "Masonry Non-Combustible", "Fire Resistive"]),
                "year_built": rng.int(1952, 2021), "square_feet": rng.int(2500, 120000), "sprinklered": rng.choice(["Y", "N"]),
                "building_value": rng.int(3, 120) * 50000 if "PROP" in lines else 0, "contents_value": rng.int(1, 40) * 25000,
                "business_income_value": rng.int(0, 30) * 50000,
            })

    # Planted DQ: legal name vs DBA entered as two separate clients (low tier)
    if tier == "low":
        dup_src = clients[3]
        dup = dict(dup_src)
        dup["client_id"] = f"{short[:3].upper()}-C{len(clients) + 1:04d}"
        dup["client_name"] = "LH Express"
        dup_src["client_name"] = "Lackawanna Hauling LLC"
        dup_src["industry"] = "Trucking - General Freight"; dup_src["naics_code"] = "484121"; dup_src["lines_of_business"] = "AUTO,GL,WC,UMB"
        dup["industry"] = dup_src["industry"]; dup["naics_code"] = dup_src["naics_code"]; dup["lines_of_business"] = dup_src["lines_of_business"]
        dup["fein"] = ""
        dup["dba_name"] = ""
        dup["client_since"] = iso(date(2023, 2, 1))
        clients.append(dup)
        key.add("INS-DQ-01", "data_quality", [short], "Same insured entered twice (legal name vs DBA)",
                f"'{dup_src['client_name']}' ({dup_src['client_id']}) and 'LH Express' ({dup['client_id']}) share the same address and producer; LH Express is the DBA and has no FEIN. Policies are split across both records.",
                ["01_clients_crm/clients", "02_policies_exposures/policies"], "Propose merge; keep legal name as primary, DBA in dba_name field; require human confirmation.")
        # missing FEINs
        for c in rng.sample(clients, 5):
            c["fein"] = ""
        key.add("INS-DQ-04", "data_quality", [short], "Missing FEINs on multiple client records",
                "Several clients have blank FEIN; FEIN is required on ACORD 125 and for carrier submissions.",
                ["01_clients_crm/clients"], "Create data-collection tasks for account managers; do not fabricate values.")
        # duplicate contacts
        dc = dict(contacts[6]); dc["contact_id"] = dc["contact_id"] + "-DUP"; dc["full_name"] = dc["full_name"].upper(); dc["email"] = dc["email"].upper()
        contacts.append(dc)
        key.add("INS-DQ-05", "data_quality", [short], "Duplicate contact records", f"Contact {contacts[6]['full_name']} appears twice with different casing.",
                ["01_clients_crm/contacts"], "Deduplicate case-insensitively; keep the record with the most recent activity.")

    # Shared client across brokerages (portfolio cross-sell / consolidation)
    if short in ("Harborline", "Castlebrook"):
        shared = clients[5]
        shared["client_name"] = "Allied Freight Systems, Inc."
        shared["fein"] = "46-3318824"
        shared["industry"] = "Trucking - General Freight"; shared["naics_code"] = "484121"
        shared["billing_street"] = "4400 Commerce Blvd"; shared["billing_city"] = "Harrisburg"; shared["billing_state"] = "PA"; shared["billing_zip"] = "17110"
        shared["lines_of_business"] = "PROP,GL" if short == "Harborline" else "AUTO,WC,UMB"
        shared["annual_revenue"] = 41000000; shared["employee_count"] = 210

    # ---------------- policies / coverages / exposures ----------------
    policies: list[Row] = []
    coverages: list[Row] = []
    vehicles: list[Row] = []
    drivers: list[Row] = []
    payroll_exp: list[Row] = []
    pol_seq = 1
    for c in clients:
        lines = c["lines_of_business"].split(",")
        for ln in lines:
            if rng.chance(0.12):
                continue
            eligible = [k for k in my_carriers if ln in k["lines"]]
            if not eligible:
                continue
            carrier = rng.choice(eligible)
            lname, pmin, pmax = LINES[ln]
            eff = rng.date_between(date(2025, 4, 1), date(2026, 3, 30))
            exp = date(eff.year + 1, eff.month, eff.day)
            premium = rng.money(pmin, pmax, 1)
            comm_pct = carrier["commission"][short]
            billing = "Agency Bill" if (ln in ("PROP", "GL", "UMB", "PL", "CYB", "DO") and rng.chance(0.65)) else "Direct Bill"
            pol_no = f"{carrier['code']}-{ln}-{rng.int(1000000, 9999999)}"
            p = {
                "policy_id": f"{short[:3].upper()}-P{pol_seq:05d}", "client_id": c["client_id"], "client_name": c["client_name"],
                "policy_number": pol_no, "line_of_business": ln, "line_description": lname, "carrier_code": carrier["code"],
                "carrier_name": carrier["name"], "effective_date": iso(eff), "expiration_date": iso(exp), "term_months": 12,
                "annual_premium": premium, "commission_pct": comm_pct, "expected_commission": round(premium * comm_pct / 100, 2),
                "billing_type": billing, "policy_status": "In Force" if exp > TODAY else "Expired",
                "producer_id": c["producer_id"], "account_manager_id": c["account_manager_id"],
                "surplus_lines": "Y" if carrier.get("surplus_lines") else "N", "new_or_renewal": "Renewal" if rng.chance(0.7) else "New",
                "policy_checked": "Y" if rng.chance(0.85 if tier == "high" else 0.5) else "N",
            }
            policies.append(p)
            # coverages
            if ln == "GL":
                for cov, lim in (("Each Occurrence", 1000000), ("General Aggregate", 2000000), ("Products-Completed Ops Aggregate", 2000000), ("Personal & Advertising Injury", 1000000), ("Damage to Rented Premises", 100000), ("Medical Expense", 5000)):
                    coverages.append({"policy_id": p["policy_id"], "coverage": cov, "limit": lim, "deductible": 0 if cov != "Each Occurrence" else rng.choice([0, 1000, 2500]), "form": rng.choice(["Occurrence", "Occurrence", "Claims-Made"]) if cov == "Each Occurrence" else ""})
            elif ln == "UMB":
                lim = rng.choice([1000000, 2000000, 3000000, 5000000, 10000000])
                p["umbrella_limit"] = lim
                coverages.append({"policy_id": p["policy_id"], "coverage": "Umbrella Each Occurrence", "limit": lim, "deductible": 10000, "form": "Follow Form"})
                coverages.append({"policy_id": p["policy_id"], "coverage": "Umbrella Aggregate", "limit": lim, "deductible": 0, "form": ""})
            elif ln == "AUTO":
                coverages.append({"policy_id": p["policy_id"], "coverage": "Combined Single Limit", "limit": 1000000, "deductible": 0, "form": "Symbol 1 (Any Auto)" if rng.chance(0.5) else "Symbols 7,8,9"})
                coverages.append({"policy_id": p["policy_id"], "coverage": "Comprehensive", "limit": "ACV", "deductible": rng.choice([500, 1000, 2500]), "form": ""})
                coverages.append({"policy_id": p["policy_id"], "coverage": "Collision", "limit": "ACV", "deductible": rng.choice([1000, 2500, 5000]), "form": ""})
                n_v = rng.int(2, 14) if "Trucking" not in c["industry"] else rng.int(20, 60)
                for v in range(n_v):
                    yr = rng.int(2012, 2025)
                    make, model = rng.choice([("Ford", "F-250"), ("Ford", "Transit 250"), ("Chevrolet", "Silverado 2500"), ("RAM", "ProMaster 2500"), ("Freightliner", "Cascadia"), ("International", "LT625"), ("Peterbilt", "579"), ("Isuzu", "NPR-HD")])
                    if "Trucking" in c["industry"] and rng.chance(0.6):
                        make, model = rng.choice([("Freightliner", "Cascadia"), ("Kenworth", "T680"), ("Peterbilt", "579"), ("Volvo", "VNL 760"), ("Great Dane", "53' Dry Van Trailer"), ("Utility", "3000R Reefer Trailer")])
                    vehicles.append({"vehicle_id": f"{p['policy_id']}-V{v + 1:02d}", "policy_id": p["policy_id"], "client_id": c["client_id"], "unit_number": v + 1, "year": yr, "make": make, "model": model, "vin": rng.vin(),
                                     "vehicle_type": "Trailer" if "Trailer" in model else ("Heavy Truck" if make in ("Freightliner", "Kenworth", "Peterbilt", "Volvo", "International") else "Light Truck/Van"),
                                     "gvw": 80000 if make in ("Freightliner", "Kenworth", "Peterbilt", "Volvo", "International") else rng.choice([8500, 10000, 14000]),
                                     "garaging_city": c["billing_city"], "garaging_state": c["billing_state"], "stated_value": rng.int(18, 165) * 1000, "radius": rng.choice(["Local (<50mi)", "Intermediate (50-200mi)", "Long Haul (>200mi)"]), "comp_ded": rng.choice([500, 1000, 2500]), "coll_ded": rng.choice([1000, 2500, 5000])})
                for d in range(max(1, int(n_v * 0.8))):
                    dn = rng.person()
                    drivers.append({"driver_id": f"{p['policy_id']}-D{d + 1:02d}", "policy_id": p["policy_id"], "client_id": c["client_id"], "driver_name": dn, "date_of_birth": iso(rng.date_between(date(1960, 1, 1), date(2003, 12, 31))),
                                    "license_state": c["billing_state"], "license_number": f"{c['billing_state']}{rng.int(10000000, 99999999)}", "cdl": "Y" if "Trucking" in c["industry"] else "N",
                                    "years_experience": rng.int(1, 35), "mvr_date": iso(rng.date_between(date(2025, 1, 1), TODAY)), "violations_3yr": rng.choice([0, 0, 0, 1, 1, 2]), "accidents_3yr": rng.choice([0, 0, 0, 0, 1])})
            elif ln == "WC":
                coverages.append({"policy_id": p["policy_id"], "coverage": "Employers Liability Each Accident", "limit": 1000000, "deductible": 0, "form": ""})
                coverages.append({"policy_id": p["policy_id"], "coverage": "Employers Liability Disease - Policy Limit", "limit": 1000000, "deductible": 0, "form": ""})
                exp_mod = round(rng.money(0.72, 1.35, 0.01), 2)
                p["experience_mod"] = exp_mod
                classes = {"Contractors - Electrical": [("5190", "Electrical Wiring", 3.12)], "Contractors - HVAC/Plumbing": [("5183", "Plumbing NOC", 3.45), ("5537", "HVAC Install", 3.98)],
                           "Contractors - General Building": [("5645", "Carpentry - Residential", 7.9), ("5403", "Carpentry NOC", 6.1)], "Manufacturing - Fabricated Metal": [("3400", "Metal Goods Mfg", 2.9), ("3632", "Machine Shop", 2.1)],
                           "Trucking - General Freight": [("7219", "Trucking NOC", 6.8)]}.get(c["industry"], [("8810", "Clerical Office", 0.19), ("8742", "Outside Sales", 0.35)])
                classes = classes + [("8810", "Clerical Office", 0.19)]
                for code, desc, rate in classes:
                    payroll_exp.append({"policy_id": p["policy_id"], "client_id": c["client_id"], "state": c["billing_state"], "class_code": code, "class_description": desc,
                                        "annual_payroll": rng.int(2, 80) * 25000 if code != "8810" else rng.int(1, 12) * 25000, "rate_per_100": rate, "experience_mod": exp_mod})
            elif ln == "PROP":
                for loc in [l for l in locations if l["client_id"] == c["client_id"]]:
                    coverages.append({"policy_id": p["policy_id"], "coverage": f"Building - Loc {loc['location_number']}", "limit": loc["building_value"], "deductible": rng.choice([2500, 5000, 10000, 25000]), "form": "Special (Replacement Cost)"})
                    coverages.append({"policy_id": p["policy_id"], "coverage": f"Business Personal Property - Loc {loc['location_number']}", "limit": loc["contents_value"], "deductible": rng.choice([2500, 5000]), "form": "Special"})
            elif ln in ("PL", "CYB", "DO"):
                lim = rng.choice([1000000, 2000000, 3000000, 5000000])
                coverages.append({"policy_id": p["policy_id"], "coverage": f"{lname} Each Claim", "limit": lim, "deductible": rng.choice([5000, 10000, 25000, 50000]), "form": "Claims-Made", "retro_date": iso(rng.date_between(date(2012, 1, 1), eff))})
                coverages.append({"policy_id": p["policy_id"], "coverage": f"{lname} Aggregate", "limit": lim, "deductible": 0, "form": ""})
            elif ln == "BOP":
                coverages.append({"policy_id": p["policy_id"], "coverage": "Liability Each Occurrence", "limit": 1000000, "deductible": 0, "form": "Occurrence"})
                coverages.append({"policy_id": p["policy_id"], "coverage": "Business Personal Property", "limit": rng.int(2, 20) * 25000, "deductible": 1000, "form": "Special"})
            pol_seq += 1

    # Planted: vehicle count mismatch (spreadsheet 45 vs application 47) - Castlebrook trucking client
    if tier == "low":
        truck_pol = next(p for p in policies if p["line_of_business"] == "AUTO" and "Trucking" in next(c for c in clients if c["client_id"] == p["client_id"])["industry"])
        pol_vehicles = [v for v in vehicles if v["policy_id"] == truck_pol["policy_id"]]
        # trim/pad schedule to 45
        vehicles[:] = [v for v in vehicles if v["policy_id"] != truck_pol["policy_id"]] + pol_vehicles[:45]
        while len([v for v in vehicles if v["policy_id"] == truck_pol["policy_id"]]) < 45:
            src = dict(pol_vehicles[0]); src["unit_number"] = len([v for v in vehicles if v["policy_id"] == truck_pol["policy_id"]]) + 1
            src["vehicle_id"] = f"{truck_pol['policy_id']}-V{src['unit_number']:02d}"; src["vin"] = rng.vin(); vehicles.append(src)
        truck_pol["scheduled_vehicle_count_per_application"] = 47
        key.add("INS-DQ-02", "data_quality", [short], "Vehicle schedule disagrees with ACORD application",
                f"Vehicle schedule for {truck_pol['policy_number']} lists 45 units; the ACORD 127 application (see 13_documents_emails) and policies sheet say 47.",
                ["02_policies_exposures/vehicles", "02_policies_exposures/policies", "13_documents_emails/acord_127_vehicle_schedule"],
                "Flag exposure discrepancy; request updated schedule from client before renewal marketing.")

    # ---------------- carriers / underwriters / submissions / quotes ----------------
    carriers_rows = [{"carrier_code": k["code"], "carrier_name": k["name"], "am_best_rating": k["am_best"], "lines_appointed": ",".join(k["lines"]),
                      "commission_pct": k["commission"][short], "surplus_lines": "Y" if k.get("surplus_lines") else "N",
                      "appointment_date": iso(rng.date_between(date(profile["founded"], 1, 1), date(2022, 1, 1))), "download_enabled": "Y" if tier == "high" or rng.chance(0.5) else "N",
                      "agency_code": f"{rng.int(100000, 999999)}", "payment_terms": rng.choice(["Net 30 from statement", "Net 45", "Monthly account current"])} for k in my_carriers]
    underwriters = []
    for k in my_carriers:
        for _ in range(rng.int(1, 3)):
            n = rng.person()
            underwriters.append({"underwriter_id": f"UW-{k['code']}-{rng.int(100, 999)}", "carrier_code": k["code"], "underwriter_name": n, "email": email_for(n, k["name"].split(" ")[0].lower().strip("(") + ".com"),
                                 "phone": rng.phone(), "lines": rng.choice(k["lines"]), "territory": hq_state, "appetite_notes": rng.choice(["Prefers <$5M revenue; no roofing", "Strong on real estate; avoids habitational frame", "Aggressive on trucking fleets 10-50 units", "Cyber: requires MFA + EDR", "Contractors ok, no residential GC"])})
    submissions = []
    quotes = []
    declinations = []
    sub_seq = 1
    for p in policies:
        if p["new_or_renewal"] == "New" or rng.chance(0.35):
            n_markets = rng.int(1, 4)
            markets = rng.sample([k for k in my_carriers if p["line_of_business"] in k["lines"]], min(n_markets, len([k for k in my_carriers if p["line_of_business"] in k["lines"]])))
            eff = date.fromisoformat(p["effective_date"])
            for k in markets:
                sent = eff - timedelta(days=rng.int(35, 95))
                status = "Bound" if k["code"] == p["carrier_code"] else rng.choice(["Quoted - Not Selected", "Declined", "Quoted - Not Selected", "No Response"])
                s = {"submission_id": f"{short[:3].upper()}-S{sub_seq:05d}", "policy_id": p["policy_id"], "client_id": p["client_id"], "line_of_business": p["line_of_business"], "carrier_code": k["code"],
                     "underwriter_id": next((u["underwriter_id"] for u in underwriters if u["carrier_code"] == k["code"]), ""), "submitted_date": iso(sent), "target_effective_date": p["effective_date"],
                     "acknowledged_date": iso(sent + timedelta(days=rng.int(0, 4))) if status != "No Response" else "", "status": status, "submission_version": rng.choice([1, 1, 1, 2]),
                     "checklist_complete": "Y" if tier == "high" or rng.chance(0.7) else "N", "loss_runs_included": "Y" if rng.chance(0.85) else "N"}
                submissions.append(s)
                if status.startswith("Quoted") or status == "Bound":
                    qp = p["annual_premium"] if status == "Bound" else round(p["annual_premium"] * rng.money(0.92, 1.28, 0.01), 0)
                    quotes.append({"quote_id": f"{s['submission_id']}-Q1", "submission_id": s["submission_id"], "policy_id": p["policy_id"], "carrier_code": k["code"], "quoted_premium": qp,
                                   "quote_date": iso(sent + timedelta(days=rng.int(7, 30))), "quote_expiration_date": iso(eff + timedelta(days=rng.int(0, 15))),
                                   "deductible": rng.choice(["$1,000", "$2,500", "$5,000", "1,000", "2500", "$10,000 per occurrence"]) if tier != "high" else rng.choice([1000, 2500, 5000, 10000]),
                                   "limit_summary": rng.choice(["1M/2M/2M", "$1,000,000 occ / $2,000,000 agg", "1,000,000 CSL", "2M/4M"]),
                                   "subjectivities": rng.choice(["", "Signed application; loss runs 5 yrs", "Completed supplemental; MFA attestation", "Updated SOV; sprinkler cert", "Driver list with MVRs"]),
                                   "selected": "Y" if status == "Bound" else "N", "commission_pct": k["commission"][short]})
                elif status == "Declined":
                    declinations.append({"submission_id": s["submission_id"], "carrier_code": k["code"], "declined_date": iso(sent + timedelta(days=rng.int(3, 21))),
                                         "reason_code": rng.choice(["APPETITE", "LOSS_HISTORY", "CAPACITY", "PRICE", "CLASS_EXCLUDED", "INCOMPLETE_SUBMISSION"]), "reason_text": rng.choice(["Outside appetite for class", "Loss ratio >70% over 5 years", "Capacity exhausted in territory", "Incomplete submission - no loss runs", "Roofing exposure excluded"])})
                sub_seq += 1

    # ---------------- renewals ----------------
    renewals = []
    renewal_tasks = []
    for p in policies:
        exp = date.fromisoformat(p["expiration_date"])
        if TODAY - timedelta(days=30) <= exp <= TODAY + timedelta(days=150):
            days_out = (exp - TODAY).days
            stage = "120-day: Renewal identified" if days_out > 90 else "90-day: Exposure update requested" if days_out > 60 else "60-day: Marketing" if days_out > 30 else "30-day: Proposal / bind" if days_out > 0 else "Post-expiration: Bound - awaiting policy"
            strat = rng.choice(["Renew with incumbent", "Market to 3 carriers", "Remarket - incumbent non-renewing", "Renew as-is (rate < 5%)"])
            r = {"renewal_id": f"REN-{p['policy_id']}", "policy_id": p["policy_id"], "client_id": p["client_id"], "line_of_business": p["line_of_business"], "carrier_code": p["carrier_code"],
                 "expiration_date": p["expiration_date"], "days_to_expiration": days_out, "expiring_premium": p["annual_premium"], "renewal_stage": stage, "renewal_strategy": strat,
                 "exposure_update_received": "Y" if days_out < 75 and rng.chance(0.7) else "N", "loss_runs_ordered": "Y" if days_out < 100 and rng.chance(0.8) else "N",
                 "renewal_quote_premium": round(p["annual_premium"] * rng.money(0.95, 1.18, 0.01)) if days_out < 45 else "", "account_manager_id": p["account_manager_id"], "producer_id": p["producer_id"]}
            renewals.append(r)
            for offset, task in ((120, "Send renewal questionnaire"), (90, "Request updated exposures / SOV"), (75, "Order loss runs"), (60, "Prepare and send submissions"), (30, "Deliver renewal proposal"), (10, "Obtain signed bind order")):
                due = exp - timedelta(days=offset)
                renewal_tasks.append({"task_id": f"{r['renewal_id']}-T{offset}", "renewal_id": r["renewal_id"], "policy_id": p["policy_id"], "task": task, "due_date": iso(due),
                                      "assigned_to": p["account_manager_id"], "status": "Complete" if due < TODAY - timedelta(days=5) and rng.chance(0.85) else ("Overdue" if due < TODAY else "Open"),
                                      "completed_date": iso(due + timedelta(days=rng.int(-3, 6))) if due < TODAY - timedelta(days=5) and rng.chance(0.85) else ""})
    if tier == "medium" and renewals:
        bad = renewals[2]
        real = date.fromisoformat(bad["expiration_date"])
        bad["expiration_date"] = iso(real + timedelta(days=31))
        key.add("INS-DQ-03", "data_quality", [short], "Renewal tracker expiration date disagrees with policy record",
                f"Renewal {bad['renewal_id']} shows expiration {bad['expiration_date']} while policies.csv shows {iso(real)} for {bad['policy_id']}.",
                ["04_renewals/renewals", "02_policies_exposures/policies"], "Treat the policy record (carrier download) as source of truth; correct tracker; recompute 120/90/60/30 milestones.")

    # ---------------- certificates ----------------
    cert_requests = []
    certs_issued = []
    gl_pols = [p for p in policies if p["line_of_business"] == "GL" and p["policy_status"] == "In Force"]
    n_cert = {"high": 60, "medium": 35, "low": 18}[tier]
    holders = ["City of " + hq_city, "Turner Construction Company", "Brookfield Properties", "Whiting-Turner Contracting", "CBRE Property Management", "Amazon.com Services LLC", "Skanska USA Building", "Home Depot U.S.A., Inc.", "Gilbane Building Company", "Prologis, L.P.", "Suffolk Construction", "State DOT", "JLL Property Management", "Walmart Inc.", "Kiewit Corporation"]
    for i in range(n_cert):
        p = rng.choice(gl_pols)
        req = rng.date_between(date(2025, 10, 1), TODAY)
        umb = next((q for q in policies if q["client_id"] == p["client_id"] and q["line_of_business"] == "UMB"), None)
        req_umb = rng.choice([0, 0, 1000000, 2000000, 5000000])
        needs = {"additional_insured": rng.chance(0.7), "waiver_of_subrogation": rng.chance(0.5), "primary_noncontributory": rng.chance(0.4)}
        cr = {"certificate_request_id": f"{short[:3].upper()}-CERT{i + 1:04d}", "client_id": p["client_id"], "gl_policy_id": p["policy_id"], "certificate_holder": rng.choice(holders), "requested_date": iso(req),
              "requested_by": rng.choice(["Client email", "Client portal", "Holder direct", "Phone"]), "project_description": rng.choice(["Ongoing operations", "Tenant improvement - Suite 200", "Site work - Phase 2", "Annual vendor compliance", "Service contract 2026", "Bid requirement"]),
              "required_gl_each_occurrence": rng.choice([1000000, 1000000, 2000000]), "required_umbrella_limit": req_umb, "additional_insured_required": "Y" if needs["additional_insured"] else "N",
              "waiver_of_subrogation_required": "Y" if needs["waiver_of_subrogation"] else "N", "primary_noncontributory_required": "Y" if needs["primary_noncontributory"] else "N",
              "status": "Issued", "issued_date": iso(req + timedelta(days=rng.int(0, 3))), "handled_by": p["account_manager_id"], "turnaround_hours": rng.int(1, 72)}
        available_umb = umb["umbrella_limit"] if umb else 0
        if req_umb > available_umb:
            cr["status"] = "Exception - limits not supported"; cr["issued_date"] = ""
            cr["exception_note"] = f"Holder requires ${req_umb:,} umbrella; account carries ${available_umb:,}."
        cert_requests.append(cr)
        if cr["status"] == "Issued":
            certs_issued.append({"certificate_id": cr["certificate_request_id"].replace("CERT", "COI"), "certificate_request_id": cr["certificate_request_id"], "client_id": p["client_id"], "form": "ACORD 25 (2016/03)", "issued_date": cr["issued_date"],
                                 "gl_policy_number": p["policy_number"], "gl_each_occurrence": 1000000, "gl_aggregate": 2000000, "umbrella_limit": available_umb, "additional_insured": cr["additional_insured_required"], "waiver_of_subrogation": cr["waiver_of_subrogation_required"],
                                 "primary_noncontributory": cr["primary_noncontributory_required"], "description_of_operations": cr["project_description"], "holder": cr["certificate_holder"], "issued_by": cr["handled_by"]})
    # Planted certificate exceptions
    if tier == "high":
        p = gl_pols[0]
        umb = next((q for q in policies if q["client_id"] == p["client_id"] and q["line_of_business"] == "UMB"), None)
        if umb is None:
            umb = dict(next(q for q in policies if q["line_of_business"] == "UMB")); umb["client_id"] = p["client_id"]; umb["client_name"] = p["client_name"]; umb["policy_id"] = f"{short[:3].upper()}-P{pol_seq:05d}"; pol_seq += 1; policies.append(umb)
        umb["umbrella_limit"] = 3000000
        for cv in coverages:
            if cv["policy_id"] == umb["policy_id"]:
                cv["limit"] = 3000000
        cert_requests.append({"certificate_request_id": f"{short[:3].upper()}-CERT{n_cert + 1:04d}", "client_id": p["client_id"], "gl_policy_id": p["policy_id"], "certificate_holder": "Turner Construction Company", "requested_date": iso(TODAY - timedelta(days=2)),
                              "requested_by": "Holder direct", "project_description": "Hartford HealthCare Tower - structural steel package", "required_gl_each_occurrence": 2000000, "required_umbrella_limit": 5000000, "additional_insured_required": "Y", "waiver_of_subrogation_required": "Y",
                              "primary_noncontributory_required": "Y", "status": "Exception - limits not supported", "issued_date": "", "handled_by": p["account_manager_id"], "turnaround_hours": "", "exception_note": "Holder contract requires $5,000,000 umbrella; in-force umbrella is $3,000,000."})
        key.add("INS-CERT-01", "certificate", [short], "Certificate request exceeds in-force umbrella limit",
                f"Turner Construction requires a $5M umbrella for {p['client_name']}; policy {umb['policy_number']} carries $3M. Certificate must not be issued as requested.",
                ["05_certificates/certificate_requests", "02_policies_exposures/coverages"], "Flag to account manager; quote excess layer increase or obtain holder waiver. Do not fabricate a certificate.")
    if tier == "medium":
        p = gl_pols[1]
        cert_requests.append({"certificate_request_id": f"{short[:3].upper()}-CERT{n_cert + 1:04d}", "client_id": p["client_id"], "gl_policy_id": p["policy_id"], "certificate_holder": "Port Tampa Bay", "requested_date": iso(TODAY - timedelta(days=1)),
                              "requested_by": "Client email", "project_description": "Berth 212 fender repair", "required_gl_each_occurrence": 1000000, "required_umbrella_limit": 0, "additional_insured_required": "Y", "waiver_of_subrogation_required": "Y",
                              "primary_noncontributory_required": "N", "status": "Exception - endorsement missing", "issued_date": "", "handled_by": p["account_manager_id"], "turnaround_hours": "", "exception_note": "Blanket waiver of subrogation endorsement (CG 24 04) not on policy."})
        key.add("INS-CERT-02", "certificate", [short], "Certificate requires waiver of subrogation not on policy",
                f"Port Tampa Bay requires waiver of subrogation for {p['client_name']}; GL policy {p['policy_number']} has no CG 24 04 endorsement.",
                ["05_certificates/certificate_requests", "06_endorsements/endorsement_requests"], "Create endorsement request to carrier; hold certificate until endorsement is bound.")

    # ---------------- endorsements ----------------
    endorsements = []
    n_end = {"high": 45, "medium": 28, "low": 12}[tier]
    end_types = [("Add Vehicle", "AUTO"), ("Delete Vehicle", "AUTO"), ("Add Driver", "AUTO"), ("Add Location", "PROP"), ("Add Additional Insured", "GL"), ("Add Waiver of Subrogation", "GL"), ("Increase Limits", "UMB"), ("Change Mailing Address", "*"), ("Add Mortgagee", "PROP"), ("Change Named Insured", "*"), ("Payroll Audit Adjustment", "WC")]
    for i in range(n_end):
        et, ln = rng.choice(end_types)
        pool = [p for p in policies if ln == "*" or p["line_of_business"] == ln] or policies
        p = rng.choice(pool)
        req = rng.date_between(date(2025, 7, 1), TODAY)
        status = rng.choice(["Issued", "Issued", "Issued", "Pending Carrier", "Requested", "Issued - Discrepancy"])
        prem_chg = {"Add Vehicle": rng.int(800, 6000), "Delete Vehicle": -rng.int(600, 4500), "Add Driver": 0, "Add Location": rng.int(1500, 12000), "Add Additional Insured": rng.choice([0, 0, 250]), "Add Waiver of Subrogation": rng.choice([0, 150, 250]), "Increase Limits": rng.int(1200, 9000), "Change Mailing Address": 0, "Add Mortgagee": 0, "Change Named Insured": 0, "Payroll Audit Adjustment": rng.int(-9000, 14000)}[et]
        endorsements.append({"endorsement_request_id": f"{short[:3].upper()}-END{i + 1:04d}", "policy_id": p["policy_id"], "client_id": p["client_id"], "request_type": et, "requested_value": rng.choice(["2024 Ford Transit 250 VIN 1FTBR1C8XRKA55210", "Unit 14 - sold", "New hire: see attached MVR", "Loc 3: 1800 Enterprise Ct", "Turner Construction Company - CG 20 10", "Blanket WOS CG 24 04", "$5,000,000", "PO Box 4410", "First National Bank ISAOA", "Add subsidiary: XYZ Holdings LLC", "Audited payroll +$412,000 class 5190"]),
                             "effective_date": iso(req + timedelta(days=rng.int(0, 10))), "requested_by": rng.choice(["Client", "Client", "Lender", "Certificate holder", "Carrier audit"]), "carrier_code": p["carrier_code"], "submitted_to_carrier_date": iso(req + timedelta(days=rng.int(0, 3))),
                             "status": status, "carrier_endorsement_number": f"END-{rng.int(100000, 999999)}" if status.startswith("Issued") else "", "premium_change": prem_chg, "received_date": iso(req + timedelta(days=rng.int(5, 40))) if status.startswith("Issued") else "",
                             "checked_against_request": "Y" if status == "Issued" else ("N - carrier issued wrong VIN" if status == "Issued - Discrepancy" else ""), "handled_by": p["account_manager_id"]})

    # ---------------- claims ----------------
    claims = []
    n_claims = {"high": 40, "medium": 26, "low": 14}[tier]
    for i in range(n_claims):
        p = rng.choice(policies)
        dol = rng.date_between(date(2021, 1, 1), TODAY - timedelta(days=5))
        status = "Closed" if dol < TODAY - timedelta(days=240) and rng.chance(0.85) else rng.choice(["Open", "Open", "Reopened", "Closed"])
        paid = rng.money(0, 85000, 1) if status != "Open" or rng.chance(0.5) else 0
        reserve = rng.money(2500, 150000, 1) if status in ("Open", "Reopened") else 0
        claims.append({"claim_id": f"{short[:3].upper()}-CLM{i + 1:04d}", "client_id": p["client_id"], "policy_id": p["policy_id"], "policy_number": p["policy_number"], "line_of_business": p["line_of_business"], "carrier_code": p["carrier_code"], "carrier_claim_number": f"{p['carrier_code']}{rng.int(10000000, 99999999)}",
                       "date_of_loss": iso(dol), "reported_date": iso(dol + timedelta(days=rng.int(0, 45))), "loss_description": rng.choice(["Rear-end collision, unit 7, I-84", "Water damage - burst pipe, Loc 1", "Slip and fall - customer, parking lot", "Employee back strain lifting", "Wind damage to roof membrane", "Theft of tools from jobsite", "Property damage to third party during install", "Ransomware event - systems down 3 days", "Alleged design error - HVAC sizing", "Fire - electrical panel"]),
                       "claim_type": rng.choice(["Liability", "Property", "Auto PD", "Auto BI", "WC Indemnity", "WC Medical Only", "Cyber", "E&O"]), "status": status, "paid_to_date": paid, "outstanding_reserve": reserve, "incurred": round(paid + reserve, 2),
                       "adjuster_name": rng.person(), "adjuster_phone": rng.phone(), "last_status_update": iso(rng.date_between(dol, TODAY)), "litigated": "Y" if rng.chance(0.08) else "N", "subrogation_potential": "Y" if rng.chance(0.15) else "N"})
    loss_runs = [{"loss_run_id": f"LR-{c['client_id']}-{rng.int(100, 999)}", "client_id": c["client_id"], "carrier_code": rng.choice(my_carriers)["code"], "requested_date": iso(rng.date_between(date(2025, 9, 1), TODAY)), "years_requested": 5,
                  "received_date": iso(rng.date_between(date(2025, 9, 15), TODAY)) if rng.chance(0.75) else "", "status": "Received" if rng.chance(0.75) else "Outstanding - 2nd follow-up sent", "valued_as_of": iso(rng.date_between(date(2025, 9, 1), TODAY))} for c in rng.sample(clients, min(len(clients), {"high": 25, "medium": 15, "low": 8}[tier]))]

    # ---------------- billing / AR ----------------
    invoices = []
    installments = []
    payments = []
    inv_seq = 1
    pay_seq = 1
    for p in policies:
        if p["billing_type"] != "Agency Bill":
            continue
        eff = date.fromisoformat(p["effective_date"])
        fees = rng.choice([0, 0, 150, 250, 500]) if p["surplus_lines"] == "N" else round(p["annual_premium"] * 0.0475, 2)
        sl_tax = round(p["annual_premium"] * 0.04, 2) if p["surplus_lines"] == "Y" else 0
        total = round(p["annual_premium"] + fees + sl_tax, 2)
        plan = rng.choice(["Full Pay", "Full Pay", "25% Down + 9", "Quarterly"])
        inv = {"invoice_id": f"{short[:3].upper()}-INV{inv_seq:05d}", "client_id": p["client_id"], "policy_id": p["policy_id"], "policy_number": p["policy_number"], "carrier_code": p["carrier_code"], "invoice_date": iso(eff - timedelta(days=rng.int(5, 25))), "due_date": iso(eff + timedelta(days=rng.int(0, 30))),
               "transaction_type": "New Business" if p["new_or_renewal"] == "New" else "Renewal", "premium": p["annual_premium"], "agency_fee": fees, "surplus_lines_tax": sl_tax, "total_amount": total, "payment_plan": plan,
               "amount_paid": 0.0, "balance": total, "status": "Open", "premium_finance": "Y" if total > 40000 and rng.chance(0.4) else "N", "premium_finance_company": ""}
        if inv["premium_finance"] == "Y":
            inv["premium_finance_company"] = rng.choice(["Imperial PFS", "AFCO", "First Insurance Funding"])
        invoices.append(inv)
        n_inst = 1 if plan == "Full Pay" else (10 if plan.startswith("25%") else 4)
        for k in range(n_inst):
            amt = total if n_inst == 1 else (round(total * 0.25, 2) if (n_inst == 10 and k == 0) else round((total * (0.75 if n_inst == 10 else 1.0)) / (n_inst - (1 if n_inst == 10 else 0)), 2))
            installments.append({"installment_id": f"{inv['invoice_id']}-{k + 1:02d}", "invoice_id": inv["invoice_id"], "installment_number": k + 1, "due_date": iso(date.fromisoformat(inv["due_date"]) + timedelta(days=30 * k)), "amount_due": amt})
        # payments
        due = date.fromisoformat(inv["due_date"])
        if inv["premium_finance"] == "Y":
            payments.append({"payment_id": f"{short[:3].upper()}-PMT{pay_seq:05d}", "client_id": p["client_id"], "invoice_id": inv["invoice_id"], "payment_date": iso(eff + timedelta(days=rng.int(3, 20))), "amount": total, "method": "ACH", "payer": inv["premium_finance_company"], "reference": f"PF{rng.int(100000, 999999)}", "applied": "Y", "deposited_to": "Premium Trust Account"}); pay_seq += 1
            inv["amount_paid"] = total; inv["balance"] = 0.0; inv["status"] = "Paid"
        elif due < TODAY:
            paid_frac = 1.0 if rng.chance(0.78) else rng.choice([0.0, 0.25, 0.5])
            if paid_frac > 0:
                payments.append({"payment_id": f"{short[:3].upper()}-PMT{pay_seq:05d}", "client_id": p["client_id"], "invoice_id": inv["invoice_id"], "payment_date": iso(due + timedelta(days=rng.int(-10, 25))), "amount": round(total * paid_frac, 2), "method": rng.choice(["Check", "ACH", "ACH", "Credit Card", "Wire"]), "payer": p["client_name"], "reference": f"CHK {rng.int(1000, 99999)}" if rng.chance(0.5) else f"ACH{rng.int(100000000, 999999999)}", "applied": "Y", "deposited_to": "Premium Trust Account"}); pay_seq += 1
            inv["amount_paid"] = round(total * paid_frac, 2); inv["balance"] = round(total - inv["amount_paid"], 2)
            inv["status"] = "Paid" if paid_frac == 1.0 else ("Partial" if paid_frac > 0 else "Past Due")
            if inv["balance"] > 0 and (TODAY - due).days > 60:
                inv["status"] = "Past Due >60"
        inv_seq += 1
    # unapplied cash
    for _ in range(2 if tier != "low" else 1):
        c = rng.choice(clients)
        payments.append({"payment_id": f"{short[:3].upper()}-PMT{pay_seq:05d}", "client_id": c["client_id"], "invoice_id": "", "payment_date": iso(rng.date_between(date(2026, 1, 1), TODAY)), "amount": rng.money(800, 9000, 1), "method": "Check", "payer": c["client_name"], "reference": f"CHK {rng.int(1000, 99999)}", "applied": "N - unapplied, no invoice reference", "deposited_to": "Premium Trust Account"}); pay_seq += 1
    past_due = [i for i in invoices if i["status"].startswith("Past Due") and (TODAY - date.fromisoformat(i["due_date"])).days > 90]
    if past_due:
        i0 = past_due[0]
        i0["cancellation_notice"] = "Carrier NOC issued - cancel eff " + iso(TODAY + timedelta(days=9))
        key.add(f"INS-AR-01-{short[:3].upper()}", "receivables", [short], "Agency-bill invoice >90 days past due with pending cancellation",
                f"Invoice {i0['invoice_id']} ({i0['client_id']}) balance {i0['balance']} is more than 90 days past due; carrier notice of cancellation is pending.",
                ["08_billing_ar/invoices", "08_billing_ar/payments"], "Escalate to producer; contact client; consider premium finance or return of policy.")
    aging = []
    for c in clients:
        cinv = [i for i in invoices if i["client_id"] == c["client_id"] and i["balance"] > 0]
        if not cinv:
            continue
        b = {"current": 0.0, "1_30": 0.0, "31_60": 0.0, "61_90": 0.0, "over_90": 0.0}
        for i in cinv:
            age = (TODAY - date.fromisoformat(i["due_date"])).days
            k = "current" if age <= 0 else "1_30" if age <= 30 else "31_60" if age <= 60 else "61_90" if age <= 90 else "over_90"
            b[k] = round(b[k] + i["balance"], 2)
        aging.append({"as_of_date": iso(TODAY), "client_id": c["client_id"], "client_name": c["client_name"], **b, "total_balance": round(sum(b.values()), 2)})

    # ---------------- carrier statements / commissions ----------------
    carrier_statements = []
    commission_statements = []
    producer_comm = []
    cs_seq = 1
    for k in my_carriers:
        kp = [p for p in policies if p["carrier_code"] == k["code"]]
        for me in month_ends(date(2025, 10, 1), TODAY):
            mp = [p for p in kp if date.fromisoformat(p["effective_date"]).month == me.month and date.fromisoformat(p["effective_date"]).year == me.year]
            if not mp:
                continue
            stmt_id = f"{k['code']}-STMT-{me.strftime('%Y%m')}"
            gross = 0.0
            comm_total = 0.0
            for p in mp:
                comm = p["expected_commission"]
                paid_comm = comm
                gross += p["annual_premium"]
                if p["billing_type"] == "Agency Bill":
                    carrier_statements.append({"statement_id": stmt_id, "carrier_code": k["code"], "statement_date": iso(me), "line_number": len([x for x in carrier_statements if x["statement_id"] == stmt_id]) + 1, "policy_number": p["policy_number"], "insured_name": p["client_name"], "transaction_type": "NB" if p["new_or_renewal"] == "New" else "RN",
                                               "effective_date": p["effective_date"], "gross_premium": p["annual_premium"], "commission_pct": p["commission_pct"], "commission_amount": comm, "net_premium_due_carrier": round(p["annual_premium"] - comm, 2), "matched_to_policy_id": p["policy_id"], "match_status": "Matched"})
                else:
                    commission_statements.append({"statement_id": stmt_id.replace("STMT", "COMM"), "carrier_code": k["code"], "statement_date": iso(me), "policy_number": p["policy_number"], "insured_name": p["client_name"], "transaction_type": "NB" if p["new_or_renewal"] == "New" else "RN",
                                                  "premium_basis": p["annual_premium"], "commission_pct_paid": p["commission_pct"], "commission_paid": paid_comm, "expected_commission": comm, "variance": 0.0, "matched_to_policy_id": p["policy_id"], "match_status": "Matched"})
                comm_total += paid_comm
            cs_seq += 1
    # Planted commission discrepancy (high): 12% expected, 10% paid
    if tier == "high" and commission_statements:
        target = next((c for c in commission_statements if c["carrier_code"] == "LIB" and c["premium_basis"] > 60000), commission_statements[0])
        target["commission_pct_paid"] = 10.0
        target["commission_paid"] = round(target["premium_basis"] * 0.10, 2)
        target["variance"] = round(target["commission_paid"] - target["expected_commission"], 2)
        target["match_status"] = "Matched - Rate Variance"
        key.add("INS-COMM-01", "commission", [short], "Carrier paid commission at 10% instead of contracted 12%",
                f"Policy {target['policy_number']} premium {target['premium_basis']}: expected {target['expected_commission']} at {12.0}%, statement {target['statement_id']} paid {target['commission_paid']} at 10%. Variance {target['variance']}.",
                ["09_carrier_payables_commissions/commission_statements", "02_policies_exposures/policies", "03_marketing_submissions/carriers"], "Create carrier inquiry task; request corrected statement.")
    if tier == "medium" and commission_statements:
        commission_statements.append({"statement_id": commission_statements[-1]["statement_id"], "carrier_code": commission_statements[-1]["carrier_code"], "statement_date": commission_statements[-1]["statement_date"], "policy_number": f"{commission_statements[-1]['carrier_code']}-BOP-{rng.int(1000000, 9999999)}", "insured_name": "Suncoast Dental Partners PA", "transaction_type": "RN",
                                      "premium_basis": 6420.0, "commission_pct_paid": 12.0, "commission_paid": 770.4, "expected_commission": "", "variance": "", "matched_to_policy_id": "", "match_status": "Unmatched - policy not in book"})
        key.add("INS-COMM-02", "commission", [short], "Commission statement line does not match any policy in the book",
                "Carrier statement includes 'Suncoast Dental Partners PA' which is not a client; likely a BOR to another agency or a carrier mis-post.",
                ["09_carrier_payables_commissions/commission_statements", "02_policies_exposures/policies"], "Investigate with carrier before recognizing revenue; possible BOR letter or mis-posted agency code.")
    for p in policies:
        prod = next(e for e in employees if e["employee_id"] == p["producer_id"])
        split = rng.choice([0.30, 0.35, 0.40]) if p["new_or_renewal"] == "New" else rng.choice([0.20, 0.25])
        producer_comm.append({"policy_id": p["policy_id"], "producer_id": prod["employee_id"], "producer_name": prod["full_name"], "agency_commission": p["expected_commission"], "producer_split_pct": int(split * 100), "producer_commission": round(p["expected_commission"] * split, 2), "house_commission": round(p["expected_commission"] * (1 - split), 2), "payable_month": p["effective_date"][:7], "paid": "Y" if date.fromisoformat(p["effective_date"]) < TODAY - timedelta(days=45) else "N"})

    # ---------------- compliance / licensing ----------------
    licenses = []
    for e in producers + account_managers:
        for st in sorted({hq_state} | ({rng.choice(["NY", "MA", "NJ", "GA", "AL", "OH"])} if rng.chance(0.5) else set())):
            exp = rng.date_between(date(2026, 1, 1), date(2027, 12, 31))
            licenses.append({"employee_id": e["employee_id"], "licensee_name": e["full_name"], "npn": rng.int(10000000, 19999999), "state": st, "license_type": "Property & Casualty Producer", "license_number": f"{rng.int(1000000, 9999999)}", "issue_date": iso(exp - timedelta(days=730)), "expiration_date": iso(exp),
                             "ce_hours_required": 24, "ce_hours_completed": rng.choice([24, 24, 18, 12, 6]), "resident": "Y" if st == hq_state else "N", "status": "Active" if exp > TODAY else "EXPIRED"})
    if tier == "low":
        licenses[1]["expiration_date"] = iso(TODAY - timedelta(days=41)); licenses[1]["status"] = "EXPIRED"
        key.add("INS-COMP-01", "compliance", [short], "Producer writing business on an expired license", f"{licenses[1]['licensee_name']} ({licenses[1]['state']}) license expired {licenses[1]['expiration_date']} but has policies effective after that date.",
                ["10_compliance_licensing/licenses", "02_policies_exposures/policies"], "Immediate escalation to compliance; stop new binds; file renewal.")
    appointments = [{"carrier_code": k["code"], "carrier_name": k["name"], "state": hq_state, "appointment_type": "Agency", "appointment_date": r["appointment_date"], "status": "Active", "binding_authority": rng.choice(["None", "BOP < $10k premium", "Auto - up to 10 units", "None"]), "agency_agreement_on_file": "Y" if tier != "low" or rng.chance(0.5) else "N"} for k, r in zip(my_carriers, carriers_rows)]
    sl_filings = [{"filing_id": f"SL-{p['policy_id']}", "policy_id": p["policy_id"], "state": hq_state, "carrier_code": p["carrier_code"], "premium": p["annual_premium"], "sl_tax_pct": 4.0, "sl_tax": round(p["annual_premium"] * 0.04, 2), "stamping_fee": round(p["annual_premium"] * 0.00175, 2), "diligent_search_form": "Y" if rng.chance(0.85) else "N", "filed_date": iso(date.fromisoformat(p["effective_date"]) + timedelta(days=rng.int(10, 45))), "status": rng.choice(["Filed", "Filed", "Pending"])} for p in policies if p["surplus_lines"] == "Y"]
    eo = {"policy_type": "Agency E&O", "carrier": rng.choice(["Swiss Re Corporate Solutions", "Westport Insurance Corp (Swiss Re)", "Utica National"]), "limit": rng.choice([1000000, 2000000, 5000000]), "deductible": rng.choice([10000, 25000]), "annual_premium": rng.int(18, 62) * 1000, "expiration_date": iso(rng.date_between(date(2026, 5, 1), date(2027, 2, 1)))}

    # ---------------- finance: vendors, AP, GL, bank, software ----------------
    vendors = []
    vid = 1
    for vkey, v in SHARED_VENDORS.items():
        if short in v["variants"]:
            vendors.append({"vendor_id": f"V{vid:03d}", "vendor_name": v["variants"][short], "category": v["category"], "payment_terms": rng.choice(["Net 30", "Net 30", "Net 15", "Due on receipt"]), "tax_id_on_file": "Y" if tier == "high" or rng.chance(0.6) else "N", "1099_eligible": "N", "status": "Active", "_key": vkey}); vid += 1
    for tv in TRAP_VENDORS:
        if tv["company"] == short:
            vendors.append({"vendor_id": f"V{vid:03d}", "vendor_name": tv["name"], "category": tv["category"], "payment_terms": "Net 30", "tax_id_on_file": "Y", "1099_eligible": "Y", "status": "Active", "_key": "trap"}); vid += 1
    local = [("Landlord - " + rng.choice(["Constitution Plaza Assoc.", "Bayshore Office Park LLC", "Lackawanna Ave Realty"]), "Rent"), (rng.choice(["Eversource", "TECO Energy", "PPL Electric"]), "Utilities"), (rng.choice(["Comcast Business", "Spectrum Business", "Frontier"]), "Telecom / Internet"), ("Marsh Berry (consulting)" if tier == "high" else rng.choice(["Local CPA - " + rng.person(), "Kaplan & Sons CPA"]), "Professional Fees"), (rng.choice(["Big I " + hq_state, "IIAB" + hq_state, "PIA " + hq_state]), "Association Dues"), (eo["carrier"], "E&O Insurance"), (rng.choice(["Shred-it", "ProShred"]), "Shredding"), (rng.choice(["Coffee Service Co", "Aramark Refreshments"]), "Office"), (rng.choice(["Local Print Shop", "Vistaprint"]), "Marketing")]
    for n, cat in local:
        vendors.append({"vendor_id": f"V{vid:03d}", "vendor_name": n, "category": cat, "payment_terms": "Net 30", "tax_id_on_file": "Y" if rng.chance(0.8) else "N", "1099_eligible": "Y" if cat in ("Professional Fees",) else "N", "status": "Active", "_key": ""}); vid += 1
    # software
    sw_choice = {"Meridian": ["Applied Epic", "IVANS Exchange", "Zywave Miedge", "Indio", "DocuSign eSignature", "Microsoft 365 Business Standard", "Salesforce Sales Cloud", "Zoom Workplace", "RingCentral MVP", "Sage Intacct", "ADP Workforce Now", "Bill.com", "Expensify", "Box Business", "Slack Business+", "KnowBe4"],
                 "Harborline": ["Vertafore AMS360", "IVANS Exchange", "Zywave Miedge", "Adobe Acrobat Sign", "Microsoft 365 Business Standard", "HubSpot Sales Hub", "Zoom Workplace", "8x8 Work", "QuickBooks Online Advanced", "Paychex Flex", "Dropbox Business", "KnowBe4"],
                 "Castlebrook": ["HawkSoft CMS", "Microsoft 365 Business Standard", "QuickBooks Desktop Enterprise", "Gusto", "Dropbox Business", "Zoom Workplace", "DocuSign eSignature"]}[short]
    software = []
    for i, prod in enumerate(sw_choice):
        vendor, func, unit = SOFTWARE[prod]
        seats = {"Agency Management System": profile["employees"], "Productivity Suite / Email": profile["employees"] + rng.int(0, 6), "e-Signature": rng.int(3, 12), "CRM": len(producers) + 2, "Video Conferencing": rng.int(5, profile["employees"]), "VoIP Phone System": profile["employees"], "Payroll / HRIS": profile["employees"], "Security Awareness Training": profile["employees"], "Team Messaging": profile["employees"], "File Storage": profile["employees"], "Expense Management": rng.int(8, 20)}.get(func, 1)
        annual = round(unit * seats * rng.money(0.85, 1.15, 0.01), 2) if unit else 0
        software.append({"subscription_id": f"SW{i + 1:03d}", "product": prod, "vendor": vendor, "function": func, "seats_licensed": seats, "seats_active": max(1, seats - rng.int(0, max(1, seats // 5))), "billing_frequency": rng.choice(["Annual", "Annual", "Monthly"]), "annual_cost": annual,
                         "contract_start": iso(rng.date_between(date(2023, 1, 1), date(2025, 12, 1))), "renewal_date": iso(rng.date_between(TODAY, date(2027, 3, 1))), "auto_renew": rng.choice(["Y", "Y", "N"]), "owner": rng.choice(employees)["full_name"], "notes": rng.choice(["", "", "3-year term; 60-day notice", "Month-to-month", "Includes 2 sandbox seats"])})
    # E&O appears via vendor AP
    ap_invoices = []
    ap_seq = 1
    for me in month_ends(date(2025, 4, 1), TODAY):
        for v in vendors:
            if v["category"] in ("Rent", "Utilities", "Telecom / Internet", "Records Storage / Shredding", "Shredding", "Office", "Office Supplies", "MRO Supplies") or rng.chance(0.25):
                amt = {"Rent": rng.int(7, 22) * 1000, "Utilities": rng.money(600, 2400, 1), "Telecom / Internet": rng.money(400, 1800, 1), "Records Storage / Shredding": rng.money(180, 650, 1), "Shredding": rng.money(60, 140, 1), "Office": rng.money(80, 400, 1), "Office Supplies": rng.money(120, 900, 1), "Professional Fees": rng.money(1500, 9000, 1), "E&O Insurance": eo["annual_premium"] / 12, "Association Dues": rng.money(300, 2500, 1), "Landscaping": rng.money(350, 900, 1), "HR Consulting": rng.money(2000, 6000, 1), "Marketing": rng.money(200, 3000, 1), "MRO Supplies": rng.money(90, 500, 1)}.get(v["category"], rng.money(100, 2000, 1))
                inv_d = me - timedelta(days=rng.int(0, 27))
                ap_invoices.append({"ap_invoice_id": f"AP{ap_seq:05d}", "vendor_id": v["vendor_id"], "vendor_name": v["vendor_name"], "invoice_number": f"{rng.int(10000, 999999)}", "invoice_date": iso(inv_d), "due_date": iso(inv_d + timedelta(days=30)), "amount": round(amt, 2), "gl_account": {"Rent": "6300", "Utilities": "6700", "Telecom / Internet": "6700", "Professional Fees": "6500", "E&O Insurance": "6650", "Association Dues": "6500"}.get(v["category"], "6700"),
                                    "approved_by": rng.choice(employees)["full_name"], "status": "Paid" if inv_d < TODAY - timedelta(days=35) else rng.choice(["Approved - scheduled", "Pending approval", "Paid"]), "paid_date": iso(inv_d + timedelta(days=rng.int(20, 45))) if inv_d < TODAY - timedelta(days=35) else "", "payment_method": rng.choice(["ACH", "Check", "Corporate Card"]), "category": v["category"]})
                ap_seq += 1
    for s in software:
        n = 12 if s["billing_frequency"] == "Monthly" else 1
        for k in range(n):
            d = rng.date_between(date(2025, 4, 1), TODAY)
            ap_invoices.append({"ap_invoice_id": f"AP{ap_seq:05d}", "vendor_id": "", "vendor_name": s["vendor"], "invoice_number": f"INV-{rng.int(100000, 9999999)}", "invoice_date": iso(d), "due_date": iso(d + timedelta(days=30)), "amount": round(s["annual_cost"] / n, 2), "gl_account": "6400", "approved_by": s["owner"], "status": "Paid", "paid_date": iso(d + timedelta(days=rng.int(2, 30))), "payment_method": "Corporate Card", "category": "Software Subscriptions"}); ap_seq += 1
    for v in vendors:
        v.pop("_key", None)

    coa = [{"account_number": a, "account_name": n, "account_type": t} for a, n, t in gl_chart("insurance")]
    gl = []
    je = 1
    for me in month_ends(date(2025, 4, 1), TODAY):
        mstr = me.strftime("%Y-%m")
        comm_ab = round(sum(p["expected_commission"] for p in policies if p["billing_type"] == "Agency Bill" and p["effective_date"][:7] == mstr), 2)
        comm_db = round(sum(p["expected_commission"] for p in policies if p["billing_type"] == "Direct Bill" and p["effective_date"][:7] == mstr), 2)
        payroll = round(sum(e["annual_salary"] for e in employees if e["status"] == "Active") / 12, 2)
        gl += [{"journal_id": f"JE{je:05d}", "posting_date": iso(me), "account_number": "1200", "account_name": "Premiums Receivable (Agency Bill)", "debit": comm_ab, "credit": 0, "memo": f"Agency bill commission earned {mstr}", "source": "AMS"},
               {"journal_id": f"JE{je:05d}", "posting_date": iso(me), "account_number": "4000", "account_name": "Commission Income - Agency Bill", "debit": 0, "credit": comm_ab, "memo": f"Agency bill commission earned {mstr}", "source": "AMS"},
               {"journal_id": f"JE{je + 1:05d}", "posting_date": iso(me), "account_number": "1000", "account_name": "Operating Cash", "debit": comm_db, "credit": 0, "memo": f"Direct bill commissions received {mstr}", "source": "Bank"},
               {"journal_id": f"JE{je + 1:05d}", "posting_date": iso(me), "account_number": "4010", "account_name": "Commission Income - Direct Bill", "debit": 0, "credit": comm_db, "memo": f"Direct bill commissions received {mstr}", "source": "Bank"},
               {"journal_id": f"JE{je + 2:05d}", "posting_date": iso(me), "account_number": "6000", "account_name": "Salaries & Wages", "debit": payroll, "credit": 0, "memo": f"Payroll {mstr}", "source": "Payroll"},
               {"journal_id": f"JE{je + 2:05d}", "posting_date": iso(me), "account_number": "1000", "account_name": "Operating Cash", "debit": 0, "credit": payroll, "memo": f"Payroll {mstr}", "source": "Payroll"}]
        ap_m = round(sum(a["amount"] for a in ap_invoices if a["invoice_date"][:7] == mstr), 2)
        gl += [{"journal_id": f"JE{je + 3:05d}", "posting_date": iso(me), "account_number": "6700", "account_name": "Office & Supplies", "debit": ap_m, "credit": 0, "memo": f"AP invoices {mstr} (summary)", "source": "AP"},
               {"journal_id": f"JE{je + 3:05d}", "posting_date": iso(me), "account_number": "2000", "account_name": "Accounts Payable", "debit": 0, "credit": ap_m, "memo": f"AP invoices {mstr} (summary)", "source": "AP"}]
        je += 4
    bank = []
    bal = 412_500.00
    for pm in sorted(payments, key=lambda x: x["payment_date"]):
        try:
            d = pm["payment_date"]
            bal = round(bal + pm["amount"], 2)
            bank.append({"account": "Premium Trust Acct x4471", "date": d, "description": f"DEPOSIT {pm['method'].upper()} {pm['payer'][:22].upper()} {pm['reference']}", "amount": pm["amount"], "balance": bal})
        except Exception:
            pass
    for cs_id in sorted({c["statement_id"] for c in carrier_statements}):
        lines_ = [c for c in carrier_statements if c["statement_id"] == cs_id]
        amt = round(sum(l["net_premium_due_carrier"] for l in lines_), 2)
        d = date.fromisoformat(lines_[0]["statement_date"]) + timedelta(days=rng.int(20, 40))
        if d <= TODAY:
            bal = round(bal - amt, 2)
            bank.append({"account": "Premium Trust Acct x4471", "date": iso(d), "description": f"ACH DEBIT {lines_[0]['carrier_code']} ACCT CURRENT {cs_id}", "amount": -amt, "balance": bal})
    bank.sort(key=lambda x: x["date"])

    # ---------------- HR / payroll ----------------
    payroll = []
    for me in month_ends(date(2025, 10, 1), TODAY):
        for e in employees:
            if e["status"] != "Active":
                continue
            gross = round(e["annual_salary"] / 24, 2)
            for half in (15, me.day):
                pd = date(me.year, me.month, half)
                payroll.append({"pay_date": iso(pd), "employee_id": e["employee_id"], "employee_name": e["full_name"], "gross_pay": gross, "commission_bonus": round(rng.money(0, 4000, 1), 2) if e["role"] == "Producer" and half != 15 else 0, "federal_withholding": round(gross * 0.16, 2), "state_withholding": round(gross * (0.0 if hq_state == "FL" else 0.045), 2), "fica": round(gross * 0.0765, 2), "401k_employee": round(gross * rng.choice([0, 0.03, 0.05, 0.06]), 2), "health_premium_employee": rng.choice([0, 142.5, 310.0, 485.0]), "net_pay": 0})
    for r in payroll:
        r["net_pay"] = round(r["gross_pay"] + r["commission_bonus"] - r["federal_withholding"] - r["state_withholding"] - r["fica"] - r["401k_employee"] - r["health_premium_employee"], 2)
    pto = [{"employee_id": e["employee_id"], "employee_name": e["full_name"], "as_of": iso(TODAY), "pto_accrued_hours": rng.int(40, 160), "pto_used_hours": rng.int(0, 96), "sick_balance_hours": rng.int(0, 48)} for e in employees if e["status"] == "Active"]

    # ---------------- documents / emails / ACORD ----------------
    documents = []
    for p in policies:
        documents.append({"document_id": f"DOC-{p['policy_id']}-POL", "client_id": p["client_id"], "policy_id": p["policy_id"], "document_type": "Policy", "file_name": f"{p['policy_number']}_Policy.pdf", "received_date": iso(date.fromisoformat(p["effective_date"]) + timedelta(days=rng.int(5, 60))), "source": "Carrier download" if rng.chance(0.6) else "Email attachment", "indexed_by": p["account_manager_id"], "pages": rng.int(24, 140)})
        if rng.chance(0.5):
            documents.append({"document_id": f"DOC-{p['policy_id']}-APP", "client_id": p["client_id"], "policy_id": p["policy_id"], "document_type": "ACORD 125 Application", "file_name": f"{p['client_id']}_ACORD125_{p['effective_date'][:4]}.pdf", "received_date": iso(date.fromisoformat(p["effective_date"]) - timedelta(days=rng.int(30, 90))), "source": "Indio" if short == "Meridian" else "Email attachment", "indexed_by": p["account_manager_id"], "pages": rng.int(4, 12)})
    for c in certs_issued[:20]:
        documents.append({"document_id": f"DOC-{c['certificate_id']}", "client_id": c["client_id"], "policy_id": "", "document_type": "Certificate of Insurance (ACORD 25)", "file_name": f"{c['certificate_id']}_{c['holder'].split(' ')[0]}.pdf", "received_date": c["issued_date"], "source": "Generated", "indexed_by": c["issued_by"], "pages": 1})
    # ACORD 125 sample as JSON (structured) for first client
    c0 = clients[0]
    p0 = next(p for p in policies if p["client_id"] == c0["client_id"])
    acord125 = {"form": "ACORD 125 (2016/03) - Commercial Insurance Application", "agency": {"name": profile["name"], "address": f"{hq_city}, {hq_state}"}, "applicant": {"named_insured": c0["client_name"], "fein": c0["fein"], "naics": c0["naics_code"], "mailing_address": f"{c0['billing_street']}, {c0['billing_city']}, {c0['billing_state']} {c0['billing_zip']}", "entity_type": "LLC" if "LLC" in c0["client_name"] else "Corporation", "years_in_business": rng.int(4, 40), "annual_revenue": c0["annual_revenue"], "employees_full_time": c0["employee_count"]},
                "policy_information": {"proposed_effective_date": p0["effective_date"], "proposed_expiration_date": p0["expiration_date"], "lines_requested": c0["lines_of_business"].split(","), "billing_plan": p0["billing_type"]}, "premises": [{"loc": l["location_number"], "address": f"{l['street']}, {l['city']}, {l['state']} {l['zip']}", "occupancy": l["occupancy"], "sq_ft": l["square_feet"]} for l in locations if l["client_id"] == c0["client_id"]],
                "loss_history_5yr": [{"date": cl["date_of_loss"], "type": cl["claim_type"], "paid": cl["paid_to_date"]} for cl in claims if cl["client_id"] == c0["client_id"]], "signature": {"producer": c0["producer_name"], "date": iso(date.fromisoformat(p0["effective_date"]) - timedelta(days=40))}}
    if tier == "low":
        tp = truck_pol
        acord127 = {"form": "ACORD 127 - Business Auto Section", "named_insured": tp["client_name"], "policy_number_expiring": tp["policy_number"], "total_vehicles_scheduled": 47, "note": "Schedule attached separately (Excel). 2 units added Jan-2026 not yet reflected in AMS.", "effective_date": tp["effective_date"]}
    emails = []
    dt0 = datetime(2026, 3, 2, 9, 14)
    am0 = account_managers[0]
    cl = clients[1]; ct = next(x for x in contacts if x["client_id"] == cl["client_id"])
    emails.append(("cert_request_" + cl["client_id"], eml(ct["email"], am0["email"], f"COI needed - {cl['client_name']} - {rng.choice(holders)}", f"Hi {am0['full_name'].split()[0]},\n\nCan you send a certificate to the holder below by Friday? They need us listed as additional insured, waiver of subrogation, and $2M umbrella.\n\nHolder: {rng.choice(holders)}\n\nThanks,\n{ct['full_name']}\n{ct['title']}, {cl['client_name']}", dt0)))
    ren = renewals[0] if renewals else None
    if ren:
        rc = next(c for c in clients if c["client_id"] == ren["client_id"]); rct = next(x for x in contacts if x["client_id"] == rc["client_id"])
        emails.append(("renewal_exposure_request_" + rc["client_id"], eml(am0["email"], rct["email"], f"{rc['client_name']} - {ren['line_of_business']} renewal {ren['expiration_date']} - updated exposures needed", f"Hello {rct['full_name'].split()[0]},\n\nYour {LINES[ren['line_of_business']][0]} policy renews on {ren['expiration_date']}. To market the renewal we need:\n  1. Updated payroll by class / revenue estimate for the coming year\n  2. Current vehicle and driver list\n  3. Any changes in operations or locations\n\nPlease return by {iso(TODAY + timedelta(days=10))}.\n\nBest regards,\n{am0['full_name']}\n{profile['name']}", dt0 + timedelta(days=1, hours=2))))
    uw = underwriters[0]
    emails.append(("carrier_question_" + uw["carrier_code"], eml(uw["email"], am0["email"], f"RE: Submission - {clients[2]['client_name']} - GL/Umb {policies[0]['effective_date']}", f"{am0['full_name'].split()[0]},\n\nThanks for the submission. Before we can release terms we need:\n- 5-year currently valued loss runs (the ones attached are 14 months old)\n- Confirmation of no residential roofing work\n- Completed contractors supplemental\n\nQuote deadline on our side is {iso(TODAY + timedelta(days=6))}.\n\nRegards,\n{uw['underwriter_name']}\nUnderwriter, {next(k['name'] for k in CARRIERS if k['code'] == uw['carrier_code'])}", dt0 + timedelta(days=3))))
    if tier == "high":
        emails.append(("commission_inquiry_LIB", eml(next(e for e in employees if e["role"] == "Accounting Specialist")["email"], "agencycommissions@libertymutual.com", "Commission rate variance - March statement", "Hello,\n\nOur March commission statement shows one direct-bill renewal paid at 10%. Our agency agreement (Schedule A, rev. 2024) specifies 12% for GL/Auto renewals. Please review and issue a corrected statement.\n\nThank you,\nAccounting, " + profile["name"], dt0 + timedelta(days=20))))
    if tier == "low":
        emails.append(("po_style_vehicle_add", eml(ct["email"], am0["email"], "Fwd: new trucks", "hey can u add these 2 to the policy asap, drivers r starting monday\n\n2025 Freightliner Cascadia 3AKJHHDR5SSNK1187\n2024 Great Dane trailer 1GRAA0625RB712209\n\nthx", dt0 + timedelta(days=5))))

    # ---------------- workflow events ----------------
    events = []
    ev = 1
    def add_event(wf, otype, oid, ts, actor, action, prev, new, src="", conf=1.0, review="N"):
        nonlocal ev
        events.append({"event_id": f"EV{ev:06d}", "workflow_type": wf, "object_type": otype, "object_id": oid, "timestamp": iso(ts), "actor": actor, "action": action, "previous_state": prev, "new_state": new, "source_document": src, "confidence": conf, "requires_review": review}); ev += 1
    for s in submissions:
        d = datetime.fromisoformat(s["submitted_date"]) if len(s["submitted_date"]) == 10 else datetime(2026, 1, 1)
        am = next(p for p in policies if p["policy_id"] == s["policy_id"])["account_manager_id"]
        add_event("marketing", "submission", s["submission_id"], d + timedelta(hours=rng.int(8, 17)), am, "submitted", "draft", "submitted", f"{s['submission_id']}_package.pdf")
        if s["acknowledged_date"]:
            add_event("marketing", "submission", s["submission_id"], datetime.fromisoformat(s["acknowledged_date"]) + timedelta(hours=10), "carrier:" + s["carrier_code"], "acknowledged", "submitted", "acknowledged")
    for q in quotes:
        add_event("quote", "quote", q["quote_id"], datetime.fromisoformat(q["quote_date"]) + timedelta(hours=rng.int(9, 16)), "carrier:" + q["carrier_code"], "quote_received", "", "received", f"{q['quote_id']}.pdf", 0.92, "Y" if rng.chance(0.2) else "N")
    for cr in cert_requests:
        d = datetime.fromisoformat(cr["requested_date"]) + timedelta(hours=rng.int(8, 17))
        add_event("certificates", "certificate_request", cr["certificate_request_id"], d, "system:email_intake", "request_received", "", "open", "email", 0.88, "N")
        if cr["status"] == "Issued":
            add_event("certificates", "certificate_request", cr["certificate_request_id"], d + timedelta(hours=cr["turnaround_hours"] or 4), cr["handled_by"], "certificate_issued", "open", "issued", cr["certificate_request_id"].replace("CERT", "COI") + ".pdf")
        else:
            add_event("certificates", "certificate_request", cr["certificate_request_id"], d + timedelta(hours=2), cr["handled_by"], "exception_flagged", "open", "exception", "", 0.97, "Y")
    for e_ in endorsements:
        d = datetime.fromisoformat(e_["submitted_to_carrier_date"]) + timedelta(hours=10)
        add_event("endorsements", "endorsement_request", e_["endorsement_request_id"], d, e_["handled_by"], "sent_to_carrier", "requested", "pending_carrier")
        if e_["received_date"]:
            add_event("endorsements", "endorsement_request", e_["endorsement_request_id"], datetime.fromisoformat(e_["received_date"]) + timedelta(hours=11), "carrier:" + e_["carrier_code"], "endorsement_received", "pending_carrier", e_["status"].lower().replace(" ", "_"), e_["carrier_endorsement_number"] + ".pdf", 0.9, "Y" if "Discrepancy" in e_["status"] else "N")
    for pm in payments:
        add_event("billing", "payment", pm["payment_id"], datetime.fromisoformat(pm["payment_date"]) + timedelta(hours=14), "accounting", "cash_applied" if pm["applied"] == "Y" else "cash_unapplied", "received", "applied" if pm["applied"] == "Y" else "unapplied", pm["reference"], 1.0, "N" if pm["applied"] == "Y" else "Y")
    for cs in commission_statements:
        add_event("commissions", "commission_line", cs["statement_id"] + ":" + cs["policy_number"], datetime.fromisoformat(cs["statement_date"]) + timedelta(days=3, hours=10), "accounting", "statement_line_matched" if cs["match_status"] == "Matched" else "statement_line_exception", "", cs["match_status"], cs["statement_id"] + ".pdf", 0.95, "N" if cs["match_status"] == "Matched" else "Y")
    for t in renewal_tasks:
        if t["completed_date"]:
            add_event("renewals", "renewal_task", t["task_id"], datetime.fromisoformat(t["completed_date"]) + timedelta(hours=15), t["assigned_to"], "task_completed", "open", "complete")
    events.sort(key=lambda x: x["timestamp"])

    # ---------------- operating policy doc / profile ----------------
    op_policy = f"""# {profile['name']} — Operating Procedures (excerpt)

_Last revised: {'2025-09-15' if tier == 'high' else '2022-03-01' if tier == 'medium' else '2016 (handwritten notes transcribed 2024)'}_

## Renewal timeline
- 120 days: renewal identified in {profile['ams']}; account manager sends exposure questionnaire.
- 90 days: updated exposures and loss runs on file; producer decides renew-as-is vs. market.
- 60 days: submissions out to a minimum of {'three' if tier != 'low' else 'two'} markets when marketing.
- 30 days: proposal delivered to client; signed bind order required before binding.

## Certificates of insurance
- Turnaround target: {'same business day' if tier == 'high' else '2 business days'}.
- Certificates may only reflect coverage actually in force. Requests for limits or endorsements not on the policy are routed to the account manager as exceptions — never issued "as requested".
- {'All certificates issued via Applied Epic certificate module; holders stored on account.' if tier == 'high' else 'Certificates generated from ACORD 25 template in Word; holders tracked in spreadsheet.'}

## Agency bill / receivables
- Premium invoices due on the policy effective date unless a payment plan is approved.
- {'Fiduciary funds held in Premium Trust account; monthly trust reconciliation signed by CFO.' if tier != 'low' else 'Premium checks deposited to operating account and swept monthly (needs cleanup).'}
- Accounts >60 days past due: producer notified; >90 days: cancellation-for-nonpayment request unless payment plan in place.

## Commissions
- Contracted rates per carrier agreement (see carriers table). Statements reconciled monthly; variances >$50 investigated.
- Producer splits: {'35% new / 25% renewal' if tier != 'low' else '40% new / 20% renewal (verbal agreement with producers)'}.

## Systems
- Agency management system: {profile['ams']}.
- {'Carrier downloads via IVANS for all appointed carriers.' if tier != 'low' else 'Carrier downloads not configured; policies keyed manually from PDFs.'}
"""
    company_profile = {"company": profile["name"], "short_name": short, "industry": "Insurance Brokerage (Commercial P&C)", "headquarters": f"{hq_city}, {hq_state}", "founded": profile["founded"], "employees": profile["employees"], "agency_management_system": profile["ams"], "data_quality_tier": tier, "as_of_date": iso(TODAY), "description": profile["description"], "annual_revenue_estimate": round(sum(p["expected_commission"] for p in policies) * 1.05, 2), "total_written_premium": round(sum(p["annual_premium"] for p in policies), 2), "client_count": len(clients), "policy_count": len(policies), "carriers": [k["code"] for k in my_carriers], "agency_eo": eo, "synthetic": True}

    # ---------------- register datasets ----------------
    em.add_text("00_company", "company_profile", json.dumps(company_profile, indent=2), "json")
    em.add_text("00_company", "operating_procedures", op_policy, "md")
    em.add("01_clients_crm", "clients", clients)
    em.add("01_clients_crm", "contacts", contacts)
    em.add("01_clients_crm", "locations", locations)
    em.add("01_clients_crm", "account_activities", _activities(rng, clients, employees, tier))
    em.add("02_policies_exposures", "policies", policies)
    em.add("02_policies_exposures", "coverages", coverages)
    em.add("02_policies_exposures", "vehicles", vehicles)
    em.add("02_policies_exposures", "drivers", drivers)
    em.add("02_policies_exposures", "payroll_exposures", payroll_exp)
    em.add("03_marketing_submissions", "carriers", carriers_rows)
    em.add("03_marketing_submissions", "underwriters", underwriters)
    em.add("03_marketing_submissions", "submissions", submissions)
    em.add("03_marketing_submissions", "quotes", quotes)
    em.add("03_marketing_submissions", "declinations", declinations)
    em.add("04_renewals", "renewals", renewals)
    em.add("04_renewals", "renewal_tasks", renewal_tasks)
    em.add("05_certificates", "certificate_requests", cert_requests)
    em.add("05_certificates", "certificates_issued", certs_issued)
    em.add("06_endorsements", "endorsement_requests", endorsements)
    em.add("07_claims", "claims", claims)
    em.add("07_claims", "loss_run_requests", loss_runs)
    em.add("08_billing_ar", "invoices", invoices)
    em.add("08_billing_ar", "installment_schedules", installments)
    em.add("08_billing_ar", "payments", payments)
    em.add("08_billing_ar", "ar_aging", aging)
    em.add("09_carrier_payables_commissions", "carrier_statements", carrier_statements)
    em.add("09_carrier_payables_commissions", "commission_statements", commission_statements)
    em.add("09_carrier_payables_commissions", "producer_commissions", producer_comm)
    em.add("10_compliance_licensing", "licenses", licenses)
    em.add("10_compliance_licensing", "carrier_appointments", appointments)
    em.add("10_compliance_licensing", "surplus_lines_filings", sl_filings)
    em.add("11_finance_gl", "chart_of_accounts", coa)
    em.add("11_finance_gl", "general_ledger", gl)
    em.add("11_finance_gl", "vendors", vendors)
    em.add("11_finance_gl", "ap_vendor_invoices", ap_invoices)
    em.add("11_finance_gl", "bank_statement_premium_trust", bank)
    em.add("11_finance_gl", "software_subscriptions", software)
    em.add("12_hr_payroll", "employees", employees)
    em.add("12_hr_payroll", "payroll_register", payroll)
    em.add("12_hr_payroll", "pto_balances", pto)
    em.add("13_documents_emails", "document_index", documents)
    em.add_text("13_documents_emails", f"acord_125_{c0['client_id']}", json.dumps(acord125, indent=2), "json")
    if tier == "low":
        em.add_text("13_documents_emails", "acord_127_vehicle_schedule", json.dumps(acord127, indent=2), "json")
    for name, text in emails:
        em.add_text("13_documents_emails", name, text, "eml")
    em.add("14_workflow_events", "workflow_events", events)

    # ---------------- tier-specific write ----------------
    if tier == "high":
        manifest = em.write_all()
    elif tier == "medium":
        manifest = em.write_all(
            drop=["account_activities", "pto_balances"],
            merge_to_workbook=["ar_aging", "installment_schedules"],
            renames={"quotes": {"quoted_premium": "Premium", "deductible": "Ded"}},
            header_style="title")
    else:
        manifest = em.write_all(
            drop=["workflow_events", "commission_statements", "producer_commissions", "declinations", "underwriters", "account_activities", "pto_balances", "surplus_lines_filings", "installment_schedules", "ar_aging", "renewal_tasks", "certificates_issued", "general_ledger"],
            merge_to_workbook=["clients", "contacts", "locations", "policies", "coverages", "vehicles", "drivers", "payroll_exposures", "renewals", "licenses", "carrier_appointments"],
            renames={"policies": {"effective_date": "EXP_DATE", "expiration_date": "EFF_DATE", "annual_premium": "PREM", "expected_commission": "COMM_EST"},
                     "vendors": {"vendor_name": "CUSTOMER"},
                     "invoices": {"total_amount": "PREMIUM", "premium": "BASE_PREM"},
                     "claims": {"paid_to_date": "RESERVE", "outstanding_reserve": "PAID"}},
            header_style="legacy")
        key.add("INS-DQ-06", "data_quality", [short], "Swapped column headers in legacy policy export",
                "In the MASTER_WORKBOOK 'policies' sheet the EXP_DATE column actually holds effective dates and EFF_DATE holds expirations (all EXP_DATE < EFF_DATE). Similarly in claims.csv 'RESERVE' holds paid-to-date and 'PAID' holds outstanding reserve; in vendors.csv the vendor name column is labelled CUSTOMER.",
                ["00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx", "07_claims/claims", "11_finance_gl/vendors"],
                "Infer semantics from values (date ordering, closed claims having reserve = 0); propose column mapping and require human confirmation.")
        key.add("INS-DQ-07", "data_quality", [short], "Datasets missing entirely",
                "Castlebrook has no workflow event log, no commission statements, no producer commission records, no GL detail, no renewal task tracker and no issued-certificate register. Commission income can only be inferred from bank deposits and the carriers table.",
                ["00_company/company_profile"], "Report gaps explicitly; recommend enabling carrier downloads and a commission reconciliation process; do not fabricate missing history.")
    em.resolve_evidence(key, short)
    return {"slug": profile["slug"], "name": profile["name"], "short": short, "tier": tier, "files": manifest, "dropped_datasets": em.dropped, "merged_into_legacy_workbook": em.merged_into_workbook}


def _activities(rng: Rng, clients: list[Row], employees: list[Row], tier: str) -> list[Row]:
    out = []
    kinds = ["Phone call - coverage question", "Email - billing inquiry", "Email - certificate request", "Meeting - renewal review", "Email - auto ID card request", "Phone call - claim reported", "Email - policy copy request", "Note - client added location", "Email - loss run request to carrier", "Task - follow up on unpaid invoice"]
    n = {"high": 220, "medium": 120, "low": 40}[tier]
    for i in range(n):
        c = rng.choice(clients)
        d = rng.date_between(date(2025, 10, 1), TODAY)
        out.append({"activity_id": f"ACT{i + 1:05d}", "client_id": c["client_id"], "activity_date": iso(d), "activity_type": rng.choice(kinds), "logged_by": rng.choice(employees)["employee_id"], "minutes_spent": rng.choice([5, 10, 15, 20, 30, 45, 60]), "follow_up_due": iso(d + timedelta(days=rng.int(1, 14))) if rng.chance(0.4) else "", "closed": "Y" if d < TODAY - timedelta(days=7) else rng.choice(["Y", "N"])})
    return out
