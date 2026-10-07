# Regulation changes as a feed — what is actually available

> 2026-10-06: sections 1–4 preserve the historical 2026-08-18 research. Section
> 5 records the panel/reuse boundary; section 6 updates technical access and the
> explicit-run local metadata collectors. Section 7 documents the opt-in local
> review preview. Earlier claims about email being the only route do not describe
> the current collector capability.

The question: can new canton and municipality publications be pulled into the
tool, so that a parcel under analysis shows the building-regulation changes that
affect it?

Current answer: **search metadata can be collected locally without email.**
`planning_collect.py` reads the Amtsblatt's filtered HTML search and ag.ch's
consultation JSON search interface into review candidates (§6). These snapshots
do not establish legal stage, parcel relevance or complete historical coverage,
and are not connected to production synchronization. An opt-in local panel
preview can show only explicitly reviewed relevance/territory decisions (§7). Amtsblatt robots restrictions
and reuse permission remain separate, unresolved constraints (§5).
`oereblex.ag.ch` remains the legal-document source for regulations in force.

Sections 1–4 below are historical findings checked on 2026-08-18; current
technical collector capability is documented in §6.

## 1. The Amtsblatt has the right content and closes the door

`amtsblatt.ag.ch` classifies publications into exactly the categories that
matter, as search facets:

| facet id | Rubrik |
|---|---|
| `190:192` | Gemeinden → **Bau- und Nutzungsordnung** |
| `190:193` | Gemeinden → Bau- und Rodungsgesuche |
| `162:175` | Kanton → Raumplanung |
| `162:166` | Kanton → Anhörungs- und Mitwirkungsverfahren |
| `162:167` | Kanton → Projektauflagen |
| `162:172` | Kanton → Plangenehmigungsverfahren |
| `203:204` | Betreibungen → Betreibungsamtliche Grundstücksteigerung |

It also publishes earlier than anything else: a revision appears at public
consultation, months before it is in force.

But `amtsblatt.ag.ch/robots.txt` disallows `/publikationen/` (the search) and
`/ekab/` (each publication), which is every URL a feed would need. There is no
documented API — the only JSON endpoint on the page is the search-box
autocomplete. Crawling it anyway would break the site's stated policy for a
result that a markup change could silently invalidate, and this tool already
holds the line that it scrapes nothing.

What the platform does offer, officially:

* **Suchabonnement** — any saved search, including these facets, mailed daily at
  about 07:00. Free, and no account: a form asks for name, e-mail address and a
  subject, then an activation link is mailed (corrected 2026-10-05 from the
  site's own help page; an account is only needed for bookmarks and for
  publishing). This is the sanctioned channel for early warning, and it lands
  in Philipp's inbox, not in the tool.
* **Export** of selected publications from the UI, up to 50 at a time.

## 2. OEREBlex — the source that does answer per parcel

