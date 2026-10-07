# Densification Potential Finder

Finds built and vacant parcels where the zoning allows materially more
residential floor area than what stands there today, and ranks the best leads.
Built for Canton Aargau; the utilization-metric layer is canton-configurable so
Lucerne and Nidwalden can follow.

    potential = Σ(area(parcel ∩ zone_i) × figure_i) − Σ(footprint × floors)

The existing floor area is estimated from the federal building register, not the
*anrechenbare Geschossfläche* a planner would compute. The number is a floor,
not a promise, and the interface says so.

**No owner data is automatically collected or scraped.** Each row deep-links
to the AGIS geoportal, where ownership is looked up by hand with an eGovernment
login. A manually entered owner/contact name and the parcel's contact status can
then be stored in the CRM list.

## The four pages

**Screening** is the ranked hotlist and its filters. **Merkliste** totals what
has been kept and lists it. **Analyse** is one parcel on its own. **Akquisition**
is the board of owner conversations. One control switches between them, and only
the selected page runs — Analyse recomputes residual values, reads the cadastre
cache and can build a PDF, so a navigation that drew all four on every keystroke
would make the cheapest page pay for the most expensive one.

A screening run can be kept: **Suche speichern** stores the twelve filter values
under a name in `saved_searches`, beside the lead decisions and equally safe
from a recompute. Picking one back up puts the filters where they were, and
quietly skips any stored value the data no longer offers — a municipality that
has since left the results, say — rather than failing to restore anything.

## Lead workflow

The hotlist supports multi-row selection. Selected parcels can be saved to
the acquisition board or marked **Nicht interessant**, which removes them
before the next shortlist is ranked. Hidden parcels remain recoverable from the
workflow panel.

The board groups saved leads by contact stage rather than by municipality — a
lead's municipality still appears on its card, but what the work moves through
day to day is where the owner conversation stands. A lead carries one of five
stages: *Nicht kontaktiert*, *Brief versandt*, *Im Gespräch*, *Termin
vereinbart*, or *Abgelehnt*. Alongside the stage, a lead can hold a follow-up
date (*Wiedervorlage*), a last-contact date, a next step, a note, and a
contact person with phone and email. A *Fällige Wiedervorlagen* list above the
board surfaces the leads whose follow-up date needs chasing, so a promised
call-back does not depend on anyone remembering to look for it. These
decisions live in `parcel_workflow`, separate from calculated results, so a
cascade recompute cannot erase them. On Railway they share the same
persistent SQLite volume as the ÖREB cache.

Owner/contact fields save individually on change (blur or Enter). **Fertig**
closes the dialog; it is not a batch-save button. Invalid fields are not written
and remain marked until corrected, without discarding other valid edits.

## Organisation and settings

The account menu opens **Team** and **Einstellungen**. Organisation details,
planned member roles, pending invitations and setting preferences persist in
`organisation_profile` and `organisation_members` on the same SQLite volume.
Schema changes are additive and do not reseed parcel results or lead decisions.

The default remains single-organisation shared-password access. Opt-in personal
mode verifies members against **Normiq's shared Supabase Auth project** and logs
them in through **Normiq's custom passcode API** — the same six-digit code
Normiq emails via its own Resend template (no Supabase Magic Link template or
redirect URL is involved, and the shared project's email templates stay
untouched). `isSignup: false` means invitations require an existing Normiq
account. Access is granted only through local invitation membership with an
immutable provider-UUID binding, and owner/editor/reader permission checks are
enforced. See [Scope account setup](docs/scope-accounts.md) before enabling it.
Resend sends invitation notifications. Neither sends anything just by starting
the app.
In personal mode owners can enforce a TOTP second factor and switch on the
daily due-date reminder and the Monday digest (07:00 Europe/Zurich, sent by
`python -m scheduler`, which the container starts beside Streamlit). Shared
calculations remain unavailable. No example users or licensed datasets are seeded.

## Tests

Install `requirements-dev.txt` into the local virtual environment, then run:

```bash
.venv/bin/python -m pytest -q
node --test tests/calculation_table.test.cjs
```

`unittest discover` also works but collects a smaller subset, because several
tests are pytest-style. Use `pytest` for the full suite.

The extra PDF reader is for export assertions only and is not installed in the
production image. For browser tests that edit data, set `DENSIFICATION_DB` to a
temporary copy; do not write UAT contacts or invitations into the committed seed.

## Running it

```bash
.venv/bin/streamlit run app.py
```

With personal accounts (`SCOPE_AUTH_MODE=personal`) the app needs the
variables listed in `.env.example` in its process environment; it does not
read dotenv files. `scripts/run-local.sh` exports `.env.local` and starts
Streamlit on `127.0.0.1:${SCOPE_PORT:-8521}`.

