# Densification Potential Finder

Finds parcels where the zoning allows materially more residential floor area than
what stands there today, and ranks the best leads. Built for Canton Aargau; the
utilization-metric layer is canton-configurable so Lucerne and Nidwalden can
follow.

    potential = Σ(area(parcel ∩ zone_i) × figure_i) − Σ(footprint × floors)

The existing floor area is estimated from the federal building register, not the
*anrechenbare Geschossfläche* a planner would compute. The number is a floor,
not a promise, and the interface says so.

**No owner data is collected or scraped.** Each row deep-links to the AGIS
geoportal, where ownership is looked up by hand with an eGovernment login. That
step stays manual by design.

## Running it

```bash
.venv/bin/streamlit run app.py
```

`results.sqlite` is committed — ~20,600 assessed candidates across 165
municipalities — so a fresh clone opens a working list without downloading
anything. **Neu berechnen** re-runs the cascade over the stored parcel geometry
and then queries the ÖREB cadastre for the shortlist: about two minutes.

Recomputing from source needs `data/`, which is gitignored at ~600 MB:

| file | source |
|---|---|
| `are_bzbauzone_*.gpkg` | AGIS — zones and their utilization figures |
| `are_Planungszonen_*.gpkg` | AGIS — planning freezes |
| `are_DNPUPolygon_*.gpkg` | AGIS — design plans, Substanz-/Volumenschutz |
| `ka_denkmalschutzobj_*.gpkg`, `ka_bauinventarobj_*.gpkg`, `dp_kurzinventarobj_*.gpkg` | AGIS — heritage registers |
| `gwr/*.csv` | `public.madd.bfs.admin.ch/ag.zip` — buildings, addresses, code table |
| `parcels_*.xml` | fetched per municipality by `ingest.py` from geodienste.ch WFS |

Only the parcels are fetched automatically; the rest are manual downloads.
Python 3.11 with shapely, pandas and streamlit — no PostGIS.

## Layout

    app.py          the interface: filters, Run button, ranked table
    ingest.py       canton-wide pass; writes results.sqlite
    cascade.py      the filter cascade as a reusable engine
    metrics.py      utilization metrics per canton — the AZ/ÜZ/BMZ seam
    constraints.py  heritage registers, planning freezes, design plans, age
    oereb.py        per-parcel cadastre of public-law restrictions
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

## Deviations from the brief

* **SQLite, not Supabase/PostGIS.** Geometry is only needed while resolving the
  parcel/zone intersections; afterwards the result is a plain table. Revisit when
  the app is hosted for Philipp rather than run locally.
* **Parcels come from the geodienste.ch WFS, not INTERLIS via `ili2pg`.** Same
  data, one less conversion step.
* **Advisory inventories are flagged, not excluded** — confirmed with Philipp.
  Hard protection is excluded outright.
