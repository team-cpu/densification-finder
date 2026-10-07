# Scope planning continuation: security verification

## 1. Scope

Base commit: `dcf3fa111421926a6f1f73194506ca4df9ec28af`. Inspected the planning
continuation against `/tmp/scope-remaining-20261006-baseline/manifest.json`, plus
the existing regulation-panel diff from HEAD. Current continuation runtime edits
are limited to `planning_preview.py`, `planning_refresh.py`, `scheduler.py` and
the regulation preview renderer in `detail.py`. No credentials, auth configuration,
production variables or dependencies were changed by this continuation.

The broader worktree includes pre-existing auth-adjacent Scope writes, SQLite
startup handling, ÖREB cache refresh and PDF output. Adjacent callers reviewed:
`scope_auth.current/require_write/may_write/check_transaction`, the guarded
`screening.check_oereb` writes, `app.gate/database_access`, importer atomic writes,
collector fixed-origin transport, `email_outbox` delivery and scheduler recipients.

Full release-worktree changed/untracked file list at scan time:

- `.env.example`
- `NEWSFEED.md`
- `README.md`
- `amtsblatt.py`
- `amtsblatt_import.py`
- `app.py`
- `detail.py`
- `docs/2026-10-05-amtsblatt-freigabe-anfrage.md`
- `docs/2026-10-06-planning-release-readiness.md`
- `ingest.py`
- `oereb.py`
- `planning.py`
- `planning_collect.py`
- `planning_preview.py`
- `planning_refresh.py`
- `regulations.py`
- `report.py`
- `requirements-dev.txt`
- `requirements.txt`
- `scheduler.py`
- `scope_auth.py`
- `screening.py`
- `tests/fixtures/planning_sources/README.md`
- `tests/fixtures/planning_sources/amtsblatt-menziken-versions.html`
- `tests/fixtures/planning_sources/amtsblatt-seon-page1.html`
- `tests/fixtures/planning_sources/amtsblatt-seon-page2.html`
- `tests/fixtures/planning_sources/consultations-archive-query.json`
- `tests/fixtures/planning_sources/consultations-archive-widget.html`
- `tests/fixtures/planning_sources/consultations-current-page0.json`
- `tests/fixtures/planning_sources/consultations-current-page1.json`
- `tests/fixtures/planning_sources/consultations-current-query.json`
- `tests/fixtures/planning_sources/consultations-current-widget.html`
- `tests/test_amtsblatt.py`
- `tests/test_amtsblatt_import.py`
- `tests/test_app.py`
- `tests/test_detail.py`
- `tests/test_detail_preview.py`
- `tests/test_oereb.py`
- `tests/test_planning.py`
- `tests/test_planning_collect.py`
- `tests/test_planning_preview.py`
- `tests/test_planning_refresh.py`
- `tests/test_regulations.py`
- `tests/test_schema.py`
- `tests/test_scope_auth.py`

## 2. Commands executed

| Step | Command / method | Exit | Actual result |
|---|---|---:|---|
| Scope | `git diff --name-only`; `git ls-files --others --exclude-standard`; baseline SHA256 comparison | 0 | Four runtime files changed in this continuation; prior work preserved |
| Secrets | Python regex scan of all 45 changed/new text paths for private-key headers, provider keys and quoted secret assignments; values never emitted | 0 | One match: `tests/test_scope_auth.py:30`, reviewed as explicit dummy test secret; no real secret found |
| Dependency audit, direct attempt | `uvx pip-audit -r requirements.txt --format json` | 1 | Tool's temporary `ensurepip` process aborted; not treated as a passed scan |
| Dependency resolution/audit, local | `uv pip compile requirements.txt --python-version 3.11`; `uvx pip-audit --no-deps --disable-pip -r <resolved-file> --format json` | 0 | 39 resolved packages, no known vulnerabilities |
| Dependency resolution/audit, production platform | `uv pip compile requirements.txt --python-version 3.11 --python-platform x86_64-unknown-linux-gnu`; same audit against its full resolution | 0 | 40 resolved Linux packages, no known vulnerabilities |
| SAST | `uvx bandit -q -f json` on the 12 planning/UI/auth/scheduler modules named in the evidence JSON | 0 | No Bandit findings |
| Manual SAST | Targeted `rg` for eval/exec, shell execution, unsafe SQL, unsafe TLS, file writes, caches and auth guards; caller inspection | 0 | No actionable new security finding in this scope |
| Python full suite, lead-run | `/Users/krisnafirdaus/Documents/normiq/densification-finder/.venv/bin/python -m pytest -q` | 0 | 952 passed, 1 skipped, 56 subtests passed in 107.59s |
| Python affected suite, executor-run | Planning preview/refresh, scheduler, detail, planning, importer and collector tests | 0 | 556 passed in 21.28s |
| Node suite, lead-run | `node --test tests/calculation_table.test.cjs` | 0 | 12 passed |
| Syntax/diff | `python -m py_compile planning_preview.py planning_refresh.py detail.py scheduler.py`; `git diff --check` | 0 | Passed |
| Disabled CLI | `python -m planning_refresh --check`; `python -m planning_refresh --once` with default disabled config | 0 | Both returned `off`; no provider fetch or schedule enabled |

