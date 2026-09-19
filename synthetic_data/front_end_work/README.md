# Front-end work — demo UIs for the six synthetic portfolio companies

Six deliberately simple, visually distinct, read-only front ends — one per company — that display that
company's synthetic back-office data (CRM, policies/orders, billing, HR, documents, …). Static HTML/CSS/JS,
no backend, no build step. Demo purposes only.

| Folder | Company | Look & feel (mimics the company's real system) | Data quality |
|---|---|---|---|
| `meridian_risk_partners/` | Meridian Risk Partners (insurance) | Navy enterprise AMS sidebar (Applied Epic-style) | High |
| `harborline_insurance_brokers/` | Harborline Insurance Brokers (insurance) | Coastal teal, rounded cards, horizontal tabs (AMS360-style) | Medium |
| `castlebrook_agency/` | Castlebrook Agency (insurance) | Dated Win95-style desktop app (HawkSoft + Excel) | Low |
| `northfield_industrial_components/` | Northfield Industrial Components (industrial) | Dark plant-floor ERP console, orange accents (Epicor-style) | High |
| `keystone_bearing_and_drive/` | Keystone Bearing & Drive (industrial) | Black top bar, green accents, portlets (NetSuite-style) | Medium |
| `ridgeway_fasteners_and_supply/` | Ridgeway Fasteners & Supply (industrial) | QuickBooks Desktop / Fishbowl style, cream forms | Low |

## Running

Open any `<company>/index.html` over a local static server (the pages load `data.js` and the shared
engine via relative paths; `file://` also works in most browsers):

```bash
cd synthetic_data/front_end_work
python3 -m http.server 8765
# http://localhost:8765/meridian_risk_partners/index.html   (etc.)
```

## How it works

```
front_end_work/
  build_data.py          # bundles ../<sector>/<company>/** CSV/XLSX/TXT/JSON/EML into <company>/data.js
  _shared/base.css       # layout + generic components (nav, KPIs, tables, detail panel, docs)
  _shared/vista-ui.js    # tiny engine: hash nav, searchable/sortable/paged tables, row detail + related records
  <company>/data.js      # generated: window.VISTA_DATA = { company, tables, docs }
  <company>/index.html   # company-specific shell (branding, layout)
  <company>/styles.css   # company theme overriding base.css
  <company>/app.js       # module config: which tables/columns/KPIs/related records to show
```

* `build_data.py` reads the synthetic exports directly; each CSV or XLSX sheet becomes a table (capped at
  400 rows per table for bundle size; the UI shows "showing first 400 of N"). Re-run it after regenerating
  the synthetic data: `python3 synthetic_data/front_end_work/build_data.py`.
* Each `app.js` is configured against that company's *actual* headers (snake_case / Title Case / UPPERCASE
  truncated), so low-tier quirks are visible on purpose: missing datasets render as "Not available in this
  company's exports", legacy workbook sheets show their `.xlsx#sheet` source, and mislabeled or swapped
  columns are called out in table notes rather than silently fixed.
* Click any row to open a detail panel with related records (e.g. client → contacts, policies, invoices,
  claims; sales order → lines, shipments, invoices). "All Files" lists every bundled source file.
