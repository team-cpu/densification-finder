# Local team calculations and HTML mail — 12 September 2026

## Behavior

Personal-mode organisations can enable “Kalkulationen teamweit sichtbar” in settings. The default is off. A one-time migration resets the old disabled-placeholder default; subsequent owner choices survive startup.

Analysis now offers explicit save/load of one latest team snapshot per parcel. Owner and Editor can save; Reader can load and experiment with a personal draft. Snapshots include potential, unit size, cost/revenue assumptions, demolition choice and manual CHF overrides. Existing approved formulas are unchanged. Loading a snapshot also replaces session-wide rate assumptions, as the existing analysis model shares those rates across parcels. Draft changes, resets and new parcels do not silently publish.

Snapshots use a revision check: if another session saves since the current draft's baseline, saving fails with a prompt to load the latest version. Loading is explicit and replaces the current draft. This is snapshot sharing, not real-time coediting or full version history.

Reminder/digest mail now contains escaped HTML with Scope colours, a readable card and a Board button, plus the existing plain-text alternative. HTML is persisted in the durable outbox; retrying does not regenerate content or change the idempotency key. Legacy messages without HTML continue sending their original text payload. “1 Lead” and “1 Parzelle” now use singular text.

## Data and access

- New `shared_calculations` table: parcel key, validated JSON payload, revision, updating member and UTC timestamp. One Scope organisation per database, consistent with existing architecture; no multitenant claim.
- New nullable `email_outbox.body_html` column; existing records retained unchanged.
- Team read/write paths recheck membership grant, active status, organisation setting and MFA. Writes recheck roles under the transaction lock and use optimistic revision control.
- Disabling sharing hides access; it does not delete snapshots. Existing loaded drafts remain in their authorised session.
- No dependencies added. No production configuration, deployment, push, real-team permissions or data changed.
- Rollback: revert application code and leave additive tables/columns in place. Do not drop snapshot/outbox data. Restore from backup only if a data rollback is explicitly required.

## Verification

- Final full Python suite: **337 passed, 1 skipped, 34 subtests passed** (`.venv/bin/python -m pytest -q --tb=short`).

- Service tests: save/load, parcel isolation, stale revision rejection, Reader denial with stale cached role, Editor write, revoked read, MFA and disabled setting denial, invalid/nonfinite inputs, and migration preserving owner choice.
- Streamlit widget test: load stored assumptions into controls, change sale price and save a new revision. Fixed the test fixture to use Streamlit's real session-state proxy for widget tests.
- Email tests: escaped untrusted content, safe HTTPS link, existing transport and outbox tests, persistent HTML retry payload.
- Browser: actual analysis UI on localhost:8522 with an isolated DB and a synthetic member adapter (not live authentication). First session saved price 8400; second session loaded 8400 and saved version 2; first session's stale save was rejected with the intended message.
- Browser email previews: desktop plus iframe documents at 390 and 320 px. Measured document/scroll widths 390/390 and 320/320, respectively. No horizontal overflow. This verifies responsive HTML in Chromium, not rendering in a native mobile email client.
- Real send: Resend accepted reminder and digest previews to kris@normiq.ai with `[TEST HTML]` subjects. Inbox rendering of these new messages awaits recipient confirmation.
- JavaScript calculation component: 12 tests passed. Python compileall and `git diff --check` passed.

## Focused security review

PASS for this change scope: no unresolved High/Critical findings after manual diff review and targeted auth/mail regression checks. SQL is parameterized; inputs and overrides are validated; HTML content and URLs are escaped; HTTP transport remains fixed to Resend HTTPS with redirect denial; secrets are not included in artifacts or logs. A targeted search found no eval/exec, shell=True, verify=False or pickle.loads in the new sharing/mail modules and adjacent transport/outbox.

Bandit/pip-audit and the security skill's referenced `.ai` report templates were unavailable; this is a focused manual/regression review, not a dependency-audit certification. Billing, uploads and Normiq document access are outside this Scope-only change.

## Still outstanding

- Real JWT lifetime expiry: no active login session was available. User asked to log in at localhost:8521; do not replace this with a simulated-401 claim.
- Physical phone keyboard/touch and native email-client screenshots, older browser versions and exhaustive visual parity.
- Production rollout remains deliberately excluded.
