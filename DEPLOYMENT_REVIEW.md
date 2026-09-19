# Deployment completion and code review — 2026-09-19

The public recording-report workspace is live at https://bumpsolutions.org.
This is a deployed report-review product; autonomous workflow execution and
portfolio synergy analysis remain separate roadmap work.

## Completed setup

- Cloudflare Worker `vista` now deploys with `npx wrangler deploy`; previews use
  `npx wrangler versions upload`. Both previously referenced the deleted
  `company-portal` directory. The corrected Git build succeeded.
- Runtime `API_ORIGIN` is
  `https://vi-6526b1efec4446e48c627173e9e805ce.ecs.us-east-1.on.aws`.
  `keep_vars: true` preserves the dashboard setting during subsequent deployments.
- Firm **Vista Solutions**, company **Vista Demo**, owner **help@bumpsolutions.org**.
  Company ID: `c30577dd-f989-426f-a678-c533f6895de1`.
- The personal workspace key is in macOS Keychain under service
  **Vista Solutions - Vista Demo**, account **help@bumpsolutions.org**.
  No key is committed to Git. The command's CloudWatch key copy was removed.
- AWS CLI default and `vista-operator` profiles use IAM user
  `emiliano-vista-operator`; its credential is stored in macOS Keychain under
  **AWS Vista Operator**. The cached root CLI login was removed.
- The operator can inspect the Vista stack, run its existing management task,
  pass only Vista task roles, and read Vista logs. It is a workspace administrator
  through that task, but cannot create IAM users, read Secrets Manager directly,
  or deploy infrastructure. Infrastructure changes require a separately authorized
  deployment identity. No console password or SSO identity was created.

## Review scope and fixes

Reviewed changes since `3c3842f`: authentication and migrations; browser sessions;
recording validation, storage and permissions; desktop cloud integration; Worker
proxy and web interface; Docker and AWS infrastructure; deployment/operator scripts;
CI; and the synthetic-company frontend engine and configurations. Generated dataset
bundles were exercised through the six frontends, rather than reviewed line by line.

Issues corrected:

1. **Public deployment failed:** Cloudflare's saved commands still referenced a
   removed asset directory. Corrected and successfully rebuilt the current Worker.
2. **Public API disconnected:** added the runtime API origin and verified the
   public domain reaches the AWS backend.
3. **Issued keys persisted in console/log output:** `manage.sh --output-file PATH`
   now writes a new mode-0600 file without echoing the key, and removes the
   successful credential command's CloudWatch stream after saving the result.
   The previous stdout mode remains available and retains its log copy.
4. **ECS launch failures were not handled explicitly:** the wrapper now checks the
   launch response before waiting, and warns against rerunning successful
   credential commands when output retrieval fails. Existing key files are never
   overwritten. Three regression tests cover these behaviors.

## Verification

- 55 Python tests passed, including real local Postgres integration tests.
- 13 JavaScript tests passed; configured lint/format checks, explicit checks on
  the new test module, shell syntax, and Wrangler deployment dry-run passed.
- All 80 navigation modules across six synthetic-company frontends rendered in
  a DOM smoke test. Those frontends remain separate static demos.
- AWS API and public-domain `/api/health` returned 200.
- Public API accepted one intentionally synthetic report, source ID
  `SYNTHETIC-DEMO-verification-20260919`, containing 20 steps and three cases.
  Reupload returned the same recording ID; evidence and JSON/CSV downloads passed;
  an invalid access key was rejected. This report is demonstration data.
- Browser sign-in, report display, activity-evidence filtering, and sign-out were
  exercised through the public website.
- An actual Fargate `vista.manage --help` task succeeded under the restricted
  operator identity. IAM simulation allowed its management task and denied IAM
  user creation and direct secret retrieval.

## Remaining product and operational limits

- Reports are computed locally and uploaded; their scores are review candidates,
  not proven savings. The backend validates report structure and selected evidence
  relationships; it does not independently recompute every submitted metric.
- Employee-discovery agents still generate role-based hypotheses. Desktop reports
  are not yet inputs to their findings/company summaries. No portfolio hierarchy,
  business-system connectors, or execution approval workflow was added here.
- The new live roundtrip used synthetic events. Real desktop recording permissions,
  screen redaction, employee consent/retention, and an actual employee capture/upload
  session were not certified by this verification.
- Public authentication still needs rate limiting and operational monitoring before
  broader customer use. The AWS operator uses a scoped long-lived IAM key in Keychain;
  workforce SSO and a dedicated deployment role are future improvements.
