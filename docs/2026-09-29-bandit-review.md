# Bandit review — bounded local remediation (2026-09-29)

Executor: agent, under lead review (parent AGENTS user workflow). Local only:
no push, no commit, no deploy, no `.env` / endpoint / production-database
changes. The committed seed database (`results.sqlite`) was not touched; all
tests run against in-memory or disposable fixtures.

## Baseline (verified by lead before this pass)

Bandit 1.9.4, Python 3.11, production code only (`tests/` excluded):

- 15 medium: 10× B608, 4× B310, 1× B307
- 2 low: 1× B112, 1× B406

## Result after this pass

- `bandit -r . -x './tests'` → **No issues identified** (12 findings
  suppressed by per-line `# nosec` annotations, each justified below).
- `bandit -r . -x './tests' --ignore-nosec` → 12 raw findings: 10× B608,
  1× B307, 1× B406 — every one of them an annotated residual, none new.
- Fixed outright: 4× B310 (URL downloads), 1× B112 (silent `except: continue`).

Commands:

    /private/tmp/scope-verify-venv/bin/python -m bandit -r . -x './tests' -f txt
    /private/tmp/scope-verify-venv/bin/python -m bandit -r . -x './tests' --ignore-nosec -f txt
    /private/tmp/scope-verify-venv/bin/python -m pytest tests/ -q
    # 404 passed, 1 skipped (pre-existing), 56 subtests passed

No scanner-evasion techniques were used: no global skips, no config
downgrades, no string splitting to dodge the heuristic. The `# nosec` lines
below annotate genuinely safe code and each carries its reasoning inline.

## New helpers

### `sqlquote.py` — `ident(name)` (B608 remediation)

Correct SQLite identifier quoting: double-quote delimit, embedded quotes
doubled; rejects non-string, empty, and NUL. Deliberately **no** ASCII/regex
restriction — SQL identifiers can legitimately contain spaces, unicode, start
with digits, or contain double quotes.
The remediated external-identifier sites now route their identifiers (table names from
`gpkg_contents`, metric columns from `metrics.py`, workflow column names)
through `ident`.

Regression proof in `tests/test_bandit_helpers.py`, run against a real
in-memory SQLite:

- odd-but-legal names (`'my table'`, `'7tablé'`, `'quo"te'`, `'tabelle «ä»'`)
  create/insert/select cleanly;
- a malicious-looking name (`'x"; DROP TABLE safe; --'`) becomes a boring
  table name — the neighbouring table survives, no injection;
- the exact statement shapes used by `constraints.py` and the
  `parcel_results` INSERT used by `ingest.py` execute correctly.

### `http_fetch.py` — restricted HTTPS client (B310 remediation)

`get(url, timeout)` replaces the four raw `urllib.request.urlopen` call sites
(`ingest.fetch_parcels`, `land_cover.fetch`, `oereb.fetch`,
`regulations.fetch`). Policy:

- **Hosts:** exactly `geodienste.ch`, `api.geo.ag.ch`, `oereblex.ag.ch`
  (exact match — look-alikes and subdomains rejected). These are the three
  fixed cantonal endpoints already hardcoded at the call sites.
- **Scheme/port:** `https` on 443 only. Scheme-only checking was deemed
  insufficient per lead direction, so the full URL is validated: no
  credentials (`user:pass@`), no control characters, no backslashes, no
  explicit non-443 port.
- **Redirects:** a custom `HTTPRedirectHandler` validates the resolved target
  (`urljoin` first, so relative redirects are covered) **before** following,
  and refuses any redirect whose host differs from the origin host. No
  cross-host destination is configured as trusted. Upstream redirects were
  not live-tested; verify the endpoints before rollout, and explicitly review
  any legitimate cross-origin redirect rather than weakening the guard.
- **Proxies:** ambient proxies are disabled via `ProxyHandler({})`, which
  suppresses the `getproxies()`-populated default handler — an environment
  variable cannot reroute a request.
- **Timeout contract:** unchanged; the caller's `timeout` is the per-attempt
  socket timeout, as with the `urlopen` calls it replaces.
- **EGRID:** `oereb.fetch` now builds its query with
  `urllib.parse.urlencode({"EGRID": egrid})` — the EGRID arrives from parcel
  XML and stays one opaque query value instead of being interpolated.

