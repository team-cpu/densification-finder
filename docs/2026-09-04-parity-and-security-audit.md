# Scope HTML parity and security audit — 2026-09-04

Reference: the user-supplied `Parcel Potential.dc.html` export.
Baseline: `31801bf`; this report covers the subsequent organisation/backend and
dialog changes. **This is an incremental implementation, not 100% parity.**

## Implemented and verified in this batch

| Area | Result | Evidence |
| --- | --- | --- |
| Team | Persistent pending invitations, duplicate handling, role changes and re-queue action; no fictitious members | Organisation persistence/widget tests; populated local dialog inspected |
| Einstellungen | Company fields and four preference values persist; each field validates independently | Widget tests, 31px fields/compact labels inspected in browser |
| Dialog layout | React Aria selectors replace obsolete BaseWeb selectors; dynamic member rows sized correctly; only Settings body scrolls | Browser inspection at 1159 × 863; Close and Fertig remain visible |
| Owner/contact modal | Each valid field saves on blur/Enter; Fertig closes; invalid fields remain reported until corrected | Widget regression tests; local owner change read back from temporary SQLite before closing |
| Data migration | Additive organisation tables, repeated schema initialization preserves existing rows | Migration/persistence tests |
| Screening | 25/50/100 result limits, default 50; reference parcel placeholder | App regression tests and browser |
| Analyse | Legal sources expanded initially, matching the reference | Detail tests and browser |
| Regression | Navigation, calculations, exports, saved searches, shortlist and board event validation remain covered | 215 tests passed in 61.035s |

Local browser testing uses a temporary database, not the committed seed or the
production volume. Test contacts/invitations must not be shipped.

## Known gaps — do not describe these as implemented

- Team membership is metadata under the existing shared-password model. There
  is no personal login, invitation acceptance, account identity, role-based
  authorization or real 2FA. The HTML export itself only simulates these states.
- Invitations and resends are queued records only. No transactional email,
  weekly digest or due-date email is delivered. A provider, verified sender,
  recipients and delivery/acceptance workflow are still required.
- The calculation-sharing toggle stores a preference only. Calculation inputs
  remain session-scoped; it does not enforce per-user visibility.
- Clicking a CHF amount to manually override an individual calculation row
  (reference lines 737–744, 1151–1204) is not implemented. Inputs above the table
  do recalculate the result, but the table remains read-only.
- The production model deliberately retains Philipp's confirmed **reserve on
  costs**, whereas the HTML mock uses developer profit on revenue. Unit counts
  also use the existing floor-area model, not the mock's saleable-area rounding.
  Changing this requires an explicit calculation-model decision, not a CSS fix.
- Actual Aargau data, public sources and benchmark provenance are retained.
  Mock Zurich subscriptions, 2026 licensed prices, regulatory examples and
  noise-exclusion data are not invented. Additional sources/licenses are needed
  before those filters and examples can represent real results.
- Saved-search management and safe reset defaults retain the working app's
  behavior rather than copying the mock's temporary in-memory feedback verbatim.

## Security gate

**Verdict: PASS for this incremental change in the existing single-organisation,
shared-password deployment model.** This is not approval to offer multi-tenant
accounts or to treat the stored role/2FA preferences as authorization controls.

The skill's three prescribed `.ai` security reference/template files are absent
from this workspace. The strict severity policy was applied with this explicit
fallback report instead.

### Commands and evidence

- `python -m unittest discover -s tests -q`: 215 tests, OK. Includes password
  gate, forged component intent, schema preservation, validation and PDF tests.
- `git diff --check`: clean.
- `gitleaks detect --no-git --redact --no-banner --source . --max-target-megabytes 5`:
  no leaks found. Files over 5 MB were skipped (geodata, seed database and a
  dependency source map); this is not a content scan of those binaries/datasets.
- `pip-audit --path .venv/lib/python3.11/site-packages --progress-spinner off`:
  no known vulnerabilities after updating the test-only `pypdf` from 6.15.0 to
  6.16.1. The fixed version is pinned in `requirements-dev.txt`; production
  requirements are unchanged. The requirements resolver audit initially failed
  in `ensurepip`, so the installed environment was audited instead.
- `bandit -q -r acquisition.py detail.py ingest.py organisation.py screening.py shell.py`:
  no High/Critical findings; three pre-existing Medium findings and one Low
  finding in ingestion, triaged below. New organisation SQL uses bound values
  and a static UPDATE statement; concurrent invitations use `BEGIN IMMEDIATE`.

### Findings and disposition

| Severity | Location | Exposure and mitigation |
| --- | --- | --- |
| Medium, B608 | `ingest.py:66` | Identifier interpolation uses a closed canton/metric configuration plus a table name from a local administrator-provided GeoPackage, not web input. Keep ingestion inputs administrator-only. Hardening plan: validate/quote the dataset table identifier before adding further ingestion sources; target 2026-09-11. |
| Medium, B310 | `ingest.py:96` | URL is constructed from fixed HTTPS `WFS` plus URL-encoded query values, so web users cannot select a scheme/host. Keep the endpoint constant. Hardening plan: explicit redirect-host/scheme validation before accepting configurable sources; target 2026-09-11. |
| Medium, B608 | `ingest.py:305` | Migration identifiers come from the static `WORKFLOW_COLUMNS` allowlist intersected with the local schema; values are not interpolated. Preserve that allowlist. Hardening plan: centralize identifier quoting for schema helpers; target 2026-09-11. |
| Low, B112 | `ingest.py:468` | Existing batch-ingest exception handling skips failed municipalities and retains previous rows. Not a new dialog/access path. Follow-up: add bounded, non-sensitive per-municipality failure reporting. |

No severity was lowered to produce the verdict. There are no unresolved
High/Critical findings in the scanned scope; the Medium items have explicit
containment and dated hardening plans.

### Mandatory boundary checks

- Authentication: the existing shared-password gate is before application
  rendering; its regression test passes. A fresh production browser session
  displayed only the password gate; no password was entered during this audit.
- Invitations: no token is minted or accepted, so no token-validation pathway
  is represented as working. Email/name output is escaped, email/role inputs
  validated, and simultaneous duplicate invitations tested.
- Workspace/tenant access: one organisation per database; no new tenant or
  licensed-document access was added. Stored roles do not grant access.
- Billing/webhooks/uploads: no billing endpoint, webhook handler or user file
  upload was introduced; these checks are not applicable to this change.
- Production target resolved read-only: Railway `blissful-presence`, service
  `densification-finder`, repository `team-cpu/densification-finder`, branch
  `master`, persistent `/data` volume ready. The default CLI link points to a
  different project and must not be used for a deployment.

## Release/rollback precautions

Only commit the intended source, tests and documentation. Exclude the existing
untracked videos and temporary UAT data. Do not set `DENSIFICATION_RESEED` for
this release. Rolling back application code leaves the additive organisation
tables intact; no destructive database rollback is required. A successful
deployment must be checked against its commit and health status separately.
