# Auth review follow-up — 10 September 2026

Local only. No commit, push, deployment or production change.

## Finding: per-render provider traffic

`scope_auth.current()` verified the session token against the Scope Supabase
project on every call, and the render path called it repeatedly. Measured with a
Streamlit `AppTest` that renders the shell header plus an open team dialog:

- before: **5** provider calls for one rerun,
- after: **1**.

Each call is an HTTPS round trip with a 15-second timeout, and Streamlit reruns
the whole script on every interaction, so the previous behaviour added several
round trips per click and could fail a page render whenever the provider was slow
or rate-limiting, even though the session was still valid.

Root cause was the absence of any run-scoped memo, not a logic error:
`organisation.load_members()` called `current()`, `account_summary()` called both
`load_members()` and `current()` again, and `organisation.render()` reached
`require_owner()` followed by two more `load_members()` calls.

## Fix

`scope_auth` now keeps `_verified_identities`, keyed by access token and cleared by
`gate()` at the start of every script run, so a snapshot never outlives the run
that created it. `logout()` drops its entry explicitly.

Security boundary unchanged and re-verified by a new test: the memo covers
identity only, roles are still read from local SQLite, and every write still
re-checks role and membership through `check_transaction` under the write's own
transaction lock. A revocation that lands mid-run is still rejected by the next
write, and the next rerun re-verifies from scratch.

## Validation

- `.venv/bin/python -m pytest -q`: 289 passed, 1 skipped, 34 subtests passed.
- `.venv/bin/python -m unittest discover -s tests -q`: 258 ran, OK, 1 skipped.
- `node --test tests/calculation_table.test.cjs`: 12 passed.
- `git diff --check`: clean.
- Re-measured render path after the fix: 1 provider call.

## Tooling that earlier notes recorded as unavailable

Run on this change, resolving the "scanners not installed" limitation for the
`requirements.txt` set. Results apply to the pinned dependencies, not to a full
transitive lock.

- `uvx pip-audit --local` (against `.venv`): **No known vulnerabilities found.**
- `uvx bandit -r .` (excluding `.venv`, `tests`, `data`): 0 High. The 15 Medium
  and 2 Low findings are all pre-existing; the two B608 SQL findings in
  `workflow.py` and `ingest.py` were confirmed present at `HEAD` with only shifted
  line numbers. **No new finding in the added auth or outbox code**, and
  `scope_auth.py`, `email_outbox.py` and `email_delivery.py` reported nothing.

`pip-audit -r requirements.txt` cannot run on this machine: it builds a temporary
virtualenv and `ensurepip` dies with SIGABRT. The `--local` form against the
project `.venv` was used instead.

## Not covered

Live login, real invitation delivery, revocation against a running deployment and
the dedicated Scope Supabase project remain untested. Tool limits described in the
other 10 September notes still apply to anything they did not cover.
