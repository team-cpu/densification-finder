# Scope auth and scheduled email fixes — 12 September 2026

Local changes only; no push or production deployment.

## Changes

- Owner and write guards now require the second factor when organisation policy requires it or the account has an enrolled factor. Database transactions recheck the policy under the write lock, covering callbacks that run before the page gate.
- Scheduled mail requires an active member with a bound Scope access grant. Recipient access is checked again before delivery, covering revocation after planning.

## Verification

- Full Python suite: `321 passed, 1 skipped, 34 subtests passed` (`.venv/bin/python -m pytest -q --tb=short`).

- Targeted MFA and scheduler tests: 23 passed, including policy enforcement and revocation after planning.
- Real scheduler smoke test with an isolated SQLite database and synthetic data: Resend accepted one reminder and one weekly digest for kris@normiq.ai. Both subjects begin with `[TEST]`.
- Repeating the same scheduler run produced no duplicate sends. Provider acceptance is not confirmation of inbox delivery.
- Local outbox evidence: `/tmp/scope-verification/email.sqlite` (temporary, not committed).

## Focused security review: PASS

Scope: changed auth guards and scheduled recipient selection; adjacent mail transport and durable outbox. Reviewed the diff and parameterized SQL, second-factor state, access checks, retry idempotency, and safe transport errors. No unresolved high or critical findings in this fix scope; no new secrets or dependencies introduced.

`git diff --check` passed. Targeted `rg` search for eval/exec, shell execution, disabled TLS verification, and unsafe pickle loading returned no matches in the reviewed modules. Bandit and pip-audit were unavailable; manual review and regression tests were used. This is not a full dependency or application security certification. The security skill's referenced `.ai` report/checklist files are absent.

Delivery authorization is checked immediately before the provider call; a revocation concurrent with an already-started external send cannot recall that email.
