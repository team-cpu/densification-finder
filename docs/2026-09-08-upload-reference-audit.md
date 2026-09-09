# Uploaded reference audit — 8 September 2026

Inspected **all 19 PNG files** in `/Users/krisnafirdaus/Downloads/Swiss Real Estate Screening Tool/uploads/` with `view_image`, then compared their visible requirements with the supplied `Parcel Potential.dc.html`, implementation source, and the existing 8 September verification notes. The HTML was read as source in this audit; current browser verification belongs to the accompanying interaction audit. No application code or data was changed by this audit.

The uploads are an evolution of the design, not nineteen mutually consistent final specifications. Where a screenshot conflicts with the final HTML and the user's annotated feedback, the final design and explicit feedback take precedence. This classification follows visible differences, not filename timestamps alone.

## Complete image inventory

All filenames below begin with `Bildschirmfoto ` and end in `.png`.

| File date and time | Visible content actually inspected | Relation to final reference / implementation |
| --- | --- | --- |
| 2026-08-29 um 12.07.15 | Solid dark teal colour sample, 238 × 68 | Palette sample. Final HTML specifies accent `#1c4e4a`; it is used by the implementation. This swatch is not a separate page or interaction. |
| 2026-08-29 um 12.08.26 | Entire old acquisition page; overdue strip, five columns, stage dropdowns, contact-list and serial-letter buttons | Historical. Final HTML removes the overdue strip and serial-letter button; uses stacked Analyse / Eigentümer actions. Do not restore the old page structure. |
| 2026-08-29 um 12.23.22 | Overdue strip with four rows and Analyse actions | Historical; explicitly removed by user feedback. Card date chips remain the relevant display. |
| 2026-08-29 um 12.23.41 | Saved list; four metrics, seven rows, stage dropdown per row, Analyse action | The metrics/table structure remains relevant. Final reference replaces row dropdown with stage pill, adds Eigentümer and three-dot actions plus parcel number; current components contain those newer controls. |
| 2026-08-29 um 13.22.06 | Single old board card; contact-not-recorded text, dates, visible stage dropdown, Kontakt / Analyse side by side | Historical controls. Keep card information hierarchy, but final actions are Analyse followed by white Eigentümer. Accessible stage selection is retained as an application adaptation. |
| 2026-08-29 um 13.22.11 | Revised overdue strip with coloured stage pills and Eigentümer / Analyse | Historical whole strip, even though individual pill styles remain useful. It must stay absent from the acquisition page. |
| 2026-08-29 um 13.22.38 | Inline Kontakt editor inside card; owner, contact person, phone, email, last contact, follow-up, next step, one-line note, Fertig | Historical editor. Final HTML uses a separate owner modal, adds full-width address and notes, removes editable follow-up and relabels next step as Status. Current `acquisition.py` follows the newer field model. |
| 2026-08-29 um 16.16.58 | Five acquisition columns; contact details, due chips, side-by-side Eigentümer / Analyse | Intermediate layout. White card/column surfaces, compact metrics and dates remain relevant. Action arrangement is superseded by the stacked final HTML and user annotation. |
| 2026-08-29 um 16.17.38 | Collapsed regulatory accordion with relevant-count pill and Alle herunterladen; disclaimer underneath | Intermediate download placement. Final HTML puts Alle herunterladen on the legal-sources card and keeps municipality-specific regulatory accordion separate. Current `detail.py` follows that separation. |
| 2026-08-29 um 16.20.25 | Screening header and single-row filter layout, exclusions vertically at right | Historical filter arrangement. Final HTML groups Kanton / Gemeinde / Objekttyp, then numeric inputs, then exclusions and parcel search. |
| 2026-08-29 um 16.21.47 | Exclusions crop with empty squares and teal selected checkbox | Checkbox colour/shape remains relevant. Final HTML lays these horizontally. The third criterion's semantics differ in real data; see intentional differences below. |
| 2026-08-30 um 14.23.26 | Organisation settings modal, four switches, licence row, Mitglieder / Einstellungen tabs | Intermediate navigation/content. Final HTML opens Team and Einstellungen separately through the account menu and includes company fields before settings. Switch appearance, header/footer and overlay still inform visual comparison. Mock licence/account claims are not production facts. |
| 2026-08-30 um 14.23.49 | Areal / Kanton Zürich header; Screening with one-row minimum potential, AZ, area, municipality, type and result count | Historical brand and filter layout. Final HTML is Scope with no canton beside the wordmark; result count moves to table toolbar. |
| 2026-08-30 um 14.29.16 | Near-identical Areal header/filter screenshot with adjusted spacing | Same superseded structure as preceding image; no additional final control requirement. |
| 2026-08-30 um 14.31.18 | Second Organisation settings modal crop | Same intermediate modal as 14.23.26. No additional hidden setting or control visible. |
| 2026-08-30 um 14.35.50 | Six white oval marks on a blue background | Historical logo inspiration. Final HTML explicitly uses eight white ellipses on a teal rounded square; current shell serves `static/scope-mark.svg`. Do not replace the final mark with this raster. |
| 2026-08-30 um 14.37.23 | Screening filter adds Aargau canton in a six-column row; count on next row | Intermediate. Kanton remains required, but final three-column grouping and results-toolbar count supersede its placement. |
| 2026-08-31 um 08.22.55 | Three equal primary columns, three numeric groups below, horizontal separators and exclusions | Closest uploaded filter-layout reference. These groupings appear in final HTML and `screening.py`'s primary/numeric/flags containers. Final HTML additionally has the parcel search on the exclusions row. |
| 2026-08-31 um 08.27.38 | Parzellen-Nr. suchen input with search icon and example “z. B. HO 1284 oder Seestrasse” | Still relevant: visible search icon, mono input and support for number or street. Current handler supports parcel/address/municipality search, but its placeholder only showed the Zurich-style example at inspection time; see follow-up below. |