`results.sqlite` is committed — 36,274 candidates across 165 municipalities
(20,659 built and 15,615 vacant) — so a fresh clone opens a working list without
downloading anything. **ÖREB prüfen**, beside the CSV export above the list,
asks the ÖREB cadastre for the shortlist on screen — the parcels without a
stored extract, and those whose extract predates the documents' legal status
(`lawstatus`) — and nothing else: it never recomputes the cascade. It is offered
only to members who may write (`scope_auth.may_write`), and
`screening.check_oereb` checks again: before the run, and — since a run takes
minutes — under each write's own lock (`scope_auth.check_transaction`), so a
member whose access is revoked meanwhile writes nothing more. Progress shows
under the page header, then what the run did: done, how many failed, nothing to
ask, or stopped. It stops — with a message, not an error page — when access is
revoked or another writer holds the database past SQLite's wait, wherever the
lock meets it (access check, write or commit); that parcel's write is rolled
back, the answers stored before it stay. A failed call — or an answer that is no
extract — leaves every cached row as it was (a failure is written only where no
row exists); an answer that still states no status is stored with a marker and
not asked again (`screening.oereb_targets`). Checked in the browser on
2026-10-06: a live run of 16 parcels stored their legal status; a simulated
cadastre outage on 100 left all 78 earlier rows byte for byte, and its warning
showed once. **Neu berechnen**, for operators in the hidden "Daten
aktualisieren" section, recomputes the cascade over the stored parcel geometry
first: about two minutes.

The **Parzellenfläche** control covers every stored area, from 109 m² to
412,503 m², and its upper end is open — *ohne Limite* rather than a number. It
used to be a fixed 300–5,000 m² slider over a database that stored exactly
300–5,000 m², so both ends were walls rather than the end of the data, and the
largest lead in the canton — Rheinfelden 574, 199,442 m² of Wohnzone B with
~108,000 m² of unused potential — could not be reached at any setting. The steps
are uneven because 95% of candidates are smaller than 3,400 m²; a linear slider
spends nearly all of its travel on the remaining 5%. Storing every area costs
5.6% more rows.

The **Grundstückstyp** filter defaults to the original built-parcel list.
**Unbebaut** means that the parcel EGRID has no standing GWR building of any
class. Parcels without an EGRID are never inferred to be vacant, and vacant
leads are limited to harmonised residential, mixed, and centre zones. The GWR
classification is still a screening signal, not a site inspection.

**Strassen-/Bahnparzellen ausblenden** is enabled by default. It uses the
official cadastral `LCSF` land-cover layer and hides a parcel only when at least
60% of its area is classified as road/path, sidewalk, traffic island, or rail.
The threshold is deliberately conservative: a normal plot with a driveway is
not treated as transport land. Rows from an older database remain visible until
the local cascade has classified them; missing data is never interpreted as a
clean result.

Each result links directly to AGIS, the ÖREB PDF, Google Maps, and the nearest
available Street View panorama. Google Maps uses the parcel's representative
LV95 point transformed to WGS84. Street View uses the selected building's GWR
entrance coordinate when available and safely falls back to the parcel point for
vacant parcels and older databases.

## Land-price references

The result table shows a rough reference price per square metre,
`parcel area × reference price`, and that reference land value divided by the
additional floor-area potential. The last figure is a screening ratio: lower is
more favourable, but it is not a project return. References are loaded from
`land_prices.csv`;
set `DENSIFICATION_LAND_PRICES` to use another file. Rows may target a BFS
municipality, municipality name, a shell-style `zone_pattern`, or a combination.
The most specific matching row wins.

The committed file contains only a transparent canton-wide fallback: CHF 950/m²,
the Wüest Partner median published by moneyland.ch for fully serviced vacant
single-family residential land with low utilization in Aargau in Q2 2021. It is
not presented as a current municipality valuation. The UI shows the reference
level explicitly as `Kanton AG`; it does not invent municipality multipliers.
Licensed Wüest Partner data can be added as rows without a code change. These
figures are screening references only; they omit the existing
building, demolition, construction, financing, tax, and site-specific costs.

Recomputing from source needs `data/`, which is gitignored at ~600 MB:

| file | source |
|---|---|
| `are_bzbauzone_*.gpkg` | AGIS — zones and their utilization figures |
| `are_Planungszonen_*.gpkg` | AGIS — planning freezes |
| `are_DNPUPolygon_*.gpkg` | AGIS — design plans, Substanz-/Volumenschutz |
| `ka_denkmalschutzobj_*.gpkg`, `ka_bauinventarobj_*.gpkg`, `dp_kurzinventarobj_*.gpkg` | AGIS — heritage registers |
| `gwr/*.csv` | `public.madd.bfs.admin.ch/ag.zip` — buildings, addresses, code table |
| `parcels_*.xml` | fetched per municipality by `ingest.py` from geodienste.ch WFS |
| `landcover_transport_*.xml` | transport classes from the official geodienste.ch AV `LCSF` WFS |

Parcels and transport land cover are fetched automatically; the rest are manual downloads.
Python 3.11 with shapely, pandas and streamlit — no PostGIS.

## Single-parcel analysis

