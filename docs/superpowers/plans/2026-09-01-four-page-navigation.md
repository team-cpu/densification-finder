# Four-Page Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the application the four pages its workflow already implies — Screening, Merkliste, Analyse, Akquisition — behind one navigation control, and split `app.py` so each page has a home.

**Architecture:** A `st.segmented_control` backed by two session-state keys drives a router in `app.py`. Only the selected page's body runs. `app.py` keeps the shared preamble — loading, land prices, navigation — and hands off to `screening.py`, `merkliste.py`, `acquisition.py` and `detail.py`.

**Tech Stack:** Python 3.11, SQLite (stdlib `sqlite3`), pandas 3.0.5, Streamlit 1.61.1, `unittest` run under pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-four-page-navigation-design.md`

---

## Before you start

Run the suite the way this repository needs it run:

```bash
.venv/bin/python -m pytest tests/ -q
```

**`.venv/bin/pytest` does not work** — it fails collection with `ModuleNotFoundError: No module named 'ingest'`, because the modules sit at the repository root and only `python -m` puts the working directory on `sys.path`. Every command here uses the `python -m` form.

Baseline: **130 passing**. Confirm that before touching anything.

Work in the existing worktree on branch `feat/akquisition-crm`:

```bash
cd /Users/krisnafirdaus/Documents/normiq/.worktrees/densification-akquisition
```

Do not push. Do not switch branches.

**Line numbers below are as of `f110ecd`.** They shift as tasks land, so each instruction also names the anchor — a function, or one of `app.py`'s `# ── banner ──` comments. Trust the anchor over the number.

### Two Streamlit rules this plan depends on

Both were verified against 1.61.1, and both are easy to get wrong:

1. **A widget-keyed session value cannot be written once its widget exists this run.** `st.session_state["page"] = ...` after `st.segmented_control(key="page")` raises `StreamlitAPIException`. Hence the two-key pattern in Task 1.
2. **`st.tabs` renders every tab body.** A three-tab probe ran all three. That is why navigation is a segmented control plus an `if`, and why Task 7 pins it with a test.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `navigation.py` | The page list, the two state keys, `go_to`, the nav control | **Create** |
| `screening.py` | Filters, ranking, the ÖREB shortlist check, the result table and its row actions | **Create** (moved out of `app.py`) |
| `merkliste.py` | Summary tiles and the shortlist table | **Create** |
| `acquisition.py` | The board — unchanged except gaining a contact-list export | Modify (Task 6 only) |
| `app.py` | Loading, land prices, navigation, router | Modify: shrinks to the preamble |
| `tests/test_navigation.py` | Page switching as plain logic | **Create** |
| `tests/test_merkliste.py` | Summary-tile arithmetic | **Create** |
| `tests/test_app.py` | End-to-end per page, lazy rendering, cross-page state | Modify |

`screening.py` and `merkliste.py` follow the split `acquisition.py` already made: the decisions are plain functions over DataFrames, and one `page(...)` function does the rendering.

---

## Task 1: The navigation module

**Files:**
- Create: `navigation.py`
- Create: `tests/test_navigation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_navigation.py`:

```python
import unittest

import navigation


class FakeState(dict):
    """`st.session_state` behaves as a dict for everything this module does.

    Testing against a dict rather than a Streamlit runtime keeps the page
    reconciliation — the part that has an ordering bug waiting in it — provable
    without starting an app.
    """


class NavigationTest(unittest.TestCase):
    def test_a_pending_request_becomes_the_current_page(self):
        state = FakeState({navigation.PENDING: "Analyse"})

        navigation.reconcile(state)

        self.assertEqual(state[navigation.PAGE], "Analyse")
        self.assertNotIn(navigation.PENDING, state)

    def test_reconcile_defaults_to_screening_when_nothing_is_set(self):
        state = FakeState()

        navigation.reconcile(state)

        self.assertEqual(state[navigation.PAGE], "Screening")

    def test_reconcile_leaves_a_chosen_page_alone(self):
        state = FakeState({navigation.PAGE: "Merkliste"})

        navigation.reconcile(state)

        self.assertEqual(state[navigation.PAGE], "Merkliste")

    def test_an_unknown_page_is_refused(self):
        """A typo in a `go_to` call must fail where it is written, not paint an
        empty page that looks like missing data."""
        with self.assertRaises(ValueError):
            navigation.go_to("Aquisition", FakeState())

    def test_go_to_records_a_request_without_touching_the_current_page(self):
        """The write has to be deferred: Streamlit refuses a write to a
        widget-keyed value once that widget has been created this run, so the
        request is parked and reconciled at the top of the next one."""
        state = FakeState({navigation.PAGE: "Screening"})

        navigation.go_to("Akquisition", state)

        self.assertEqual(state[navigation.PENDING], "Akquisition")
        self.assertEqual(state[navigation.PAGE], "Screening")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_navigation.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'navigation'`.

