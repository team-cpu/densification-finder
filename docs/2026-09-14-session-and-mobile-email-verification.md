# Session and physical-phone email verification — 14 September 2026

## Second attempt — authenticated baseline

### Follow-up result

On 2026-09-14, the scheduled follow-up arrived at 09:52:56 UTC; the UI check completed by 09:59:57 UTC (16:59:57 WIB), about 80 minutes after the baseline. The SAME providerTabId was present and initially displayed Screening and kris. Clicking Merkliste triggered the authentication gate, which replaced the protected UI with Anmelden and: `Sitzung abgelaufen oder Zugang nicht mehr gültig. Bitte erneut anmelden.` No reload, logout, token changes, server restart or provider configuration changes were performed during this check.

Observed result: the previous session was rejected on navigation after the wait and reauthentication was required. This verifies the visible session-rejection behavior, but does not isolate JWT expiry as the provider's exact reason: the application combines expiration and other invalid-access errors into this message. Exact JWT-expiry diagnosis remains unverified. The heartbeat was paused after this single follow-up. This documentation update remains local; no push or production change was made for the check.

At 2026-09-14 08:39:48 UTC (15:39:48 WIB), Codex browser 1 tab 3, providerTabId `browser-use:185f95d1-7579-419c-bf6d-34ec81a4ebb7`, visibly showed Screening and the kris account at localhost:8521. The local server had been started before this fresh login (the earlier server was unavailable). No reload or token change was made after confirming this baseline. Tab marked for handoff to preserve it. Follow-up reactivated for no earlier than 09:45 UTC (16:45 WIB); JWT expiry remains pending, not passed.

## Physical-phone inbox evidence

User supplied IMG_1484.PNG (reminder) and IMG_1483.PNG (digest), captured on a phone through webmail.normiq.ai. Both show the delivered TEST HTML email with Scope branding, readable body text, wrapping within the visible card, and the Board öffnen button. No visible horizontal clipping of the card/content in the supplied captures.

This confirms the visible email sections in mobile webmail. It is not a native Apple Mail/Gmail app test. The footer is below the captured region; link activation and footer visibility have not been established from these images.

## Real session test — inconclusive (original tab unavailable)

- Earlier local server was stopped; it was restarted on port 8521 before the user logged in. That old disconnected state is not JWT-expiry evidence.
- User confirmed a new login. At 2026-09-14 03:48 UTC (10:48 WIB), the existing Codex browser tab at localhost:8521 visibly showed Screening and the account kris.
- Preserve that original tab and server session. No reload, token edits, provider lifetime changes or forced logout.
- Follow-up scheduled after 65 minutes, approximately 11:53 WIB. Trigger ordinary navigation to cause the server-side auth gate to run and inspect the result.
- The current gate combines expiry and other invalid-access failures into one message. A generic login rejection alone does not prove the provider's exact failure reason; retain that limitation in the result.

No production changes or push.

At the follow-up on 2026-09-14 at approximately 07:05 UTC (14:05 WIB), more than 65 minutes after the baseline, the Codex browser inventory returned an empty tab list. The original tab/providerTabId was unavailable, so Merkliste navigation could not be triggered in the original session. No replacement tab was opened and no session/token/server changes were made. JWT expiration remains unverified; a missing tab is not evidence of token expiration. The follow-up automation was paused after this attempt. A new live test would require a fresh authenticated baseline and preserving that tab through the wait.

## Follow-up: footer and CTA browser QA

Kimi generated synthetic reminder/digest fixtures from the current `notification_html` template in `/tmp/scope-mobile-email-qa`. Astra independently inspected them in the Codex in-app browser. These are regenerated local fixtures, not the original delivered email MIME payloads.

- Inspected both footers visually in 320 px and 390 px iframe previews. Footer text wrapped within the card and remained fully readable; no visible clipping. These are constrained-width browser previews, not physical-device tests.
- Clicked `Board öffnen` in both 320 px previews. Each navigated to `https://densification-finder-production.up.railway.app/` and visibly rendered Scope's `Anmelden` page with `Gemeinsamer Zugang mit Passwort.` No login or production mutation was performed.
- The CTA currently leads to the application entry/login page; this does not establish direct authenticated board navigation.
- Physical-phone inbox footer rendering and tapping the delivered message's button remain unverified. Existing phone screenshots do not include those actions/regions. Completing that check requires the user's phone or new evidence from it.

No application code changed, no email resent, and no push. Synthetic fixture counts are layout data only, not production board evidence.

## Follow-up 2026-09-18

Exact JWT-expiry diagnosis completed against the live provider: token lifetime 3600 s, expiry answered as `403 bad_jwt` ("token is expired"). See `docs/2026-09-18-jwt-expiry-real-provider.md`.
