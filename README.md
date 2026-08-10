# Densification Potential Finder — Canton Aargau

Finds parcels where the zoning allows materially more residential floor area
than what stands there today.

    potential = SUM(area(parcel ∩ building_zone_i) × AZmax_i) − SUM(footprint × floors)

## Layout

    potential.py    geometry + the corrected formula, validated against the cadastre
    constraints.py  heritage registers, planning freezes, design plans, building age
    oereb.py        per-parcel cadastre of public-law restrictions
    run.py          the seven-step filter cascade for one municipality
    slice.py        the first naive version, kept so the corrections stay visible

## Data

`data/` is gitignored. Everything in it is a public download; see `fetch.py`.

## Notes worth keeping

* The AZ is **not** in the nationally harmonized zoning feed. It only exists in
  the cantonal `are_bzbauzone` extract, and only for 163 of 196 municipalities.
* Parcels have holes. Taking only the exterior ring overstated one parcel by 28%.
* The AZ applies to the part of a parcel inside the building zone, not the whole
  parcel — and a parcel can span two zones with different AZ.
* Heritage is spread over four registers that barely overlap, and the strongest
  constraint (Substanz-/Volumenschutz) is in the zoning overlay, not a register.
* Nearly half of Aargau's planning freezes are not exported to ÖREB, so that
  check is done locally.