- [ ] **Step 3: Create the module**

Create `navigation.py`:

```python
"""Which of the four pages the application is showing.

Two keys, not one. Streamlit refuses a write to a widget-keyed session value
once that widget has been instantiated during the current run:

    StreamlitAPIException: `st.session_state.acq_page` cannot be modified after
    the widget with key `acq_page` is instantiated.

A button that navigates — "Analyse" on an acquisition card, "Zur Akquisition"
on the shortlist — is rendered after the navigation control, so it cannot set
the page directly. It parks a request instead, and `reconcile` applies it at
the top of the next run, before the control exists.
"""
from __future__ import annotations

import streamlit as st

#: Left to right, the order a lead actually moves through: find it, keep it,
#: work out what it is worth, then approach the owner.
PAGES = ("Screening", "Merkliste", "Analyse", "Akquisition")

DEFAULT_PAGE = PAGES[0]

#: The navigation widget's own key.
PAGE = "acq_page"

#: A parked request to move, honoured by `reconcile` on the next run.
PENDING = "acq_page_go"


def reconcile(state) -> str:
    """Apply any parked navigation request. Call before rendering the control.

    Returns the page that should now be selected.
    """
    requested = state.pop(PENDING, None)
    if requested:
        state[PAGE] = requested
    elif state.get(PAGE) not in PAGES:
        state[PAGE] = DEFAULT_PAGE
    return state[PAGE]


def go_to(page: str, state=None) -> None:
    """Ask for a page. Takes effect on the next run; the caller reruns.

    The page name is checked here rather than where it is read, so a typo
    fails at the call site instead of quietly painting an empty page.
    """
    if page not in PAGES:
        raise ValueError(f"Unknown page: {page}")
    (st.session_state if state is None else state)[PENDING] = page


def render() -> str:
    """Draw the control and return the selected page."""
    page = reconcile(st.session_state)
    st.segmented_control(
        "Navigation",
        PAGES,
        key=PAGE,
        label_visibility="collapsed",
    )
    return st.session_state.get(PAGE, page)
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_navigation.py -v
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add navigation.py tests/test_navigation.py
git commit -m "feat: which of the four pages the app is showing

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: The router, and Screening moves out

The structural task. Nothing about Screening's behaviour changes — it moves file, and gains a caller.

**Files:**
- Create: `screening.py`
- Modify: `app.py`

- [ ] **Step 1: Create `screening.py` by moving code, not rewriting it**

Move these from `app.py` into a new `screening.py`, **verbatim**, keeping their comments and docstrings — those comments are the record of decisions and must not be paraphrased:

| From `app.py` | Anchor |
|---|---|
| `read_oereb_cache`, `_column`, `with_extract`, `failed_egrids`, `check_oereb` | lines 147–222 |
| `workflow_by_key`, `hidden_keys`, `parcel_key` | lines 238–250 |
| everything from `# ── controls ──` to just before `ACQ.render(...)` | lines 266–800 |
| the excluded-parcels expander and the caption after it | lines 807–842 |

`load_regulation_news` (line 128) and `load_land_prices` (line 143) stay in `app.py` — the Analyse branch uses them too.

`screening.py` needs its own imports. Take them from `app.py`'s import block: `concurrent.futures`, `json`, `pandas as pd`, `streamlit as st`, `detail`, `formatting as F`, `land_prices as LP`, `land_cover as LC`, `links as L`, `oereb as O`, `workflow as WF`, `paths`, `navigation`, and `from ranking import rank_candidates`. Copy the module-level constants Screening uses — `SHORTLIST`, `MIN_STORED_DELTA`, `AREA_STEPS`, `NO_LIMIT`, `AREA_DEFAULT`, `OEREB_PDF` — with the comment blocks that explain them.

Wrap the moved rendering in:

```python
def page(parcels, decisions, db, price_of, land_price_references):
    """The screening list: filters, ranking, the ÖREB check and the table."""
```

Inside, replace the module-level names the moved code closed over:

