These minimized fixtures come from the official public search responses saved
on 2026-10-06 in the local research folder outside this repository. Amtsblatt
Seon has 10 initial cards and a 1-card Ajax fragment; canton current
consultations have 10+1 JSON items. The source widget configuration and observed
current/archive POST query structures are retained. Navigation, article/teaser
text, unrelated JSON fields, headers and cookies are omitted. IDs, titles,
publication dates, source links and relevant structural/pagination fields
preserve the observed values. These fixtures are search metadata, not verified
planning records, legal-stage evidence or a complete legal coverage period.

The minimized Aarau multi-page fixtures (`amtsblatt-aarau-page1.html` and
`amtsblatt-aarau-page2.html`) were saved on 2026-10-06 from the official
public Amtsblatt search responses in the same local research folder outside
this repository. They are the first two pages of the Aarau authority listing
(`data-total="33"`, `data-limit="10"`, ten publication cards per page), with
the observed page-1-to-page-2 `control-pagebrowser__next-page` pagination link
retained. The search-result wrapper attributes, card markup, IDs, titles,
publication dates and source links preserve the observed values; navigation,
article/teaser text, headers and cookies are omitted. Trailing spaces and tabs
on individual lines were normalized after capture so the files pass
`git diff --check`; this touched only line-ending whitespace, so all markup,
values and line breaks are otherwise unchanged and the capture metadata
remains as observed.

The minimized Menziken version cards were saved on 2026-10-06 during local
preview verification. They retain the observed `Korrektur` / `ursprüngliche
Version` span markers alongside the actual publication-date div. Those markers
are source metadata, not legal stages or inferred supersession links.
