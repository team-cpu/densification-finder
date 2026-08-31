# Akquisition CRM — design

Date: 2026-08-31
Status: approved, not yet planned

## Why

The lead workflow today stores four facts about a saved parcel: whether it is
saved, whether it is hidden, a manually entered owner name, and one of four
contact statuses. That is enough to mark a lead and forget about it. It is not
enough to run an acquisition: there is no record of when the owner was last
contacted, when to come back, what the next step is, or who to call.

The "Parcel Potential" design canvas answers exactly that gap with an
*Akquisition* view — five stages, a follow-up date per lead, and a list of
follow-ups that have come due. This spec ports that one view into the existing
Streamlit application. Nothing else from the canvas is in scope.

## Scope

In scope:

- Five contact stages instead of four.
- Follow-up date (*Wiedervorlage*), last-contact date, next step, note, and
  contact person / phone / email per saved lead.
- A *Fällige Wiedervorlagen* list of leads whose follow-up date has passed.
- A stage-column board replacing the municipality-grouped saved-leads panel.
- A migration that upgrades an existing database without losing leads.

Out of scope, deliberately:

- The canvas's four-tab navigation. The application keeps its single-page
  structure and the one session-state key that opens a parcel on its own.
- Restyling the Screening filter bar or the result table.
- A separate Merkliste view. The board is the saved-leads view.
- Contact-list export and *Serienbrief* mail merge.
- The organisation and team modal. There is no multi-tenant model to hang it
  on, and building one is a separate project.
- Drag-and-drop between stages. Streamlit cannot express it, and the canvas
  itself falls back to a dropdown.
- The canvas's Zürich sample data. The application remains Canton Aargau.

## Data model

`workflow.CONTACT_STATUS_LABELS` gains one entry and relabels three. The status
*codes* are deliberately left alone where a mapping already exists, so no stored
value has to be rewritten:

```python
CONTACT_STATUS_LABELS = {
    "not_contacted":     "Nicht kontaktiert",
    "contacted":         "Brief versandt",
    "in_discussion":     "Im Gespräch",       # new
    "meeting_scheduled": "Termin vereinbart",
    "declined":          "Abgelehnt",
}
```

The dictionary is ordered to match the board's column order, left to right.
Ordering is display information that happens to live in the same structure; the
migration below compares statuses as a set so that reordering this dictionary is
never itself a schema change.

`ingest.WORKFLOW_COLUMNS` gains seven columns. All are nullable text, which is
what `_add_missing_columns` can add to a populated table:

| column | meaning | format |
|---|---|---|
| `due_date` | *Wiedervorlage* — when to come back to this lead | `YYYY-MM-DD` |
| `last_contact` | when the owner was last contacted | `YYYY-MM-DD` |
| `next_step` | one line: what happens next | free text, ≤ 300 chars |
| `note` | standing context about the lead | free text, ≤ 1000 chars |
| `contact_person` | name and function of the person to contact | free text, ≤ 200 chars |
| `phone` | phone number, entered as the user has it | free text, ≤ 50 chars |
| `email` | email address | free text, ≤ 200 chars |

Dates are stored as ISO strings, not as SQLite dates, matching how `updated_at`
and `calculated_at` are already stored.

Every new column is declared `TEXT NOT NULL DEFAULT ''`. `_add_missing_columns`
strips `NOT NULL` but keeps a quoted `DEFAULT`, and SQLite backfills existing
rows from that default on `ALTER TABLE ADD COLUMN` — verified against SQLite
3.50.4. So a lead saved before this release reads back `''`, not `NULL`, and no
coalescing layer is needed: absent is the empty string everywhere, and sorting
and comparison need no null branch.

The columns are therefore nullable in the widened table and `NOT NULL` in a
freshly created one. That asymmetry is pre-existing — `owner_name` already has
it — and the rebuild in the next section restores `NOT NULL` on any database it
touches.

The existing `owner_name` column already carries what the canvas calls
*Eigentümerschaft*. It is not duplicated.

### Where the fields come from

Owner and contact details remain **manually entered**. The application's central
promise — no owner data is automatically collected or scraped — is unchanged by
this work. These columns are places to write down what the user looked up in
AGIS by hand.

## Migration

This is the only risky part of the change and it needs to be written explicitly.

`ingest.schema()` builds the `contact_status` CHECK constraint from
`CONTACT_STATUS_LABELS` inside a `CREATE TABLE IF NOT EXISTS`. On a database
that already has the table, that statement does nothing, so the old four-value
constraint survives. `_add_missing_columns` can widen a table but cannot relax a
constraint. Adding a fifth status without a migration therefore produces code
that is correct on a fresh database and passes its tests, while the deployed
instance — which carries a persistent SQLite volume holding real saved leads —
raises `CHECK constraint failed` the first time a lead is moved to *Im
Gespräch*.

A new function `ingest._rebuild_workflow_check(con)` handles it. It runs inside
`schema()`, after `_add_missing_columns` and before the index creation:

1. Read the stored `parcel_workflow` DDL from `sqlite_master` and extract the
   quoted values from its `contact_status IN (...)` clause.
2. If that set equals the set of keys in `CONTACT_STATUS_LABELS`, return. This
   makes the function idempotent and makes a reordered dictionary a no-op.