- `parcel_workflow` → `decisions`
- `DB` → `db`
- `price_of(...)` → the `price_of` parameter (unchanged call sites)
- `land_price_references` → the parameter
- `workflow_by_key` and `hidden_keys` → build them at the top of `page` from `decisions`, using the same expressions that built them at module level

The three `st.stop()` calls inside the moved code (the empty-result guard among them) must become `return`. `st.stop()` inside a page function would halt the whole script, so a later page would never draw. This is the one behavioural trap in the move — check every `st.stop()` you moved.

The `open_selected` handler currently does `detail.open_parcel(...)` then `st.rerun()`. Add the navigation:

```python
if open_selected:
    detail.open_parcel(detail.parcel_id(selected.iloc[0]))
    navigation.go_to("Analyse")
    st.rerun()
```

- [ ] **Step 2: Rewrite `app.py`'s tail as a router**

Replace the block at lines 256–262 — the comment beginning "Step 2 of the brief is a conditional view" and the `if detail.selected():` early return — with the router. That comment records a decision this change reverses, so it is rewritten, not deleted:

```python
# The brief asked for a conditional view rather than a second page, and for a
# long time one session-state key was the whole navigation. The workflow has
# since grown two more places to stand — a shortlist and an acquisition board —
# and stacking them under the result table made the page a scroll rather than a
# structure. So: four pages behind one control, and the parcel key now decides
# what Analyse shows rather than whether the list is drawn at all.
#
# `st.segmented_control` rather than `st.tabs` because tabs are not lazy: every
# tab body runs on every rerun, and Analyse recomputes residual values, reads
# the ÖREB cache and can build a PDF. One `if` renders one page.
st.title("Verdichtungspotenzial — Kanton Aargau")
page = navigation.render()

if page == "Screening":
    screening.page(parcels, parcel_workflow, DB, price_of, land_price_references)
elif page == "Merkliste":
    st.info("Merkliste folgt.")
elif page == "Analyse":
    if detail.selected():
        detail.page(parcels, read_oereb_cache(), price_of, load_regulation_news())
    else:
        st.info(
            "Keine Parzelle ausgewählt. Eine Parzelle im Screening oder auf der "
            "Merkliste öffnen."
        )
elif page == "Akquisition":
    ACQ.render(parcels, parcel_workflow, DB, date.today().isoformat(), price_of)
```

Delete the now-duplicated `st.title(...)` that used to sit at line 264.

`read_oereb_cache` moved to `screening.py`, so `app.py` must now call `screening.read_oereb_cache()`. Add `import navigation` and `import screening` to the local import block, alphabetically.

- [ ] **Step 3: Check nothing dangles**

```bash
grep -n "workflow_by_key\|hidden_keys\|^SHORTLIST\|check_oereb\|with_extract" app.py
```

Expected: no output, except any line that legitimately still belongs to the preamble. Investigate anything that appears.

```bash
.venv/bin/python -c "import app" 2>&1 | tail -3
```

A `ModuleNotFoundError` or `NameError` here is a missed import or a missed rename.

- [ ] **Step 4: Run the suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Many `tests/test_app.py` and `tests/test_detail.py` cases drive the old single page and will fail. **They are not wrong — the app is.** Read each failure and update the test to reach the same assertion through the new navigation: switch to the page first with `app.segmented_control(key="acq_page").set_value("Screening").run()`, then assert as before. Tests that open a parcel must now also land on the Analyse page.

Do not delete a test to make the suite green. If a test's assertion no longer has a meaning under the new structure, say which and why.

Expected when done: 130 passing, plus the 5 from Task 1 — **135**.

- [ ] **Step 5: Commit**

```bash
git add app.py screening.py tests/
git commit -m "feat: four pages behind one control, and app.py stops drawing them all

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: The Merkliste page

**Files:**
- Create: `merkliste.py`
- Create: `tests/test_merkliste.py`
- Modify: `app.py` (replace the `st.info("Merkliste folgt.")` placeholder)

- [ ] **Step 1: Write the failing test**

Create `tests/test_merkliste.py`:

```python
import unittest

import pandas as pd

import merkliste


def shortlist_frame(rows):
    return pd.DataFrame(
        rows,
        columns=["bfs", "parcel", "area", "delta", "contact_status"],
    )


