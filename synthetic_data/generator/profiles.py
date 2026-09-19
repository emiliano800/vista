"""Company profiles and cross-company (portfolio-level) reference data.

Data-quality tiers
------------------
high   : full AMS/ERP export. All datasets present, clean snake_case CSV/JSON,
         ISO dates, referential integrity.
medium : most datasets present, a couple exported as .xlsx, Title Case headers,
         some mixed date formats, a handful of dropped/duplicated rows.
low    : several datasets missing entirely, many tables shipped as sheets of a
         single legacy workbook, UPPERCASE truncated headers, mislabeled
         columns, mixed date formats, currency strings, duplicate rows,
         structured facts buried in free-text notes.
"""

INSURANCE_COMPANIES = [
    {
        "slug": "meridian_risk_partners",
        "name": "Meridian Risk Partners, LLC",
        "short": "Meridian",
        "tier": "high",
        "seed": 1101,
        "hq": ("Hartford", "CT", "061"),
        "domain": "meridianrisk.com",
        "ams": "Applied Epic",
        "employees": 46,
        "n_clients": 38,
        "founded": 1998,
        "description": "Commercial P&C brokerage focused on contractors, real estate and manufacturing accounts in New England.",
    },
    {
        "slug": "harborline_insurance_brokers",
        "name": "Harborline Insurance Brokers, Inc.",
        "short": "Harborline",
        "tier": "medium",
        "seed": 1102,
        "hq": ("Tampa", "FL", "336"),
        "domain": "harborlineins.com",
        "ams": "Vertafore AMS360",
        "employees": 29,
        "n_clients": 30,
        "founded": 2006,
        "description": "Commercial and marine-adjacent P&C brokerage on the Florida Gulf Coast; also writes a small employee-benefits book.",
    },
    {
        "slug": "castlebrook_agency",
        "name": "Castlebrook Agency",
        "short": "Castlebrook",
        "tier": "low",
        "seed": 1103,
        "hq": ("Scranton", "PA", "185"),
        "domain": "castlebrookagency.com",
        "ams": "HawkSoft (partially adopted) + Excel",
        "employees": 11,
        "n_clients": 22,
        "founded": 1987,
        "description": "Family-owned main-street agency writing small commercial and trucking accounts in NE Pennsylvania. Records split between HawkSoft, spreadsheets and paper.",
    },
]

INDUSTRIAL_COMPANIES = [
    {
        "slug": "northfield_industrial_components",
        "name": "Northfield Industrial Components, Inc.",
        "short": "Northfield",
        "tier": "high",
        "seed": 2201,
        "hq": ("Rockford", "IL", "611"),
        "domain": "northfieldic.com",
        "erp": "Epicor Kinetic",
        "employees": 142,
        "n_customers": 40,
        "founded": 1979,
        "description": "Manufactures shaft couplings, machined housings and gearbox sub-assemblies; distributes bearings, seals and power-transmission components.",
    },
    {
        "slug": "keystone_bearing_and_drive",
        "name": "Keystone Bearing & Drive Co.",
        "short": "Keystone",
        "tier": "medium",
        "seed": 2202,
        "hq": ("Allentown", "PA", "181"),
        "domain": "keystonebd.com",
        "erp": "NetSuite",
        "employees": 63,
        "n_customers": 32,
        "founded": 1994,
        "description": "Power-transmission distributor (bearings, belts, gear reducers, motors) with a small light-assembly cell for custom conveyor drive packages.",
    },
    {
        "slug": "ridgeway_fasteners_and_supply",
        "name": "Ridgeway Fasteners & Supply",
        "short": "Ridgeway",
        "tier": "low",
        "seed": 2203,
        "hq": ("Chattanooga", "TN", "374"),
        "domain": "ridgewayfast.com",
        "erp": "QuickBooks Desktop + Excel",
        "employees": 27,
        "n_customers": 24,
        "founded": 2001,
        "description": "Industrial fastener and MRO supply house serving plants in the Tennessee Valley; runs vendor-managed inventory bins at several customer sites.",
    },
]

