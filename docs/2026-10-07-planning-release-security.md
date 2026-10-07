# Planning panel: production release security gate — 7 October 2026

Executor-run strict security gate for the Scope production release. This document
records fresh command results, exits and counts from 7 October 2026; it supersedes
nothing in the prior scoped verification ([2026-10-06-planning-security-verification.md](2026-10-06-planning-security-verification.md)),
which remains the historical record of the 6 October scan. The user has explicitly
authorized push/deployment for the current release preparation, but this executor
performed no push, deployment, live provider call or external message; no
deployment is claimed anywhere in this document.

## 1. Scope

- Diff range / revision scanned: worktree at base commit
  `dcf3fa111421926a6f1f73194506ca4df9ec28af` with the full uncommitted
  `feat/scope-regulation-panel` working tree (release candidate state).
- Changed-file list (full, 49 text paths): same list as recorded in
  [2026-10-06-planning-security-verification.md](2026-10-06-planning-security-verification.md)
  §1 plus `tests/fixtures/planning_sources/amtsblatt-aarau-page1.html` and
  `amtsblatt-aarau-page2.html` (new fixtures). Runtime edits span planning
  preview/refresh/collect, amtsblatt import, scheduler, detail rendering, auth,
  screening, ÖREB, ingest, report and organisation modules plus their tests.
- High-risk areas marked: auth (scope_auth, app.gate, shared login),
  scheduler/background jobs, external HTTP fetching (planning collector),
  local store/lock file writes, organisation profile writes. No webhook, payment,
  upload or licensed-document retrieval surface exists in this repository (see §4).
- Adjacent files inspected: `workflow.py`, `shared_calculations.py`, `searches.py`,
  `organisation.py`, `paths.py`, `economics.py`, `merkliste.py`, `http_fetch.py`
  (auth-caller and surface-existence inspection).

## 2. Commands executed

All commands run 2026-10-07 between 02:04Z and 02:10Z in the worktree unless noted.

| # | Step | Command | Actual exit | Result summary |
|---|------|---------|-------------|----------------|
| 1 | Scope | `git diff --name-only`; `git ls-files --others --exclude-standard` | 0 | 49 changed/new text paths recorded |
| 2 | Secrets | Python regex scan of all 49 changed/new text paths (private-key header, provider key shapes, JWT, secret/token/password assignments, URL basic-auth); values never emitted, only file:line:pattern | 0 | 7 matches, all in `tests/test_scope_auth.py` (lines 287, 292, 313, 330, 347, 357, 608); each reviewed and confirmed a dummy test token/redirect fixture or a not-echoed assertion; no real secret found. Evidence: `secret-scan-locations-20261007.txt` |
| 3 | Dependencies | `uv pip compile requirements.txt --python-version 3.11 --python-platform x86_64-unknown-linux-gnu`; `uvx pip-audit --no-deps --disable-pip -r <resolved> --format json` | 0 | 40 resolved Linux packages; pip-audit exit 0, no known vulnerabilities. Evidence: `pip-audit-linux-py311-20261007.json` |
| 4 | SAST | `uvx bandit -q -f json` over 28 runtime modules; targeted `rg` for eval/exec, subprocess/shell, dynamic import, pickle/yaml-unsafe, TLS `verify=False`, SQL string interpolation, file-write handling | 0 | Bandit: 0 findings, 0 errors. Manual SAST: no actionable finding; all SQL parameterized, store/lock writes use O_NOFOLLOW + 0o600 + atomic replace. Evidence: `bandit-20261007.json`, `sast-auth-review-20261007.txt` |
| 5 | App-specific | Manual caller inspection (see below) | 0 | In-repo checks pass; billing/SIA/upload surfaces absent from this repository, explicitly out of scope (§4) |
| 6 | Tests | `/Users/krisnafirdaus/Documents/normiq/densification-finder/.venv/bin/python -m pytest -q` | 0 | 973 passed, 1 skipped (existing address-fixture assertion in `tests/test_app.py`), 56 subtests passed, 111.82s. Evidence: `pytest-full-20261007.log` |
| 6b | Tests (Node) | `node --test tests/calculation_table.test.cjs` | 0 | 12 passed, 0 failed |
| 6c | Diff/syntax | `git diff --check`; `python -m py_compile` over all changed runtime modules | 0 | Both clean |
| 6d | Disabled CLI | `python -m planning_refresh --check`; `python -m planning_refresh --once` with refresh explicitly disabled, no preview/publications configured | 0 | Both returned `{"status": "off"}`; no provider fetch, no state written |

