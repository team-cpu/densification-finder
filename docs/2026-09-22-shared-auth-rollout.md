# 2026-09-22 — Shared-auth phase-1 rollout checklist

Decision recorded 2026-09-22: Scope reuses Normiq's existing Supabase Auth
project instead of a dedicated Scope project, and logs in through Normiq's
existing custom passcode API so members get the same six-digit Resend code as
in Normiq (the shared Supabase Magic Link template and its redirect URL are not
usable for Scope and were left untouched). Design, behavior and security
boundary are documented in `docs/scope-accounts.md`; this note is only the
operational checklist.

## Code prepared in this phase

- `scope_auth.request_code` calls Normiq's
  `/api/auth/request-passcode` with `isSignup: false` — after the local
  invitation check — so Scope never registers a new user in Normiq's shared
  auth directory, and `verify_code` calls `/api/auth/verify-passcode` with
  `rememberMe: false`, validates the returned token with Supabase
  `GET /auth/v1/user` and binds only the locally invited member.
- `SCOPE_NORMIQ_AUTH_URL` is strictly validated (canonical HTTPS origin; only
  `http://127.0.0.1:<port>`/`http://localhost:<port>` for local development;
  no path/query/fragment/userinfo, no redirects, bounded bodies, timeout).
- Focused tests prove the Normiq payloads, the deny-without-invitation
  boundary, the immutable provider-UUID binding, URL validation and error
  redaction (`tests/test_scope_auth.py`).

## Operator checklist

1. Set `SCOPE_NORMIQ_AUTH_URL` to the canonical HTTPS origin of the Normiq
   deployment whose passcode API should send the login codes.
2. Copy Normiq's `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   into Scope's secret store as `SCOPE_SUPABASE_URL` / `SCOPE_SUPABASE_ANON_KEY`.
   No service-role key. These remain required for token validation and TOTP.
3. Set `SCOPE_AUTH_MODE=personal`, `SCOPE_OWNER_EMAIL` (existing Normiq user),
   `SCOPE_PUBLIC_URL`, Resend variables.
4. Confirm Normiq's passcode API accepts server-side calls from Scope and that
   its Resend code delivery and expiry fit the rollout; Scope's own 60-second
   request cooldown and five-attempt verification window apply locally.
5. Stage first: live login, invitation of an existing Normiq user, role change,
   revocation, logout, second factor with authorized test recipients.
6. Roll out production; invite members only after their Normiq accounts exist.

## Rollback

Set `SCOPE_AUTH_MODE=shared` and remove the personal variables; the shared
password gate applies again. Local data and invitations persist.

## Phase-2 prerequisite

Scope-only users require a product entitlement deciding who may exist in
Normiq's auth directory; until then invitations work only for existing Normiq
users.
