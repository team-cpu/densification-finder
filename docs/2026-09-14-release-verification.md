# Release verification — 14 September 2026

User authorized pushing the latest Scope updates while retaining production's shared-password login.

## Verified before push

- Railway Scope project/service explicitly selected by ID; existing deployment source is `team-cpu/densification-finder`, branch `master`.
- Production `SCOPE_AUTH_MODE` is unset, so the application uses `shared`; password configuration is present. No variables changed.
- Full Python suite: **337 passed, 1 skipped, 34 subtests passed**, 81.14 seconds.
- JavaScript: `node --test tests/calculation_table.test.cjs`, **12 passed**.
- `git diff --check` passed.

## Focused security verdict: PASS

Reviewed MFA write guards, grant checks, snapshot validation and revision conflicts, HTML escaping, HTTPS mail links, recipient rechecks and durable outbox idempotency. No unresolved high/critical findings identified in this release scope. Secret-pattern scan of 30 changed/new files found no potential credential matches. Targeted scan of affected authentication/mail/snapshot modules found no eval/exec, shell=True, disabled TLS verification or pickle.loads patterns. Dependencies are unchanged.

Bandit and pip-audit are not installed; manual code review, targeted pattern scans and regression tests are the fallback. The security skill's three referenced `.ai` checklist/report files are absent. This is not a full dependency vulnerability certification. Billing, uploads and licensed-document isolation are outside this Scope change.

## Limits retained

- The real JWT expiry check is pending and must preserve its local tab/server.
- Physical-phone footer/tap verification remains pending; constrained-width browser preview and CTA navigation passed.
- Production shared mode keeps personal-account features disabled, including MFA, scheduled mail and shared calculations. This release does not activate personal mode.
- Existing historical verification documents describe their state at the recorded time, including previous no-push restrictions; this release has new explicit push authorization.