App-specific manual checks performed (this repository only):

- Write path: `scope_auth.require_write`/`may_write` (role + MFA) then
  `check_transaction` re-checks role under `BEGIN IMMEDIATE` in the same
  connection as the domain write (`workflow.py`, `shared_calculations.py`,
  `searches.py`, `screening.py`, `organisation.py` callers) — membership
  revocation takes effect promptly, fail-closed.
- Identity pinning: `scope_auth.current` binds the session to provider-verified
  userinfo plus local membership; no client-supplied userId is trusted.
- Shared-login compatibility preserved: `app.gate` keeps the optional shared
  `APP_PASSWORD` branch with `hmac.compare_digest`; personal mode is fail-closed
  and never falls back to shared access.
- Invitation flow: expiry checked with a uniform unknown/expired response;
  provider identity binding rechecks expiry; `_NoRedirect` blocks token capture
  via redirects; error messages never echo tokens (asserted by tests).
- Collector/refresh: fixed provider origins (`amtsblatt.ag.ch`, `www.ag.ch`)
  with fixed path allowlists, bounded pages/size/timeouts, process locks, atomic
  replacement, last-good-data preservation; refresh disabled by default.
- Rendering: preview renderer escapes untrusted titles/notes and does not fetch
  providers; no automatic relevance or legal-stage promotion.

## 3. Findings

No unresolved Critical, High, Medium or Low security finding was identified in
the inspected scope.

Recorded (non-blocking, informational) observations:

- Low (documented, non-critical-path): 7 secret-pattern matches in
  `tests/test_scope_auth.py` are dummy fixture strings and negative assertions;
  they are required by the tests and expose nothing.
- Low (documented): `--no-deps --disable-pip` pip-audit mode avoids the tool's
  failing temporary ensurepip step; the full transitive closure was still
  resolved first via `uv pip compile`, so no transitive dependency was omitted
  from audit coverage.

## 4. Exclusions and limitations

- Billing webhook signature verification/idempotency, Normiq workspace/SIA
  licensed-document retrieval and file-upload validation are **not part of this
  Python Scope repository**. Presence was inspected before exclusion: the only
  "billing" hits are the organisation profile's `billing_email` text field, the
  only "upload" hit is a comment in `paths.py`, and SIA hits are comments citing
  the SIA 416 rate and README-licensed municipality rows. These mandatory checks
  are therefore explicitly out of scope and are **not claimed as checked**; they
  remain with the Normiq application's own gates.
- No live integration evidence: no production deployment, live provider access,
  email/message send, or production variable mutation was performed by this
  scan. All evidence is unit/mock/static/local.
- The repository has no Node dependency manifest; `pnpm audit --prod` is not
  applicable. The plain Node regression suite ran instead (12/12).
- The 1 skipped test is the pre-existing UI/address-fixture assertion (first
  fixture row has no address); it is documented and not new.
- Existing production protections respected throughout: no reseed, no schema
  change, no credential reads; secret scan reported filenames/lines/patterns
  only, never values.

## 5. Re-check evidence

No runtime fixes were applied in this pass, so no after-fix re-scan applies.
Fresh 7 October evidence artifacts (executor-local directory):

- `pytest-full-20261007.log` — full suite result.
- `secret-scan-locations-20261007.txt` — sanitized secret-scan locations.
- `bandit-20261007.json` — Bandit JSON (0 results).
- `pip-audit-linux-py311-20261007.json` — dependency audit output.
- `sast-auth-review-20261007.txt` — SAST and manual auth-caller review record.