class SummaryTest(unittest.TestCase):
    def test_the_tiles_agree_with_the_rows_beneath_them(self):
        """A tile that disagrees with its own table is worse than no tile: it
        is the number someone quotes without scrolling."""
        leads = shortlist_frame(
            [
                (4001, "1", 1000.0, 500.0, "contacted"),
                (4002, "2", 2000.0, 700.0, "in_discussion"),
            ]
        )

        summary = merkliste.summary(leads, land_value=lambda row: row["area"] * 10)

        self.assertEqual(summary["parcels"], 2)
        self.assertEqual(summary["potential"], 1200.0)
        self.assertEqual(summary["land_value"], 30000.0)

    def test_in_dialog_counts_only_the_stages_that_are_conversations(self):
        """Neither an untouched lead nor a refusal is a dialogue; counting
        either would make the tile read as progress that has not happened."""
        leads = shortlist_frame(
            [
                (4001, "1", 100.0, 1.0, "not_contacted"),
                (4002, "2", 100.0, 1.0, "contacted"),
                (4003, "3", 100.0, 1.0, "in_discussion"),
                (4004, "4", 100.0, 1.0, "meeting_scheduled"),
                (4005, "5", 100.0, 1.0, "declined"),
            ]
        )

        summary = merkliste.summary(leads, land_value=lambda row: 0.0)

        self.assertEqual(summary["in_dialog"], 3)

    def test_an_empty_shortlist_reports_zeroes_rather_than_raising(self):
        summary = merkliste.summary(shortlist_frame([]), land_value=lambda row: 0.0)

        self.assertEqual(
            (summary["parcels"], summary["potential"], summary["land_value"],
             summary["in_dialog"]),
            (0, 0.0, 0.0, 0),
        )

    def test_a_parcel_without_a_price_reference_does_not_poison_the_total(self):
        """`price_of` returns None where no reference matches. Summing that as
        NaN would blank the whole tile because of one unpriced parcel."""
        leads = shortlist_frame(
            [
                (4001, "1", 1000.0, 100.0, "contacted"),
                (4002, "2", 2000.0, 100.0, "contacted"),
            ]
        )

        summary = merkliste.summary(
            leads,
            land_value=lambda row: None if row["bfs"] == 4002 else 5000.0,
        )

        self.assertEqual(summary["land_value"], 5000.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_merkliste.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'merkliste'`.

- [ ] **Step 3: Create the module**

Create `merkliste.py`:

```python
"""The shortlist: what has been kept, and how far each one has got.

The board in `acquisition.py` groups the same leads by stage, which is the
right shape for working through them one at a time. This page is the other
question — how much is on the list at all — so it totals them and puts them in
one table.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import acquisition as ACQ
import detail
import navigation
import workflow as WF

#: Stages that count as a live conversation. Neither an untouched lead nor a
#: refusal is one, and counting either would report progress that has not
#: happened.
IN_DIALOG = ("contacted", "in_discussion", "meeting_scheduled")


def summary(leads: pd.DataFrame, land_value) -> dict:
    """The four tiles, computed from the rows the table will show.

    `land_value` is a callable rather than a column so that this page and the
    result table resolve a parcel's reference price the same way. It returns
    None where no reference matches, and those parcels are skipped rather than
    summed as NaN — one unpriced parcel would otherwise blank the whole tile.
    """
    if leads.empty:
        return {"parcels": 0, "potential": 0.0, "land_value": 0.0, "in_dialog": 0}
    values = [land_value(row) for _, row in leads.iterrows()]
    return {
        "parcels": len(leads),
        "potential": float(leads["delta"].fillna(0).sum()),
        "land_value": float(sum(v for v in values if v is not None)),
        "in_dialog": int(leads["contact_status"].isin(IN_DIALOG).sum()),
    }


def page(parcels, decisions, db, price_of):
    """Summary tiles, the shortlist, and the way on to the board."""
    st.subheader("Merkliste")
    st.caption(
        "Gemerkte Parzellen. Kontaktstand und Wiedervorlagen werden in der "
        "Akquisition geführt; hier steht, was insgesamt auf der Liste liegt."
    )

    leads = ACQ.leads(parcels, decisions, "saved")
    if leads.empty:
        st.info("Noch keine Parzellen gemerkt.")
        return

    def land_value(row):
        reference = price_of(row)
        if reference is None or pd.isna(row["area"]):
            return None
        return row["area"] * reference.price_chf_m2

    totals = summary(leads, land_value)
    tiles = st.columns(4)
    tiles[0].metric("Parzellen", ACQ._swiss(totals["parcels"]))
    tiles[1].metric("Summe Potenzial m²", ACQ._swiss(totals["potential"]))
    tiles[2].metric("Summe Landwert CHF", ACQ._swiss(totals["land_value"]))
    tiles[3].metric("Im Dialog", ACQ._swiss(totals["in_dialog"]))

    ordered = leads.sort_values(["municipality", "parcel"], kind="stable")
    table = pd.DataFrame(
        {
            "_bfs": ordered["bfs"].astype(int),
            "_parcel": ordered["parcel"].astype(str),
            "Adresse": ordered["address"].map(ACQ._or_dash),
            "Gemeinde": ordered["municipality"],
            "Potenzial m²": ordered["delta"].round(0),
            "Landwert CHF": [land_value(row) for _, row in ordered.iterrows()],
            "Kontaktstand": ordered["contact_status"].map(
                lambda code: WF.CONTACT_STATUS_LABELS.get(
                    code, WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS]
                )
            ),
            "Letzter Kontakt": ordered["last_contact"].map(ACQ._or_dash),
            "Eigentümerschaft / Notiz": [
                " · ".join(x for x in (str(row["owner_name"]).strip(),
                                       str(row["note"]).strip()) if x) or "—"
                for _, row in ordered.iterrows()
            ],
            "Entfernen": False,
        }
    )

    with st.form("merkliste_form"):
        edited = st.data_editor(
            table,
            key="merkliste_editor",
            width="stretch",
            hide_index=True,
            column_order=(
                "Adresse", "Gemeinde", "Potenzial m²", "Landwert CHF",
                "Kontaktstand", "Letzter Kontakt", "Eigentümerschaft / Notiz",
                "Entfernen",
            ),
            disabled=(
                "_bfs", "_parcel", "Adresse", "Gemeinde", "Potenzial m²",
                "Landwert CHF", "Letzter Kontakt", "Eigentümerschaft / Notiz",
            ),
            column_config={
                "Potenzial m²": st.column_config.NumberColumn(format="%.0f"),
                "Landwert CHF": st.column_config.NumberColumn(format="%.0f"),
                "Kontaktstand": st.column_config.SelectboxColumn(
                    options=list(WF.CONTACT_STATUS_LABELS.values()),
                    required=True,
                    width="medium",
                ),
                "Entfernen": st.column_config.CheckboxColumn(width="small"),
            },
        )
        store = st.form_submit_button("Änderungen speichern")

    if store:
        codes = {label: code for code, label in WF.CONTACT_STATUS_LABELS.items()}
        for _, row in edited.iterrows():
            key = int(row["_bfs"]), str(row["_parcel"])
            if bool(row["Entfernen"]):
                WF.set_saved([key], False, db)
            else:
                WF.update([key], contact_status=codes[row["Kontaktstand"]], db=db)
        st.toast("Merkliste gespeichert.")
        st.rerun()

    left, right = st.columns(2)
    if left.button("Zur Akquisition", width="stretch"):
        navigation.go_to("Akquisition")
        st.rerun()
    chosen = right.selectbox(
        "Parzelle analysieren",
        list(ordered.index),
        format_func=lambda i: (
            f"{ACQ._or_dash(ordered.loc[i, 'address'])} · "
            f"{ordered.loc[i, 'municipality']}"
        ),
        key="merkliste_analyse_pick",
    )
    if right.button("Analyse öffnen", width="stretch"):
        detail.open_parcel(detail.parcel_id(ordered.loc[chosen]))
        navigation.go_to("Analyse")
        st.rerun()
```

- [ ] **Step 4: Wire it into the router**

In `app.py`, replace `st.info("Merkliste folgt.")` with:

```python
    merkliste.page(parcels, parcel_workflow, DB, price_of)
```

and add `import merkliste` to the local import block, alphabetically.

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 139 passing (135 + 4).

- [ ] **Step 6: Commit**

```bash
git add merkliste.py tests/test_merkliste.py app.py
git commit -m "feat: the shortlist gets a page and says what is on it

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Screening — search, summary, reset, export

**Files:**
- Modify: `screening.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Add to `AppRegressionTest` in `tests/test_app.py`. Each drives the real app, so each must first switch to the Screening page.

```python
    def screening(self, timeout=60):
        """The app with the Screening page showing, which is its default."""
        return AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=timeout
        ).run()

    def test_the_parcel_search_narrows_the_table(self):
        app = self.screening()
        full = len(app.dataframe[0].value)
        target = str(app.dataframe[0].value.iloc[0]["Parzelle"])

        field(app, "Parzellen-Nr. suchen").set_value(target).run()

        self.assertFalse(app.exception)
        narrowed = app.dataframe[0].value
        self.assertLess(len(narrowed), full)
        self.assertTrue((narrowed["Parzelle"].astype(str) == target).any())

    def test_the_search_also_matches_an_address(self):
        """Philipp knows the street more often than the parcel number."""
        app = self.screening()
        address = str(app.dataframe[0].value.iloc[0]["Adresse"])
        if address == "—":
            self.skipTest("first row has no address in this fixture")

        field(app, "Parzellen-Nr. suchen").set_value(address[:6]).run()

        self.assertFalse(app.exception)
        self.assertTrue(len(app.dataframe[0].value) >= 1)

    def test_the_summary_line_agrees_with_the_table(self):
        app = self.screening()
        shown = app.dataframe[0].value
        text = " ".join(element.value for element in app.markdown)

        self.assertIn(str(len(shown)), text)

    def test_reset_restores_the_defaults(self):
        app = self.screening()
        app.number_input[0].set_value(2000).run()
        self.assertNotEqual(app.number_input[0].value, 130)

        app.button(key="screening_reset").click().run()

        self.assertFalse(app.exception)
        self.assertEqual(app.number_input[0].value, 130)

    def test_the_csv_export_carries_the_shown_rows(self):
        import io

        app = self.screening()
        shown = app.dataframe[0].value

        exported = [b for b in app.get("download_button")]
        self.assertTrue(exported, "no CSV export on the screening page")
        payload = exported[0].proto.data
        frame = pd.read_csv(io.BytesIO(payload))
        self.assertEqual(len(frame), len(shown))
        self.assertIn("Parzelle", frame.columns)
```

`field(app, label)` is the existing module-level helper.

**If `app.get("download_button")` or `.proto.data` is not how this Streamlit version exposes a download button's payload, find what is** — inspect the element — and adjust the test. Report what you found. Do not drop the assertion that the CSV matches the table.

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_app.py -q
```

Expected: the five new tests fail — no search field, no reset button, no download button.

- [ ] **Step 3: Implement in `screening.py`**

Inside `page`, add to the controls block a search input, placed above the filter columns:

```python
    query = st.text_input(
        "Parzellen-Nr. suchen",
        key="screening_query",
        placeholder="z. B. 1284 oder Seestrasse",
        help="Sucht in Parzellennummer, Adresse und Gemeinde.",
    )
```

In `select`, apply it before ranking — after the hidden-parcel filter, before the numeric filters:

```python
    # Applied before ranking so a search returns the best matches, not the
    # matches that happen to survive the ranking of everything else.
    text = (query or "").strip().lower()
    if text:
        haystack = (
            visible["parcel"].astype(str).str.lower()
            + " " + visible["address"].fillna("").str.lower()
            + " " + visible["municipality"].fillna("").str.lower()
        )
        visible = visible[haystack.str.contains(text, regex=False)]
```

After the table is built and before the row actions, add the summary line and the export:

```python
    st.markdown(
        f"**{len(view)}** Parzellen · "
        f"Potenzial **{F.swiss(final['delta'].sum())} m²** · "
        f"Landwert **CHF {F.swiss(land_value_total)}**"
    )
    st.download_button(
        "CSV exportieren",
        view.to_csv(index=False).encode("utf-8"),
        file_name="verdichtungspotenzial.csv",
        mime="text/csv",
        key="screening_csv",
    )
```

Compute the total immediately above that block. Skipping nulls rather than summing them is the same rule as the Merkliste tiles, for the same reason — one unpriced parcel would otherwise blank the figure:

```python
    priced = final[final["_land_price"].notna()]
    land_value_total = float((priced["area"] * priced["_land_price"]).sum())
```

`F.swiss` does not exist yet. Add it to `formatting.py`, since two modules now need it and `acquisition._swiss` is private:

```python
def swiss(value):
    """1'740, not 1,740 — the separator the cadastre and the canton use."""
    return f"{value:,.0f}".replace(",", "’")
```

Then make `acquisition._swiss` delegate to it rather than keeping a second copy:

```python
def _swiss(value: float) -> str:
    return F.swiss(value)
```

adding `import formatting as F` to `acquisition.py`. Do not change `acquisition._swiss`'s call sites.

Add the reset button beside the search:

```python
    if st.button("Zurücksetzen", key="screening_reset"):
        for key in (
            "screening_query", "screening_min_delta", "screening_area",
            "screening_municipality", "screening_type", "screening_ziffer",
            "screening_hide_inventory", "screening_hide_design_plan",
            "screening_hide_transport", "screening_top_n",
        ):
            st.session_state.pop(key, None)
        st.rerun()
```

**This requires giving every filter widget an explicit `key`** matching that list — they have none today, so their values live in anonymous state that cannot be cleared. Add the keys as you go, and keep each widget's existing default, label and help text exactly. The key list in the reset button and the keys on the widgets must agree; a key in one and not the other is a control that silently does not reset.

- [ ] **Step 3b: Regroup the filters**

The spec asks for the prototype's order. Reorder the controls — **moving the existing widget calls, not rewriting them** — to:

1. Kanton *(added in Task 5; leave the slot for now)*
2. Mindestpotenzial (m² GF)
3. Ziffer
4. Parzellenfläche (m²)
5. Gemeinde
6. Grundstückstyp
7. Anzahl Resultate

then the exclusion checkboxes as their own row: Gestaltungsplan, Inventar, Strassen-/Bahnparzellen, and the age control.

Keep every label, default, `help` string and `key` byte-identical — this is a reordering, and any wording change here is a change to a decision recorded in that help text. The `select` function reads the values by name, so the order of the widget calls does not affect filtering; only make sure each widget is still assigned before `select` is defined.

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 144 passing (139 + 5).

- [ ] **Step 5: Commit**

```bash
git add screening.py formatting.py acquisition.py tests/test_app.py
git commit -m "feat: search the list, read its totals, take it away as CSV

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Locked cantons

**Files:**
- Modify: `screening.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_only_aargau_can_be_chosen(self):
        """The dataset is Aargau. Omitting the others implies they were never
        planned; offering them returns an empty list that reads as a bug."""
        app = self.screening()

        canton = next(w for w in app.selectbox if w.label == "Kanton")
        self.assertEqual(canton.value, "Aargau")
        self.assertEqual(len(app.dataframe[0].value) > 0, True)

        with self.assertRaises(Exception):
            canton.select("Zürich")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_app.py::AppRegressionTest::test_only_aargau_can_be_chosen -q
```

Expected: FAIL — `StopIteration`, there is no "Kanton" selectbox.

- [ ] **Step 3: Implement**

In `screening.py`'s controls, add as the first filter:

```python
    #: Only Aargau has been ingested. The others are listed and disabled rather
    #: than omitted: a selector that hides them implies they were never planned,
    #: and one that offers them returns an empty list that reads as a fault in
    #: the app rather than as the end of the data.
    CANTONS = ("Aargau", "Luzern (noch nicht verfügbar)", "Zürich (noch nicht verfügbar)")
    canton = st.selectbox(
        "Kanton", CANTONS, index=0, key="screening_canton",
        help="Die Ergebnisdatenbank deckt zurzeit nur den Kanton Aargau ab.",
    )
    if canton != "Aargau":
        st.warning("Für diesen Kanton liegen noch keine Daten vor.")
        return
```

Streamlit has no per-option disabling on `st.selectbox`. Labelling the unavailable options and stopping the page with a plain statement is the honest version: it says what is true instead of pretending the option works.

- [ ] **Step 4: Run the suite and commit**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 145 passing.

```bash
git add screening.py tests/test_app.py
git commit -m "feat: the canton list says which cantons there is data for

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Contact-list export

**Files:**
- Modify: `acquisition.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_the_contact_list_exports_every_saved_lead(self):
        """The mail-merge source the design's Serienbrief button implies —
        without building a letter composer."""
        import io

        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 2",
            sqlite3.connect(self.database),
        )
        keys = [(int(r.bfs), str(r.parcel)) for r in first.itertuples()]
        workflow.set_saved(keys, True, self.database)
        workflow.update(keys, owner_name="Muster AG", phone="+41 44 000 00 00",
                        db=self.database)

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()
        app.segmented_control(key="acq_page").set_value("Akquisition").run()
        self.assertFalse(app.exception)

        exported = [b for b in app.get("download_button")]
        self.assertTrue(exported, "no contact-list export on the board")
        frame = pd.read_csv(io.BytesIO(exported[0].proto.data))
        self.assertEqual(len(frame), 2)
        for column in ("Adresse", "Gemeinde", "Eigentümerschaft", "Telefon",
                       "Stufe", "Wiedervorlage", "Nächster Schritt"):
            self.assertIn(column, frame.columns)
