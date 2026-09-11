# Live personal-auth verification — 11 September 2026

Local only. No commit, push, deployment or production change. Production keeps
`SCOPE_AUTH_MODE=shared`.

## Setup

- App on `localhost:8521` from the working tree, `SCOPE_AUTH_MODE=personal`,
  isolated database `/private/tmp/scope-live-auth-kris.sqlite`.
- Real dedicated Scope Supabase project (OTP e-mails over its Resend SMTP) and
  the real Resend HTTP transport (`RESEND_API_KEY`, sender
  `noreply@auth.normiq.ai`, name `Scope`) injected at launch; no secret copied
  into the repository or a new file.
- Claude in-app browser; two tabs = two independent Streamlit sessions (owner
  `kris@normiq.ai`, invitee `kris+test@normiq.ai`, a plus-alias of the same
  mailbox so one person could relay both login codes).
- Provider calls observed through a launch-time wrapper around
  `scope_auth.api` that logged endpoint + HTTP status only (no tokens, no
  codes); removed again afterwards. App code was unchanged during the checks;
  the two fixes below were made and re-verified afterwards.

## Results

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Owner login with real e-mail OTP | PASS | `verify` 200 → `user` 200, header shows `kris`, `scope_access.auth_id` bound |
| 2 | Invite member (Bearbeiter) → notification e-mail | PASS | `email_outbox` row `accepted`, Resend id `6c8a0a28-…`, roster shows *E-Mail von Resend angenommen*. Recipient confirmed receipt of *Ihre Einladung zu Scope* |
| 3 | Invitee login with own OTP, binding | PASS | member 2 `active`, `auth_id` bound, header shows *Kris Test* |
| 4 | Bearbeiter can write | PASS | saved search *QA Bearbeiter* persisted |
| 5 | Bearbeiter cannot manage team | PASS | Organisation dialog shows only *Nur Inhaber können das Team und die Einstellungen ändern.* |
| 6 | Owner changes role → Leseweise; reader session | PASS | banner *Lesezugriff: …* on next rerun; save attempt blocked with *Lesezugriff: Änderungen sind nicht erlaubt.* (inside the popover), nothing written |
| 7 | Owner changes role → Inhaber | PASS | invitee opens full Team dialog, banner gone |
| 8 | Owner removes member; live session of removed member | PASS | next interaction → *Sitzung abgelaufen oder Zugang nicht mehr gültig* + login form; provider `logout` called |
| 9 | Removed member re-login | PASS | *Code anfordern* silently ignored (no provider call, no mail); any code → *Anmeldecode ist nicht gültig oder abgelaufen.* |
| 10 | Pending invite: *Versand prüfen / erneut versuchen* | PASS | idempotent — same outbox row, `attempts` stays 1, no second mail |
| 11 | Pending invite: *Widerrufen* | PASS | `scope_access` row deleted, activity *Einladung widerrufen*; later code request silently ignored |
| 12 | Logout (*Abmelden*) | PASS | login form, session state cleared, provider `logout` 200 |
| 13 | Verification rate limit | PASS | 5 wrong codes reach the provider (403), 6th blocked locally: *Zu viele Versuche. Bitte in fünf Minuten erneut versuchen.* |
| 14 | Code-request cooldown | PASS | two clicks within 60 s → one provider call, same neutral message |
| 15 | Code expiry | PASS | codes relayed after 23 and 86 min rejected by the provider (`otp_expired`, 10-minute TTL) |

## Findings and fixes

1. **Fixed.** In personal mode the Organisation dialog said *Gemeinsamer
   Zugang* in its subtitle and *Einladungen werden gespeichert; E-Mail-Versand
   benötigt persönliche Benutzerkonten.* in its footer — the shared-mode
   wording, although e-mail is sent. `organisation._header` now says
   *Persönliche Konten* and `_dialog` explains the code login and the
   seven-day validity; shared mode keeps its texts (`test_shell` still asserts
   them).
2. **Fixed.** After *Widerrufen* the row still offered *Widerrufen* (a no-op)
   and *Versand prüfen / erneut versuchen*, which silently re-granted access and
   sent a new e-mail. `load_members` now reports `revoked` (pending row without
   a `scope_access` grant, personal mode only); such a row shows a single
   *Erneut einladen* button, no *Einladung offen* badge, and the shared resend
   handler lives in `_resend_pending`. Verified live: re-invite created a new
   outbox event (`invitation/3/<new invited_until>`, Resend accepted), re-granted
   access for seven days and the row returned to the open state.
   Tests: `test_revoked_invite_is_flagged_and_reinvite_regrants`,
   `test_team_dialog_uses_personal_wording_and_revoked_invite_offers_reinvite_only`
   (each guard was broken once to confirm the tests fail). Full suite after the
   change: 294 passed, 1 skipped, 34 subtests (after all fixes below); `git diff --check` clean.
