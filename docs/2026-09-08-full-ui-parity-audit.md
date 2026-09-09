# UI and interaction audit — 8 September 2026

Reference: `/Users/krisnafirdaus/Downloads/Swiss Real Estate Screening Tool/Parcel Potential.dc.html` and its unmodified `support.js`.
Baseline: `d11b5f0`, with the local changes listed below. This is a local audit, not production verification or a claim of pixel-for-pixel identity.

## Method

Opened the reference through a local HTTP server. Opening it with `file://` prevented its runtime from fetching the original template and broke table loops; those early screenshots are not a valid populated-table baseline. HTTP screenshots supersede them.

Compared the four pages at 1440 × 1000, inspected screenshots, and exercised browser controls with Playwright/Chrome. Tested empty saved pages and long owner dialogs additionally in WebKit at 390 × 844. App writes used separate SQLite copies under `/private/tmp`; no production data or authentication was involved.

## Matrix

| Surface/state | Evidence and result |
| --- | --- |
| Screening populated, filters, focus, reset | Reference and app opened; overall structure, fonts, accent and table actions correspond. Actual data/filter availability and ranking differ; see exceptions. |
| Screening zero matches | Fixed: table headers/message and zero totals remain, save-search stays available, CSV is disabled, dismissed list still renders. Browser and regression tests pass. |
| Merkliste populated | Table, parcel number, state pill, owner/action layout inspected. Mobile uses cards, a deliberate responsive adaptation. |
| Merkliste empty | Fixed: zero metrics and table/card empty message remain. Desktop and WebKit screenshots captured. |
| Three-dot menu | Hover/open inspected. Fixed keyboard opening focus, aria-expanded, Escape dismissal and focus restoration. Final browser verification passes. |
| Akquisition populated / empty | Cards and five state columns inspected. Fixed all-empty board to retain all five columns. Desktop and WebKit checks pass. |
| Active navigation tab | Fixed: clicking the selected tab retains the page instead of deselecting it and returning to Screening. Unit and browser check pass. |
| Owner popup | Fields, layout, phone focus, long notes, footer, close and Escape tested. Fixed backdrop click by moving overlay layout from the modal wrapper to the actual overlay. Desktop and WebKit pass. |
| Account menu / Team | Open, hover, close, invalid invite input and empty roster inspected. Reference invalid input silently does nothing; app retains input and reports validation. No real invitation was sent. |
| Settings | Form and four toggles inspected. Fixed backdrop click; native control size and explanatory copy still differ. Preference service limitations remain explicit. |
| Analysis | Top, facts, assumptions, cost rows, legal sources and regulation section inspected. Tooltip responds to mouse hover and keyboard focus; cost editor opens and Escape cancels. Legal/regulation accordions open and close. |
| Source/export failures, every validation boundary, every data badge | Not exhaustively injected in this browser audit. Existing tests cover selected boundaries; do not call every possible state visually verified. |

Final machine results: `artifacts/full-ui-parity/verification.json` and `empty-mobile.json`. Earlier `interactions.json` includes failed exploratory locators and is not the final verdict. Screenshots prefixed `fixed-` show corrected behavior. `desktop-empty-*` and `mobile-empty-*` show final empty layouts.

## Remaining differences — still not 100% parity

Follow-up: width, title spacing, ranking caption and the identified widget/card differences below were subsequently addressed. See `2026-09-08-ui-polish.md` and `2026-09-08-visual-reference-followup.md` for the newer implementation and verification. The bullets below describe this audit's original baseline.

- Analysis reference uses a 1180 px outer max width; the app uses the wider application content area. This changes wrapping and density.
- Title/intro vertical spacing differs (for example Screening filter starts around y=198 in the app versus y=181 in the reference at desktop width). Native widget metrics and toggle geometry also differ.
- Ranking is not the mock's absolute potential sort: existing app logic ranks built parcels by relative reserve and interleaves vacant parcels. The displayed “Sortiert nach Potenzial ↓” label is therefore imprecise. No ranking change was made in a visual audit.
- Reference tables, account identity, licence, prices and Zurich data are fictional examples; app uses actual Aargau data and shared access. No mock members, licences or legal events were copied.
- Philipp's approved cost-reserve formulas, defaults and replacement decision remain. Reference profit-on-revenue and unit formulas are intentionally not adopted.
- Source cards include actual provenance and benchmark notes; reference example file sizes, dates and canned descriptions cannot be reproduced honestly for different source documents.
- Responsive cards, validation errors, real export and saved-search management exceed what the mock simulates. Reference export buttons have no handlers in several places.
- No physical phone/native Safari test, screen-reader audit, exhaustive browser/version matrix, production UI test, or pixel-difference comparison over identical data. The uploads folder was inventoried; each uploaded screenshot was not separately audited in this batch.

## Changes and verification

Changed `acquisition.py`, `organisation.py` (dialog overlay CSS), `navigation.py` (retain active tab), `screening.py` (shared search/result rendering for empty results), `merkliste.py` and both table component HTML files (empty layouts and menu keyboard behavior). Added regression tests in `tests/test_app.py` and `tests/test_navigation.py`. The existing `tests/test_acquisition.py` change belongs to the preceding CSV audit.

- Relevant Python regression: **96 passed, 7 subtests passed**.
- Calculation component JavaScript: **11 passed**.
- `git diff --check`: clean.
- No dependency, schema, formula, auth-service, commit, push or deployment changes.

To review: run the app, search an impossible parcel number, open save-search; hide/restore a last result; remove the final saved parcel and open Merkliste/Akquisition; open owner/Settings and click outside; use Tab/Enter/Escape on the three-dot menu; click the active navigation tab again; hover/focus a calculation label and cancel an amount edit with Escape.
