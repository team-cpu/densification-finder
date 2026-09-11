# Second factor (TOTP) — design

Date: 2026-09-11. Decision from the user: TOTP via an authenticator app.

## Why

The e-mail code proves control of a mailbox and nothing else. Owners asked
for a second factor; the Einstellungen dialog already carried a disabled
"Zwei-Faktor-Authentifizierung erzwingen" switch.

## What

Supabase Auth MFA in the dedicated Scope project, driven through the same
`scope_auth.api` transport. No new dependency, no service-role key, no JWT
decoding: the app never inspects the `aal` claim. Instead a server-side
session flag `scope_mfa` is set only after the provider returned a new
session from `POST /factors/{id}/verify`, and that session's access token
replaces the one in session state.

- **Policy.** `organisation_profile.enforce_2fa`, editable by owners in
  Einstellungen in personal mode (stays disabled in shared mode). Off: no
  second factor, no optional enrolment. On: every member completes TOTP after
  the e-mail code on every login, enrolling on first use. Turning it on takes
  effect at the next rerun of every open session.
- **Enrolment.** `POST /factors` (`factor_type: totp`, issuer "Scope").
  The card shows the provider's QR code (SVG via `st.image`), the manual key
  in groups of four, and a code field; `challenge` + `verify` activate the
  factor. Unverified leftovers are unenrolled first so a fresh secret is
  issued. The secret lives only in server-side session state until verified.
- **Verification.** A verified TOTP factor is read from `GET /user` →
  `factors`, which the gate already calls. Card with one six-digit field.
  Five attempts per five minutes per member, tracked in
  `scope_auth_verifications` under `mfa:<email>`; provider limits also apply.
- **Device change.** Account menu → "2FA zurücksetzen" (AAL2 session):
  `DELETE /factors/{id}`, then the gate enrols again. **Lost device:** an
  operator removes the factor in the Scope project's Auth dashboard; the app
  intentionally has no admin credential. Supabase TOTP offers no recovery
  codes, so none are shown.
- **Logout** clears everything as before.

## Not in scope

Optional per-member 2FA while enforcement is off, phone/WebAuthn factors,
remembering devices, admin-side reset inside the app.

## Verification

Unit: transport allow-list for factor paths; member carries `mfa_factor`;
enrolment/verification flows through the gate with mocked provider
responses; attempt limit; settings toggle writes the profile. Live: real
enrolment against the Scope project with a TOTP computed from the shown key,
then reset from the account menu.