```

- [ ] **Step 2: Run to verify it fails, then implement**

In `acquisition.py`, add a pure function beside the other decision functions:

```python
def contact_list(shortlist: pd.DataFrame) -> pd.DataFrame:
    """The saved leads as a flat table, for a mail merge or a phone list.

    Owner details are typed in by hand from the AGIS extract, so this exports
    what the user recorded and invents nothing.
    """
    if shortlist.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "Adresse": shortlist["address"].map(_or_dash),
            "Gemeinde": shortlist["municipality"],
            "Parzelle": shortlist["parcel"],
            "Potenzial m²": shortlist["delta"].round(0),
            "Eigentümerschaft": shortlist["owner_name"],
            "Kontaktperson": shortlist["contact_person"],
            "Telefon": shortlist["phone"],
            "E-Mail": shortlist["email"],
            "Stufe": shortlist["contact_status"].map(
                lambda code: WF.CONTACT_STATUS_LABELS.get(
                    code, WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS]
                )
            ),
            "Letzter Kontakt": shortlist["last_contact"],
            "Wiedervorlage": shortlist["due_date"],
            "Nächster Schritt": shortlist["next_step"],
            "Notiz": shortlist["note"],
        }
    )
```

and in `render`, after the board is drawn:

```python
        st.download_button(
            "Kontaktliste exportieren",
            contact_list(shortlist).to_csv(index=False).encode("utf-8"),
            file_name="akquisition-kontakte.csv",
            mime="text/csv",
            key="acq_contacts_csv",
        )
