# Due-date reminders and weekly digest — design

Date: 2026-09-11. Continues the Einstellungen switches that shipped disabled.

## Why

*Fällige Wiedervorlagen* only surfaces on the board, so a promised call-back
still depends on somebody opening Scope. Owners asked for the two e-mails the
settings dialog already names.

## What

A `scheduler` module with pure planning and rendering functions and one
`run_once(db, now)` that turns the organisation's switches into outbox
messages. Delivery reuses `email_outbox` + the Resend transport, so every
message has a stable event key, a lease, and a stored provider id.

- **Policy.** `organisation_profile.due_reminders` and `weekly_digest`,
  editable by owners in Einstellungen in personal mode (disabled in shared
  mode, as with 2FA). Recipients: every *active* member. Nothing is sent
  without Resend configuration and `SCOPE_PUBLIC_URL`.
- **Reminder (daily).** From 07:00 Europe/Zurich, if any saved, not hidden
  lead has a follow-up date ≤ today and is not *Abgelehnt*: one plain-text
  e-mail per member listing those leads (address or parcel number,
  municipality, stage, due date, contact), oldest first, plus the Scope link.
  Event key `reminder/<date>/<member id>`. No due leads → no e-mail.
- **Digest (weekly).** Mondays from 07:00: board totals per stage, overdue
  count, follow-ups due in the next seven days, leads touched in the last
  seven days. Sent even when quiet. Event key `digest/<ISO week>/<member id>`.
- **Idempotence.** A key that already exists in the outbox is skipped, so a
  run that repeats the same day changes nothing; a delivery that failed
  uncertainly is retried through the outbox lease rules, never re-rendered.
- **Process.** `python -m scheduler` loops every ten minutes calling
  `run_once`; the container starts it beside Streamlit (`Dockerfile` CMD),
  so it does not depend on a browser session. `python -m scheduler --once`
  runs a single pass and prints what it did. Local development does not
  start it unless asked.
- **Time zone.** Europe/Zurich via `zoneinfo`; if the image lacks tz data
  the fallback is fixed UTC+1 and the reminder shifts by an hour in summer.

## Not in scope

Per-member opt-out, HTML e-mails, sending to pending invitees, retries beyond
the outbox rules, a second replica of the scheduler.

## Verification

Pure tests for planning (before/after 07:00, Monday), rendering (German
text, ordering, empty cases) and `run_once` idempotence with a mocked Resend
call; settings toggles write the profile. Local `--once` run against the QA
database with a due lead, message received in the owner's inbox.