Selecting a row in the hotlist opens that parcel on the **Analyse** page. This
used to be a conditional view rather than a page — one session-state key
decided whether the script drew the list or a single parcel — and for as long
as there were only two things to look at, that was the simpler arrangement. The
workflow has since grown a shortlist and an acquisition board, and stacking
them under the result table made one page a scroll rather than a structure. The
parcel key now decides what Analyse *shows*, not whether the list is drawn at
all, and the back link returns to whichever page the parcel was opened from.
Analyse carries five blocks:

The two halves of the screen are read against each other, so they sit side by
side in equal columns: the registers on the left, the assumptions on the right,
and the result in a bar above both — the interaction here is changing a number
and reading the new total, and a total at the foot of a long form makes that
cost a scroll each way. The bar was pinned for a while and is not any more:
Philipp asked for the pinning and then asked for it back out once he had used
it, and it costs little — the bar and every input together span 781px, so both
are on screen at once on any window taller than that. Fields the user may change
sit in a tinted panel with an accent edge; block A, which cannot be overridden,
does not. The columns stack on a narrow screen.

The calculation is not in the split. It sits below both columns on the full
width, because it does not fit in half of one: its longest line is about seventy
characters of arithmetic, which inside the right-hand column wrapped the step
names and pushed the table into a sideways scroll of its own. Putting it there
costs little, since what the eye follows while a figure is being changed is the
total, and the total is at the top of the page — on screen together with every
input on any window taller than 781px. The step-by-step is the check you read
once. Block A is the taller of the two columns by roughly 330px at 1440,
so the right-hand side ends in whitespace — deliberately, rather than filled
with something that does not belong there.

* **A · Grunddaten** — everything the pipeline already computed for this parcel,
  read-only and refetched from nothing: address, zone and utilization figure,
  area, year built, estimated existing floor area, heritage registers, ÖREB
  status, land-price reference, and the four links.
* **B · Potenzial** — the calculated floor-area potential, pre-filled and
  overridable, with the assumed unit size next to it and the resulting number of
  dwellings recalculated as either changes.
