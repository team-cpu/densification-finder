# Local verification — 10 September 2026

Codex in-app browser, localhost:8518, isolated /private/tmp/scope-polish-20260908.sqlite. No production writes or push.

Direct UI checks:
- All-types screening: 50 rendered rows descending by potential, first values 107800, 63103, 26265, 25196, 23887.
- Owner dialog: seeded legacy phone 0787778800 displays as 078 777 88 00. Opening/closing did not rewrite the raw database value. Board displays the formatted number too.
- Address entered via UI: TEST Müller, "QA", Teststrasse 10B, 4313 Möhlin on three lines. Downloaded contact CSV parsed successfully with comma/quote/umlaut intact; address lines join with middle dots. Second download also verified formatted phone.
- Settings: all four unavailable-service switches disabled/off, company text fields enabled and explanatory captions visible.
- Möhlin regulation panel opened: 13.12.2023, Möhlin BNO, PDF attachment 10145, no other municipality shown in the relevant section. This verifies displayed scope, not independent legal completeness of the feed.

Automated checks: 87 tests passed, 7 subtests passed, 1 skipped across ranking, acquisition, organisation, regulations and app. Separate built/vacant ranking and saved-search behavior are covered by automated tests; not every dropdown combination was repeated in browser today.

No new defect found within these checks. Native Safari/Firefox, physical phone, exhaustive hover and independent verification of the regulatory feed remain outside this pass.
