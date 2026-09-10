# Release check — 10 September 2026

Scope: phone presentation/export, absolute-potential ranking, unavailable settings,
and the isolated Resend transport. Earlier local-verification notes describe their
historical state; this release was subsequently authorized by the user.

Validation:
- `.venv/bin/python -m pytest -q`: 266 passed, 1 skipped, 34 subtests passed.
- `node --test tests/calculation_table.test.cjs`: 12 passed.
- `git diff --check`: passed.
- Reviewed the complete production-code diff and new transport/tests.

Security verdict: PASS for the changed scope. No Critical, High, Medium or Low
findings identified in this targeted review. Resend uses a fixed HTTPS endpoint,
rejects redirects, redacts provider failures, does not log secrets and requires an
idempotency key. No dependency, schema, access-control or authentication changes.
Invitation tokens, uploads, billing and tenant isolation were not changed.

Limitations: `pip-audit` and `bandit` are not installed. Required `.ai` security
reference/template files are absent. Used manual diff/secret review and targeted
`rg` checks for eval/exec, shell execution, disabled TLS, unsafe deserialization
and logging instead. This is not a comprehensive dependency vulnerability audit.

Resend is not wired to the UI or scheduler and sends no email on deployment.
Live email delivery and personal-account login are not part of this release.
No credentials or local audit screenshots/CSV files are included.
