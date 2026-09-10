# Resend transport — local implementation

Scope accepts the same configuration names as Normiq: `RESEND_API_KEY`,
`RESEND_FROM_EMAIL` (fallback `EMAIL_FROM`), and optional `RESEND_FROM_NAME`.
Inject these through the runtime's secret configuration. Credentials are not
copied from the sibling application or stored in this repository. A sender is
required; there is no production fallback to Resend's test domain.

`email_delivery.send_email` sends plain-text messages to one validated recipient
over HTTPS, rejects redirects, uses a bounded timeout, and redacts provider errors.
The caller must authorize recipients and persist the exact payload plus a stable
idempotency key before sending. Provider acceptance is not proof of delivery.

There is no automatic retry. Resend deduplicates matching requests for 24 hours;
an uncertain older request needs reconciliation before resending.
References: https://resend.com/docs/api-reference/emails/send-email and
https://resend.com/docs/dashboard/emails/idempotency-keys

## Current boundaries

This transport is not yet connected to invitation UI or a reminder scheduler.
No actual email has been sent. Personal identity architecture is awaiting the
choice between separate Scope accounts and explicitly invited Normiq identities.
Existing shared-password access remains in place. Roles and 2FA remain unavailable.
No deployment or push performed.

## Verification

`tests/test_email_delivery.py`: 9 tests passed using mocked HTTP. Covers config,
secret redaction, request serialization, idempotency header, no automatic retries,
invalid inputs, and redirect rejection. Live sender/domain/delivery not tested.

Targeted security review of this transport: PASS for this limited local scope;
not an authentication or release approval. No new dependency, embedded secret,
dynamic destination, or logging of credentials. Required security-report reference
files and optional Python scanners were unavailable during the earlier audit;
manual review and focused tests used. Authorization, durable outbox, invitation
token lifecycle and scheduler must be reviewed when implemented.
