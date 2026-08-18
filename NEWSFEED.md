# Regulation changes as a feed — what is actually available

The question: can new canton and municipality publications be pulled into the
tool, so that a parcel under analysis shows the building-regulation changes that
affect it?

Short answer: **yes, but not from the Amtsblatt.** The canton's official gazette
is the wrong door — it forbids automated access and publishes prose. The right
door is `oereblex.ag.ch`, the canton's own legal-document platform, which already
feeds the ÖREB cadastre this tool queries. It answers "which building regulation
governs this parcel, since when, and here is the PDF" in one request for the
whole canton.

Everything below was checked on 2026-08-18 against the live services.

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
  about 07:00. Free, needs an account. This is the sanctioned channel for early
  warning, and it lands in Philipp's inbox, not in the tool.
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

**Stage 3 — a canton-wide "was ist neu" panel.** The same table, sorted by
`inaction_date`: the 9 municipalities that changed their BNO in 2026 and when.
That is also the signal for when a full recompute is worth running.

**Stage 4, only if Philipp wants the early warning** — an Amtsblatt
Suchabonnement on `Bau- und Nutzungsordnung` and `Raumplanung`, delivered to his
inbox. Manual by design; a revision at public-consultation stage is months of
lead time on everything above, and no automated route to it exists.

## Open questions for Philipp

* Is "in force since" enough, or does the tool need to flag revisions still in
  consultation? Only the Amtsblatt subscription can answer the second, and only
  by email.
* Should Bau- und Rodungsgesuche (building applications on and around a parcel)
  be part of this? It is the same closed door — Amtsblatt only — but it is the
  strongest signal that a neighbour is already moving.

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