* **C · Residualwertrechnung** — sale price, construction cost, ancillary
  percentage, demolition, financing and the contingency as inputs, and every
  intermediate step of

      Landwert = Verkaufserlös − Baukosten − Baunebenkosten − Abbruch
                 − Finanzierung − Reserve

  on screen rather than only the total. Recalculated on every keystroke; there
  is no recalculate button. Hovering a step name shows the expression behind it.

  The calculation lives in `economics.PATH` as a list of rules, each carrying
  the expression that produces its number — **and that same expression is what
  gets evaluated**. The formula on hover, the help on each input ("wirkt auf
  Verkaufserlös"), and the line in the PDF are all read off it, so none of them
  can drift from the arithmetic. Philipp settled two of the rules on 2026-08-18:
  the sale price is reckoned on 80% of the floor area while the construction
  cost is reckoned on all of it, and the 15% is a contingency on the cost
  estimate — the long winter, the neighbour who objects — not a margin on
  revenue.

**Every default is a published benchmark carrying its source, and is marked
*mit Philipp zu bestätigen* until he names the figure he actually prices with.**
Sale price is a canton-wide median of the existing stock, not a new-build price
at this location; construction cost is a per-m² benchmark for condominium
new-build. Both are screening values. An overridden value says so in the export,
so a number in the document can always be traced to whose assumption it was.

Revenue and construction cost are reckoned on **different** bases, which is the
point of the sale-area share: the sale price sees 80% of the floor area, the
construction cost all of it, because everything built has to be paid for while
only the saleable part is sold. Putting both on the reduced area understates the
construction cost and overstates the land value. The one seam left is that the
construction benchmark is published per m² HNF and is applied here per m² GF.
HNF is a part of the Geschossfläche, roughly four fifths of it in housing, so
that reckons the construction cost about a quarter too high and the land value
that much too low — the tool errs toward *not* chasing a parcel. It is an
accident of the unit rather than a chosen margin, it is noted on the input, and
Philipp's own rate settles it.

* **D · Rechtsgrundlagen** — folded away by default, like *Annahmen und
  Quellen*: it is a reference to open once a candidate is worth reading up on,
  not something to scroll past on every parcel. It holds the regulations that
  actually govern the parcel,
  taken from the ÖREB extract the tool already fetches for the shortlist:
  *Rechtsvorschriften* (the zoning plan, the Erschliessungsplan, and the
  municipality's **Bau- und Nutzungsordnung**) and *Gesetzliche Grundlagen*
  (RPG, BauG, BauV …), each linking to the document itself on `oereblex.ag.ch`
  or `gesetzessammlungen.ag.ch`. Block A gains the extract's own
  *Legende beteiligter Objekte* — every plan object touching the parcel with its
  area and share — and the land registry's area next to the one this tool
  computes from the geometry, with the difference stated when there is one.

  This needs no name matching: the cadastre names the documents for *this*
  EGRID, and the BNO's official number is the municipality's BFS number. One
  request answers both "is this parcel excluded" and "which rules apply".
* **E · Regulatorische Änderungen** — the parcel's municipality and the
  relevant canton-level changes, nothing else: Philipp asked for exactly that on
  2026-10-05, and a list of 227 regulations in force across Aargau is not a list
  of changes to this parcel. A canton notice counts only for the municipality it
  names; another municipality's project is not this parcel's because the canton
  published it.

  **Version 1 (the default)** shows two things side by side and draws no
  conclusion from one to the other: OEREBlex's rules in force, and the
  Amtsblatt's publications as printed. Philipp's three labels (2026-10-05)
  appear only where a source supports them on the day shown: **In Kraft
  getreten** on OEREBlex rows, and on a publication checked as in force;
  **Entwurf in Auflage** while a checked display period runs; **Genehmigt,
  Rechtskraft ausstehend** while a checked approval's appeal period, or a
  stated date of legal force, lies ahead. Nothing is read from a title for it.
  A person records the stage per publication and Gemeinde, with the source,
  the date and how it was checked (`planning.Verified`, in the store's
  `verified`), and the panel shows the badge only inside the window that
  source supports (`planning.stage_badge`). Outside it — and for every row no
  one checked — the row reads **Publikation**, with what is known: "Öffentliche
  Auflage … beendet — heutiger Stand nicht geprüft", "Genehmigt am … —
  Rechtskraft nach dem … nicht geprüft", "Genehmigung verweigert am …". A
  canton notice for several Gemeinden is one row per Gemeinde, each with its
  own check: one Gemeinde's approval is never another's. An OEREBlex edition
  with its date ahead reads **Künftig in Kraft** (our wording). So Philipp's
  labels work as far as stages are checked; no source states them on its own
  yet (below).

  * **Vorschriften (OEREBlex)** — the municipality's regulation in force with
    its date (fetched once every twelve hours for the canton, filtered here):
    **In Kraft getreten** (grey), or, for an edition OEREBlex lists with its
    date ahead, **Künftig in Kraft** (yellow) — the date is all OEREBlex says,
    not that an approval is final.
  * **Publikationen im Amtsblatt** — each publication its own row, as printed:
    its date (captioned "publiziert"), its Publ.-Nr., its title (for a canton
    notice naming several municipalities, the part naming this one, whole and as
    printed — 'Gestaltungsplan "Giessi"; Genehmigung verweigert' — with the notice's own
    title beside it; `planning.publication_title`), the badge **Publikation**,
    and an arrow (↗) to the original; a source that is only a search opens as
    **Suche ↗**, any other page as **Link ↗**. No step is read from a title
    or from a part's text: reading it from the wording kept turning refusals
    and negations into confident steps ("Verweigerung der Genehmigung" showed
    "Genehmigung"), so version 1 claims none until a source states it. What a
    publication announced is in its title, on its date — not where the plan
    stands today: an Auflage published last year does not show that anything
    is open now, and the card says so. Publications are not chained into one
    revision, no status of today is inferred, no date from their text is
    shown, and the parcel's ÖREB extract is not matched to them. (The fields
    only the inference path uses are still parsed when the store loads; should
    that fail, the card reads "Verzeichnis nicht auswertbar" and the page stays
    up.) Canton-wide changes (a title naming the
    Baugesetz, the Bauverordnung or the Richtplan, and no municipality,
    municipal plan or zone) follow in their own group. A publication that
    names the municipality but cannot be attributed is counted ("nicht
    zuordenbar"), and "none" is said with the period the directory covers.

  Everything but the OEREBlex rows comes from the Amtsblatt (facts as of
  2026-10-06 in `NEWSFEED.md` §5): its search and publications are disallowed
  to every crawler in `robots.txt`, its legal notice reserves reuse beyond the
  legally permitted cases to the Staatskanzlei's written consent, and it
  documents no API, feed or data export — only the website, a search
  subscription mailed daily or weekly, and a PDF export. The explicit-run local
  metadata collectors described below can read the search interfaces; their
  staging output is separate from this panel and its production store.
  Without a store the card shows a **temporary fallback**: a line saying so and
  the Amtsblatt search filtered to the municipality and the canton. With a
  store (`SCOPE_PLANNING_PUBLICATIONS`) the publication rows appear, filled by
  `python -m amtsblatt_import` from a batch an operator wrote (below); the card
  says what the list covers and since when, and warns when it is more than a
  week old or its last update failed — the last valid list stays. Tested with
  real publications for Seon, Oberrüti, Killwangen and Hallwil on 2026-10-06
  (`NEWSFEED.md` §5); the store is not configured in production.

  **The inference path is OFF.** It chains publications into revisions,
  derives a status of today from them (Philipp's "Entwurf in Auflage" and
  "Genehmigt, Rechtskraft ausstehend" for Amtsblatt plans, "Beschlossen",
  "Planungszone in Kraft/beendet", "Stand unbestätigt"), reads decision and
  in-force dates from the text, ends Planungszonen by the plan they secure and
  matches the parcel's ÖREB extract by name. Its text heuristics kept giving
  confident labels from sentences they misread — four review rounds, each
  finding new wordings (`NEWSFEED.md` §5) — so it is not part of version 1. It
  runs only with `SCOPE_PLANNING_INFERENCE=1`, for testing against fixtures;
  its rules are in `NEWSFEED.md` §5 and `planning.py`.

  The join is **by municipality name, never by number**. `syst_nr` looks like
  the BFS number and equals it in 162 of our 163 municipalities — Dintikon is
  4196 in OEREBlex against BFS 4194 in the building register. Keying on it would
  have attached a neighbour's building regulation to those parcels. Names match
  on all 163; the number is a cross-check, and a disagreement is printed rather
  than resolved silently.

  A failed fetch says so, with the reason. An empty list would read as "nothing
  has changed lately", which is the opposite of "the canton did not answer" —
  and block D is unaffected either way, since it comes from the parcel's own
  extract. The same holds for procedures: "not tracked", "could not read the
  store" and "none" are three different lines.

**Als PDF exportieren**, top right beside the back link, writes blocks A–D, the
whole calculation path, and block E as text to a data sheet. Block E prints the
edition of the building regulation the sheet assumed and any edition approved
for later, the publications as printed — one row each, with date, Philipp's
label where a checked stage supports it on the print date (else
"Publikation"), title, Publ.-Nr., a link to the original, and the check's note
with its evidence link; a row of any length runs over pages — with a line
saying that a step is shown only when checked and supported on that date and
that the list is not today's state, canton-wide publications, publications
that could not be attributed, how old the list is and whether its last update
failed — or that the Amtsblatt was not tracked, with the address to check. A printed analysis that
does not say which rules it assumed cannot be checked a year later, which is
why the not-found and could-not-ask cases print a line of their own rather than
the block quietly disappearing. The sheet is built while the page draws; should
it fail, the button is disabled and names the error, and the analysis on screen
stays as it is.

Economic edits in the detail calculation live in the session and are gone on
reload. The lead workflow is different: saved/hidden decisions, manually entered
owner names, and contact status persist in SQLite. Economic assumptions are split
by whose they are: the **economic assumptions** in block C are the user's and
hold for every parcel in the session — a developer's construction cost does not
change because they clicked a different row — while **potential and demolition**
belong to the parcel and stay with it for the session. Keeping the first group per
parcel would mean retyping seven numbers on every lead.

CHF amounts in the calculation table can also be overridden directly: click an
amount, then use Enter or leave the field to save; Escape cancels. Swiss grouping,
CHF prefixes and decimal commas are accepted. Emptying the field restores its
formula. Overrides are isolated to the current parcel for this session; dependent
costs, the result strip and the PDF all use the same effective amounts. The green
override reset clears only these amounts; **Standardwerte** also restores the
input assumptions. This does not change Philipp's reserve-on-costs or unit-count
formulas (explicitly reconfirmed by the user on 2026-09-05).

## Layout

    app.py          the interface: filters, Run button, ranked table
    workflow.py     persistent saved/hidden leads and owner-contact status
    detail.py       the single-parcel analysis view — blocks A, B, C
    economics.py    residual land value, its benchmarks and their sources
    report.py       the parcel data sheet as a PDF
    formatting.py   register vocabulary shared by the list and the data sheet
    ingest.py       canton-wide pass; writes results.sqlite
    cascade.py      the filter cascade as a reusable engine
    metrics.py      utilization metrics per canton — the AZ/ÜZ/BMZ seam
    constraints.py  heritage registers, planning freezes, design plans, age
    oereb.py        per-parcel cadastre of public-law restrictions
    links.py        AGIS/Google links and LV95 → WGS84 conversion
    land_prices.py  municipality/zone reference lookup
    land_prices.csv configurable reference values and canton fallback
    potential.py    geometry + the formula, validated against the cadastre
    run.py          single-municipality CLI, prints what each step removes
    slice.py        the first naive version, kept so the corrections stay visible

## Notes worth keeping

Each of these cost a wrong answer first.

* **Parcels have holes.** Taking only the exterior ring overstated one parcel by
  28%. Geometry now matches the official cadastre to 0.00% on spot checks.
* **The utilization figure is not in the harmonized national feed.** It lives in
  the cantonal `are_bzbauzone` extract, and 31 of 196 municipalities publish none
  at all — they are absent, and the interface states that rather than implying
  coverage it does not have.
* **The figure applies to the part of a parcel inside the building zone**, not
  the whole parcel, and a parcel can span zones with different figures.
* **Three metrics, three different calculations.** An Ausnützungsziffer
  multiplies straight into floor area. An Überbauungsziffer bounds the footprint
  and says nothing about floor area without a floor count — which Aargau omits on
  10 of its 22 such zones. A Baumassenziffer would need a storey height stacked
  on top, so it is recognised and deliberately not converted. Anything
  unconvertible is reported as *not assessable, with the reason*.
* **Heritage is spread over four registers that barely overlap**, and the
  strongest constraint — Substanz-/Volumenschutz — is in the zoning overlay, not
  a register. ÖREB does carry the hard tiers, inside the `Nutzungsplanung` theme
  rather than as themes of their own, so scanning theme titles misses them. It
  does not carry the advisory inventories at all.
* **Nearly half of Aargau's planning freezes are not exported to ÖREB** (20 of
  38), so that check runs against the local layer and ÖREB is the safety net
  behind it, not the source.
* **The AGIS map link has no EGRID parameter.** The parcel card opens from a
  simulated click at an LV95 coordinate: `…&center=E,N&z=13&info=E,N,2`.
* **Vacant is not the same as “no target-class house”.** Vacancy is checked
  against every standing GWR building class before a zero-existing-area parcel
  is admitted. This avoids calling workshops, barns, and incompletely measured
  buildings empty.

## Regulation changes as a feed

`NEWSFEED.md` records what is actually available if the tool is to show building
regulation changes next to a parcel: `oereblex.ag.ch` answers "which BNO governs
this municipality, in force since when, PDF here" for the whole canton in one
request. The Amtsblatt carries notices of plans decided or under way; the canton
also publishes consultation search metadata on ag.ch. Amtsblatt terms reserve
reuse beyond the legally permitted cases to the Staatskanzlei's written consent;
whether Scope's use is such a case remains an open legal question
(`NEWSFEED.md` §§5–6).
Block E is built on both: OEREBlex live, the Amtsblatt as links until a
publication store is configured. A store is filled by the importer from a CSV or
JSON batch — one row per publication and Gemeinde, only number, office,
Gemeinde, date, title, link and an optional checked stage; the contract is in
`amtsblatt_import.py`:

```bash
.venv/bin/python -m amtsblatt_import path/to/publications.json batch.csv --von 2026-09-01 --bis 2026-10-05 --gemeinden Seon,Oberrüti --quelle "manuell aus amtsblatt.ag.ch"
```

It replaces the store only with a valid one, atomically: a batch that would
leave a gap after the store's `stand`, covers other Gemeinden, names none (a
missing, blank or `null` coverage — only an explicit `alle` covers every
Gemeinde), contradicts itself or carries one bad row changes nothing, and every
attempt is recorded beside the store (`publications.json.status.json`) for the
panel to show. Run twice, it adds nothing; a corrected row replaces its
predecessor. No adapter for the subscription e-mail exists: nobody here has seen
one. Check a store before pointing the app at it:

