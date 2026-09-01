# Four-page navigation — design

Date: 2026-09-01
Status: proposed

## Why

The acquisition board shipped as a section at the foot of a single page. A
review against the design prototype found the workflow it belongs to is split
across four pages — Screening, Merkliste, Analyse, Akquisition — and that the
application has only the first and, bolted underneath it, the last.

This spec covers the navigation and the two pages that do not exist yet. It
deliberately does **not** cover the prototype's visual system.

## Scope

In scope:

- A four-page navigation control, and a router in `app.py` behind it.
- A Merkliste page: summary tiles and a shortlist table with per-lead actions.
- The Akquisition board moved onto its own page.
- The Analyse page absorbing today's early-return detail view.
- Screening: filters regrouped to the prototype's order, a parcel-number
  search, a result summary, a reset control, and a CSV export.
- A contact-list CSV export on Akquisition.
- Lucerne and Zurich shown as locked, so the canton selector says what the
  dataset does not yet cover.

Out of scope, decided rather than deferred:

- The prototype's visual system — sticky header, Instrument Sans / IBM Plex
  Mono, and the colour, spacing, border, card and badge rules. Reaching that
  through CSS injection is a different project with a different cost curve: one
  `flex-wrap` rule took three review rounds to land correctly.
- Drag-and-drop between stages. `requirements.txt` is four pure-Python
  packages; drag-and-drop needs a custom component with a Node build. The stage
  `selectbox` already performs the same state change.
- Team, roles, invitations, notification toggles and the account menu. `gate()`
  records the decision they would reverse: *"Deliberately not a login: the brief
  describes a single-user internal tool, and accounts would be more machinery
  than it is worth."* Revisiting that deserves its own ADR.
- Any change to the cascade, the residual calculation, ÖREB, or the data sheet.

## Navigation

### The control

`st.segmented_control`, not `st.tabs`. Three reasons, each measured against
Streamlit 1.61.1 rather than assumed:

1. **`st.tabs` is not lazy.** A probe with three tabs executed all three bodies
   on the first run. Analyse recomputes residual values, reads the ÖREB cache
   and can build a PDF; behind a tab it would do that on every keystroke in a
   Screening filter. A `segmented_control` plus an `if` renders one page.
2. It is the control the prototype actually draws — a rounded pill group with
   the active item raised out of a grey track.
3. Both support programmatic selection, so this is not a trade.

### Switching pages from code

An "Analyse" button on a Merkliste row or an Akquisition card has to move the
user to the Analyse page. Streamlit refuses a write to a widget-keyed value
once that widget exists this run:

    StreamlitAPIException: `st.session_state.page` cannot be modified after the
    widget with key `page` is instantiated.

So navigation carries two keys, and the request is reconciled *before* the
widget is created:

```python
PAGE = "acq_page"          # the widget's own key
PENDING = "acq_page_go"    # a request to move, set by any button

if st.session_state.get(PENDING):
    st.session_state[PAGE] = st.session_state.pop(PENDING)
st.session_state.setdefault(PAGE, "Screening")

page = st.segmented_control("Navigation", PAGES, key=PAGE,
                            label_visibility="collapsed")
```

A caller anywhere then does `st.session_state[PENDING] = "Analyse"` followed by
`st.rerun()`. Verified end-to-end under `AppTest`: the jump lands, a manual
click still works, and exactly one page body runs per rerun.

`go_to(page)` in a new `navigation.py` wraps that so no caller repeats it.

## Restructuring `app.py`

`app.py` is a linear script. Today it computes everything, then at line 260
short-circuits into the detail view:

```python
if detail.selected():
    ...render the parcel...
    st.stop()
```

That early return becomes one branch of a router. The work divides cleanly by
what each page needs:

| Stage | Needed by |
|---|---|
| load, `parcel_workflow`, land-price references, `price_of` | every page |
| filter widgets, ranking, ÖREB shortlist check, `final` | Screening only |
| `parcels` (unfiltered) | Merkliste, Analyse, Akquisition |

This matters: Merkliste and Akquisition read saved decisions joined to
`parcels`, never to the filtered `final`, so the filter widgets and the ÖREB
call can live inside the Screening branch and simply not run elsewhere. That is
most of the page's cost.

The Screening body moves into `screening.py` and Merkliste into `merkliste.py`,
matching the split that produced `acquisition.py`. `app.py` keeps loading,
navigation and the router — the shared preamble — and stops being the place
every page is written.

## Merkliste

A page, not the expander it replaced. Top to bottom:

1. **Summary tiles** — Parzellen, Summe Potenzial m², Summe Landwert CHF, Im
   Dialog. `st.metric` in four columns. "Im Dialog" counts leads whose stage is
   neither `not_contacted` nor `declined`, which is the prototype's own reading
   of the word.
2. **The shortlist table** — address, municipality, potential, reference land
   value, contact stage as an editable dropdown, last contact, and owner or
   note. `st.data_editor`, the same instrument the old panel used, so a stage
   can be corrected without opening a card.
3. **Row actions** — Analyse (navigates) and remove from the shortlist.
4. **Zur Akquisition** — a button to the board.

The page states plainly when the shortlist is empty rather than drawing an
empty frame.

## Screening

Behaviour is unchanged; presentation and two additions:

- Filters regrouped to the prototype's order: minimum potential, minimum
  utilisation figure, area from–to, municipality, property type, result count,
  then the exclusions.
- **Parzellen-Nr. suchen** — a text input matching parcel number, address or
  municipality, applied before ranking.
- **A summary line** above the table: how many parcels match, their combined
  potential, and their combined reference land value.
- **Zurücksetzen** — returns every filter to its default.
- **CSV export** — `st.download_button` over the displayed table. The
  application exports a per-parcel PDF today and no CSV at all.

### Locked cantons

The canton selector offers Aargau, Luzern and Zürich, with the latter two
disabled and labelled as not yet available. The dataset is Aargau only; a
selector that silently omits the others implies they were never planned, and
one that offers them returns an empty list that reads as a bug.

## Akquisition

The board moves to its own page unchanged, and gains a **contact-list CSV
export** — one row per saved lead with its owner, contact person, phone, email,
stage, dates, next step and note. That is the mail-merge source the prototype's
*Serienbrief* button implies, without building a letter composer.

## Testing

Every page gets an `AppTest` case asserting it renders and shows what it
should. Beyond that, the tests that matter are the ones for behaviour that
would otherwise regress silently:

- Only the selected page's body runs — assert that switching to Merkliste does
  not render Screening's result table. This is the reason for the control and
  the reason `st.tabs` was rejected; without a test it erodes.
- `go_to` moves the page and survives a rerun, including from an Akquisition
  card and from a Merkliste row.
- The parcel search narrows results, and matches on address as well as number.
- The reset control restores defaults after several filters are changed.
- Both CSV exports produce a parseable frame with the expected columns.
- The summary tiles agree with the shortlist beneath them — a tile that
  disagrees with the table under it is worse than no tile.
- Selecting a locked canton is not possible, and Aargau stays selected.

## Consequences

`app.py` stops being the whole application. It keeps the preamble every page
needs and hands off to three page modules and `detail.py`. That is the same
move `acquisition.py` already made, for the same reason: a file that renders
everything cannot be reasoned about in one piece.

The detail view stops being an early return and becomes a page with an empty
state, because a user can now select Analyse with nothing chosen. Today that
state is unreachable and therefore undesigned.