## 6. Verdict

**PASS — scoped strict security gate, 7 October 2026.** No unresolved finding
meets a blocking threshold (no Critical/High; the two Low observations are
documented and non-critical-path). Every applicable mandatory check has fresh,
recorded evidence above; the only non-executable mandatory app-specific checks
(billing webhook, SIA licensed retrieval, upload) were excluded only after
confirming the surfaces do not exist in this repository, and are recorded as
out of scope rather than guessed.

This PASS is a security gate only. It is not approval of provider data rights,
not activation of the recurring feed, and not evidence of deployment. The
provider-reuse basis remains unresolved, the recurring refresh remains off, and
held review items remain excluded; those release-gating facts live in
[2026-10-06-planning-release-readiness.md](2026-10-06-planning-release-readiness.md).
Whether the release proceeds is the lead's decision after independent review,
commit/push and the actual deployment.

## 7. Addendum — post-stage fixture whitespace cleanup, 7 October 2026

- Complete post-stage check: `git diff --check HEAD^` (exit 2, 7 October 2026)
  reported trailing whitespace only in
  `tests/fixtures/planning_sources/amtsblatt-aarau-page1.html` and
  `amtsblatt-aarau-page2.html` — the two previously untracked Aarau fixtures,
  which is why the earlier unstaged checks did not cover them.
- Correction applied: trailing spaces/tabs on individual lines normalized in
  those two fixtures only (761 lines each before and after; markup, observed
  values and line breaks preserved). The fixture README now records the Aarau
  provenance and the normalization; capture metadata is unchanged.
- Verification after correction: `git diff --check HEAD^` exit 0; the lead's
  independent suite run reported 246 passing tests for the release.
- No runtime code, configuration or git history was touched in this addendum;
  no push, deployment or live provider fetch was performed.

## 8. Addendum — block E Swiss-date correction, 7 October 2026

Pre-production correction, applied before any push/deploy of release commit
`f2250805ef85214d96ec172de20169318e4ea7fd` (unpushed at the time of writing).

- Finding: block E computed "today" inconsistently. The card and the preview
  used `PP.swiss_today()` (Europe/Zurich), while `detail.page` called
  `R.for_municipality` and `PL.state_for` without `today` (server
  `date.today()`), and the PDF helpers `_regulation_block`/`_planning_rows`
  defaulted to the server date. Late in the UTC evening Switzerland can
  already be on the next day, so the governing BNO
  edition and the current/expired stage of a checked öffentliche Auflage
  could disagree between the on-screen card and the exported PDF.
- Correction (runtime edits in `detail.py` only; no API change, no new
  dependency, no edits to `regulations.py`/`planning.py`): `detail.page`
  captures one `regulation_today = PP.swiss_today()` and passes it through
  `R.for_municipality`, `PL.state_for`, `PP.state_for`,
  `_regulation_card_html` and the `_regulation_block` PDF export call; the
  default `today` in `_regulation_block` and `_planning_rows` is now
  `PP.swiss_today()`, so standalone helper calls agree with the card as well.
  Explicit `today` overrides are untouched.
- Regression coverage (tests/test_detail.py): a `SwissDateBoundaryTest` class
  simulates 2026-10-07 22:30 UTC (a `datetime.date` subclass `FakeUTCDate`
  with `today() → 2026-10-07`; `PP.swiss_today` patched to 2026-10-08) and
  renders the actual card/`_regulation_block`/`_planning_rows` without an
  explicit `today`: a BNO taking force on the Swiss day is governing on card
  and sheet with no future-edition duplicate; a checked Auflage ending 7 Oct
  reads neutral ("beendet") and one starting 8 Oct reads "Entwurf in
  Auflage" on both outputs, with identical checked notes and source-bound
  dates; a counterfactual test proves the same helpers answer differently on
  the server date, i.e. the boundary is genuinely straddled. An AppTest page
  test (`DetailViewTest`) drives the real detail page with a two-edition BNO
  fixture at the boundary and asserts from the actual rendered card and the
  actually-built PDF block rows that the governing edition is the same-day
  one, plus `today == 2026-10-08` reaching the governing lookup and the PDF
  block. Explicit-date tests are preserved; no legal-status assertion was
  weakened; no provider fetch occurs in any test.