```bash
.venv/bin/python -m planning check path/to/publications.json
```

It fails on what would make the panel wrong rather than incomplete: a record it
cannot read, a notice it cannot attribute to exactly one municipality, a
publication imported twice (overlapping pages), a store more than a week old, a
store that does not say since when it looks (`since`) — without that, "no
procedure under way" means nothing.

### Local planning-source candidate collectors

`planning_collect.py` provides explicit-run metadata collection without an email
subscription. It reads the municipality-filtered Amtsblatt search HTML and the
canton's consultation widget/search JSON interface. It follows search pagination
only, without fetching notice details or PDFs:

```bash
python -m planning_collect amtsblatt --municipality Seon --kind municipal --output /tmp/seon-candidates.json
python -m planning_collect amtsblatt --municipality Seon --kind canton-approvals --output /tmp/seon-canton-candidates.json
python -m planning_collect consultations --output /tmp/ag-consultation-candidates.json
python -m planning_collect consultations --mode archive --term Richtplan --max-pages 10 --output /tmp/ag-archive-candidates.json
```

Unknown municipalities are rejected. The default limit is five search-result
pages, configurable with `--max-pages` up to a hard cap of 50 for explicit manual reads; a larger search
fails explicitly. Each request has a 20-second deadline (`--timeout`, at most
120 seconds) and a 2 MiB response limit. Only the fixed official HTTPS search
origins/paths are allowed; redirects preserve selection filters and cannot turn
the consultation POST into GET. Ambient proxy settings and cookies are unused.
No scheduler, app integration or production synchronization is configured.

