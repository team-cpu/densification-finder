# Scope — consolidated verification, 12 September 2026

## Conclusion

The six original feedback groups in `scopebeforeafter.pdf` have implementation and recorded verification evidence. This does not mean every feature in the reference, every browser state, and production activation are complete.

Earlier chat statements that postal CSV, non-Chrome field sizing, and live TOTP had never been tested were incorrect: the repository contains dated evidence for those checks. This review distinguishes existing evidence from checks repeated today.

## Verification matrix

| Requirement | Evidence | Status / limitation |
| --- | --- | --- |
| Municipality single select/reset | 8 September feedback report and three engine result JSON files | Previously passed; not all filter combinations rerun today |
| ÖREB label | Visible in today's Screening browser tree | Confirmed locally |
| Saved parcel number and removal menu preserving history | Feedback and interaction reports; saved card 954 visible today | Implemented; destructive interaction not repeated today |
| Board strip removed; Analyse then white owner button | Current board DOM and earlier visual evidence | Confirmed locally; no new pointer drag today |
| Owner phone, postal address, Status, notes, no follow-up input | Current 390×844 owner dialog screenshot and DOM; phone 078 777 88 00; three-line address; width 366 px, left margin 12 px; Escape closes | Confirmed locally; long notes use scrolling form body |
| Municipality-specific regulations | Opened Möhlin panel today: one relevant BNO, 13.12.2023, PDF attachment 10145; other canton entries collapsed separately | Confirmed displayed filtering, not independent legal completeness |
| CSV Postadresse | Reopened and parsed actual Chrome/Firefox/WebKit exports in artifacts/mobile-csv-audit; all 14 columns, one record, correct comma/umlaut/address. Also reopened 10 September downloaded exports with embedded quotes | Passed archived download evidence; current regression test passed. Today's export button was clicked, but a new downloaded file was not located, so no fresh-download claim |
| Mobile / field-sizing outside Chrome | Existing JSON and screenshots for Chromium 152, Firefox 155 and Playwright WebKit 26.6 at 390×844; notes 58→234 px. Touch-emulation evidence at 320 and 430 px | Browser-engine coverage exists. This is not physical-phone/native Safari coverage; not repeated across engines today |
| Invitations, roles, revocation, logout, OTP expiration/rate limits | 11 September live auth report | Previously passed; not a fresh authenticated run today |
| TOTP setup, reset, incorrect/correct challenge, login again | 11 September live auth report, second-factor addendum | Live testing documented, not merely unit tests. Physical authenticator app/recovery not established |
| MFA callback/write policy and mail recipient access | 12 September fix verification and regression tests | Passed locally |
| Reminder/digest delivery | Two 12 September emails confirmed by user's inbox screenshots; isolated scheduler execution and duplicate run | Delivery confirmed; not proof of automatic production scheduling |
| Production personal mode | Production browser opened today; displays “Gemeinsamer Zugang mit Passwort.” | Not active in the UI checked today |
| Shared team calculations | organisation.py says “Noch nicht verfügbar. Annahmen bleiben derzeit pro Sitzung; keine Freigabesteuerung.” | Not implemented; reference parity exception |
| Exact visual parity | Multiple prior page/interaction audits and follow-ups; actual data/formulas intentionally differ from mock | No claim of pixel identity or all possible hover/focus/error/data states |

## Checks run today

- `.venv/bin/python -m pytest tests/test_acquisition.py tests/test_scope_mfa.py tests/test_scope_auth.py tests/test_scheduler.py -q --tb=short`: **61 passed**.
- Latest full-suite result from the preceding fix in this same session: **321 passed, 1 skipped, 34 subtests passed**. No application code changed during this verification.
- Codex in-app browser: local personal login at 8521; shared QA UI at 8518; production login page read-only.
- QA UI: Screening loads, saved mobile card displays, owner dialog opens/closes with Escape, board renders five columns, Analyse opens, relevant Möhlin regulation expands.
- Original PDF text inspected for its six feedback groups. Detailed visual reference evidence reused from the dated audits; the PDF was not re-rendered in this pass.

## Remaining acceptance work

1. Production activation/deployment of local auth/MFA/mail changes and an observed automatic scheduler run. No deployment authorized in this pass.
2. JWT lifetime expiration in a real authenticated session. Session at 8521 currently requires login; user asked to log in again. An HTTP-401 regression does not establish actual lifetime expiry.
3. Physical mobile keyboard/touch drag, email layouts on phone, native Safari, and older browsers where CSS support differs.
4. Shared calculations require implementation/scope decision. Recovery without the second-factor device and provider-side MFA rate limits remain unverified.
5. Small copy polish remains: singular “1 Leads” (email) and “1 Parzellen” (saved list). Reminder/digest email body remains plain text.

No production data, environment, commits, or pushes changed during this verification.

## Source evidence

- `docs/2026-09-08-feedback-verification.md`
- `docs/2026-09-08-full-ui-parity-audit.md`
- `docs/2026-09-08-upload-reference-audit.md`
- `docs/2026-09-08-visual-reference-followup.md`
- `docs/2026-09-09-interaction-verification.md`
- `docs/2026-09-10-local-verification.md`
- `docs/2026-09-11-live-auth-verification.md`
- `docs/2026-09-12-auth-mail-fix-verification.md`
- `artifacts/mobile-csv-audit/{chrome,firefox,webkit}.json` and corresponding `*-contacts.csv`

## Later local implementation

Team snapshot sharing, HTML mail and singular copy were subsequently implemented. See `2026-09-12-team-calculations-and-email.md` for the newer status and verification limits. Production and real JWT lifetime testing remain outstanding.
