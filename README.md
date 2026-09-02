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

## Running it

```bash
.venv/bin/streamlit run app.py
```

`results.sqlite` is committed — 36,274 candidates across 165 municipalities
(20,659 built and 15,615 vacant) — so a fresh clone opens a working list without
downloading anything. **Neu berechnen** re-runs the cascade over the stored
parcel geometry and then queries the ÖREB cadastre for the shortlist: about two
minutes.

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
* **E · Neueste Änderungen** — what block D cannot say: *since when*. The ÖREB
  extract marks each document `inForce` and carries no date. `oereblex.ag.ch` —
  the same platform block D's links point into — answers that for the whole
  canton in one 76 KB request, so the panel opens with this parcel's own
  regulation and its in-force date, then the three most recent changes anywhere
  in Aargau, with the remaining ~224 folded away. Fetched once every twelve
  hours; one to two municipalities put a new BNO in force per month.

  The join is **by municipality name, never by number**. `syst_nr` looks like
  the BFS number and equals it in 162 of our 163 municipalities — Dintikon is
  4196 in OEREBlex against BFS 4194 in the building register. Keying on it would
  have attached a neighbour's building regulation to those parcels. Names match
  on all 163; the number is a cross-check, and a disagreement is printed rather
  than resolved silently.

  A failed fetch says so, with the reason. An empty change list would read as
  "nothing has changed lately", which is the opposite of "the canton did not
  answer" — and block D is unaffected either way, since it comes from the
  parcel's own extract. The panel records what is **in force**; revisions still
  in consultation appear only in the Amtsblatt, which forbids automated access
  (`NEWSFEED.md`).

**Als PDF exportieren**, top right beside the back link, writes blocks A–D, the
whole calculation path, and the parcel's own regulation with its in-force date
to a data sheet — one page, two once several assumptions carry an overridden
value and its source. Only that one line of block E goes on the paper: the
canton-wide change list is news rather than a fact about this parcel, and 227
rows would bury the sheet. A printed analysis that does not say which edition of
the building regulation it assumed cannot be checked a year later, which is why
the not-found and could-not-ask cases print a line of their own rather than the
block quietly disappearing.

Economic edits in the detail calculation live in the session and are gone on
reload. The lead workflow is different: saved/hidden decisions, manually entered
owner names, and contact status persist in SQLite. Economic assumptions are split
by whose they are: the **economic assumptions** in block C are the user's and
hold for every parcel in the session — a developer's construction cost does not
change because they clicked a different row — while **potential and demolition**
belong to the parcel and stay with it for the session. Keeping the first group per
parcel would mean retyping seven numbers on every lead.

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
regulation changes next to a parcel: the Amtsblatt has the right content but
forbids automated access, while `oereblex.ag.ch` answers "which BNO governs this
municipality, in force since when, PDF here" for the whole canton in one request.
Findings, measured volumes, the one join that does not hold, and a staged
proposal are in that file. Nothing is wired into the app yet.

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
