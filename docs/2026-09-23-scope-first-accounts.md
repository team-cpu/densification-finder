# 2026-09-23 — Scope-first accounts: staging run

Phase 2 of `docs/scope-accounts.md`: an invited member without a Normiq account
gets a Normiq identity with no Normiq product access. Normiq side: branch
`feat/scope-first-accounts` in `normiq-frontend` (route
`POST /api/auth/scope/provision`); decision record
`docs/decisions/0022-scope-first-accounts.md` in `normiq-frontend`. At the time
of this staging run, nothing had been pushed or deployed.

## Setup

- Normiq: the `feat/scope-first-accounts` worktree running locally
  (`next dev`, port 5199) against the **staging** Supabase project
  `xtnyxeulkkzitqwmchyq`, with `SCOPE_PROVISIONING_SECRET` set to a throwaway
  value and `NEXT_PUBLIC_CAPABILITY_GROUPS_ENFORCED` unset (the stricter case:
  a user without a claim would fall back to everything in the app layer).
- Resend stood in for: Normiq's SDK honours `RESEND_BASE_URL`, so passcode mail
  went to a local capture server and the six-digit code was read from the
  captured HTML. No mail left the machine.
- Scope: this branch's `scope_auth`/`organisation` code in personal mode,
  `SCOPE_NORMIQ_AUTH_URL=http://localhost:5199`, the staging anon key, a copy of
  `results.sqlite`. Test addresses `delivered+scope-e2e-<run>-<role>@resend.dev`.

## Results (run `0923c`): 31/31

| Area | Check | Result |
|---|---|---|
| Route | reachable past the middleware only with the secret (bad email → 400); wrong secret → 401 and no identity created | pass |
| Normiq | self-sign-up of a regular Normiq customer still works | pass |
| Scope-first owner | first code request creates the identity; login binds it; `app_metadata` = `project_capability_groups: []`, `signup_source: scope`; email confirmed by the passcode | pass |
| Scope-first member | invited → first code → login; same zero-access claim | pass |
| Existing customer | invited to Scope, signs in; Normiq `app_metadata` identical before/after; still signs in to Normiq | pass |
| Normiq RLS | `has_project_capability` false for all four capabilities for both Scope-first accounts; true for meetings for the customer; PostgREST `meetings` empty for the member | pass |
| Never provisioned | expired invitation, revoked invitation, uninvited address: no code mailed, Normiq still answers "unknown user" | pass |
| Roles | Bearbeiter writes; Leseweise refused; role change effective on next write | pass |
| Codes | superseded code refused with the generic message; current code accepted | pass |
| Removal | removed member's live session → `AuthAccessRevoked`; no new code | pass |
| Expiry | unused code refused after 620 s with the generic message | pass |

Browser (in-app Chromium): a fresh invitee signed in through the Scope login
card (Code anfordern → code → Anmelden) and landed on Screening with their name
in the header. The same account signed in to Normiq web landed on Sitzungen with
"Kein Zugriff auf diesen Bereich"; the sidebar showed only Team-Einstellungen
and Feedback; no onboarding or billing redirect.

## Second run (`0923d`): with Normiq's session fix: 31/31

Same driver against the Scope-first branch with `fix/passcode-login-keeps-sessions`
overlaid on the local Normiq server. All 31 checks passed; the one that recorded
"a Normiq login ends the Scope session" now records that the Scope session
survives. A separate run of that fix on its own: two Normiq logins, a Scope
login and a Normiq login in both orders, remember-me — every earlier session
still answered 200 (9/9).

## Found

- **A Normiq login ended the person's Scope session (and vice versa).**
  Normiq's `verify-passcode` rotated the account password on every login and
  the provider then dropped all other sessions (`403 session_not_found` on the
  older token, reproduced with two Normiq logins and no Scope involved). Scope
  handles it safely — the next interaction shows "Sitzung ist nicht mehr gültig.
  Bitte erneut anmelden." Fixed on the Normiq side in
  `fix/passcode-login-keeps-sessions`, which must ship before shared accounts
  go live.
- Normiq's Projekte page is reachable by URL for a zero-access account and
  offers "create project" (also true for Group A); documents, chat and meetings
  stay closed.

Not covered: a real inbox and phone rendering, the deployed staging build,
production, TOTP for a Scope-first account.
