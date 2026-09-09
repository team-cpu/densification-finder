# Visual reference follow-up — 8 September 2026

Reference: the original `Parcel Potential.dc.html`, served over local HTTP. Browser: Codex in-app browser. Database: isolated `/private/tmp/scope-polish-20260908.sqlite`; one parcel saved through the UI for populated-list and owner-dialog checks.

## Changes

- Screening: fixed-width action area lets the introduction use the remaining space; numeric fields use IBM Plex Mono; checkbox dimensions and contrast match the reference; filter footer spacing corrected; desktop table minimum width aligned to 1900 px.
- Results toolbar: corrected the responsive selector that accidentally wrapped the contents of the limit control instead of the toolbar. Anzeigen stays intact at intermediate widths.
- Merkliste: desktop table minimum width aligned to 1100 px; existing responsive cards retained.
- Analyse: moved the width constraint from the main application container into a dedicated detail container. Content is 1124 px plus the 28 px outer gutters, matching the reference's 1180 px envelope. Navigation remains full width. Removed the extra CSS markdown block and corrected doubled vertical gaps between the title, result panel and facts.
- Owner dialog: removed the second textarea border, aligned the notes minimum height to 54 px, and retained the visible green focus ring and automatic growth.
- Settings: 34 × 20 px toggle track and 16 px thumb, explicit on/off positions, keyboard focus outline, reference label weights and row text rhythm.
- Akquisition: heading/intro line heights and header spacing aligned; cards use reference padding, border, gap and white column background. Existing drag/drop and keyboard stage controls retained.

## Verification

- Reference and application inspected directly in Codex browser. Matched 884 px desktop viewport for comparisons; 390 × 844 for responsive checks; wider desktop checked for the constrained Analyse content.
- Screening header and buttons, numeric fields, checkbox appearance and uncut Anzeigen control inspected.
- Analyse: shell starts at y=0, height 53 px; at 884 px viewport content width is 828 px, with 28 px gutters. Result panel starts at y=176.5 px, close to reference y=179 px. Source content and formulas differ intentionally.
- Team empty roster, invite controls, close button and footer inspected; no invitation sent.
- Settings switch track and thumb measured at 34 × 20 and 16 × 16 px.
- Mobile Merkliste populated card, three-dot menu opening/focus, Escape, owner dialog fields/footer, and all five acquisition columns inspected.
- Owner notes measured 54 px minimum height, `field-sizing: content`, and focused 3 px green ring. Escape dismissal confirmed.
- Python: 126 tests and 7 subtests passed across app, navigation, detail, acquisition and organisation. JavaScript calculation editor: 11 tests passed. Python compilation and whitespace checks passed.

## Content boundaries

Real Aargau addresses, longer zone names, sources, dates, contact data and approved formulas remain authoritative. Those values affect wrapping and table column allocation. Shared-access identity and truthful service-status descriptions are retained instead of fictional reference accounts, licences or working-email/2FA claims. Mobile cards and scrollable dialogs retain the application's responsive behavior because the reference does not provide equivalent complete mobile layouts.

These checks cover the rendered surfaces and interactions listed above, not every possible data combination or browser version. No physical-phone or authenticated-production test was performed. Changes remain local, uncommitted and unpushed.
