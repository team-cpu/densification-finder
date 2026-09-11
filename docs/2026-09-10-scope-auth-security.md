# Scope personal-auth security review — local only

Scope: opt-in personal login, membership binding, invitation notification,
revocation, role checks, logout, SQLite migration and outbox integration.

Verdict: **PASS for local code review**, not a live integration or deployment
approval. No unresolved Critical, High, Medium or Low findings identified in the
changed paths. Real provider configuration and end-to-end checks remain required
before personal mode is enabled for production.

Review evidence:
- Checked changed files and new modules for embedded credentials, token logging,
  unsafe SQL, eval/exec, shell execution and disabled TLS using diff review and
  targeted `rg`. No matching unsafe patterns in new auth/outbox code.
- Fixed a denial path found by tests: `st.stop()` is a no-op outside Streamlit;
  it now has an explicit exception fallback, tested before any database write.
- Rechecked local owner/editor roles under the domain write transaction lock to
  prevent stale permission decisions after a concurrent revocation or role change.
- Scope requires a provider-verified email and bound immutable user ID. Existing
  roster entries, public provider signup, editable profile metadata and shared
  password sessions do not independently authorize personal access.
- Pending invitations expire and can be revoked. Removing a member deletes the
  identity grant, so later actions using an old token are denied.
- Outbox emails contain no login credential; OTPs remain provider-managed. Tokens
  are not persisted; secret files are excluded from git and Docker build context.
- No billing, uploads or external tenant data were added. The app remains one
  Scope organisation with shared organisation data and personal permissions.

Tool limitations: `pip-audit` and `bandit` are not installed; the workspace's
three `.ai` security reference/template files are absent. Manual review and
targeted regression tests were used. Dependencies were not changed; this review
does not establish that every installed dependency is vulnerability-free.

Browser: Codex in-app browser, isolated `/private/tmp/scope-personal-auth-qa.sqlite`,
localhost:8520, placeholder provider configuration. Confirmed form rendering,
invalid-email error and rejection of an uninvited email/code. No real OTP,
invitation email, external user creation or production request was performed.

Before activation: use a dedicated Scope Supabase project, configure verified
Resend SMTP, short OTP expiry/provider rate limits, real first owner, then test
real login, invitation, revocation, roles and logout. See `scope-accounts.md`.
