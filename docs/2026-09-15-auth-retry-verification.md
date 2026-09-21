# Authentication retry fix — 15 September 2026

Local only; no commit, push or deployment.

Network/transport failures and provider 429/5xx responses now retain session state, block protected rendering and offer retry. Explicit session_expired responses clear local state with an expiry notice. Other invalid-session failures and denied local membership have separate notices. MFA transport failures are no longer labelled as wrong codes.

Kimi implemented the main change and tests. Astra reviewed the diff, hardened malformed provider-code parsing and corrected the transient-error docstring: retaining a token does not establish its validity.

## Verification

- Independent targeted suite: 79 passed, 7 subtests passed (auth, MFA, login layout, shared calculations, organisation).
- py_compile and git diff --check passed.
- Codex browser on isolated localhost:8523 with synthetic provider transport and temporary database: network failure retained token/draft and blocked protected content; retry during failure remained blocked; healthy response restored access without a new login. Explicit expiry displayed the expiry notice; subsequent healthy response still required login and showed token/draft cleared.
- Browser tests simulate provider responses, not real JWT expiration or a real outage. Existing authenticated user session was not intentionally logged out or modified.

## Focused security verdict: PASS

Reviewed bounded provider error parsing, output redaction, fail-closed rendering, cache clearing and local session cleanup. Raw provider messages are never displayed; only the allowlisted session_expired code establishes the expiry notice. bad_jwt remains generic invalid-session evidence. Malformed list/object/null error codes are covered by regressions. No new dependencies or secrets introduced. Targeted insecure-pattern scan found no eval/exec, shell=True, verify=False or pickle.loads in scope_auth.py.

Bandit/pip-audit and the security skill's referenced .ai checklist/report files were unavailable in prior inspection; code review and regression tests are the fallback. No claim of a full dependency security audit. Physical-phone inbox footer/tap verification remains separate and pending.

## Superseded on 2026-09-18

A live probe showed a plain access-token expiry is answered as `bad_jwt` with the wording `token is expired`, never as `session_expired`; the expiry notice was therefore unreachable. See `docs/2026-09-18-jwt-expiry-real-provider.md` for the evidence and the change.
