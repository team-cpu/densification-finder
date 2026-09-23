# Shared Normiq Auth preparation — security scan

Date: 2026-09-22

Updated 2026-09-22 (second pass): login moved from the shared Supabase OTP
endpoint to Normiq's custom passcode API (`/api/auth/request-passcode`,
`/api/auth/verify-passcode`), because the shared project's Magic Link template
emails a link to localhost:3000 rather than a six-digit code and must not be
changed for Scope (it would affect Normiq flows).

## Scope

Reviewed the phase-1 change in `.env.example`, `DEPLOY.md`, `README.md`,
`docs/scope-accounts.md`, `docs/2026-09-22-shared-auth-rollout.md`,
`scope_auth.py`, and `tests/test_scope_auth.py`. The high-risk area is
authentication and cross-product access isolation, now including a new
server-side client of Normiq's custom passcode API and strict validation of the
`SCOPE_NORMIQ_AUTH_URL` origin (SSRF surface: only that validated origin is
called, with no redirects, a 15-second timeout and 64 KB bounded bodies).
Billing, webhooks, uploads, dependencies, and database schemas did not change.

## Checks

- Full test suite: `379 passed, 1 skipped, 34 subtests passed`.
- `git diff --check`: passed.
- Targeted secret scan over changed files: no credentials or private keys.
- Targeted insecure-pattern scan: no `eval`, `exec`, `shell=True`, disabled TLS
  verification, or unsafe deserialization in the changed scope.
- `bandit` and `pip-audit` were unavailable in the project environment. The
  fallback review covered the changed Python auth path; dependency files were
  unchanged.
- The strict security skill's repository-specific agent, checklist, and report
  template paths under `.ai/` were absent, so this report follows the available
  skill rules directly.

## Findings

No Critical, High, Medium, or Low findings remain in the changed scope.

The key cross-product risks are explicitly mitigated: Scope requests passcodes
from Normiq's own API with `isSignup: false`, so it cannot silently add an
identity to Normiq's shared Auth directory, and unknown users get one generic
error (no enumeration oracle). Normiq API response bodies and tokens are never
surfaced in errors or logs; 429/5xx/network faults are classified as transient
and 4xx verify failures as one generic invalid/expired-code error. A valid
provider identity still needs a local `scope_access` grant, and its provider
UUID is bound immutably on first login. Scope receives only the
publishable/anon key, never the service-role key, and the unused Supabase `otp`
endpoint was removed from the endpoint allowlist.

## App-specific review

- Invitation/access isolation: PASS. Tests cover denial without a local grant,
  successful binding after invitation, and rejection of a different provider
  UUID.
- Tenant/workspace isolation: PASS for the documented single Scope organisation.
  This change does not claim multitenancy.
- Secrets: PASS. Configuration is documented as runtime-only and no values were
  added to the repository.
- Billing, webhook signatures, uploads, and licensed-document access: not
  affected by this change.

## Verdict

**PASS** for code and configuration preparation. Production activation remains
conditional on staging verification against the real shared Supabase project,
including passcode delivery, invitation, role enforcement, revocation, logout,
and second factor.