```

- [ ] **Step 3: Run the suite and commit**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 146 passing.

```bash
git add acquisition.py tests/test_app.py
git commit -m "feat: the shortlist leaves as a contact list

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: The tests that stop this eroding

Two guarantees are invisible in normal use and would rot silently.

**Files:**
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the tests**

```python
    def test_only_the_selected_page_renders(self):
        """The reason navigation is a segmented control and not `st.tabs`:
        tabs run every tab body on every rerun, and Analyse recomputes residual
        values, reads the ÖREB cache and can build a PDF. If this ever passes
        with the result table present on another page, the laziness is gone."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()
        self.assertTrue(app.dataframe, "screening table missing on the default page")

        app.segmented_control(key="acq_page").set_value("Merkliste").run()

        self.assertFalse(app.exception)
        headings = " ".join(h.value for h in app.subheader)
        self.assertIn("Merkliste", headings)
        columns = [
            list(frame.value.columns) for frame in app.dataframe
            if hasattr(frame.value, "columns")
        ]
        self.assertFalse(
            any("Ziffer" in cols for cols in columns),
            "the screening table rendered while another page was selected",
        )

    def test_analyse_says_so_when_nothing_is_selected(self):
        """Reachable only now that Analyse is a page rather than an early
        return, so it had never been designed."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        app.segmented_control(key="acq_page").set_value("Analyse").run()

        self.assertFalse(app.exception)
        self.assertTrue(app.info)
        self.assertIn("Keine Parzelle", " ".join(i.value for i in app.info))

    def test_opening_a_lead_from_the_board_lands_on_analyse(self):
        """The whole reason navigation carries a second state key."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        workflow.set_saved([(bfs, parcel)], True, self.database)

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()
        app.segmented_control(key="acq_page").set_value("Akquisition").run()
        app.button(key=f"open_{bfs}_{parcel}").click().run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["acq_page"], "Analyse")
        self.assertEqual(
            app.session_state["selected_parcel_id"], f"{bfs}:{parcel}"
        )
```