The versioned `scope.planning-candidates` envelope contains `candidates`, source
and query parameters, one UTC run timestamp, counts and page traversal metadata.
It preserves provider IDs in distinct namespaces: Amtsblatt publication numbers
and ag.ch dynamic-content UUIDs. Only title, source link, original publication
date, authority/rubric, any official publication version marker and the
consultation's explicitly named CMS validity
fields are retained. Teaser/article bodies and request/response headers are not
stored. A municipality search is a query hint, not evidence of territorial scope;
consultation matches include subjects unrelated to building regulation. CMS
`cms_valid_from`/`cms_valid_until` and current/archive placement do not establish a legal
stage, appeal deadline or entry into force.

Every candidate needs review of relevance, territorial scope and legal stage.
Traversing every result page establishes a search snapshot, not complete
historical/legal coverage. Staging has no `records`, `since` or `stand` and is
rejected by `amtsblatt_import`; an operator must separately prepare an attributed,
reviewed batch and justify its coverage period. The collectors refuse existing
production stores/import batches, unrelated output files and output symlinks.
They atomically replace only a complete validated snapshot, leaving an earlier
snapshot unchanged on fetch, parser, pagination or write failure. An advisory `OUTPUT.collect.lock` serializes replacement by cooperating
collectors; a destination changed during collection is refused.