3. **Fixed.** There was no "last owner" guard. In personal mode the invariant
   already held structurally (an actor must be an active owner and cannot
   change or remove itself), so the guard is defence in depth there and a real
   change in shared mode, where roles are advisory and the sole active owner
   could be demoted or removed. `organisation._guard_last_owner` now refuses to
   demote (`set_member_role`) or remove (`remove_member`) the only *active*
   owner — pending owner invitations do not count — with *Mindestens ein
   aktiver Inhaber muss bleiben.*; both writes take the write lock first
   (`_begin_owner_write`, `BEGIN IMMEDIATE` in shared mode too), so two sessions
   cannot each remove "the other" owner. `load_members` reports `last_owner`
   and the roster disables that row's role select and *Entfernen* up front; a
   stale click from an older render is dropped by Streamlit, and an in-run race
   surfaces the guard message as `org_error`. Tests:
   `test_last_active_owner_cannot_be_demoted_or_removed`,
   `test_last_owner_flag_marks_only_the_sole_active_owner`,
   `test_last_owner_controls_lock_and_a_stale_removal_is_dropped` (five
   mutations of the guard/flag/UI each turned a test red). A co-owner can still
   remove or demote the founding owner while another active owner remains —
   intended under the current model.
5. **Fixed.** Pending rows squeezed the identity block (`flex: 1 1 0`) below
   the long *Versand prüfen / erneut versuchen* button, truncating name and
   e-mail to *Kris …* / *kris+r…*. The row CSS now keeps identity at ≥180 px,
   lets the activity note shrink first and wraps the buttons under the row;
   verified in the dialog at the same width.
4. Streamlit form note: a code typed after clearing the field with a bare
   `Delete` key from browser automation was submitted stale (form kept the
   previous value). Select-all + type, and a fresh page, submit correctly; a
   human keyboard did not reproduce it. Not an app defect.

## Not covered

- Session expiry by JWT lifetime (Scope project setting; default 3600 s). The
  code path is the same as check 8 (`user` → 401 → logout + warning), only the
  trigger differs. Not waited for.
- Rendering of the two e-mails (OTP template, invitation text) on phone/desktop
  clients — recipient's check.
- 2FA, reminder/digest scheduling, production activation: unchanged, not
  started.

## Addendum — login card (same day)

Both gates now render inside the shared `login_page.card` (spec:
`docs/superpowers/specs/2026-09-11-login-page-design.md`). Authentication
logic, labels and messages are unchanged; `tests/test_login_page.py` covers
the chrome, the personal form and the shared-password form end to end, and
the `test_scope_auth` widget tests pass unmodified. Browser check: personal
gate at 1280/820/375 px (no horizontal overflow on mobile, card 315 px wide),
shared gate wrong password → *Falsches Passwort.* inside the card, correct
password → application. Full suite: 297 passed, 1 skipped.

## Addendum — second factor (TOTP), same day

Spec: `docs/superpowers/specs/2026-09-11-totp-2fa-design.md`. Live against
the dedicated Scope project, owner account, enforcement switched on in
Einstellungen (toggle writes `organisation_profile.enforce_2fa`; takes effect
at the next full rerun, e.g. *Fertig*):

- Enrolment card: QR (provider SVG) + manual key + code → *Aktivieren* →
  application (AAL2 token replaces the session token). TOTP computed from
  the displayed key with RFC 6238, so no phone was needed for the check.
- Account menu → *2FA zurücksetzen* → factor unenrolled → fresh enrolment
  with a new key and a rendered QR.
- Logout, new e-mail code, then *Zweiter Faktor* challenge: wrong code
  rejected, current code → application.
- Cleanup: enforcement off, factor removed; the owner account is back to
  e-mail code only.

Defect found and fixed on the way: the transport capped provider answers at
64 KB, and the enrolment answer carries the QR as inline SVG (~200 KB), so
the body was cut and the factor was created without the app seeing it
(`mfa_factor_name_conflict` on the next try). Cap is 1 MB now, with a
regression test; abandoned unverified factors are removed before enrolling.
Also: the provider's `qr_code` may be bare SVG or a data URL — both handled.

Not covered: recovery without the device (operator deletes the factor in the
Scope project's Auth dashboard), provider-side rate limits. Full suite: 306
passed, 1 skipped.

## Addendum — reminders and digest, same day

Spec: `docs/superpowers/specs/2026-09-11-reminders-digest-design.md`.
`scheduler.py`: daily due-date reminder and Monday digest from 07:00
Europe/Zurich, one plain-text mail per active member through the outbox,
event keys `reminder/<date>/<member>` and `digest/<ISO week>/<member>`; the
container starts `python -m scheduler` beside Streamlit. Einstellungen
switches *Wöchentliche Zusammenfassung* and *Erinnerung bei fälligen
Kontakten* are live in personal mode (profile defaults are on).

Live: `python -m scheduler --once` against the QA database with one lead due
10.09. → `{"reminder/2026-09-11": 1}`, Resend accepted (`086f073c-…`), mail
to the owner with address, parcel, stage, due date and contact; a second run
the same minute → `{}` (nothing re-sent). Tests: planning, rendering,
idempotence, switches/mode/config guards, uncertain-delivery retry with the
stored text (7 tests, five mutations each turned a test red).