- [ ] **Step 2: Run the full suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 149 passing.

- [ ] **Step 3: Prove each new test is not vacuous**

Back up the file with `cp` first — **do not use `git checkout <file>` on uncommitted work**, it discards the implementation, not just the mutation.

| Mutation | Test that must fail |
|---|---|
| In `app.py`'s router, call `screening.page(...)` unconditionally before the `if` | `test_only_the_selected_page_renders` |
| In the Analyse branch, drop the `else` that shows the empty state | `test_analyse_says_so_when_nothing_is_selected` |
| In `acquisition._render_card`, remove the `navigation.go_to("Analyse")` call | `test_opening_a_lead_from_the_board_lands_on_analyse` |

Restore from the backup after each and confirm `git status --short` is clean.

Report a table of which test caught which mutation. If any mutation leaves the suite green, say so loudly.

- [ ] **Step 4: Commit**

```bash
git add tests/test_app.py
git commit -m "test: one page renders, and a card can send you to the right one

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Done

Expected final state: **149 passing**, `app.py` reduced to preamble and router, four pages behind one control.

Not built, by decision recorded in the spec: the prototype's visual system, drag-and-drop between stages, and Team/Settings with its auth. The last of those would reverse what `gate()`'s docstring records, and needs its own ADR before any code.

Do not push or merge. Hand back for review.