3. Otherwise, within a single transaction:
   - record `SELECT COUNT(*) FROM parcel_workflow`;
   - create `parcel_workflow_new` from `WORKFLOW_COLUMNS` with the current
     CHECK constraints and primary key;
   - `INSERT INTO parcel_workflow_new (...) SELECT ... FROM parcel_workflow`
     over an explicit, shared column list — never `SELECT *`, so a column added
     in either direction cannot silently shift values into the wrong column;
   - assert the new table's row count equals the recorded count, raising if it
     does not;
   - drop `parcel_workflow`, rename `parcel_workflow_new` into its place;
   - recreate `idx_workflow_saved` and `idx_workflow_hidden`.

The row-count assertion is raised inside the transaction so that a mismatch
rolls the whole rebuild back rather than committing a partial copy. No backup
table is left behind: the transaction is the recovery mechanism, and a stray
`parcel_workflow_backup` in the schema would outlive its usefulness immediately.

`PRAGMA foreign_keys` is not touched. The table deliberately has no foreign
keys — `parcel_results` is deleted and reinserted on every recompute while these
decisions must survive — so there is nothing to defer.

## Interface

### `workflow.py`

`load()` gains the seven new columns in its `SELECT` list. No coalescing — see
the data-model section.

`update()` gains keyword arguments for the seven new fields, following the
existing pattern exactly: a field the caller omits keeps its current value, and
the function stays a single atomic batch write. Its existing validation grows to
match:

- an unknown `contact_status` raises `ValueError` (already true);
- a date that is not `YYYY-MM-DD` and not empty raises `ValueError`;
- each free-text field is stripped and length-checked against the table above.

Thin `set_*` wrappers are added only where a caller needs one. The board writes
several fields at once, so it calls `update()` directly.

### `acquisition.py`

A new module. `app.py` is 43k and `render_workflow()` is the largest thing in
it; the replacement is larger still, and putting it inline would make the file
harder to work in rather than easier.

The module separates decisions from rendering, so the logic is testable without
a Streamlit runtime:

```python
def leads(parcels, decisions, field):   # saved/hidden decisions ⋈ parcel facts
def overdue(shortlist, today):          # -> due_date < today, ascending
def by_stage(shortlist):                # -> dict[code, DataFrame], board order
def render(parcels, decisions, db, today, price_of)   # the only Streamlit part
```

`leads`, `overdue` and `by_stage` take and return plain data. `render` composes
them and takes its column counts from `len()` of each `by_stage` frame.

Two of `render`'s arguments are injected rather than imported. `today` is an ISO
string instead of a call to `date.today()`, so the overdue boundary is testable.
`price_of` comes from `app.py` because resolving a land-price reference needs
the loaded `land_prices.csv`; two lookups that disagreed about the most specific
matching row would put one number in the result table and a different one on the
card.

### Layout

Top to bottom, following the canvas:

1. **Fällige Wiedervorlagen** — a table of leads whose `due_date` has passed,
   ascending, with a count. Columns: date, address, Gemeinde · Parzelle, owner,
   stage, next step. Overdue dates are tinted. The section is hidden entirely
   when nothing is due, rather than showing an empty frame.
2. **The board** — `st.columns(5)`, one per stage in dictionary order, each with
   a header carrying the stage name and its count, then one bordered container
   per lead. A card shows address, Gemeinde · Parzelle, Potenzial m², reference
   land value, owner, `last_contact → due_date`, and the next step. Below that a
   stage `selectbox` and an *Analyse* button that opens the parcel through the
   existing detail session-state key.
3. **Kontaktdetails** — an expander inside each card holding a form for contact
   person, phone, email, both dates, next step and note. One submit per card;
   the write goes through a single `workflow.update()` call.
4. **Ausgeblendete Parzellen** — the existing hidden-parcels expander, moved
   across unchanged.

A card with no `due_date` sorts last within its column and shows an em dash,
which is how the canvas renders the same case.

## Testing

Extending `tests/test_workflow.py` and adding `tests/test_acquisition.py`. The
existing `tests/` convention — `unittest`, a `TemporaryDirectory` per test, and
`ingest.schema` to build the fixture — is followed.

The test that matters most builds a **legacy** database: `parcel_workflow`
created with the old four-value CHECK constraint and populated with rows across
all four statuses. It then runs `ingest.schema` and asserts that every row
survived with its status intact, and that a lead can now be set to
`in_discussion`. Without that fixture the migration is untested, because a
database built by the current `schema()` already has the new constraint and the
rebuild never fires.

Alongside it:

- running `schema()` twice changes no data and raises nothing;
- each new field survives a write-and-read round trip through `update()`;
- an omitted field keeps its stored value while a sibling field is written;
- a malformed date and an over-length text field each raise `ValueError`;
- `overdue()` treats a lead due *today* as not yet overdue, and excludes leads
  with an empty `due_date`;
- `by_stage()` returns all five stages in dictionary order, including the empty
  ones, so the board renders five columns on an empty database.

## Consequences

The municipality grouping of saved leads is lost. It was the organising idea of
the old panel and the board replaces it with stage grouping; a lead's
municipality still appears on its card. If grouping by municipality turns out to
be load-bearing, it returns as a filter above the board rather than as a second
level of nesting.

A database upgraded by this change cannot be opened by an older release of the
application: the older code would accept the new columns but reject
`in_discussion` rows against its own status map. That is acceptable for a
single-instance tool, and it is the reason the migration preserves rows rather
than rewriting statuses.
