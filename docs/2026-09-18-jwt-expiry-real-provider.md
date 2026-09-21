# Real JWT expiry against the live provider — 18 September 2026

Local only; no push, no deployment, no provider configuration change.

Closes the open point from 2026-09-14/15: the session rejection seen ~80 minutes
after login was never attributed to a provider reason, and the expiry notice had
only been exercised with a simulated `session_expired` body.

## Method

A real session for the owner account (`kris@normiq.ai`) was minted through the
app's own login path, without the UI: `POST /auth/v1/otp` (code relayed by the
user) then `POST /auth/v1/verify` with `type: email`. The same headers as
`scope_auth.api` were sent; unlike the app, the probe kept the raw HTTP status
and body. `GET /auth/v1/user` was then called at fixed offsets from the JWT's
`exp` claim by a detached watcher. Tokens were written to a 0600 file only,
redacted from all output and discarded afterwards (the refresh token was never
used).

## Result

| UTC | Probe | Answer |
|---|---|---|
| 05:39:09 | verify | `expires_in` 3600; JWT `exp − iat` = 3600 s; `aal1`, `amr: otp`, `session_id` present |
| 05:39:17 | GET /user | 200 |
| 06:38:11 | exp − 60 s | 200 |
| 06:39:25 | exp + 15 s | **403** `error_code: bad_jwt` |
| 06:40:25 | exp + 75 s | **403** `error_code: bad_jwt` |

Verbatim body at both post-expiry probes:

```json
{"code":403,"error_code":"bad_jwt","msg":"invalid JWT: unable to parse or verify signature, token has invalid claims: token is expired"}
```

Facts established:

- The access token lives exactly 3600 s. The 80-minute rejection on 2026-09-14
  is explained: the token had expired at 60 minutes.
- A plain access-token expiry is answered as `bad_jwt`, **not** `session_expired`.
  `session_expired` is the refresh-token-grant code; the app never refreshes
  (`token` is not in `_ENDPOINTS`), so that code cannot occur here.
- Fed through `scope_auth._http_error`, the real body classified as generic
  `AuthError` → `gate()` called `logout()` (a provider round-trip that fails on a
  dead token) and showed "Sitzung ist nicht mehr gültig". The
  `AuthSessionExpired` path ("Sitzung abgelaufen", local clear only) was
  unreachable in practice, and the unit test modelling expiry as
  `session_expired` pinned the wrong assumption.

## Change

`scope_auth._provider_expired` now treats as provider evidence of expiry either
an allowlisted code (`session_expired`) or `bad_jwt` whose `msg` contains
`token is expired`. The wording is matched, never surfaced; it is produced by
the provider's JWT library, not echoed from the request. `bad_jwt` with any
other wording (tampered or foreign token) and expiry wording under any other
code stay generic invalid-session errors.

Tests (`tests/test_scope_auth.py`): the verbatim body above is pinned as
`REAL_EXPIRED_JWT_BODY` and must classify as `AuthSessionExpired`; negatives
cover `bad_jwt` with signature wording, expiry wording without `error_code`,
and expiry wording under `session_not_found`. Test-first: the fixture test
failed with the generic `AuthError` before the change. Mutation check: replacing
the wording constant fails the fixture test; dropping the `bad_jwt` code check
fails the negatives.

## Verification

- `tests/test_scope_auth.py`: 30 passed. Full suite: 348 passed, 1 skipped,
  34 subtests passed.
- The two real captured bodies re-classified with the changed code:
  `AuthSessionExpired: Sitzung abgelaufen. Bitte erneut anmelden.`
- `py_compile` and `git diff --check` clean.

## Browser replay of the real body — 19 September 2026

A first wall-clock attempt on 2026-09-18 (login 14:12 UTC, check planned
15:14 UTC) was lost: the desktop app stops the preview server and its tab as
soon as the session has no running task, which happened at 15:12:20 UTC in the
gap between a monitor expiring and the next turn. Same failure shape as the
2026-09-14 attempt.

Replay instead (`~/.cache/normiq/replay/app_replay.py`, port 8523): the
mirrored app runs unchanged with only `scope_auth.build_opener` replaced by a
stub. `GET /user` answers an owner record until a flag file exists, then raises
`HTTPError` 403 with the verbatim body above; every provider call is appended
to `calls.log`. `urllib` error handling in `scope_auth.api`, the gate, session
clearing and rendering are the real code.

- Login through the app's form (stub `otp`/`verify`): Screening and the `kris`
  header rendered.
- Flag set, `Merkliste` clicked: one `GET user` answered with the real 403 body;
  the protected UI was replaced by the login card with the notice
  `Sitzung abgelaufen. Bitte erneut anmelden.`
- `calls.log`: `otp, verify, user, user, user` — no `logout` call, so the
  `AuthSessionExpired` branch (`_clear_local_session`) ran, not `logout()`.
- Cosmetic only: the harness runs from its own directory, so the app's
  `static/` assets (logo, stylesheet) are not served; unrelated to auth.

## Wall-clock browser run — 19 September 2026

Second attempt, with the session kept busy by overlapping background monitors
(a new one armed at minute 25 of the previous 30-minute one) so the desktop app
never tore the preview down.

- Local app from the mirror on port 8502, real provider, real e-mail code
  entered by the user in the app's own form. Baseline 11:11:14 UTC: Screening
  and the `kris` header rendered. No reload, logout, token or configuration
  change afterwards; the tab stayed open.
- 12:13:30 UTC (62 minutes later, 2 minutes past the token's 3600 s lifetime):
  `Merkliste` clicked. The protected UI was replaced by the login card with
  `Sitzung abgelaufen. Bitte erneut anmelden.`

That closes the chain end to end: real token ageing past `exp` in a live app
session → provider `403 bad_jwt` → `AuthSessionExpired` → local clear and the
expiry notice on screen.

Physical-phone inbox footer/tap check (2026-09-14) is still pending and
unrelated to this change.