## Concrete checklist from the combined references

These are source/previous-evidence checks, not claims of new UI execution in this document.

- [x] Final Scope wordmark and eight-ellipse mark, four navigation destinations; old Areal/canton wordmark excluded. Implementation: `shell.py`, `static/scope-mark.svg`, `navigation.py`.
- [x] Filter hierarchy: Kanton / Gemeinde / Objekttyp, numeric group, exclusions plus parcel search, reset and result count. Implementation: `screening.py`. Previous browser measurements are in `2026-09-08-visual-reference-followup.md`.
- [x] Saved-list metrics, parcel number, status pill, owner action and menu. Implementation: `merkliste.py`, `components/merkliste/index.html`; previous populated/empty/mobile evidence is documented.
- [x] Five-column acquisition board, compact white cards, due chips, green Analyse above white Eigentümer. Implementation: `components/acquisition_board/index.html`. Overdue strip remains removed.
- [x] Owner modal replaces old inline contact editor; address and notes use full-width blocks; Status is beside Letzter Kontakt; no editable Wiedervorlage. Implementation: `acquisition.py`; previous persistence and notes-growth checks are documented.
- [x] Account menu opens Team / Einstellungen as separate views; company fields and four settings switches are present. Implementation: `organisation.py`; previous toggle measurements are documented.
- [x] Legal-sources download action and municipality-specific regulatory accordion remain separate. Implementation: `detail.py`.
- [ ] Search placeholder: at inspection, `screening.py` used `z. B. HO 1284`, while production data uses actual Aargau parcel identifiers and its search handler matches literal strings. Replace with a valid supported example and make address searching discoverable. This is a copy/usability discrepancy, not a missing search implementation.
- [ ] Long unbroken owner/contact/status/address values on board cards: source inspection found no `overflow-wrap` handling for `.address`, `.owner`, `.contact` or `.next`. Reproduce visually before calling it a defect; ordinary screenshots only contain breakable text.

## Intentional differences to retain

- The screenshot's “Lärmempfindlichkeit ES II verletzt” checkbox cannot become a real noise filter without the required data. The application uses “Strassen-/Bahnparzellen”; this distinction was already recorded in `2026-09-04-parity-and-security-audit.md`. Do not relabel the existing criterion as noise exclusion.
- The mock's Zurich addresses, prices, licensed-source claims, Hochbau AG members and subscription details are examples. Current Aargau data and truthful shared-access/service descriptions remain authoritative.
- The owner's auto-populated dates and the accepted cost-reserve formulas are not to be replaced with historical screenshots or mock calculations.
- Mobile cards, horizontal board scrolling, accessible stage changes and validation messages are application behavior for which these nineteen desktop crops contain no complete equivalent.

## Evidence boundary

All nineteen uploaded images were individually viewed. None contains a complete mobile-page layout, browser error condition, opened account-menu screenshot, or comprehensive hover/focus sequence. Most show no pointer/focus indication at all. Therefore this closes the **uploaded-image inventory/inspection gap**, but screenshots alone cannot close the keyboard, hover, error-state or browser-matrix gaps. Those need direct current UI checks against the final HTML and the working application.

No additional missing final page or modal was found solely in the uploaded images. The copy discrepancy and long-text case above were passed to the implementation audit for follow-up.
