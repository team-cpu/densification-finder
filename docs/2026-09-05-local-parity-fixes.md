# Local HTML parity fixes — 2026-09-05

Scope: the concrete findings from the latest user-requested audit against
`Parcel Potential.dc.html`. **Local only: no commit, push or deployment.**
Philipp's calculation rules, the production password gate, actual Aargau data,
database schemas and dependency versions are unchanged.

## Changes

| Area | Fix | Files |
| --- | --- | --- |
| Amount editing | Preserve draft, focus, selection and validation through server rerenders. Send committed edits sequentially with per-parcel acknowledgements; reopening uses the latest pending amount. A rejected event is also acknowledged. | `components/calculation_table/index.html`, `ui_components.py`, `detail.py` |
| Standardwerte | Reset C's cost/rate assumptions and current parcel's overrides, preserving B's potential/unit size, replacement decision and other parcels' overrides. | `detail.py` |
| Replacement explanation | Use current checkbox and calculated/manual demolition cost, including zero overrides, instead of always claiming replacement. | `detail.py` |
| Merkliste | Count only conversations and scheduled meetings in Im Dialog. Restore reference table sizing, typography, header hint and owner/note wrapping. | `merkliste.py`, `components/merkliste/index.html` |
| Dates and status pills | Swiss display dates and matching normal/empty/overdue pills; stage pills on follow-up rows. ISO storage, comparisons and CSV export stay unchanged. | `acquisition.py`, `components/acquisition_board/index.html` |
| Team | Clear email only after valid invitation metadata is saved; retain invalid input/role and keep the modal open. No email delivery or account features added. | `organisation.py` |
| Legal sources | Add on-demand Alle herunterladen ZIP with available PDFs and escaped Quellen.html index. Link-only, failed or size/time-limited documents are explicitly identified. Disabled when no sources exist. | `source_downloads.py`, `detail.py` |

## Verification

- `.venv/bin/python -m unittest discover -s tests -q`: **255 tests passed**
  in 96.054 seconds. Includes the password gate, unchanged Philipp formulas,
  validation, reset isolation, acknowledgement of rejected edits, invitation
  clearing, date/stage regressions and archive safety tests.
- `node --test tests/calculation_table.test.cjs`: **11 tests passed**, including
  rapid commits, stale/unchanged-HTML acknowledgements, reopened pending edits,
  invalid drafts, Escape and parcel changes.
- Local browser against a separate temporary database: reset preserved unit
  size 100; disabling demolition changed the explanation and zeroed its cost;
  editing construction to 60,000,000 while typing ancillary cost 9,000,000
  preserved the second draft and stored both edits. All four tabs opened.
- Source download placement inspected at desktop width and 736 × 863; temporary
  viewport override reset afterward. Merkliste and acquisition cards inspected.
- Live read-only source smoke test: approved OEREBlex PDF included successfully;
  a Fedlex webpage remained a correctly labelled source link. No files were
  written to the repository and no production data was used for testing.
- `git diff --check`: clean. Existing untracked user videos preserved.

## Security scan

**Verdict: PASS for this scoped local change.** This is not a release approval
or a claim that personal accounts/email/2FA have been implemented.

The skill's three prescribed `.ai` references/templates are absent. This report
uses its strict severity rules with explicit boundary tests as the fallback.

- Bandit on `acquisition.py detail.py organisation.py source_downloads.py
  ui_components.py`: no findings.
- Gitleaks redacted scan of the diff and new source/test files: no leaks found.
- `pip-audit --path .venv/lib/python3.11/site-packages --progress-spinner off`:
  no known vulnerabilities. Used the available cached scanner runtimes; no
  installation or dependency changes.
- PDF fetches: exact approved HTTPS hosts/path prefixes, verified TLS, default
  port only, no redirects, no proxy credentials and no arbitrary-host fetches.
  Limits: 10 MiB/file, 50 MiB/archive, 40 downloads, 5-second per-file and
  25-second total deadlines. Generated filenames prevent archive path traversal.
  PDF signature and declared/actual size are checked. Failure messages expose
  no raw network exception; index content and links are escaped and validated.
- Invitation validation and parameterized SQL remain intact. No tokens or
  invitation acceptance endpoint were introduced; stored roles are not new
  authorization controls. No billing, webhook, upload or tenant changes.
- Existing Medium B307 in unchanged `economics.py` remains contained to static
  source-controlled expressions and validated numeric inputs. Keep this
  boundary; follow-up expression-evaluator hardening target: 2026-09-11,
  consistent with the previous audit. No new High/Critical findings.

## Remaining limits

Real account identity, invitation email delivery and 2FA still require a chosen
provider and explicit implementation scope. Source ZIPs cannot promise every
external source is an available PDF; the index explains omissions. The current
production deployment has not been modified by this task.
