# 2026-09-29 — Local endpoint verification (bounded release checks)

Scope: local, read-only verification of the hardened `http_fetch` client and its
four call sites, plus the offline test suite, Bandit, pip-audit and shared-mode
auth behaviour. No commit/push/deploy, no Auth or real email, no billing, no
product-code changes (only `tests/test_login_page.py` gained one test and this
document was added). Interpreter throughout:
`/private/tmp/scope-verify-venv/bin/python` (Python 3.11.14).

## 1. Offline test suite

Command: `python -m pytest tests/ -q -p no:cacheprovider`

Result: **404 passed, 1 skipped, 56 subtests passed in 79.05 s.** No network,
no failures. The single skip is pre-existing and was not investigated further.

Independent final rerun after adding the parametrized regression test:
`env -i PATH="$PATH" python -m pytest tests/ -q` — **406 passed, 1 skipped,
56 subtests passed in 78.72 s**.

Targeted auth run:
`python -m pytest tests/test_login_page.py tests/test_scope_auth.py -q`

Result: **74 passed in 2.34 s.**

### Shared-mode (personal auth OFF) checks

`tests/test_login_page.py` (Streamlit `AppTest`, no browser automation):

- `test_shared_gate_uses_card_and_form_button` — default/shared mode renders
  the shared card, wrong password is denied with `Falsches Passwort.` and no
  `_ok` session flag, correct password sets `_ok` and renders the app.
- `test_shared_mode_makes_no_auth_or_provisioning_calls` **(new)** — runs the
  same wrong/correct password flow with `scope_auth.api` and
  `scope_auth.normiq_api` replaced by mocks whose call is an `AssertionError`.
  Neither is invoked: shared/default mode makes **no Supabase Auth and no
  Normiq passcode/provisioning call**. The lead parametrized both unset and
  explicit `shared` mode and added explicit `assert_not_called()` assertions.
  Independent login-page + security-helper rerun: **18 passed, 22 subtests**.

Provisioning-call discipline in personal mode is covered by the existing
suite (`test_bound_member_is_never_provisioned_again`,
`test_uninvited_expired_and_revoked_emails_are_never_provisioned`,
`test_failed_provisioning_sends_no_code`). Bound members call request-passcode
without provisioning; uninvited/expired/revoked members make no calls; failed
provisioning makes the provisioning call but does not request a passcode.

## 2. Bandit (production code, tests excluded)

Command: `python -m bandit -r . -x tests,__pycache__,.pytest_cache,scripts -q`

Result: **0 unsuppressed findings**, with **12 specific `nosec` exceptions**.
Independent scan covered 10,111 lines with no skipped files; this is not a
claim of zero raw findings. The B310 remediation (`http_fetch.py` — https-only,
fixed hosts, port 443, no userinfo, proxies disabled, same-origin redirect
re-validation) and the B608 fix (`sqlquote.ident`) scan clean. Provenance:
`docs/2026-09-29-bandit-review.md`.

## 3. pip-audit

pip-audit was **not installed** in the verify venv. Runtime environment
failure on first attempt: `pip_audit -r requirements.txt` builds a temporary
venv via `ensurepip`, which SIGABRTs under the uv-managed CPython 3.11.14 —
an environment limitation, not a dependency defect. Workaround: installed the
pinned `requirements.txt` into the disposable verify venv and ran
`python -m pip_audit --local`.

Result: **No known vulnerabilities found.**

## 4. Live endpoint compatibility (bounded, read-only)

Harness: disposable `/private/tmp/scope-verify-harness.JxIGz7/`, one GET per
call site routed through the actual hardened `http_fetch.get`
(proxies disabled, URL validated pre-request and at every redirect,
same-host redirect policy intact — no code changes, no TLS bypass, no host
broadening). Timeout 30 s per socket operation, not a total deadline;
at most 1 retry, no bulk ingestion, no
canton download. Fixture: public cadastral data for BFS 4001, EGRID
`CH515203237715`, read from the committed seed `results.sqlite` opened
`mode=ro`. Cache writes: none — responses saved only inside the harness dir.