## 3. Findings

No unresolved Critical, High, Medium or Low security finding was identified in
the inspected scope. This is a bounded engineering check, not a guarantee that
all application behavior or future provider changes are safe.

Manual checks and regression evidence:

- Scheduling is disabled by default and requires explicit source-use configuration.
  Only fixed provider origins/routes are used; redirects, response size, deadlines,
  pages and municipality counts are bounded. No arbitrary operator URL is fetched.
- Rendering does not fetch providers. Untrusted titles, notes and attributes are
  escaped; evidence is constrained to the same publication/consultation identity.
  No automatic relevance or legal-stage promotion is introduced.
- Local stores and sidecars validate schemas, reject symlink/unrelated destinations,
  use process locks and atomic replacement. Failed fetches preserve last good data.
  Durable claims/cooldowns cover restarts; one background worker avoids blocking mail.
- Metadata or territorial review changes invalidate legal proofs. Reverting a hash
  or re-including an item does not revive its proof. Time windows use Swiss dates.
- Existing email recipient checks and transactional outbox behavior remain in place;
  planning refresh sends no email. Tests show the email loop progresses while a
  planning collector is blocked.
- Scope write callers still check identity/role/MFA, then recheck permission in the
  SQLite transaction. New planning writes are operator CLI/server jobs, not exposed
  unauthenticated web write endpoints. Existing invitation/provider identity binding
  and expiration checks are unchanged and covered by the full suite.

## 4. Exclusions and limitations

Billing webhooks, Normiq workspace/SIA retrieval, and upload endpoints are not
part of this Python Scope change. They were not represented as newly audited
Normiq functionality. Scope shares an organisation database; this change adds no
new tenant model. No new file-upload surface or document-licensing bypass exists.

The repository has no Node dependency manifest; `pnpm audit --prod` is therefore
not applicable. Its plain Node regression suite ran. Python production dependencies
were fully resolved before `--no-deps --disable-pip` auditing, which avoids the
failing temporary ensurepip step without omitting transitive dependencies.
No dependency was installed into or changed in the application virtualenv.

The one skipped full-suite test is an existing UI/address assertion whose first
fixture row has no address (`tests/test_app.py`); it is not a skipped new scheduler
or legal-verification test. Type/lint configs are not defined for this repository;
compile, full tests, Bandit and manual diff review were performed.

Browser evidence is local: Menziken displays 14 selected publications and explicitly
marks the 4 September–5 October 2026 Auflage as ended, with a neutral badge and
original-source links. This is not a production check. Scheduled live provider
access, production rollout and external permission have not been completed.
A read-only Railway query verified the existing deployment and timezone data only.

## 5. Re-check evidence

Lead review led to two corrections before the final full suite: legal-proof
invalidation uses processing time for late imports; the scheduler uses a separate
worker so slow providers cannot block email. Swiss-date boundary tests cover the
next-day transition at UTC 22:30 in summer. The executor's only reported test
failure was an assertion expecting an abbreviated label; it was corrected to the
existing product label, and the affected/full suites passed afterwards.

Evidence files are retained with the release artifacts: full pytest log, Linux
dependency resolution/audit, final Bandit JSON, sanitized secret-scan locations,
review decisions/legal-source facts and browser screenshot/DOM snapshot.

## 6. Verdict

**PASS — scoped security verification.** No unresolved finding meets a blocking
severity threshold; required applicable checks have evidence above. This PASS
is not approval of provider data rights or a claim that the feature is live.
**Production data activation remains pending** the documented source-use basis
and the release procedure in `2026-10-06-planning-release-readiness.md`.
