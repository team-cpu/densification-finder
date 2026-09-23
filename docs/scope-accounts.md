# Scope accounts — shared Normiq Auth (phase 1)

User decision (2026-09-22): Scope reuses **Normiq's existing Supabase Auth
project and user directory**. Each Scope member signs in with their existing
Normiq identity; there is no separate Scope auth project.

## Authentication vs. Scope authorization

These are deliberately separate and both are required:

- **Authentication** proves a Normiq identity (six-digit passcode through
  Normiq's custom passcode API, the same code Normiq emails via its Resend
  template). Proving a Normiq identity grants nothing.
- **Scope authorization** comes only from the local invitation: a row in
  `organisation_members` plus a `scope_access` grant whose immutable provider
  user UUID is bound on first verified login. An authenticated Normiq identity
  without a local invitation is denied with the same generic responses as an
  unknown user, so identities cannot be enumerated through the login form.

JWT payloads and editable user metadata are never trusted for roles; the
bound provider UUID is the only identity anchor.

## Phase-1 limitation: existing Normiq users only

The passcode request is sent with `isSignup: false`, so Normiq's API only
permits existing Normiq users and Scope never registers a new user in the
shared Normiq project. That is intentional: Normiq currently treats auth-user
existence as sufficient to self-provision its `public.users` row on passcode
request, and Scope must not silently create Normiq users. Consequence:
**invitations only work for people who already have a Normiq Auth account.**
Support for Scope-only users (product entitlement decides who may exist in
Normiq's directory) is a prerequisite for phase 2, not an automatic by-product
of phase 1.

Scope keeps no service-role key; it only ever uses the anon/publishable key,
and only for the allowlisted Supabase auth endpoints (user, logout, TOTP
factor lifecycle) that validate the Normiq-issued token.

## Passcode flow

1. The invited member enters their email and clicks "Code anfordern". Scope
   checks the local invitation first; unknown, uninvited and expired emails get
   the same UI response and no provider call, so the response leaks nothing.
2. `POST {SCOPE_NORMIQ_AUTH_URL}/api/auth/request-passcode` with
   `{"email", "isSignup": false}` asks Normiq's API to email its six-digit
   Resend code to an **existing** Normiq user. Normiq's own expiry and rate
   limits apply; Scope adds a 60-second per-member request cooldown and five
   verification attempts per five-minute window.
3. "Anmelden" posts the code to `/api/auth/verify-passcode` with
   `{"email", "passcode", "rememberMe": false}` and reads `access_token` from
   the bounded JSON response, then `GET /auth/v1/user` on the shared Supabase
   project proves the identity.
   On first login the provider UUID is bound immutably into `scope_access` and
   the member becomes active. A later ID mismatch or removed membership is
   rejected.
4. Owners can invite, revoke pending invitations, change roles and remove
   members. Editors persist lead/search changes; readers cannot. Every domain
   write re-checks role and membership under its own transaction lock.

Invite notification emails carry only the Scope URL, never a token; the
recipient still proves mailbox ownership through the login code. Tokens live
only in server-side Streamlit session state, no refresh token is retained, and
logout clears all session drafts even if the provider logout fails. An already
displayed page can remain visible until the next interaction; revocation is
checked on subsequent actions.

The TOTP second factor (`enforce_2fa`) and the reminder/digest emails are
owner switches in Einstellungen; recovery without the authenticator device is
an operator action in the shared project's Auth dashboard.

## Setup and environment mapping

Keep the app-specific names; copy the values from Normiq's configuration
(never into git):

| Scope variable          | Source in Normiq                                    |
|-------------------------|-----------------------------------------------------|
| `SCOPE_NORMIQ_AUTH_URL` | Canonical HTTPS origin of the Normiq app            |
| `SCOPE_SUPABASE_URL`    | `NEXT_PUBLIC_SUPABASE_URL`                          |
| `SCOPE_SUPABASE_ANON_KEY` | `NEXT_PUBLIC_SUPABASE_ANON_KEY`                   |

- `SCOPE_NORMIQ_AUTH_URL`: origin of the Normiq deployment whose custom
  passcode API Scope calls (validated strictly: HTTPS origin only, no
  path/query/fragment/userinfo; `http://127.0.0.1:<port>` or
  `http://localhost:<port>` for local development). Login-code emails use
  Normiq's custom API and its Resend template — Scope no longer depends on the
  shared Supabase project's email template or its redirect URL, and the Magic
  Link template must not be changed for Scope.
- `SCOPE_AUTH_MODE=personal` activates personal login. Default `shared`
  preserves current production behavior; invalid modes or missing personal
  configuration stop the application without falling back to the shared
  password.
- `SCOPE_SUPABASE_URL`/`SCOPE_SUPABASE_ANON_KEY` remain required: the
  Normiq-issued access token is validated with `GET /auth/v1/user` and the
  TOTP second factor uses the provider's factor lifecycle.
- `SCOPE_OWNER_EMAIL`: first owner's verified email, granted only on initial
  setup. Changing it later does not promote another person; the initial
  invitation lasts seven days and an operator must renew an expired unclaimed
  bootstrap grant.
- `SCOPE_PUBLIC_URL`: canonical HTTPS Scope address, without query or fragment.
- `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, optional `RESEND_FROM_NAME`: existing
  Resend configuration for invitation notifications.

## Rollout

1. Set `SCOPE_NORMIQ_AUTH_URL` to the Normiq deployment origin and copy
   `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` from Normiq
   into Scope's secret store as `SCOPE_SUPABASE_URL` /
   `SCOPE_SUPABASE_ANON_KEY`. No service-role key.
2. Set `SCOPE_AUTH_MODE=personal`, `SCOPE_OWNER_EMAIL` (an existing Normiq
   user), `SCOPE_PUBLIC_URL` and the Resend variables.
3. Deploy to a staging instance and verify with authorized test recipients:
   login, invitation of an existing Normiq user, role change, revocation,
   logout, second factor.
4. Invite real members only after their Normiq accounts exist.
5. Roll out production. Rollback is `SCOPE_AUTH_MODE=shared` plus removing the
   personal variables; the shared password gate then applies again. Switching
   to shared mode removes personal role enforcement, so it is an operator
   security decision, not an automatic rollback.

## Verification status

Tests use isolated SQLite databases and mocked provider responses; they prove
the passcode request uses Normiq's API with `isSignup: false` after the local
invitation check, the verify answer's token is validated via `GET /auth/v1/user`
and binds only the invited member, `SCOPE_NORMIQ_AUTH_URL` is strictly
validated, error classes never surface provider bodies, and an authenticated
identity without `scope_access` stays denied. Local browser checks exercise
login rendering and rejection paths; they do not prove real email delivery or a
live shared-project login. No external change, deployment or push was performed
for this phase-1 preparation. Live verification (login, invitation, revocation,
roles, logout, second factor) with authorized test recipients is part of the
rollout above.

Official references:
- Normiq custom passcode API: `/api/auth/request-passcode`,
  `/api/auth/verify-passcode` (existing Normiq endpoints, phase 1 calls them
  server-side with `isSignup: false` / `rememberMe: false`)
- https://supabase.com/docs/guides/auth/auth-email-passwordless