# ---------------------------------------------------------------------------
# Carriers (insurance) -- shared across the three brokerages with different
# commission schedules so the portfolio agent can normalize them.
# ---------------------------------------------------------------------------
CARRIERS = [
    # code, name, AM Best, lines, {company_short: commission_pct}
    {"code": "TRV", "name": "Travelers Indemnity Company", "am_best": "A++", "lines": ["GL", "PROP", "AUTO", "WC", "UMB"],
     "commission": {"Meridian": 15.0, "Harborline": 13.0, "Castlebrook": 12.0}},
    {"code": "HIG", "name": "The Hartford Fire Insurance Company", "am_best": "A+", "lines": ["GL", "PROP", "WC", "BOP"],
     "commission": {"Meridian": 14.0, "Harborline": 14.0, "Castlebrook": 12.5}},
    {"code": "LIB", "name": "Liberty Mutual Insurance", "am_best": "A", "lines": ["GL", "AUTO", "WC", "UMB", "PROP"],
     "commission": {"Meridian": 12.0, "Harborline": 12.0, "Castlebrook": 10.0}},
    {"code": "CNA", "name": "CNA Insurance", "am_best": "A", "lines": ["GL", "PROP", "PL", "CYB"],
     "commission": {"Meridian": 15.0, "Harborline": 12.5}},
    {"code": "CHB", "name": "Chubb (Federal Insurance Company)", "am_best": "A++", "lines": ["PROP", "GL", "UMB", "CYB", "DO"],
     "commission": {"Meridian": 15.0}},
    {"code": "NAT", "name": "Nationwide Mutual Insurance", "am_best": "A+", "lines": ["GL", "PROP", "AUTO", "BOP", "WC"],
     "commission": {"Harborline": 13.5, "Castlebrook": 12.0}},
    {"code": "PRG", "name": "Progressive Commercial", "am_best": "A+", "lines": ["AUTO"],
     "commission": {"Harborline": 10.0, "Castlebrook": 10.0}},
    {"code": "ERI", "name": "Erie Insurance Exchange", "am_best": "A+", "lines": ["GL", "PROP", "AUTO", "BOP", "WC"],
     "commission": {"Castlebrook": 13.0}},
    {"code": "AMT", "name": "AmTrust Financial", "am_best": "A-", "lines": ["WC", "BOP"],
     "commission": {"Meridian": 10.0, "Harborline": 10.0, "Castlebrook": 9.0}},
    {"code": "MKL", "name": "Markel Specialty (Evanston Insurance Co.)", "am_best": "A", "lines": ["GL", "PL", "UMB"], "surplus_lines": True,
     "commission": {"Meridian": 12.0, "Harborline": 12.0}},
    {"code": "BEZ", "name": "Beazley (Lloyd's Syndicate 2623)", "am_best": "A", "lines": ["CYB", "PL"], "surplus_lines": True,
     "commission": {"Meridian": 15.0}},
    {"code": "GRD", "name": "Guard Insurance (Berkshire Hathaway GUARD)", "am_best": "A+", "lines": ["WC", "BOP"],
     "commission": {"Castlebrook": 11.0, "Harborline": 11.0}},
]

LINES = {
    "GL": ("General Liability", 4500, 65000),
    "PROP": ("Commercial Property", 3000, 90000),
    "AUTO": ("Commercial Auto", 6000, 120000),
    "WC": ("Workers Compensation", 8000, 180000),
    "UMB": ("Umbrella / Excess Liability", 2500, 30000),
    "BOP": ("Business Owners Policy", 1800, 9000),
    "PL": ("Professional Liability / E&O", 3500, 40000),
    "CYB": ("Cyber Liability", 2000, 25000),
    "DO": ("Directors & Officers", 5000, 45000),
}

# ---------------------------------------------------------------------------
# Software / SaaS catalog. Same function -> different products across the
# portfolio (overlap findings), plus shared products with different seat
# prices (consolidation findings).
# ---------------------------------------------------------------------------
SOFTWARE = {
    # product: (vendor, function, typical annual per seat or flat)
    "Applied Epic": ("Applied Systems", "Agency Management System", 2400),
    "Vertafore AMS360": ("Vertafore", "Agency Management System", 2100),
    "HawkSoft CMS": ("HawkSoft", "Agency Management System", 900),
    "IVANS Exchange": ("IVANS (Applied)", "Carrier Download / Connectivity", 1800),
    "Zywave Miedge": ("Zywave", "Carrier Appetite / Market Intelligence", 6500),
    "Indio": ("Applied Systems", "Application / Renewal Intake Forms", 3600),
    "DocuSign eSignature": ("DocuSign", "e-Signature", 480),
    "Adobe Acrobat Sign": ("Adobe", "e-Signature", 420),
    "Microsoft 365 Business Standard": ("Microsoft", "Productivity Suite / Email", 150),
    "Google Workspace Business": ("Google", "Productivity Suite / Email", 144),
    "Salesforce Sales Cloud": ("Salesforce", "CRM", 1800),
    "HubSpot Sales Hub": ("HubSpot", "CRM", 1080),
    "Zoom Workplace": ("Zoom", "Video Conferencing", 180),
    "RingCentral MVP": ("RingCentral", "VoIP Phone System", 360),
    "8x8 Work": ("8x8", "VoIP Phone System", 336),
    "QuickBooks Online Advanced": ("Intuit", "Accounting / GL", 2400),
    "QuickBooks Desktop Enterprise": ("Intuit", "Accounting / GL", 1900),
    "Sage Intacct": ("Sage", "Accounting / GL", 15000),
    "Gusto": ("Gusto", "Payroll / HRIS", 480),
    "ADP Workforce Now": ("ADP", "Payroll / HRIS", 720),
    "Paychex Flex": ("Paychex", "Payroll / HRIS", 600),
    "Epicor Kinetic": ("Epicor", "ERP", 4200),
    "NetSuite": ("Oracle", "ERP", 3600),
    "Fishbowl Inventory": ("Fishbowl", "Inventory Management", 1500),
    "ShipStation": ("Auctane", "Shipping / Parcel", 2400),
    "UPS WorldShip": ("UPS", "Shipping / Parcel", 0),
    "FedEx Ship Manager": ("FedEx", "Shipping / Parcel", 0),
    "Bill.com": ("BILL", "AP Automation", 1200),
    "Expensify": ("Expensify", "Expense Management", 108),
    "Ramp": ("Ramp", "Corporate Cards / Expense Management", 0),
    "Dropbox Business": ("Dropbox", "File Storage", 240),
    "Box Business": ("Box", "File Storage", 300),
    "Slack Business+": ("Salesforce", "Team Messaging", 150),
    "KnowBe4": ("KnowBe4", "Security Awareness Training", 30),
    "SolidWorks Standard": ("Dassault Systemes", "CAD", 1295),
    "Autodesk Inventor": ("Autodesk", "CAD", 2300),
    "MasterControl": ("MasterControl", "Quality Management System", 12000),
    "uniPoint QMS": ("uniPoint", "Quality Management System", 4800),
    "Paycom": ("Paycom", "Payroll / HRIS", 660),
}