`oereblex.ag.ch` is the canton's legal-document platform. The ÖREB extracts this
tool already fetches link straight into it (`.../api/attachments/…` under each
restriction's *Rechtsvorschriften*). Its `robots.txt` contains no restrictions,
and three JSON endpoints carry everything a feed needs:

| endpoint | what it gives |
|---|---|
| `GET /api/edicts.json` | **every municipality with its edicts** — 76 KB, one request, whole canton |
| `GET /api/towns.json` | 197 municipalities, with `has_decrees` / `has_prepublications` |
| `GET /api/decrees/{id}.json` | one decree: `decree_nr`, `decree_date`, `decree_instance` (e.g. RR) |

An edict looks like this:

```json
{"id": 769, "syst_nr": "4001", "title": "Bau- und Nutzungsordnung",
 "abbreviation": "BNO", "inaction_date": "2025-04-30", "outaction_date": null,
 "town": "Aarau", "is_active": true,
 "main_document": {"filename": "NUPLA-4001-2025-V.pdf",
                   "document_path": "/api/attachments/…"}}
```

`inaction_date` is the date the regulation came into force, `outaction_date` the
date it was superseded, and `main_document` is the BNO itself as a PDF.

**Measured, not assumed:**

* 195 of 197 municipalities carry an active BNO.
* 234 BNO-type edicts in total, so the history of superseded versions is there
  as well as the current one.
* Of the 165 municipalities in `results.sqlite`, **9 got a new BNO in force in
  2026 so far and 24 since the start of 2025** — roughly one to two a month.
  That is the volume of the feed. (Canton-wide, counting the municipalities this
  tool has no results for, 2026 stands at 11.)
* All 165 municipalities in `results.sqlite` are found by name.
* `has_prepublications` is **false for all 197 today**, so the field exists but
  the canton is not feeding upcoming changes into it. Early warning has to come
  from the Amtsblatt subscription instead.

**The join has one trap.** `syst_nr` looks like the BFS number and matches it on
164 of our 165 municipalities — but Dintikon is `4196` in OEREBlex against BFS
`4194` in the federal building register, which this tool keys on. Join by name,
cross-check `syst_nr`, and report a mismatch rather than trusting the number: a
silently wrong join would attach a neighbour's building regulation to a parcel.

## 3. geodienste.ch says when the data itself moved

`GET https://geodienste.ch/info/services.json` (3.4 MB, no key) carries an
`updated_at` per canton and topic. For Aargau on 2026-08-18:

    npl_nutzungsplanung_v1_2   updated_at 2026-08-13   Freie Nutzung, Quellenangabe Pflicht
    planungszonen_v1_1         updated_at 2024-11-08

Each also has a STAC item and an OGC API Features endpoint. The features
themselves carry `rechtsstatus` and `publiziertab`:

    https://geodienste.ch/db/npl_nutzungsplanung_v1_2_0/deu/ogcapi/collections/
      grundnutzung/items?f=json&crs=…EPSG/0/2056&bbox-crs=…EPSG/0/2056&bbox=E,N,E,N

(The `crs` parameter in EPSG:2056 is mandatory — without it the service answers
403, which reads like a permission problem and is not one.)

Two findings that decide how useful this is:

* **Aargau stamps its whole delivery with one date.** Every AG feature sampled
  carries `publiziertab = 2026-08-13`, the delivery date. Solothurn and
  Basel-Landschaft in the same query carry real per-object dates (2008-05-27,
  2023-01-20, 2024-11-28…). So in Aargau `publiziertab` says when the file was
  handed over, not when the zone changed.
* Every AG object sampled is `rechtsstatus = inKraft`. Ongoing revisions are not
  delivered, so the harmonised feed cannot warn about a change in progress.

Useful for "is our copy of the zoning older than the canton's", not for "what
changed on this parcel".

## 4. What we already fetch — now used (2026-08-18)

The ÖREB extract queried for every shortlisted parcel names the documents that
govern it, each with a link into OEREBlex or the cantonal law collection. That
was a per-parcel regulation reference sitting unused in a response the tool
already paid for. It is now stored with the cache row and rendered as block D of
the detail view, with the extract's zone legend and land-registry area in block
A. See the README.

It changes what the remaining stages are worth:

* **The per-parcel question is answered without OEREBlex** — no municipality-name
  join, so the Dintikon trap below does not apply. The cadastre attaches the
  right BNO to the right EGRID, and its official number *is* the BFS number.
* **What ÖREB does not give is a date.** `Lawstatus` says `inForce`; it does not
  say since when. That is exactly what `edicts.json` carries, which is why the
  two remain complementary rather than redundant.

## Proposal

Staged, cheapest first. Nothing here needs the Amtsblatt.

**Stage 1 — the parcel says which regulation governs it.** ~~One daily fetch of
`edicts.json`, joined by municipality name.~~ **Done differently, and better:**
the ÖREB extract already names the documents per parcel, so block D was built on
that instead — no second source, no name join. What is still missing from it is
the in-force date, and `edicts.json` is where that comes from: one daily fetch,
cached, to add *in Kraft seit 30.04.2025* to the BNO line block D already shows.

**Stage 2 — the tool warns when its own numbers are stale.** Compare each
municipality's `inaction_date` with the vintage of the zone extract the results
were computed from (`are_bzbauzone_20260717.gpkg` → 2026-07-17). Where the BNO is
newer, the potential shown for that municipality was computed under the old
rules, and the parcel should say so. **Today that count is zero** — every current
BNO predates the extract — which is the right answer and proves the check rather
than making it look decorative.

**Stage 3 — a canton-wide "was ist neu" panel.** ~~The same table, sorted by
`inaction_date`.~~ **Done 2026-08-20**, and it carried Stage 1 with it: block E
sits under block D and opens with the parcel's own regulation and its in-force
date — the thing the ÖREB extract never says — then the three most recent
changes in the canton, the rest folded. `regulations.py` holds the fetch, the
parse and the name join; the interface holds none of it. Re-measured against the
live feed on the day: 227 edicts in force, 192 of them in municipalities this
tool has parcels for, all 163 matched by name, and Dintikon still 4196 against
BFS 4194.

Still open from this stage: the panel is per-parcel, so the canton-wide list is
the same three rows on every parcel. A list view of it — sorted by date, as the
signal for when a full recompute is worth running — has no home yet.

**Superseded 2026-10-05.** Philipp asked for the panel to show the parcel's
municipality and the canton only — finished changes and changes being planned
or discussed — so that analysing a plot shows "they change the rules about X in
two years". The canton-wide rows and the fold are gone; see section 5.

**Stage 4, only if Philipp wants the early warning** — an Amtsblatt
Suchabonnement on `Bau- und Nutzungsordnung` and `Raumplanung`, delivered to his
inbox. Manual by design; a revision at public-consultation stage is months of
lead time on everything above, and no automated route to it exists.

## 5. The Amtsblatt, revisited (2026-10-05)

Philipp named the Amtsblatt as the source for what is planned or under
discussion. Rechecked against the live site:

* **`robots.txt` is unchanged**: `/publikationen/`, `/ekab/` and
  `/fileadmin/ekab/` are disallowed for every crawler.
* **The legal notice is the stronger constraint**
  (`/informationen/rechtliche-hinweise/`): the Amtsblatt is declared
  copyright-protected, content is for "Eigengebrauch" only, any other use needs
  the Staatskanzlei's *prior written consent*, and "Einspeisung in
  Online-Dienste durch unberechtigte Dritte" is forbidden. Scope is an online
  service. Whether official notices are protected at all (URG Art. 5) is a
  question for a lawyer, not for this file; the consent request in
  `docs/2026-10-05-amtsblatt-freigabe-anfrage.md` makes it moot.
* **The sanctioned automated channel is the Suchabonnement** —
  `/informationen/` says so in as many words ("können kategorisierte Inhalte
  zudem automatisiert bezogen werden"). Daily e-mail at about 07:00, free; the
  e-mails still land under the same reuse terms.

Checked in a browser on 2026-10-05 — driven by Claude at the user's request,
five search views, no crawler:

* **The search state is a plain GET link without `cHash`** — the page's own
  script builds it that way (`search.min.js`, a static file robots.txt does not
  restrict). `filter[authority][]=10,398&filter[category][]=190,192&timerange[type]=4`
  listed Seon's 11 planning publications and nothing else; two offices for one
  municipality are combined, not intersected (Wohlen: 14); `searchQuery="Seon"`
  with category `162,175` found the canton's 4 approval notices naming Seon.
  Every one of the 196 OEREBlex municipalities has at least one office ID; the
  snapshot is in `amtsblatt.py`. Baden, Lenzburg and Aarburg publish only
  through departments.
* **The list view carries** title, publication date, Publ.-Nr., office,
  category and a teaser of about 300 characters cut off with "[…]". The full
  text is only in the publication's page and signed PDF — both disallowed.
* **Paging**: 10 per page, `page=N` on an AJAX URL.
* **New publications**: Monday to Friday, 07:00.
* **Canton approvals name several municipalities in one notice** ("Gemeinde
  Reinach: Gestaltungsplan Friedstrasse | Gemeinde Seon: Aufhebung …"). A
  feed that attaches the notice to every municipality it mentions puts
  Reinach's plan on Seon's parcels. `planning.py` splits on "Gemeinde X:" and
  reports a notice it cannot split rather than guessing.
* **One plan, several publications.** Seon's Gestaltungsplan "Giessi":
  Mitwirkung 01.07.2021 → Auflage 27.06.2024 → Beschluss 05.09.2024, no approval
  notice found. Yet the ÖREB extract of parcel 1268 (2026-08-18) lists
  "Gestaltungsplan Giessi" (Nr. 21.259) as governing it — checked live on
  2026-10-05: restriction and document both `inForce`. Whether that document
  is the plan decided in 2024 or an earlier plan of the same name, nothing in
  either source says: the extract carries title, official number, legal
  status and the files' links, no dates; the notices carry no number (and a
  notice that did might cite the plan it replaces). So the extract never
  settles a published revision. Version 1 shows Giessi's three publications
  as printed (Mitwirkung 2021, Auflage and Beschluss 2024) and says nothing
  about the extract. The inference path (OFF) reads it **Stand unbestätigt**: its
  Beschluss published on 05.09.2024, and — from the extract stored on
  2026-08-18, which kept no
  legal status — "im ÖREB-Auszug vom 18.08.2026 ist ein Plan dieses Namens
  verzeichnet (Nr. 21.259), Rechtsstatus im gespeicherten Auszug nicht
  enthalten — ob es diese Revision ist, ist nicht belegt", with a link to the
  current extract. A fresh extract would say "in Kraft verzeichnet"; the
  verdict on the revision stays the same. (Until 2026-10-05 the
  panel let an `inForce` listing settle a decided plan; that inherited a
  predecessor's status whenever a new plan kept the old name, and is gone.)
* **"ÖREB prüfen" refreshes stored extracts that lack the legal status —
  once.** Until 2026-10-05 it did not: it fetched only shortlist parcels with
  no stored `details`, and the 50 extracts cached on 2026-08-18 (all of them)
  carry no `lawstatus`. Now `oereb.details` writes a `lawstatus_kept` marker;
  `screening.oereb_targets` asks, of the shortlist the user runs, for parcels
  without an extract and for those whose extract lacks the marker. A fresh
  answer carries the marker whether or not the cadastre states a status, so
  it is not asked again; a failed call keeps the stored extract untouched
  (until then `check_oereb` overwrote it with an empty error row). Run on a
  scratch copy of the local database with real cadastre answers for three
  shortlisted parcels: all three came back `inForce` with the marker, the
  next run asked for none, a forced failure left the stored row as it was.
  Since the review of 2026-10-05: a failed call leaves every cached row
  untouched — also an older row with restrictions but no `details`, whose
  exclusion it used to erase — and a 200 that is no extract (no
  `RealEstate`) counts as a failure, not as an empty extract. The run's
  failures are shown once after it. A failure is written only where no row
  exists, decided by the database as it writes, so a row another run stored
  meanwhile is kept as well. The button itself crashed: since
  `screening.page` left `app.py` (`a078246`, 2026-09-01), its handler called
  `load()`, which exists only in `app.py` — `NameError` on every click, on
  `origin/master` too; the app now hands its loader in. A day later
  (`d77af13`) the section holding the button was hidden, so the crash was out
  of reach in the browser; this branch adds a visible button (below). An AppTest drives
  the button: a failed run warns once and leaves every cached row as it was,
  the next successful run shows no warning. The production database on
  Railway was not touched.
* **Area plans.** "Öffentliche Mitwirkung zum Gestaltungsplan «Heidegrabe»"
  (10.09.2026, Mitwirkung until 12.10.2026) concerns the area between
  Aarauerstrasse and Lenzburgerstrasse, not all of Seon. Block E labels it
  "Gebiet «Heidegrabe»" in the inference path (OFF), which also reports a
  listing of the same name in the parcel's extract — for a plan whose latest
  notice is a draft step, "diese Revision ist im Amtsblatt nur als Entwurf
  publiziert". Version 1 shows the notice's own title and matches nothing.

Rechecked on 2026-10-06, each fact from an official page fetched that day
(copies kept outside the repository), three things kept apart:

* **Technically**, the search and every publication load without a login;
  ag.ch/de/aktuell/amtsblatt says use is free and needs none.
* **`robots.txt`** (unchanged) disallows `/publikationen/`, `/ekab/`,
  `/fileadmin/ekab/` and `/archiv/` to every user agent — a request to
  automated readers, not a contract.
* **The legal notice** (unchanged since 2026-10-05) reserves every use beyond
  the legally permitted cases to the Staatskanzlei's prior written consent,
  allows the contents for personal use, and forbids exploitation — "inklusive
  Einspeisung in Online-Dienste" — by unauthorised third parties. Whether a
  login-only internal tool showing number, date, title and link falls under a
  permitted case (the Urheberrechtsgesetz exempts official decrees and
  decisions in Art. 5, and allows internal use in Art. 19 — not verified here:
  fedlex renders only with JavaScript) is a legal question; nothing in this
  repository settles it.
* **Channels.** The platform (DIAM by Somedia Production — the user guide is
  `DIAM-AG_Einfuehrung`, the search script carries Somedia's header) documents,
  in its help, changelog and user guide
  (`DIAM-AG_Einfuehrung_v0.4.pdf`): the website search; a search subscription
  mailed daily at about 07:00, or weekly, listing new publications for a saved
  filter (name, e-mail, activation link; format not documented, no sample
  seen); and a PDF export of selected publications, split above 50. None of
  them, nor the search page's own script, nor opendata.swiss, offers an API,
  RSS, XML or CSV — none was found, which is not the same as none existing;
  the Redaktionsstelle (publikation@ag.digitales-amtsblatt.ch) could say.

So the store is filled by an operator, through a contract that does not depend
on any of those formats (`amtsblatt_import.py`); an adapter for the
subscription e-mail can feed the same contract once a real e-mail has been
seen. Real publications read in the browser on 2026-10-06 (about fourteen page
views, no crawling) went through it into scratch stores: Seon (16) and Oberrüti
(7) from 2019 to that day, with one canton-wide change; Killwangen to
31.03.2025 (a stale store, with the canton's refusal of the Erschliessungsplan
"Schulstrasse"); Hallwil from 01.06.2024 (no publications). Seon's list showed
eleven publications behind a "Weitere laden" button where the 2026-10-05 copy
had ten — the coverage window is what keeps such a gap from reading as "none".

What is built (branch `feat/scope-regulation-panel`), by how far it is proven:

* **Philipp's three labels: built for checked stages; no source states them
  on its own.** A row shows **Entwurf in Auflage**, **Genehmigt, Rechtskraft
  ausstehend** or **In Kraft getreten** only for a stage a person checked
  against a source, and only while that source supports it on the day shown
  (`planning.stage_badge`): the display period runs, the appeal period (or a
  stated date of legal force) lies ahead, the entry into force has happened.
  Otherwise **Publikation**, with what is known. On the real Oberrüti data all
  three appear: the old BNO in force (OEREBlex), the corrected Gesamtrevision
  in Auflage until 14.10.2026, the Erschliessungsplan "Zentrum Rössli" approved
  on 16.09.2026 with the appeal period running to 25.10.2026 — and the
  superseded first notice of that Auflage reads **Publikation**. Without a
  person checking, every Amtsblatt row reads **Publikation**: the requirement
  is met partly, as far as stages are curated.
* **Ready as the fallback on this branch (not committed, not deployed) — what
  production would show without a store:** block E restricted to the parcel's municipality and the relevant
  canton-level changes; OEREBlex's regulation in force (and an edition with
  its date ahead) as its own group; a line saying the Amtsblatt is not
  connected, with the three filtered searches. The PDF says the same and
  prints one of the three, the municipality's own search. With a store, a
  publication on paper carries its date, label, title, Publ.-Nr. and a link to
  the original (and the check's note and evidence link), one row each; a row
  of any length — a long title, a long address — runs on over the pages.
* **Built on this branch on 2026-10-06 (not committed, not deployed), for
  members who may write:** "ÖREB prüfen"
  beside the CSV export, for the shortlist on screen, the cadastre only (no
  recompute), permission checked again before every write. A database another
  writer holds stops the run with a message wherever the lock meets it —
  access check, write or commit — and rolls back that parcel's write (tested
  with a second SQLite connection holding a real lock). Checked in the
  browser: live run of 16 parcels (legal status stored), simulated outage of
  100 (every earlier row byte for byte, warning shown once). Until then the
  only button sat in the hidden "Daten aktualisieren" section (`d77af13`,
  2026-09-02), where "Neu berechnen" stays for operators.
* **Built; tested with fixtures and with real publications in scratch stores;
  not configured in production — needs the store:** version 1's
  publication list. Each publication is its own row: date ("publiziert"),
  title (for a canton notice naming several municipalities, the part naming
  this one, whole and as printed — `planning.publication_title`), the badge
  **Publikation**, and the arrow to the original ("Suche ↗" for a search,
  "Link ↗" for any other page). No step is read from a title or a part's
  text. A first version did read the one step word a title named; review
  after review found wordings it turned into confident steps — "Verweigerung
  der Genehmigung" as "Genehmigung", a canton part "…; Genehmigung
  verweigert" under "Genehmigung von Sondernutzungsplänen", and more
  ("Versagung", "Aufhebung der Genehmigung", "Beschwerde gegen den
  Beschluss", "Inkrafttreten verschoben") — so it is switched off (removed,
  2026-10-05) until a source states the step. What a publication announced
  is in its title, on its date — not where the plan stands today, which the
  card says in so many words. No chaining, no status of today, no date from
  the text shown, no ÖREB matching. Canton-wide publications in their own group;
  unattributable ones counted; "none" said with the period covered. Seon's
  rows were checked in the browser against the fixture, no more. Since the
  review of 2026-10-05: a source address the URL parser rejects
  ("https://[invalid/", no host) is a problem the check reports, not an
  error that took the Analyse page down; on paper each publication is a row
  of its own, so sixty run over pages where one cell holding them broke the
  sheet — and with it the page, which builds the sheet as it draws. A sheet
  that still fails now disables its button and leaves the page up.
* **OFF — the inference path (`SCOPE_PLANNING_INFERENCE=1`, fixtures
  only):** everything below, built 2026-10-05 and reviewed five times. Each
  review found new wordings the text heuristics misread into confident
  labels. By decision (2026-10-05) no further heuristics are added to chase
  them: version 1 shows nothing of the path. With the flag off the page does
  not chain, label or match; only the fields the path reads later (step,
  dates, periods) are still parsed as the store loads, and a failure there
  reads "Verzeichnis nicht auswertbar" instead of breaking the page. Known misreads left in place include a two-digit-year
  sentence end, a lifting "für die Parzellen …" read as the whole zone, a
  zone "auf" in an unrelated plan's notice, an amendment ending a zone that
  secures a repeal, an in-force date of the base edition or of another plan
  in the same notice, a decision date taken from "gestützt auf den Entscheid
  vom …", and two unnamed "Teiländerung" notices chained into one row. Labels on evidence only — what no source shows is never read
  as its opposite: **In Kraft
  getreten** (grey) needs a stated in-force date that has passed (approval or
  Inkraftsetzung) or OEREBlex — never the parcel's ÖREB extract; **Genehmigt,
  Rechtskraft ausstehend** (yellow) needs a stated in-force date or appeal
  period still ahead, otherwise **Genehmigt, Inkrafttreten nicht belegt**;
  **Entwurf in Auflage** / **in Mitwirkung** / **in Anhörung** (amber) needs the
  stated period still running, otherwise **Stand unbestätigt**; a decision is
  **Beschlossen**, no more. **Planungszone in Kraft** (amber) follows § 29
  Abs. 2 BauG — effective with the display of its first notice, until the
  plans it secures are in force, five years at most (text checked 2026-10-05,
  SAR 713.100), or the shorter term its notice states. **Planungszone
  beendet** (grey) only on evidence: the term ran out, or the plan a notice
  links to the zone (the zone's notice says it secures it, or the plan's
  notice names the zone) is in force by a stated date. Another plan of the
  municipality is no evidence; a plan of the zone's name in force, an
  approval of its secured plan without an in-force date, a new OEREBlex
  edition while it secures the Nutzungsplanung, or five years ending on a day
  no source gives, read **Stand unbestätigt** with the reason. Four dates
  are kept apart and named: published, decided ("Beschluss vom …", only when
  the notice gives it), in force, and the day the ÖREB extract was read.
  Nothing is hidden for its age. No "Relevant" marker.
* The fallback needs no consent (the reader's browser does the reading),
  and it is not what Philipp asked for.
* With `SCOPE_PLANNING_PUBLICATIONS` pointing at a JSON store (format in
  `planning.py`), version 1 lists the publications attributed per
  municipality (the inference path, if switched on, chains, stages and scopes
  them), and `python -m planning check <file>` validates a store first —
  counting the publications per municipality (inference statuses only with
  the flag): unreadable records, notices that name a municipality without being
  attributable to exactly one, sources that are a search rather than the
  publication, duplicates from overlapping pages, a store older
  than a week, a store without `since` (the first date it covers — a daily
  subscription knows nothing from before its first day, so "none under way"
  needs that date next to it). A store can also list the `municipalities` it
  covers; a parcel elsewhere then reads "nicht erfasst", not "keine". Without
  the key a store covers every municipality — the importer leaves it out only
  for an explicit `alle` and refuses a missing, blank or `null` coverage; a
  value that is no list makes the store unreadable rather than all-covering.
* Rules the heuristics follow, each with a test that was seen to fail when
  the rule was broken (mutation runs, 2026-10-05):
  * a canton notice is split at every municipality it names, mid-sentence
    included; a part that names another municipality is reported, not
    attributed;
  * canton-wide needs the canton's rubric, a canton-wide instrument in the
    title (Baugesetz, Bauverordnung, Richtplan), and no municipality, no
    municipal plan and no Planungszone in the title; an approval only when it
    is the Richtplan's (the Baugesetz is enacted, not approved) — a citation
    of the BauG or an office name in the text does not make a notice
    canton-wide;
  * the step is read from the title only, at the furthest step it names; a
    refusal ("Nichtgenehmigung", "nicht genehmigt") anywhere in the part is no
    approval — the row says "Nichtgenehmigung" and claims no state; a
    municipality's notice of a "Genehmigung" without the canton named is its
    own decision;
  * an approval joins only a revision of the same plan; one that names no
    plan stays a row of its own; an amendment, partial revision, extension or
    repeal is its own revision, and a Planungszone's lifting is the zone's;
  * an in-force date counts only when the text looks ahead ("tritt am … in
    Kraft"), and a date in a notice shared by several municipalities is lent
    to none of them — of the shared text, only the sentence giving the appeal
    period applies to each; a display period in a sentence about another step
    is not this step's;
  * a Planungszone runs from its first notice (a later change does not start
    its five years again); it ends five years after a stated display start —
    without one, 60 days later than five years after its publication, and is
    open in between; a zone enacted again after its lifting is a new zone; a
    term its notice states ("gilt längstens bis …", read within its sentence;
    a sentence ends after a year, not after "1." or "Nr. 5") ends it and is
    not read as a display period; a lifting is read from the title, and one
    the notice qualifies ("teilweise", "Teilgebiet", "für das Gebiet") leaves
    the zone open; it ends by the plan it secures only when a notice links
    the two — the zone's notice naming, before its verb, the same kind of
    plan and its name (the Gesamtrevision for "die Revision der
    Nutzungsplanung"), or the plan's notice saying the zone ends with it,
    with nothing negated ("bleibt", "nicht", "kein") — a kind without a name
    leaves the zone open; never by an unrelated plan, a BNO change the
    edition is explained by, or another municipality's edition;
  * a decision date is read only for a decision, approval or refusal, from
    "hat [der Gemeinderat / Regierungsrat / …] am …" followed by that step's
    verb — a date inside the sentence's object ("die von der
    Gemeindeversammlung am … beschlossene Teiländerung") is not it — else
    from "Gemeindeversammlung vom …"; the latest such date, never after the
    notice; from a canton notice's opening sentence only for the plans
    listed after it; a partial revision is one with an OEREBlex edition only
    when its decision date is the edition's in-force date;
  * only the approval or the Inkraftsetzung sets an in-force date: the first
    one the notice announces that is not looked back on ("seit / nach /
    seinem Inkrafttreten") and not before the act's decision date (without
    one, not more than 90 days before the publication); a decision's date is
    shown as planned;
  * after a decision, the same or an earlier step more than 120 days later,
    or any step more than two years later, is a new procedure of the same
    name — it borrows none of the old one's dates;
  * an ÖREB title matches a plan name only as a whole ("Zentrumszone" is not
    "Zentrum"); a listing never makes a published revision in force — not
    `inForce`, not with a cited number — and leaves a revision in Mitwirkung
    or Auflage in its procedure.

What is **not** built, on purpose — separate work, not part of the UI:

1. **Production synchronization.** The explicit-run local metadata collectors
   exist (§6), alongside the importer (`amtsblatt_import.py`), but staging is not
   an import batch and no automatic promotion, scheduler or production source is
   configured. A separate opt-in local preview shows relevance/scope-reviewed
   candidates (§7); it does not establish legal coverage. An operator still establishes coverage and writes reviewed
   batches. The subscription email format remains unseen; no email adapter
   exists. Earlier reviewed publications were imported only into scratch stores
   on 2026-10-06, never production.
2. **The permission.** The Staatskanzlei's written consent to reuse the
   publications (draft request in `docs/2026-10-05-amtsblatt-freigabe-anfrage.md`,
   not sent). Philipp choosing the display is not that consent; the store stays
   unset in production until it exists.
3. **A status of today for Amtsblatt plans.** Version 1 shows each
   publication as printed and reads no step from it. Linking publications into one procedure, or a
   procedure to the document the cadastre lists, needs an identifier the
   sources do not share today; until then the inference path stays OFF.
4. **A link between a notice and an ÖREB document version.** Without one the
   extract stays a hint. A candidate, unverified: each document's file is an
   OEREBlex attachment (`TextAtWeb`, e.g. `/api/attachments/10928` for Giessi);
   if OEREBlex exposes a decree or enactment date per document, that date
   against the notice's decision date would identify the version.

## 6. Local search metadata collectors (2026-10-06)

The saved official search evidence shows two technically accessible sources:
Amtsblatt's existing filtered HTML list (Seon municipal: eleven unique notices,
ten on the initial page plus one Ajax fragment), and the canton's consultation
page widget with a read-only POST to
`https://www.ag.ch/app/search-service/api/v1/dynamiccontent` (current search:
eleven matches over two JSON pages). The consultation matches are not all
planning or building-related. Both sources work without a subscription email;
this does not resolve source reuse permission or grant permission for production
synchronization. The Amtsblatt's `robots.txt` restrictions remain unchanged.

`planning_collect.py` implements a bounded, explicitly invoked local collector
for each interface, using the existing Amtsblatt municipality/category URL
helpers. The canton collector validates the official widget's category filters,
CMS validity selection and sort before requesting JSON. Current mode preserves
the site's `validUntil > now OR (validUntil is null AND validFrom < now)`
expression; archive mode uses `validUntil < now`. The UTC timestamp is fixed
once per run. These are CMS selection semantics, not proof of an open legal
consultation or a plan's status. A future `validFrom` can still appear in current
results, and an item's URL list can include both current and archive links; the
collector chooses the link for the requested mode.

```bash
python -m planning_collect amtsblatt --municipality Seon --kind municipal --output /tmp/seon-candidates.json
python -m planning_collect amtsblatt --municipality Seon --kind canton-approvals --output /tmp/seon-canton-candidates.json
python -m planning_collect consultations --output /tmp/ag-consultation-candidates.json
python -m planning_collect consultations --mode archive --term Richtplan --max-pages 10 --output /tmp/ag-archive-candidates.json
```

Output is a versioned staging envelope (`scope.planning-candidates`) with
`candidates`, source/query parameters, `fetched_at`, counts and traversed pages.
Amtsblatt `pub_nr` and ag.ch dynamic-content UUIDs remain separate provider ID
namespaces. Stored metadata consists only of title, original publication date,
source link, authority/rubric and explicit `cms_valid_from`/`cms_valid_until`
fields when present. No teaser/article bodies, cookies or request headers are
retained. No legal stage, verified label, effective date, canton-wide scope or
municipality attribution is inferred. A searched municipality is a query hint.
Every candidate requires review for historical/legal coverage, relevance,
territorial scope and stage.

A fully traversed search is not the importer's complete evidence period.
Staging omits `records`, `since` and `stand` and the existing importer rejects it,
even when someone supplies coverage arguments. An operator must establish the
coverage period and make a separate attributed, reviewed import batch. The app,
production store and inference rules are unchanged by these collectors.

Manual search traversal defaults to five pages with a hard maximum of 50; an incomplete
search fails, rather than succeeding with partial results. Requests have a
20-second deadline and 2 MiB response limit, fixed HTTPS origins and paths,
validated redirects and no ambient proxies. Pagination/filter drift, duplicate
IDs, missing metadata, deactivated consultation records and inconsistent totals
abort. Complete snapshots replace staging files atomically; previous output
survives failure. Existing production stores/import batches, unrelated files and
output symlinks are refused. An advisory `OUTPUT.collect.lock` serializes
cooperating collectors and rejects a destination changed during collection.

Offline tests use minimized fixtures from the saved public evidence, plus
malformed/transport failure cases. HTML and this undocumented JSON interface may
change; no stable provider API contract is claimed. Technical collection is
implemented locally, while legal reuse, production synchronization and reviewed
legal stages remain outstanding.

## 7. Reviewed local preview and update path (2026-10-06)

`planning_preview.py` provides the next local step: import the three metadata
snapshots, inspect IDs/fingerprints in the review queue, apply operator decisions,
and read the resulting preview in block E with `SCOPE_PLANNING_PREVIEW` set only
for that local app process. The environment example remains empty. Exact commands
and review-batch structure are in the README. No production store, scheduler,
email subscription, legal-status inference or deployment is configured.

The preview store keeps current metadata by provider ID and tracks each distinct
search selection's membership, attempt/error and last successful fetch. Overlap
between searches deduplicates IDs. Identical metadata retains current decisions;
correction invalidates inclusion until re-review, including a later return to an
older fingerprint. Conflicting batch versions and older source/item observations
cannot overwrite newer evidence. Failed sources preserve their last good rows
and data dates, while successful empty searches are explicitly different. A
reviewed item missing from the latest searches stays visible with an absence
note, never an assumed legal-stage change. Writes validate the whole update and
replace the local store atomically under an advisory lock.

Only an operator's include decision for the current metadata version may enter
the parcel preview. It needs an exact municipality list or explicit canton-wide
scope, a link to that same source item, a short evidence-based rationale and a
review timestamp. Unreviewed, held, excluded and changed items remain in the
queue. Title suggestions help inspection only. Generic canton approval titles
may remain held until the relevant municipal part is proved; canton consultation
matches include unrelated subjects and are not automatically canton-wide planning
changes. These decisions review relevance and territory, not legal stage or
whether a designated area includes this parcel.

The panel renders the neutral **Publikation** badge, source/date, stale/failure
warnings, pending/changed counts and municipality-not-queried wording. It uses the
existing regulation-row design and original direct source links. The source's
`Korrektur` and `ursprüngliche Version` labels are retained as metadata notes:
Menziken search cards use those span labels beside a separate div with the actual
publication date. They do not establish a supersession relationship or legal
status. Existing production publications and OEREBlex remain separate; duplicate
Amtsblatt IDs are suppressed in preview. Preview rows are excluded from PDFs.
No collector runs on page render.

This is an opt-in local review path. A search snapshot is still not complete
historical/legal coverage, and current/archive CMS fields still are not legal
stages. Permission for Amtsblatt reuse and production synchronization remain
outstanding.

## 8. Prepared scheduled refresh and explicit legal source proofs (2026-10-06)

`planning_refresh.py` adds a default-off daily metadata refresh, with an
independent background hook in the existing scheduler. Personal auth/email
configuration does not gate it or wait for its bounded source requests. An
explicit municipality list, separate preview store, Swiss calendar hour and
operational reuse authorization flag/reference are required before any scheduled
provider access. The flag is an operator gate, not permission; the actual
Amtsblatt reuse basis is still outstanding and production remains disabled.
Request/page caps, a nonblocking process lock, a durable pre-fetch claim and
one-hour failure/interruption cooldown bound repeated work and restart overlap.
Current canton consultations are fetched once across the configured towns.
Each source commits independently with a fresh observation time; failures retain
last good metadata, reviews and memberships. Collection never decides relevance,
scope or legal stage. Exact environment names and check/once commands are in the
README and `.env.example`.

The separate `planning_preview verify` batch attaches optional legal evidence
to the current metadata fingerprint and reviewed municipal territory. It reuses
`planning.Verified`, its date validation and `stage_badge`; source evidence must
be the exact same publication/PDF or consultation ID, with one proof per scoped
municipality (or explicit canton-wide proof for a canton-wide include). There
is no title inference, arbitrary document join or Mitwirkung-to-Auflage
conversion. Review dates and today's badges use Europe/Zurich.

Metadata changes invalidate legal proof even when the old fingerprint returns.
Exclusion/hold or removal from reviewed territory invalidates the affected
proof permanently until a new explicit verification. Existing preview files
without proofs stay valid. Valid source facts and a safe proof link are shown
in the local row; expired/unsupported current stages remain neutral
**Publikation**. Preview rows still do not enter PDF exports. These are prepared
local capabilities, not a production enablement, legal-coverage claim or new
email subscription.

## Open questions for Philipp

* **Planned changes are requested.** Philipp has already asked for revisions
  still in consultation. Local search metadata can supply candidates (§6);
  turning them into relevant, current legal stages still requires source review.
* Should Bau- und Rodungsgesuche (building applications on and around a parcel)
  be part of this? They are a different Amtsblatt category and outside the local
  planning collectors' current scope; permission and parcel relevance would
  still need review.

## Reproducing the checks

```bash
curl -s https://oereblex.ag.ch/api/edicts.json | head -c 400
curl -s https://oereblex.ag.ch/api/towns.json | python3 -c \
  'import json,sys; t=json.load(sys.stdin); print(len(t), sum(x["has_prepublications"] for x in t))'
curl -s https://geodienste.ch/info/services.json | python3 -c \
  'import json,sys; [print(s["topic"], s["updated_at"]) for s in json.load(sys.stdin)["services"] \
   if s.get("canton")=="AG" and s["base_topic"] in ("npl_nutzungsplanung","planungszonen")]'
curl -s https://amtsblatt.ag.ch/robots.txt | head -8
```
