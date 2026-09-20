# Data ingestion demo

The signed-in company workspace now starts with **Overview**, **Data sources**
and **Findings**. Recording reports remain at `/account/recordings/`.

## Run locally

```sh
docker compose up -d
uv sync
uv run python scripts/prepare_ingestion_demo.py
VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --port 8010
```

Open http://localhost:8010/signin/. The setup script writes the local sign-in key
to `Vista/INGESTION_DEMO_ACCESS.md` (gitignored, mode 0600). It reuses this local
workspace on subsequent runs and refuses remote database/storage endpoints.
No worker or model API key is needed for ingestion checks.

## Four-minute presentation

1. Sign in as Meridian. Click **Try Meridian sample data**. Five original synthetic
   exports appear: clients, policies, carrier agreements, commissions and invoices.
   The export date is March 31, 2026.
2. Click **Review field mapping**. Show detected record types and one column mapping.
   Expand an original-record preview. This is the human confirmation step.
3. Click **Confirm & analyze**. The company snapshot contains **246 records**,
   **122 policies**, **38 clients**, and a **$1,584.48 commission discrepancy**.
4. Open **Commission below the policy rate → Review evidence**. The drawer shows
   $79,224 × 12% = $9,506.88 expected, $7,922.40 paid, and the $1,584.48 difference.
   Both the policy and commission source rows are attached. The calculation does
   not trust the source file's prewritten variance column.
5. Mark it **Reviewed**, then refresh to demonstrate persistence. Open **Data sources**
   to browse original rows and confirmed mappings, or download the analysis JSON.

This is synthetic data processed by the real import pipeline. Label the financial
result **a discrepancy to investigate**, not recovered revenue or realized savings.
The interface identifies deterministic checks; it does not claim an LLM discovered
these results. Receivable exposure remains separate from commission discrepancies.

Re-importing identical files with the same export date reopens the saved snapshot.
Import history can reopen prior batches. Each batch is analyzed independently;
related files must be imported together. Reviewing/dismissing/reopening a finding
writes a review-history event; it never modifies the source systems.

## Supported inputs and checks

- CSV/TSV UTF-8 exports and XLSX value exports. Up to 12 files, 20 tables,
  1,999 records per table, 64 columns, 512 characters per cell, 5 MiB total.
- XLSX dates and percentage formats normalize to ISO dates and percentage points.
  Formulas/error cells must first be exported as values. No silent truncation.
- Editable header-based mappings for clients, policies, carrier agreements,
  commission statements and invoices. Missing fields or invalid values stop
  confirmation and identify the file/row to correct.
- Policy-to-statement commission comparisons flag shortfalls over $50. Duplicate
  policy IDs, identical statement rows and carrier mismatches are excluded from
  financial reconciliation and surfaced for review.
- Full-pay invoices with positive balances and due dates over 60 days before the
  snapshot date are flagged. Payment plans require separate review and are excluded.
- Original files are stored in private S3; parsed rows, mappings, analysis and review
  history are stored in the tenant's `import_batches` table. Deal roles gate all reads
  and writes. No cross-company aggregation or third-party connector is implied.

## Deployment

Deploy the Python backend **before** the web frontend. Ingestion requires tenant
migration `0007_import_batches` and the new `/api/deals/{id}/imports` and
`/api/imports/{id}` routes. The Worker allowlist has been extended to forward only
these scoped operations. The existing AWS startup migration mechanism applies the
new schema when `VISTA_MIGRATE_ON_START=true`.

```sh
deploy/aws/deploy.sh
npm run deploy
```

Those commands require the appropriate deployment credentials. The scoped workspace
operator described in `aws/README.md` cannot deploy infrastructure. A web-only deploy
will show the new interface but cannot run imports against the older API.

Static assets under `src/web/public/demo/meridian/` are deliberately synthetic and
public. The sample button appears only in a Meridian-named company workspace;
all companies can import their own supported exports.
