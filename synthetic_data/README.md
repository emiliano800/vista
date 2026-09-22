# Synthetic back-office data for the Vista demo portfolio

Six fully synthetic portfolio companies — three insurance brokerages and three
industrial-goods distributors/manufacturers — each exported as the kind of
back-office files a PE operating team actually receives at close: agency
management / ERP exports, AP and AR ledgers, payroll registers, carrier and
supplier statements, ACORD forms, customer POs, emails and (for the worst
company) one giant legacy Excel workbook.

Everything is generated deterministically by `generator/` (seeded
`random.Random`, as-of date **2026-03-31**). No real people, companies or
identifiers.

```
synthetic_data/
  insurance_broking/
    meridian_risk_partners/            high   - Applied Epic, clean, complete
    harborline_insurance_brokers/      medium - AMS360, Title Case headers, some xlsx, 2 datasets missing
    castlebrook_agency/                low    - HawkSoft + Excel, 13 datasets missing, 11 merged into one workbook
  industrial_goods/
    northfield_industrial_components/  high   - Epicor Kinetic, machining/assembly, 102 files
    keystone_bearing_and_drive/        medium - NetSuite, distribution + light assembly, 6 datasets missing
    ridgeway_fasteners_and_supply/     low    - QuickBooks + Fishbowl + Excel, 37 datasets missing, 13 merged
  manifest.json          every file written, row counts, what was dropped/merged per company
  answer_key.json / ANSWER_KEY.md   planted anomalies + portfolio findings (see below)
  generator/             regenerate with: python3 synthetic_data/generator/generate.py
```

Each company folder contains one numbered sub-folder per back-office workflow
(`00_company`, `01_clients_crm` … `14_workflow_events` for insurance;
`00_company`, `01_master_data` … `17_workflow_events` for industrial). Every
dataset is its own file in an industry-typical format: CSV for system exports,
XLSX for spreadsheets people maintain by hand, JSON for structured documents
(ACORD 125/127, customer POs, company profile), `.eml` for email
correspondence, `.md` for operating procedures, `.txt` for OCR/transcribed
paper.

## Use across the financial and automation platforms

The same six-company records support three user views; do not create separate,
contradictory financial fixtures for each audience.

| View | What the demo should demonstrate |
| --- | --- |
| **PE analyst** | Financial performance and opportunities across authorized companies: invoiced revenue, receivables, purchasing and software spend, with reproducible calculations and source evidence. |
| **Portco CFO** | The assigned company's subset of those same financial records, periods, and calculations. No other company's raw records or portfolio comparisons. |
| **FDE** | Assigned workflow evidence, process bottlenecks, automation candidates, exceptions, and operational results. Workflow events and operating procedures explain how work happens. |

The financial platform shares one model between analyst and CFO views. The FDE
platform links operational findings and measured changes back to financial impact;
planted anomalies and estimated benefits are not realized automation results.
The current loader and interpretation pipeline provide canonical records and
findings, not a complete financial forecasting model or automation executor.
Dedicated CFO/FDE experiences and their authorization remain planned work.

`front_end_work/` contains mock source-company systems for inspecting workflow
context. These static fixtures are not Vista's CFO or FDE platforms and have no
production access controls. Keep the answer key as an evaluation oracle, never
as evidence fed to a discovery agent or as proof of realized savings.

## Data-quality tiers

| Tier | Headers | Dates | Money | Rows | Files |
|---|---|---|---|---|---|
| high | `snake_case` | ISO | numeric | clean, full FK integrity | one CSV per dataset |
| medium | `Title Case`, a few renamed (`unit_price` -> `Price`) | ~15% mixed formats | numeric | trailing spaces, some datasets missing, a few as `.xlsx` | mostly CSV |
| low | `UPPER_TRUNCATED_`, several **swapped/mislabelled** | ~55% mixed (`03/24/26`, `24-Mar-2026`, `2026.03.24`) | ~40% as `"$1,234.00"` strings | duplicates, blanks, casing noise | many datasets absent; core tables only exist as sheets of `00_legacy_exports/*.xlsx` |

The low-tier companies (Castlebrook, Ridgeway) are deliberately hard but still
tractable: every record needed to reconstruct the business is present
somewhere, just not where or how a clean system would put it.

## What is planted (answer key)

`ANSWER_KEY.md` lists ~70 items, each with company, evidence file(s) and the
expected action. Kinds:

- **Company-level operational findings** — certificate requested above the
  in-force umbrella limit, commission paid at 10% vs contracted 12%, producer
  writing on an expired license, three-way-match price/qty variances,
  negative inventory, shipped-but-never-invoiced orders, customer on 90+ day
  AR still receiving orders, released ECO not reflected in the BOM, expired
  forklift certifications, sales tax paid on resale inventory, duplicate
  software subscriptions.
- **Data-quality issues to be detected, not silently fixed** — duplicate
  customer/client records, swapped column headers (`EFF_DATE`/`EXP_DATE`,
  `PO_NUM`/`SO_NUM`, `PAID`/`RESERVE`), a `Max` column that is really a
  reorder quantity, UoM mismatches (box vs each), vehicle schedule that
  disagrees with the ACORD 127.
- **Portfolio-level opportunities** — same shared items (matched on
  manufacturer + manufacturer part number, *not* internal SKU) bought at
  different prices; same vendors under different names (`Iron Mountain
  Inc.` / `IRON MTN` / `Iron Mountain Information Mgmt`); overlapping
  software by function; parcel freight rate gap; one national account
  (Cardinal Foods) buying from three companies as three unrelated plants.
- **False-positive traps** (marked TRAP) — `Iron Mtn Landscaping LLC` is not
  Iron Mountain; a generic import bearing is not the SKF 6205; Salesforce and
  Slack share a vendor but not a function; customers named "Keystone …" are
  not the portfolio company.

Every operational finding is also visible as state transitions in
`*/workflow_events.csv` (universal event schema: `event_id, company_id,
workflow_type, object_type, object_id, timestamp, actor, action,
previous_state, new_state, source_document, confidence, requires_review`)
at companies that have an event log.

## Regenerating / validating

```
pip install openpyxl
python3 synthetic_data/generator/generate.py   # rewrites all company folders + manifest + answer key
python3 synthetic_data/generator/validate.py   # FK integrity, planted anomalies present, evidence paths resolve
```

Output is byte-for-byte reproducible. Change a seed in
`generator/profiles.py` to get a different but structurally identical
company; edit `SHARED_ITEMS` / `SHARED_VENDORS` / `SOFTWARE` to change the
cross-company overlaps.