These HTML and undocumented JSON interfaces can change; schema/filter drift
fails explicitly. Technical access does not resolve the Amtsblatt's robots
restrictions or reuse permission. See `NEWSFEED.md` §6. Offline regression tests
use minimized public search fixtures:

```bash
python -m pytest -q tests/test_planning_collect.py tests/test_amtsblatt.py tests/test_amtsblatt_import.py tests/test_planning.py
```

### Opt-in local panel preview

`planning_preview.py` keeps collector metadata in a separate local preview store.
The app reads it only when `SCOPE_PLANNING_PREVIEW` is set in the local process;
the example configuration leaves this empty. Import the three reviewed search
snapshots, inspect the queue, and apply explicit relevance/territory decisions:

```bash
python -m planning_preview import /tmp/scope-planning-preview-20261006/preview.json /tmp/scope-seon-municipal-candidates-20261006.json /tmp/scope-seon-canton-candidates-20261006.json /tmp/scope-ag-consultation-candidates-20261006.json
python -m planning_preview queue /tmp/scope-planning-preview-20261006/preview.json
python -m planning_preview review /tmp/scope-planning-preview-20261006/preview.json /tmp/scope-preview-reviews.json
SCOPE_PLANNING_PREVIEW=/tmp/scope-planning-preview-20261006/preview.json python -m streamlit run app.py
```

Create the output directory beforehand. `queue` prints each original source ID,
current metadata fingerprint, source link, review state and a conservative title
suggestion. Suggestions never include an item automatically. `queue --all`
also lists current include/exclude decisions. The review batch is a JSON list;
copy the exact `source_id` and `fingerprint` from the queue, inspect the original,
and supply a current UTC review timestamp. For example, the shape is:

```json
[
  {
    "source_id": {"namespace": "amtsblatt.ag.ch:pub_nr", "value": "00.102.603"},
    "fingerprint": "<exact current fingerprint from queue>",
    "action": "include",
    "municipalities": ["Seon"],
    "canton_wide": false,
    "evidence_url": "https://amtsblatt.ag.ch/ekab/00.102.603/publikation/",
    "rationale": "<short evidence-based relevance and territory reason>",
    "reviewed_at": "<actual ISO timestamp with timezone>"
  }
]
```

This template is not a prepared review decision. Include requires known exact
municipalities or explicit `canton_wide: true` with an empty municipality list,
plus evidence for that same source item and a short rationale. A publishing
canton office, municipality query or planning-like title alone proves neither
relevance nor territory. `exclude` and `hold` require evidence/rationale/timestamp
but assert no territory; they remain outside the parcel preview. Reviews verify
relevance and municipal/canton scope only, never legal stage or parcel coverage.

Only includes pinned to the current metadata version appear in block E, with a
neutral **Publikation** badge unless separately verified below, and original
title/date/source. Official `Korrektur` or
`ursprüngliche Version` markers are shown as source notes; no relationship or
legal consequence is inferred. Original ag.ch consultation links open the item
directly. Existing OEREBlex rows and production publications continue to work;
the same Amtsblatt ID is shown once when it is in both stores. The local preview
is excluded from PDF exports.

An explicit refresh is available; it makes no requests during page rendering:

```bash
python -m planning_preview sync /tmp/scope-planning-preview-20261006/preview.json --municipality Seon --max-pages 5 --timeout 20
```

This refreshes municipal notices, canton approval matches for Seon and current
canton consultations. Each selection records its attempt, last successful search
and membership. A failed source exits nonzero and visibly retains its last good
data/date; a new failed source has no fabricated success. Empty search,
unqueried municipality, pending/changed reviews, stale searches (over seven
days) and failed fetches are distinct. Missing from a newer search keeps reviewed
last-known metadata with an absence note; it does not mean approved, withdrawn
or in force. Repeated identical snapshots deduplicate and preserve reviews;
corrected metadata invalidates earlier reviews until checked again. Older or
conflicting versions are rejected rather than replacing newer evidence.

The bounded JSON store uses atomic replacement and an advisory lock; corrupt,
unrelated, production-schema and symlink destinations are refused. Preview and
scheduled collection remain disabled in the production configuration. Amtsblatt
reuse permission remains unresolved.

Explicit legal verification is a separate operator command:

```bash
python -m planning_preview queue /tmp/scope-planning-preview-20261006/preview.json --all
python -m planning_preview verify /tmp/scope-planning-preview-20261006/preview.json /tmp/scope-preview-legal-verifications.json
```

The batch is a JSON list. This is a shape template, not a legal decision:

```json
[
  {
    "source_id": {"namespace": "amtsblatt.ag.ch:pub_nr", "value": "<exact reviewed pub_nr>"},
    "fingerprint": "<exact current fingerprint from queue>",
    "municipality": "Seon",
    "verified": {
      "stage": "genehmigt",
      "source": "<same item publication or PDF URL>",
      "verified_on": "<actual review day YYYY-MM-DD>",
      "method": "<exact municipal section and source facts checked>",
      "approved_on": "<actual approval date in source YYYY-MM-DD>"
    }
  }
]
```