| Check | URL shape | Result | Latency / attempts |
|---|---|---|---|
| Parcel WFS `ms:RESF` | `geodienste.ch/db/av_0/deu` GetFeature, `COUNT=1`, `BFSNr=4001` | PASS — `numberReturned=1`, `<ms:RESF>` present, valid FeatureCollection XML | 32.9 s, 1 attempt |
| Land-cover WFS `ms:LCSF` | same host, `COUNT=1`, production transport filter | PASS — `numberReturned=1`, `<ms:LCSF>` present, valid FeatureCollection XML | 4.9 s, 1 attempt |
| ÖREB extract | `api.geo.ag.ch/v2/oereb/extract/json/?EGRID=…` | PASS — JSON parses, returned EGRID matches request, 6 restriction entries via `oereb.restrictions` (parser semantics verified on the live body) | 4.8 s, 1 attempt |
| OEREBlex edicts | `oereblex.ag.ch/api/edicts.json` | PASS — JSON list of 196 municipalities / 234 edicts; `regulations.parse` yields 227 in force | 1.7 s, 1 attempt |

Payload/parser semantics (response Content-Type headers were not audited):
an `ows:ExceptionReport` (or any body with
neither the feature marker nor `numberReturned="0"`) is judged a **defect**,
never a pass; none occurred. The ÖREB body was additionally run through
`oereb.restrictions` (6 entries, e.g. `('ch.Planungszonen', 'Gemeinde',
19110)`) and the edicts body through `regulations.parse` — both production
parsers accept the live payloads.

### COUNT=1 vs full batch

Production uses `COUNT=40000` (`ingest.fetch_parcels`, `land_cover.fetch`),
which the harness deliberately did **not** run (bounded check, no bulk
download). What the harness verifies is request/response semantics — URL
shape, filter encoding, WFS 2.0.0 conformance, feature markers, and that the
server honours `COUNT` (`numberReturned=1`). It does **not** verify that a
full municipality batch (typ. ~4 MB, ~10 s) still succeeds at `COUNT=40000`;
that remains covered only by the cached-fixture unit tests, not by a live
full-batch call.

### Seed integrity

`shasum -a 256 results.sqlite` before and after every step:
`daede1f384097759ed287e987ddff6008a10a815a1586c4284a4063638c847e9` — unchanged.

## 5. Network and side-effect bounds

- Live requests went only to the three fixed public hosts:
  `geodienste.ch`, `api.geo.ag.ch`, `oereblex.ag.ch` (4 executor GETs plus
  2 independent lead GETs for land cover and edicts).
- Dependency installation and auditing used PyPI;
  no application traffic.
- No mail sent: the email suite (`test_email_delivery.py`, `test_email_outbox.py`)
  stubs the opener; scheduler delivery functions are mocked. No scheduler
  process was launched.
- No Auth, provisioning, billing, staging or production systems touched.
- `git status` product-code modifications predate this pass (user security
  changes, incl. untracked `http_fetch.py`/`sqlquote.py`) and were preserved
  untouched; this pass changed only `tests/test_login_page.py` (+1 test) and
  added this document.

## 6. Runtime environment failures (kept separate from defects)

1. `pip_audit -r requirements.txt` cannot build its audit venv:
   `ensurepip` SIGABRT under uv-managed CPython 3.11.14. Worked around with
   `--local` against the pinned set (see §3), which passed. Requirements-mode
   auditing itself remains unverified.
2. Parcel WFS total elapsed time was 32.94 s; time to first byte was not
   measured. The 30 s socket timeout is not a total wall-clock deadline.
   Consider that production's
   `timeout=300` batch calls have more headroom but the same upstream.

## 7. Remaining limitations

- Full-batch `COUNT=40000` WFS behaviour not live-verified (bounded by
  instruction); only COUNT=1 semantics and cached-fixture unit tests.
- Same-origin redirect policy was exercised via unit tests
  (`tests/test_bandit_helpers.py`) and the harness's unchanged code path; no
  redirect history was not recorded, so whether live redirects occurred is
  unknown. Malicious redirect denial was verified by unit tests.
- One pre-existing pytest skip not investigated further.
- Endpoint answers reflect 2026-09-29 upstream state; upstream schema drift
  between release and deploy is only detectable by re-running this harness.

Independent payload review also parsed both XML documents with ElementTree,
loaded one valid positive-area land-cover geometry using the application
parser, and verified six ÖREB restrictions and 227 active edicts.

**Scoped verdict: PASS for these local endpoint/shared-mode checks. This is
not full release clearance:** deployed email/browser sessions, deployment
configuration and full billing-provider integration are outside this report.
