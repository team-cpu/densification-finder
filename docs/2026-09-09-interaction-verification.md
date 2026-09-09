# Interaction verification — 9 September 2026

Tested directly through the Codex in-app browser at http://127.0.0.1:8516/ using the isolated scope-polish-20260908.sqlite database. This is local verification, not production acceptance.

## Change in this pass

Calculation explanations now dismiss on Escape without moving keyboard focus or changing amounts. Leaving the label or starting a new pointer visit resets dismissal. Added a regression test for dismissal, focus retention and reset. Files: detail.py, components/calculation_table/index.html, tests/calculation_table.test.cjs.

## Direct UI results

- Screening: Enter on a parcel's Analyse button opens its detail page.
- Calculation: Shift+Tab from the revenue amount reveals the formula explanation. Escape hides it while the label stays active. Revisiting the label reveals the explanation again.
- Calculation: entering 12xyz shows the German validation message; Escape cancels the draft.
- Merkliste: keyboard Enter opens Weitere Aktionen and focuses the remove action. Escape closes it and returns focus to Weitere Aktionen; no parcel was removed.
- Owner dialog: opened through Eigentümer; long notes remain readable through the scrolling form body. Header and Fertig remain visible at 390×844. The focused notes field has a visible outline.
- Owner dialog: 31.02.2026 shows the German invalid-date message. Cleared the invalid draft after checking it. Fertig closes the dialog.
- Restored the browser viewport override after the mobile check.

## Automated checks

- 58 Python tests passed (test_detail.py and test_ui_components.py).
- 12 JavaScript calculation component tests passed, including the new tooltip regression.
- git diff --check passed.
- Earlier dialog/acquisition/app/list checks: 70 Python tests passed; this overlaps the current group and must not be added as a unique total.

## Still unverified

Native Safari/Firefox, physical phones, exhaustive pointer-hover transitions, and every possible page/data state remain unverified. The available browser surface was Codex's Chromium browser. Keyboard tooltip verification does not establish pointer-hover parity. These results do not establish pixel-identical parity with every reference. No commit or push was made in this pass.