Supply one proof per municipality in the current relevance-review scope.
Alternatively, `canton_wide: true` replaces `municipality` only when the current
include review is explicitly canton-wide. Held, excluded, unreviewed, stale,
duplicate or unscoped proofs are refused as a whole batch. Evidence must be the
same Amtsblatt ID's publication/PDF or the same ag.ch consultation ID; arbitrary
document joins are not supported. `verified_on` is the actual Swiss review day,
on or after the current metadata day and no later than the operation day.

The existing `planning.Verified` verifier requires `auflage_from`/`auflage_to`
for `auflage`, `approved_on` for `genehmigt`, `effective_on` for `in_kraft`, and
`decided_on` for `verweigert`. Optional `appeal_until` and `rechtskraft_on` must
be proved by the source. Its existing `stage_badge` shows a display/approval
badge only while the checked dates support it today; expired periods or an
approval without proved pending legal force remain **Publikation**, with the
verified facts and source link in the note. Current Mitwirkung is never
automatically Auflage. CMS validity is never legal validity.

Metadata edits permanently invalidate legal proof, including a later return to
the same fingerprint. Exclusion, hold or territory removal invalidates the
affected proof. Re-inclusion or a rationale change cannot revive it; a new
explicit verification is required. Proof for one municipality is never exposed
to another. Existing version-1 preview files without legal proofs remain valid,
and all preview rows remain excluded from PDF export.

`planning_refresh.py` is the prepared daily metadata scheduler. The existing
`python -m scheduler` sidecar launches one background refresh worker independently
of personal authentication and email settings. Collection remains **off** by
default. Enabling it requires an absolute `SCOPE_PLANNING_PREVIEW` path, an
existing directory, 1–10 explicit comma-separated
`SCOPE_PLANNING_REFRESH_MUNICIPALITIES`, `SCOPE_PLANNING_REFRESH_ENABLED=1`, and
both `SCOPE_PLANNING_REUSE_AUTHORIZED=1` and a nonempty
`SCOPE_PLANNING_REUSE_REFERENCE` documenting an obtained operational source-use
basis. Setting these fields does not itself obtain permission. Do not enable
them while that basis remains unresolved.

The daily hour defaults to 08:00 Europe/Zurich (including DST), with
`SCOPE_PLANNING_REFRESH_HOUR` in 0–23. `SCOPE_PLANNING_REFRESH_MAX_PAGES` defaults
to 5 (at most 20 per source and 100 search pages for the whole pass);
`SCOPE_PLANNING_REFRESH_TIMEOUT` defaults to 20 seconds (at most 30). Each town
gets its municipal and canton-approval searches; current canton consultations
are fetched once for the entire pass. Each source has a fresh observation time
and commits independently, so a failed source preserves its last good data,
memberships and reviews. Sources still require manual queue review and legal
verification. No provider bodies or credentials are written to scheduler logs.

`STORE.refresh.json` records the durable daily claim, completion counts and retry
time; `STORE.refresh.lock` takes a nonblocking process lock. Successful passes
are skipped for the same configuration/Swiss day after restarts. Failure or
interrupted claims have at least a one-hour cooldown; completed failures wait
one hour from completion. One refresh worker per scheduler loop avoids delaying
reminders/digests. Existing explicit `planning_preview sync` is unchanged.

```bash
python -m planning_refresh --check
python -m planning_refresh --once
```

`--check` makes no requests or disk mutations. Disabled configuration reports
`off`; enabled configuration validates required gates and local file boundaries.
`--once` only runs when enabled, authorized and due; failures exit nonzero and
remain visible in the local source/store bookkeeping. This prepares a controlled
local/operational path; it is not a production activation or permission grant.

Verify locally with:

```bash
python -m pytest -q tests/test_planning_preview.py tests/test_planning_refresh.py tests/test_scheduler.py tests/test_detail_preview.py tests/test_detail.py tests/test_planning.py tests/test_amtsblatt_import.py tests/test_planning_collect.py
```

## Deviations from the brief

* **SQLite, not Supabase/PostGIS.** Geometry is only needed while resolving the
  parcel/zone intersections; afterwards the result is a plain table. Revisit when
  the app is hosted for Philipp rather than run locally.
* **Parcels come from the geodienste.ch WFS, not INTERLIS via `ili2pg`.** Same
  data, one less conversion step.
* **Advisory inventories are flagged, not excluded** — confirmed with Philipp.
  Hard protection is excluded outright.
* **A failed cadastre call is retried, not remembered as an answer.** One
  transient 502 used to be cached forever: the parcel stayed unchecked while the
  interface called the shortlist complete. A cached row now counts as complete
  only if it carries the extract itself.
* **The "Analyze" control is the row selection, not a link in the row.**
  Streamlit cannot run a callback from a cell, so a link column could not open
  the detail view. Selecting the row does it in one click, in the row, which is
  where the brief puts it.