# ---------------------------------------------------------------------------
# Shared corporate vendors (non-inventory). Name variants are intentional so
# the agent has to entity-resolve; TRAPS are different companies with similar
# names that must NOT be merged.
# ---------------------------------------------------------------------------
SHARED_VENDORS = {
    "iron_mountain": {"canonical": "Iron Mountain Inc.", "category": "Records Storage / Shredding",
                      "variants": {"Meridian": "Iron Mountain Inc.", "Harborline": "IRON MOUNTAIN INFO MGMT", "Castlebrook": "Iron Mtn",
                                   "Northfield": "Iron Mountain Inc.", "Keystone": "Iron Mountain Information Management, LLC"}},
    "grainger": {"canonical": "W.W. Grainger, Inc.", "category": "MRO Supplies",
                 "variants": {"Northfield": "W.W. Grainger, Inc.", "Keystone": "GRAINGER", "Ridgeway": "Grainger Industrial Supply",
                              "Meridian": "Grainger"}},
    "cintas": {"canonical": "Cintas Corporation", "category": "Uniforms / Facility Services",
               "variants": {"Northfield": "Cintas Corporation", "Keystone": "Cintas Corp #472", "Ridgeway": "CINTAS"}},
    "staples": {"canonical": "Staples Business Advantage", "category": "Office Supplies",
                "variants": {"Meridian": "Staples Business Advantage", "Harborline": "Staples Advantage", "Castlebrook": "Staples",
                             "Keystone": "STAPLES BUS ADV", "Ridgeway": "Staples"}},
    "waste_mgmt": {"canonical": "Waste Management, Inc.", "category": "Waste / Recycling",
                   "variants": {"Northfield": "Waste Management of Illinois", "Ridgeway": "WM - Waste Management", "Harborline": "Waste Management Inc of Florida"}},
    "ups": {"canonical": "United Parcel Service", "category": "Parcel Freight",
            "variants": {"Northfield": "UPS", "Keystone": "United Parcel Service", "Ridgeway": "UPS Freight / UPS"}},
    "fedex": {"canonical": "FedEx Corporation", "category": "Parcel Freight",
              "variants": {"Northfield": "FedEx", "Keystone": "FEDEX", "Ridgeway": "Federal Express"}},
    "sunbelt": {"canonical": "Sunbelt Rentals", "category": "Equipment Rental",
                "variants": {"Northfield": "Sunbelt Rentals", "Ridgeway": "SUNBELT RENTALS INC"}},
}
# Trap vendors: similar names, genuinely different suppliers.
TRAP_VENDORS = [
    {"name": "Iron Mtn Landscaping LLC", "company": "Castlebrook", "looks_like": "iron_mountain",
     "category": "Landscaping", "note": "Local landscaper in Scranton; not Iron Mountain records storage."},
    {"name": "Apex Fastening Systems LLC", "company": "Ridgeway", "looks_like": "apex_fastener",
     "category": "Fastener Supplier", "note": "Different company from Apex Fastener Corp (Northfield supplier)."},
    {"name": "Grainger Consulting Group", "company": "Harborline", "looks_like": "grainger",
     "category": "HR Consulting", "note": "Independent consultancy; not W.W. Grainger."},
]
