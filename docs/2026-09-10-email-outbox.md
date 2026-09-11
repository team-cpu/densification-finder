# Local email outbox

Added a durable SQLite outbox, with additive `CREATE TABLE IF NOT EXISTS` during
the existing schema bootstrap. Existing tables/data remain unchanged. Older app
versions can ignore the new table; do not drop it during rollback because it
contains deduplication state.

Enqueue joins its caller's transaction. One event key maps to an immutable
recipient/sender/subject/body. Workers claim messages with a 60-second lease,
send outside the transaction and persist provider acceptance separately from
delivery. Retries reuse the same Resend key and payload, including the original
sender. Uncertain attempts older than 23 hours require review rather than risking
a resend beyond Resend's 24-hour idempotency window.

No UI integration, scheduler or network request runs automatically. No real email
was sent. Invitation issuance still needs authenticated owner authorization and
an identity-provider decision. Do not queue access tokens until their storage,
expiration, revocation and recipient binding have been implemented and reviewed.
The outbox contains personal message content and belongs in the protected app
database; never include it in logs or exports. Keep retries on the same Resend
account; changing accounts requires reconciling outstanding attempts first.

Targeted security verdict: PASS for inactive outbox scope. Parameterized SQL,
transactional claim, stable retry identity, no secret logging, no new dependencies.
Manual code review and targeted pattern search used; optional security scanners
and the workspace security-report templates remain unavailable. This verdict
does not approve future login or invitation authorization code.

Local changes only; no commit, push or production configuration changes.