- Verification (all with
  `/Users/krisnafirdaus/Documents/normiq/densification-finder/.venv/bin/python`):
  new tests 4 passed (and 3 of 4 demonstrably fail against the pre-fix
  `detail.py` from `f225080`, with the duplicate "in Kraft ab 08.10.2026"
  paper row reproduced); affected suite
  `pytest -q tests/test_detail.py tests/test_detail_preview.py
  tests/test_planning_local_integration.py tests/test_regulations.py` —
  result and exits recorded in the evidence log
  (`scope-swissdate-addendum-20261007.log` under the executor evidence
  directory); `python -m py_compile detail.py` exit 0;
  `git diff --check dcf3fa111421926a6f1f73194506ca4df9ec28af` exit 0.
- Independent security rationale: this is an output-integrity correction, not
  a new surface. No network fetch, no file write, no auth/config/schema
  change, no new dependency; the change narrows the date source for legal
  statements to the single timezone the product serves, removing a
  card-vs-PDF contradiction in a regulated statement (which edition of the
  BNO governs; whether a checked Auflage runs today). No deployment, push or
  live provider call is performed or claimed by this addendum.

## 9. Addendum — base-image pip/setuptools upgrade, 7 October 2026

Post-deploy audit of the running production image (evidence:
`pip-audit-production-20261007.json`) found the base-image tooling vulnerable —
pip 24.0 (GHSA-qwm4-qh6w-59xr, fixed >=26.2.0) and setuptools 79.0.1
(GHSA-h35f-9h28-mq5c, fixed >=83.0.0) — while all 40 application
dependencies are clean.

- Correction (`Dockerfile` only): the single dependency `RUN` step now first
  runs `python -m pip install --no-cache-dir --upgrade "pip>=26.2"
  "setuptools>=83.0.0"`, then installs `requirements.txt` via
  `python -m pip install --no-cache-dir -r` (joined with `&&`). Base image,
  COPY ordering, CMD, volume path, environment, auth and feed-off behavior
  are untouched; no new runtime dependency and no package-index change.
  Rationale: upgrading the vulnerable build tooling before requirements
  installation removes the findings for these two tools at the layer that
  introduced them, without altering any application code or configuration.
- Verification (Docker daemon available locally; image built from this
  checkout, tagged `scope-image-security-check:20261007`):
  - `docker build` exit 0. Evidence: `image-build-20261007.log`.
  - In-image: pip 26.2.1, setuptools 84.0.0; `pip check` exit 0 (no broken
    requirements); import smoke (`streamlit, pandas, shapely, reportlab,
    requests, numpy, altair`) exit 0. Evidence: `image-pipcheck-smoke-20261007.log`.
  - In-image audit `pip_audit --no-deps --disable-pip` over the full
    `pip freeze --all` list (43 entries, incl. pip/setuptools): exit 0, no
    known vulnerabilities; pip 26.2.1 and setuptools 84.0.0 each report
    `vulns: []`. Evidence: `image-pip-audit-20261007.log`.
  - Official PyPI JSON metadata cross-check (isolated temp venv, since
    removed): latest pip 26.2.1 and setuptools 84.0.0 both declare
    `requires_python >=3.10`, compatible with the `python:3.11-slim` base;
    both satisfy the advisory fix floors.
  - `git diff --check` exit 0; the working diff touches exactly
    `Dockerfile` and this document.
- Limitations: this verifies a locally built image from this checkout, not
  the Railway-deployed image; the lead verifies the deployed image
  separately. No commit, push, deploy, production/config/auth/schema/data
  change, or provider fetch was performed; the shared local `.venv` was not
  modified.
