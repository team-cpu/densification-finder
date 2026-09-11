# Separate Scope accounts — local implementation

User decision: Scope accounts are separate from Normiq. Use a **new, dedicated
Supabase project** for Scope Auth. Do not point `SCOPE_SUPABASE_URL` at Normiq.
The application never reads Normiq's Supabase configuration or user table.
Resend can use the existing Normiq Resend account and verified sending domain.

## Runtime configuration

`.env.example` documents the process environment names; the app does not load
dotenv files. Supply credentials through the deployment/runtime secret store.
No service-role key is needed by Scope.

- `SCOPE_AUTH_MODE=personal` activates personal login. Default `shared` preserves
  current production behavior; invalid modes or missing personal configuration
  stop the application, without falling back to the shared password.
- `SCOPE_SUPABASE_URL`: dedicated project's `https://<ref>.supabase.co` URL.
- `SCOPE_SUPABASE_ANON_KEY`: matching publishable/anon key.
- `SCOPE_OWNER_EMAIL`: first owner's verified email, granted only on initial setup.
  Existing roster entries are not automatically granted personal access. Changing
  this variable after setup does not promote another person. The initial invitation
  lasts seven days; an operator must renew an expired unclaimed bootstrap grant.
- `SCOPE_PUBLIC_URL`: canonical HTTPS Scope address, without query or fragment.
- `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, optional `RESEND_FROM_NAME`: existing
  Resend configuration, used for invitation notification emails.

Configure the **Scope project's** custom SMTP with Resend: host `smtp.resend.com`,
port `465`, username `resend`, password the existing Resend API key, and verified
sender address. Set both confirmation and magic-link templates to display
`{{ .Token }}`. Configure six-digit email OTP, a short expiry (10 minutes), sender
rate limits and the Scope Site URL before live testing.

Email OTP may create a provider user. That alone gives no access to Scope: only
members with an explicit local `scope_access` grant can enter the application.
All data remains in the one Scope organisation; this is not a multitenant system.

## Behavior and security

1. The configured first owner requests an email code and verifies it with Supabase.
2. A successful `/user` response with verified email binds the immutable provider
   user ID to the local membership. JWT payloads and editable user metadata are
   never trusted for roles. Later ID mismatches or removed memberships are rejected.
3. Owners can invite, revoke pending invitations, change other members' roles and
   remove members. Editors can persist lead/search changes; readers cannot.
   Domain-write guards run in callbacks too, before the main page gate reruns.
   Lead/search/team writes recheck local permissions under their transaction lock.
4. Invite notification emails carry only the Scope URL, not an access token. The
   recipient must verify their mailbox with a separate login code. Notifications
   use the persistent outbox and distinguish provider acceptance from delivery.
5. Code requests have a 60-second per-member cooldown; verification allows five
   attempts per five-minute window. Supabase's own expiry/rate controls also apply.
6. Access tokens live only in server-side Streamlit session state; no token in
   URL, database, logs, or browser storage. No refresh token is retained. Expiration,
   reload/session loss or logout requires login again. Logout clears all session
   drafts even when provider logout fails. An already displayed page can remain
   visible until the next interaction; revocation is checked on subsequent actions.

Identity is verified against the provider at most once per script run: Streamlit
reruns the whole script on every interaction, and the header, the member list and
each write guard all need the same identity, so an uncached `current()` cost
several provider round trips per rerun (measured: five for the header plus an open
team dialog). The memo lives in the `scope_auth` module and `gate()` clears it at
the start of every run, so one run's snapshot can never authorize the next.
This memo covers identity only. It does not re-read local roles, so a member
revoked mid-run still sees the page already open, exactly as described above;
every write still calls `check_transaction` under its own lock.

Migration adds `scope_access`, `scope_auth_setup`, request counters and the email
outbox without replacing existing data. Keep them on the protected volume. Do not
reseed a configured database. Switching to shared mode removes personal role
enforcement, so it is an operator security decision, not an automatic rollback.

## Verification and remaining work

Tests use isolated SQLite databases and mocked provider responses. Local browser
checks exercise login rendering, malformed email and uninvited-code rejection;
they do not prove real email delivery or a live Supabase login. No external account,
SMTP configuration, production change or push was performed.

Still needed before activating personal mode: provision/configure the dedicated
Scope project, configure Resend SMTP, set the real owner, then verify live login,
invitation, role change, revocation and logout using authorized test recipients.
2FA and scheduled reminder/digest delivery are separate unfinished features.

Official references:
- https://supabase.com/docs/guides/auth/auth-email-passwordless
- https://supabase.com/docs/guides/auth/auth-smtp
- https://resend.com/docs/send-with-supabase-smtp
