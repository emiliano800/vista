# Vista — Demo Portfolio Access

**Demo credentials only.** Every workspace below contains exclusively synthetic
data (see `synthetic_data/`) for the fictional PE portfolio "Vista Capital
Demo". No real people, companies, or business records exist behind these keys.
They are published deliberately so anyone can tour the demo; do NOT reuse this
pattern for real customers — real keys are delivered privately via
`deploy/aws/manage.sh --output-file` and never committed.

Sign in at **https://bumpsolutions.org/signin/** with any access key below.
Each key opens that acquired company's own isolated workspace (schema-per-tenant
Postgres isolation; keys are stored only as SHA-256 digests server-side).

Each workspace ships with two recording reports generated from that company's
own synthetic back-office files — real client/supplier names, PO numbers and
the AMS/ERP each company actually runs (Applied Epic, AMS360, HawkSoft, Epicor
Kinetic, NetSuite, QuickBooks) — produced by the real task-mining pipeline and
uploaded through the production API (`scripts/seed_demo_workspaces.py`).

The previous "Vista Solutions / Vista Demo" access key (whose lone report was
placeholder verification data from nowhere) has been revoked: the key was
rotated and the replacement destroyed unread, so no working credential exists
for it.

## PE analyst workspace (portfolio side)

The acquiring firm's analysts have a separate login that opens the portfolio
command center (`/portfolio/`) instead of a single company's workspace.

- Sign in at **https://bumpsolutions.org/signin/analyst/**
- Firm: Northstar HVAC Holdings (fictional) · analyst identity: `sarah@northstarhvac.com`

The analyst access key is a **tenant API token**, not a demo string: signing in
posts it to `/api/auth/session`, which returns an httpOnly session cookie, the
same path the company workspace uses. Nothing about the key is checked or
stored in the browser, so the key is not in this file — it is printed once by
the provisioning script and belongs in a password manager.

The firm is a tenant and each portfolio company is a deal inside it. Provision
one with:

```
# locally
uv run python -m vista.manage provision-firm \
    --name "Northstar HVAC Holdings" --analyst sarah@northstarhvac.com

# in AWS (writes the key to a file instead of CloudWatch)
deploy/aws/manage.sh --output-file ./analyst-key.json provision-firm \
    --name "Northstar HVAC Holdings" --analyst sarah@northstarhvac.com
```

Every figure the workspace shows is derived from stored records — import
batches, findings, agents and runs. A company with no committed import shows
"no source" rather than a zero, and cross-company purchasing opportunities come
from `POST /api/portfolio/analysis`, which compares unit prices for the same SKU
across deals. The earlier browser-side key check, and the SHA-256 digest that
shipped in the JavaScript bundle, are gone.

## The acquired companies

### Meridian Risk Partners, LLC

- Sign-in email (identity only): `demo@meridianrisk.com`
- Company ID (for the desktop recorder): `5038cf16-3f4a-495f-8249-cd9091e233f5`
- Access key:

```
f09e925cc3877112850395a99dd4da2ff8fe648ed7c189725cfab2d00e7aaa49
```

### Harborline Insurance Brokers, Inc.

- Sign-in email (identity only): `demo@harborlineins.com`
- Company ID (for the desktop recorder): `0568a70f-c3e3-410d-999f-85e528842e6e`
- Access key:

```
aa4e7bceacb60d7ed36609130d24b336660432a5609d4748724f8354c4f7ad8e
```

### Castlebrook Agency

- Sign-in email (identity only): `demo@castlebrookagency.com`
- Company ID (for the desktop recorder): `35f274ab-f63b-4e5a-8cff-6cdf82e6ee24`
- Access key:

```
016ca8b2b2fe55f0a27b19b202265d7b150fbf0ee12dea8522351b64d3ff347a
```

### Northfield Industrial Components, Inc.

- Sign-in email (identity only): `demo@northfieldic.com`
- Company ID (for the desktop recorder): `1632b3e6-349d-4e6b-afdd-aa4e6ad84678`
- Access key:

```
cfc4ae699bfd7c089740167a64dad9e66e4350f7656b56d2c7904de7458efaeb
```

### Keystone Bearing & Drive Co.

- Sign-in email (identity only): `demo@keystonebd.com`
- Company ID (for the desktop recorder): `aa7c8cc9-0458-48c3-8b56-ce16050b2da6`
- Access key:

```
72bff8f1e24b0ebbd3985b71ac7724debaee4f0c6910969835cb883e9c8c5469
```

### Ridgeway Fasteners & Supply

- Sign-in email (identity only): `demo@ridgewayfast.com`
- Company ID (for the desktop recorder): `95bfad1d-f58d-40c4-9e6d-5df1f2e0ca54`
- Access key:

```
76b39d58aa43c4b4d9d997693de1edfeaf32c07f0f66c3289b809f2d30f0d540
```

## How uploading a report works (and where it lives)

1. **Record.** The Electron desktop app captures a work session locally
   (screenshots, video, raw keystrokes never leave the machine).
2. **Analyse on-device.** On Stop, the task-mining pipeline runs locally:
   redaction/PII masking -> keystroke aggregation -> sessionization -> activity
   labeling -> case correlation -> automation scoring.
3. **Review.** The employee approves/fixes the AI explanation of each stretch.
4. **Upload.** Pressing "Upload report" POSTs one JSON bundle — manifest,
   summary, and the cleaned activity CSV only — to
   `POST /api/deals/{company_id}/recordings` with the personal access key.
   (The seed script uploads through this exact endpoint.)
5. **Storage — yes, durable S3.** The bundle is written to the private S3
   bucket `vista-reports-630396228214` under a content-addressed key
   (`<tenant>/deals/<company>/recordings/<id>/<sha256>.json`). The bucket has
   **versioning enabled**, public access fully blocked, and S3's standard
   99.999999999% (11 nines) object durability. Postgres (RDS) stores only
   metadata + the S3 pointer; the API streams evidence back from S3 on demand.
   Uploads are idempotent — retrying or re-uploading the same session updates
   the same record instead of duplicating it, and a Postgres advisory lock
   serialises retries.