Tests: policy accept/reject matrix (http, ftp, look-alike host, subdomain,
wrong port, credentials, control chars, backslashes, non-string), same-origin
redirect followed, cross-host and scheme-downgrade redirects refused, proxy
suppression verified, and `oereb.fetch` proving the EGRID is percent-encoded
and the timeout is passed through.

## Per-finding disposition

### B608 × 10 — annotated residuals

Bandit's B608 is a heuristic: any f-string containing SQL keywords is
flagged regardless of whether the interpolated parts are quoted. After the
real fix (all identifiers pass through `sqlquote.ident`), the heuristic still
fires, so each line carries a narrow `# nosec B608` naming the mechanism and
this document:

- `cascade.py:192` — zone SELECT; columns and table via `ident`.
- `constraints.py:74, 82, 103, 126` — register/planning/design-plan SELECTs;
  table via `ident`.
- `ingest.py:72` — municipality aggregation; table, BFS column, and metric
  columns via `ident`.
- `ingest.py:318` — workflow-table rebuild INSERT; carried column names via
  `ident`, values copied column-to-column (no literals).
- `potential.py:97`, `slice.py:153` — zone SELECTs; table via `ident`, `bfs`
  bound as a parameter.
- `workflow.py:181` — UPDATE SET list; fragments are fixed strings plus keys
  of the pre-validated field dict, and every value is bound as a `?`
  parameter.

Provenance and regression coverage: `sqlquote.py`,
`tests/test_bandit_helpers.py` (real in-memory SQLite, malicious-looking
identifier, odd legal names, exact statement shapes).

### B307 × 1 — annotated (`economics.py:310`)

`eval(rule.expr, {"__builtins__": {}}, values)` evaluates the fixed
arithmetic literals in `PATH` — frozen dataclass constants defined in this
file, never user input — with builtins emptied, so no name resolves to a
builtin. The formulas were not rewritten; the annotation is justified by
`EconomicsFormulaAuditTest`, which walks every PATH expression and asserts
each symbol is either a declared input or the output of an earlier rule.

### B406 × 1 — annotated (`report.py:18`)

`from xml.sax.saxutils import escape` is an output-side encoding helper for
reportlab PDF paragraph text. Nothing here parses XML; defusedxml has no
`escape` equivalent. False positive by construction.

### B310 × 4 — fixed

See `http_fetch.py` above. All four call sites now route through it.

### B112 × 1 — fixed (`ingest.recompute`)

The bare `except Exception: continue` that hid per-municipality cascade
failures now logs the municipality id (BFS) and the exception **class** only
to stderr, then continues. Message text is deliberately excluded: exception
strings can carry paths or extract content, and the log is not the place for
them.

## Residuals for the lead review

- 12 annotated findings (10 B608, 1 B307, 1 B406) — see dispositions above.
- `tests/` is out of scope per the bounded mandate; its pre-existing Bandit
  noise (B108 hardcoded `/tmp` paths in fixtures, B101 asserts, etc.) is
  unchanged.
- This is a static-analysis remediation pass, **not** a production release
  approval. No live endpoint was called; download paths were verified by
  policy tests and mocking only.

## Lead verification and release boundary

- Independent focused run: 72 passed, 47 subtests passed.
- Independent full-suite rerun: 404 passed, one skipped, 56 subtests passed
  in 78.24 seconds. Final comment-only cleanup was rescanned successfully.
- Independent check of the actual HTTP get helper (mocked transport): rejects
  untrusted origins before creating an opener, installs same-origin redirect
  handling, passes an empty proxy configuration, and preserves timeout/body.
- Installed Python dependency audit: no known vulnerabilities.
- Scanner has 12 explicit reviewed exceptions; this is not a zero-raw-findings
  claim. Keep regression coverage when changing identifiers, formulas or URLs.
- Overall release verdict: **BLOCK**, independently of the local scanner
  result, due to the production RLS findings in
  `2026-09-29-production-rls-inspection.md`.
- Engineering follow-up: confirm live endpoint compatibility and resolve or
  explicitly classify the production access findings before rollout; target
  2026-10-02 or before rollout, whichever is earlier. No production approval
  or modification is implied.
- Security skill reference files under the parent `.ai/` directory were
  absent; this report follows the available skill's required scope/evidence/
  verdict structure instead.
