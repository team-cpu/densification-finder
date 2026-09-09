# Feedback 29.08.2026 verification

Verified against the user's annotated screenshot and local browser sessions on
7–8 September 2026. Implementation is in commit `d11b5f0`.
Tests used a separate SQLite copy, not production data.

Final verification: 116 tests passed in 48.27 seconds across acquisition, app,
detail, Merkliste and UI component tests. `git diff --check` passed.

| Feedback | Observed result |
| --- | --- |
| Municipality selection | Aarau selection filters results; reset restores Alle Gemeinden and results from other municipalities. |
| Name the source ÖREB | Desktop table shows ÖREB with the cadastre PDF URL. |
| Remove from saved list | Three-dot menu removes the parcel; saving it again retains contact status, date and notes. |
| Add parcel number | Merkliste shows Parz.-Nr., including test parcel 954. |
| Relevant regulatory changes | Möhlin analysis shows its own BNO; other municipalities appear only in the optional canton-wide expansion. |
| Phone formatting | 0787778800 becomes 078 777 88 00. |
| Email display | Email is readable without overlapping Streamlit instructions and remains visible after reopening and restarting the app. |
| Board-populated contact dates | Drag to Im Gespräch recorded 07.09.2026 and follow-up 21.09.2026; Abgelehnt cleared follow-up. |
| Remove Wiedervorlage input | Absent from owner dialog. |
| Rename/move next step | Status is beside Letzter Kontakt. |
| Full-width expanding notes | Six lines increased textarea height to approximately 127 px; dialog accommodated the content. |
| Postal address | Three-line address block persisted. |
| Remove overdue strip | Fällige Wiedervorlagen strip absent from board. |
| White owner button below Analyse | Present; opens the corresponding parcel's owner dialog. |

An initial automated DOM reading reported the email as empty despite the
rendered screenshot showing it. Database and application checks confirmed the
saved value. A speculative refresh patch was removed; the final unmodified
implementation was visually verified again. No email persistence defect was
confirmed.

Scope: local implementation and tested interactions. This is not confirmation
of the deployed production version, exhaustive browser compatibility, or a
new canton-wide legal-change ingestion system. The regulatory feed lists
enacted OEREBlex regulations, not pending consultation/revision proceedings.

## Mobile, CSV and browser follow-up

On 8 September, Playwright exercised the local app on port 8504 using the
separate test database. No production records or dependencies were modified.

| Environment | Result |
| --- | --- |
| Chrome 152, 390 × 844 | Municipality select/reset, mobile Merkliste, owner dialog edits/reopen, CSV download and opening Analyse passed; no page errors. |
| WebKit 26.6, 390 × 844 | Same flows passed; no page errors. This is Playwright WebKit, not Safari on an actual iPhone. |
| Firefox 155, 390 × 844 | Same flows passed; no page errors. |
| WebKit touch emulation, 320 and 430 × 844 | Remove and re-save parcel, change stage through dropdown, preserve notes and open/close dialog passed. Document width equalled viewport width. |

Visual checks covered Screening controls, mobile Merkliste cards, the owner
dialog, the board and the top of Analyse. The board remains a horizontally
scrollable set of columns. Native phone keyboard behaviour and touch dragging
of cards were not tested; mobile stage changes used the accessible dropdown.

All three engines support `field-sizing: content` in the tested versions.
Notes grew from 58 px to 234 px for 12 lines, with no internal vertical
overflow at that length. At 390 px the dialog was 366 px wide with 12 px side
margins. The close button remained usable after scrolling the long dialog.
Older browser versions remain outside this verification.

Actual CSV files were downloaded through Kontaktliste exportieren in all
three engines and parsed with a CSV reader. Each contained 14 columns and one
test record. `Postadresse` was exactly
`UI Audit, Empfänger · Teststrasse 10 · 4313 Möhlin`; the comma and umlaut
survived, the parcel number remained 954, and field boundaries were intact.
The exported address is deliberately one line separated by middle dots.

A regression test now covers Postadresse, blank addresses, CRLF and blank
lines, embedded commas/quotes, UTF-8 and multiline notes. All 18 acquisition
tests passed; `git diff --check` passed. Evidence: screenshots, CSV downloads
and engine result JSON files in `artifacts/mobile-csv-audit/`.
