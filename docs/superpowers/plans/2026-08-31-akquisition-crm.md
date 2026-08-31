# Akquisition CRM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the municipality-grouped saved-leads panel with an acquisition board — five contact stages, a follow-up date per lead, and a list of follow-ups that have come due.

**Architecture:** Seven nullable text columns and one new contact status extend `parcel_workflow`. Because `CREATE TABLE IF NOT EXISTS` cannot change a constraint on an existing table, a new `ingest._rebuild_workflow_check` copies the table when its status CHECK is stale. The board itself lives in a new `acquisition.py`, split so that the decisions behind it — which leads are due, which column a lead belongs in — are plain functions over DataFrames, testable without a Streamlit runtime.

**Tech Stack:** Python 3.11, SQLite (stdlib `sqlite3`), pandas, Streamlit, `unittest` run under pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-akquisition-crm-design.md`

---

## Before you start

Run the test suite the way this repository needs it run:

```bash
.venv/bin/python -m pytest tests/ -q
```

**`.venv/bin/pytest` does not work** — it fails collection with `ModuleNotFoundError: No module named 'ingest'`, because the modules sit at the repository root and only `python -m` puts the working directory on `sys.path`. Every command in this plan uses the `python -m` form.

Baseline before any change: all tests pass. Confirm that first, so a later failure is unambiguously yours.

Do not commit to `master`. Create the branch first:

```bash
git checkout -b feat/akquisition-crm
```

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `ingest.py` | Table definitions and forward migration | Modify: 7 columns added to `WORKFLOW_COLUMNS`; new `_workflow_statuses` and `_rebuild_workflow_check`; one call added inside `schema` |
| `workflow.py` | Persistence and validation for user decisions | Modify: new status, relabels, 7 keyword arguments on `update`, two validation helpers |
| `acquisition.py` | The board: lead selection, ordering, rendering | **Create** |
| `app.py` | Page assembly | Modify: delete `workflow_parcels` and `render_workflow`, call `acquisition.render` instead |
| `tests/test_schema.py` | Migration behaviour on legacy databases | Modify: one new test class |
| `tests/test_workflow.py` | Persistence and validation | Modify: new tests |
| `tests/test_acquisition.py` | Ordering and due-date logic | **Create** |
| `tests/test_app.py` | End-to-end regression | Modify: one new test |

`acquisition.py` is a new module rather than more of `app.py` because `app.py` is already 43k and `render_workflow` is the largest thing in it. The replacement is larger still.

---

## Task 1: Acquisition columns

Purely additive. SQLite backfills existing rows from a quoted `DEFAULT` on `ALTER TABLE ADD COLUMN`, so a lead saved before this release reads back `''` rather than `NULL`, and nothing downstream needs a null branch.

**Files:**
- Modify: `ingest.py:144-155` (`WORKFLOW_COLUMNS`)
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing test**

Add this method to the existing `SchemaMigrationTest` class in `tests/test_schema.py`, after `test_schema_widens_old_database_without_losing_rows`:

```python
    def test_acquisition_columns_reach_a_legacy_workflow_row(self):
        """A lead saved by an older release must read back as empty strings,
        not as NULL: the board sorts and compares these fields directly, and a
        null branch in every one of them would be the cost of getting this
        wrong."""
        self.con.execute(
            "INSERT INTO parcel_workflow (bfs, parcel, saved) VALUES (4001, '1', 1)"
        )

        ingest.schema(self.con)

        columns = {
            row[1] for row in self.con.execute("PRAGMA table_info(parcel_workflow)")
        }
        for name in (
            "due_date",
            "last_contact",
            "next_step",
            "note",
            "contact_person",
            "phone",
            "email",
        ):
            self.assertIn(name, columns)
        self.assertEqual(
            self.con.execute(
                "SELECT saved, due_date, last_contact, next_step, note, "
                "contact_person, phone, email FROM parcel_workflow"
            ).fetchone(),
            (1, "", "", "", "", "", "", ""),
        )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_schema.py::SchemaMigrationTest::test_acquisition_columns_reach_a_legacy_workflow_row -v
```

Expected: FAIL — `AssertionError: 'due_date' not found in {...}`.

- [ ] **Step 3: Add the columns**

In `ingest.py`, replace the `WORKFLOW_COLUMNS` list (currently ending at the `updated_at` entry) with:

```python
WORKFLOW_COLUMNS = [
    ("bfs", "INTEGER NOT NULL"),
    ("parcel", "TEXT NOT NULL"),
    ("saved", "INTEGER NOT NULL DEFAULT 0"),
    ("hidden", "INTEGER NOT NULL DEFAULT 0"),
    ("owner_name", "TEXT NOT NULL DEFAULT ''"),
    (
        "contact_status",
        f"TEXT NOT NULL DEFAULT '{WF.DEFAULT_CONTACT_STATUS}'",
    ),
    ("updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    # The acquisition fields. Every one carries DEFAULT '' rather than being
    # left nullable: `_add_missing_columns` strips NOT NULL but keeps a quoted
    # default, and SQLite backfills existing rows from it, so a lead saved
    # before this release arrives as an empty string like every other. Absent
    # is one value here, not two.
    ("due_date", "TEXT NOT NULL DEFAULT ''"),
    ("last_contact", "TEXT NOT NULL DEFAULT ''"),
    ("next_step", "TEXT NOT NULL DEFAULT ''"),
    ("note", "TEXT NOT NULL DEFAULT ''"),
    ("contact_person", "TEXT NOT NULL DEFAULT ''"),
    ("phone", "TEXT NOT NULL DEFAULT ''"),
    ("email", "TEXT NOT NULL DEFAULT ''"),
]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_schema.py -v
```

Expected: PASS, 3 tests in `SchemaMigrationTest`.

- [ ] **Step 5: Commit**

```bash
git add ingest.py tests/test_schema.py
git commit -m "feat: the seven fields an acquisition needs to be run"
```

---

## Task 2: Rebuild a stale status constraint

The migration machinery, built and tested before any status is added, so that adding one in Task 3 is safe on a populated database.

The trigger for this task's test is a legacy database whose CHECK lists only three of the four statuses the code already knows — a plausible older release, and enough to exercise the rebuild without depending on Task 3.

**Files:**
- Modify: `ingest.py` (new functions before `schema`; one call inside `schema`)
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_schema.py`, after `SchemaMigrationTest` and before `DatabaseBootstrapTest`:

```python
class WorkflowStatusRebuildTest(unittest.TestCase):
    """A database whose CHECK constraint predates a status the code allows.

    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table and
    `_add_missing_columns` can only widen one, so without an explicit rebuild
    the deployment rejects the new status while every test on a fresh database
    passes. This fixture is the only thing that can fail.
    """

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(
            """
            CREATE TABLE parcel_workflow (
                bfs            INTEGER NOT NULL,
                parcel         TEXT NOT NULL,
                saved          INTEGER NOT NULL DEFAULT 0,
                hidden         INTEGER NOT NULL DEFAULT 0,
                owner_name     TEXT NOT NULL DEFAULT '',
                contact_status TEXT NOT NULL DEFAULT 'not_contacted',
                updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (saved IN (0, 1)),
                CHECK (hidden IN (0, 1)),
                CHECK (contact_status IN
                    ('not_contacted', 'contacted', 'meeting_scheduled')),
                PRIMARY KEY (bfs, parcel)
            );
            INSERT INTO parcel_workflow
                (bfs, parcel, saved, hidden, owner_name, contact_status)
            VALUES
                (4001, '1', 1, 0, 'Muster AG', 'contacted'),
                (4002, '7', 1, 0, '', 'meeting_scheduled'),
                (4003, '12', 0, 1, '', 'not_contacted');
            """
        )

    def tearDown(self):
        self.con.close()

    def test_rebuild_keeps_every_row_and_admits_the_missing_status(self):
        ingest.schema(self.con)

        self.assertEqual(
            self.con.execute(
                "SELECT bfs, parcel, saved, hidden, owner_name, contact_status "
                "FROM parcel_workflow ORDER BY bfs"
            ).fetchall(),
            [
                (4001, "1", 1, 0, "Muster AG", "contacted"),
                (4002, "7", 1, 0, "", "meeting_scheduled"),
                (4003, "12", 0, 1, "", "not_contacted"),
            ],
        )
        # The status the legacy constraint refused.
        self.con.execute(
            "INSERT INTO parcel_workflow (bfs, parcel, contact_status) "
            "VALUES (4004, '9', 'declined')"
        )

    def test_rebuild_restores_the_indexes_it_dropped(self):
        ingest.schema(self.con)

        indexes = {
            row[0]
            for row in self.con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'parcel_workflow'"
            )
        }
        self.assertIn("idx_workflow_saved", indexes)
        self.assertIn("idx_workflow_hidden", indexes)

    def test_rebuild_reports_that_it_fired_once_and_then_stops(self):
        ingest.schema(self.con)

        self.assertFalse(ingest._rebuild_workflow_check(self.con))
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM parcel_workflow").fetchone()[0],
            3,
        )

    def test_a_table_without_any_status_constraint_is_rebuilt(self):
        """The oldest shape on the volume: `parcel_workflow` from before the
        contact status existed at all. An unconstrained column is not the
        schema this release declares, so it is rebuilt rather than left."""
        con = sqlite3.connect(":memory:")
        con.executescript(
            """
            CREATE TABLE parcel_workflow (
                bfs    INTEGER NOT NULL,
                parcel TEXT NOT NULL,
                saved  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (bfs, parcel)
            );
            INSERT INTO parcel_workflow (bfs, parcel, saved) VALUES (4001, '1', 1);
            """
        )

        ingest.schema(con)

        self.assertEqual(
            con.execute(
                "SELECT bfs, parcel, saved, contact_status FROM parcel_workflow"
            ).fetchone(),
            (4001, "1", 1, "not_contacted"),
        )
        con.close()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_schema.py::WorkflowStatusRebuildTest -v
```

Expected: FAIL. The first two tests fail with `sqlite3.IntegrityError: CHECK constraint failed` when inserting `'declined'`; the third fails with `AttributeError: module 'ingest' has no attribute '_rebuild_workflow_check'`.

- [ ] **Step 3: Add `re` to the imports**

Check the top of `ingest.py`. If `import re` is not already there, add it to the standard-library import block in alphabetical order.

- [ ] **Step 4: Write the two functions**

Insert both into `ingest.py` immediately before `def schema(con):`:

```python
def _workflow_statuses(con):
    """The status values the stored `parcel_workflow` CHECK constraint allows.

    An empty set means the table has no such constraint — either it predates
    the contact status entirely, or it was created by hand. Both are reasons to
    rebuild: an unconstrained column is not the schema this release declares.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'parcel_workflow'"
    ).fetchone()
    if not row or not row[0]:
        return set()
    clause = re.search(
        r"CHECK\s*\(\s*contact_status\s+IN\s*\((.*?)\)\s*\)", row[0], re.S | re.I
    )
    if not clause:
        return set()
    return set(re.findall(r"'([^']*)'", clause.group(1)))


def _rebuild_workflow_check(con):
    """Copy `parcel_workflow` when its status constraint is out of date.

    Compared as sets, so reordering `CONTACT_STATUS_LABELS` — which is also the
    board's column order — is never mistaken for a schema change.

    Returns whether the table was rebuilt.
    """
    if _workflow_statuses(con) == set(WF.CONTACT_STATUS_LABELS):
        return False

    have = {row[1] for row in con.execute("PRAGMA table_info(parcel_workflow)")}
    carried = ", ".join(name for name, _ in WORKFLOW_COLUMNS if name in have)
    statuses = ", ".join(f"'{status}'" for status in WF.CONTACT_STATUS_LABELS)
    before = con.execute("SELECT COUNT(*) FROM parcel_workflow").fetchone()[0]

    # SQLite runs DDL inside a transaction, but python's sqlite3 opens implicit
    # transactions for DML only — so CREATE and DROP would each commit on their
    # own and a failure halfway through would leave no table at all. Explicit
    # control for the duration of the copy, restored afterwards.
    previous = con.isolation_level
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                f"""
                CREATE TABLE parcel_workflow_new (
                    {_column_definitions(WORKFLOW_COLUMNS)},
                    CHECK (saved IN (0, 1)),
                    CHECK (hidden IN (0, 1)),
                    CHECK (contact_status IN ({statuses})),
                    PRIMARY KEY (bfs, parcel)
                )
                """
            )
            # Both halves name their columns. `SELECT *` would silently shift
            # every value one place the first time the two column orders
            # diverge, and the shift would be invisible until someone read a
            # phone number out of the note field.
            con.execute(
                f"INSERT INTO parcel_workflow_new ({carried}) "
                f"SELECT {carried} FROM parcel_workflow"
            )
            after = con.execute(
                "SELECT COUNT(*) FROM parcel_workflow_new"
            ).fetchone()[0]
            if after != before:
                raise RuntimeError(
                    "parcel_workflow rebuild would lose leads: "
                    f"{before} rows in, {after} rows out"
                )
            con.execute("DROP TABLE parcel_workflow")
            con.execute(
                "ALTER TABLE parcel_workflow_new RENAME TO parcel_workflow"
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.isolation_level = previous
    return True
```

- [ ] **Step 5: Call it from `schema`**

In `ingest.py`, find this line inside `schema`:

```python
    _add_missing_columns(con, "parcel_workflow", WORKFLOW_COLUMNS)
```

Add directly beneath it:

```python
    # After widening, because the copy carries whichever columns the widened
    # table has; before the indexes, because DROP TABLE takes its indexes with
    # it and the CREATE INDEX statements below put them back.
    _rebuild_workflow_check(con)
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_schema.py -v
```

Expected: PASS, 7 tests.

- [ ] **Step 7: Run the whole suite — the rebuild touches shared bootstrap code**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS, no failures.

- [ ] **Step 8: Commit**

```bash
git add ingest.py tests/test_schema.py
git commit -m "feat: a stale status constraint is rebuilt rather than obeyed"
```

---

## Task 3: The fifth stage

**Files:**
- Modify: `workflow.py:18-24` (`CONTACT_STATUS_LABELS`)
- Test: `tests/test_schema.py`, `tests/test_workflow.py`

- [ ] **Step 1: Write the failing tests**

Add to `WorkflowStatusRebuildTest` in `tests/test_schema.py`:

```python
    def test_the_four_status_release_upgrades_to_five(self):
        """The exact shape on the deployed volume: the four statuses this
        application shipped with, and leads sitting in them."""
        con = sqlite3.connect(":memory:")
        con.executescript(
            """
            CREATE TABLE parcel_workflow (
                bfs            INTEGER NOT NULL,
                parcel         TEXT NOT NULL,
                saved          INTEGER NOT NULL DEFAULT 0,
                hidden         INTEGER NOT NULL DEFAULT 0,
                owner_name     TEXT NOT NULL DEFAULT '',
                contact_status TEXT NOT NULL DEFAULT 'not_contacted',
                updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (contact_status IN ('not_contacted', 'contacted',
                                          'declined', 'meeting_scheduled')),
                PRIMARY KEY (bfs, parcel)
            );
            INSERT INTO parcel_workflow (bfs, parcel, saved, contact_status)
            VALUES (4001, '1', 1, 'contacted'),
                   (4002, '7', 1, 'declined');
            """
        )

        ingest.schema(con)

        self.assertEqual(
            con.execute(
                "SELECT contact_status FROM parcel_workflow ORDER BY bfs"
            ).fetchall(),
            [("contacted",), ("declined",)],
        )
        con.execute(
            "INSERT INTO parcel_workflow (bfs, parcel, contact_status) "
            "VALUES (4003, '9', 'in_discussion')"
        )
        con.close()
```

Add to `ParcelWorkflowTest` in `tests/test_workflow.py`:

```python
    def test_the_new_stage_is_stored_and_the_order_is_the_board_order(self):
        workflow.set_contact_status(
            [(4001, "12")], "in_discussion", self.database
        )
        self.assertEqual(
            workflow.load(self.database).iloc[0]["contact_status"],
            "in_discussion",
        )
        self.assertEqual(
            list(workflow.CONTACT_STATUS_LABELS),
            [
                "not_contacted",
                "contacted",
                "in_discussion",
                "meeting_scheduled",
                "declined",
            ],
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_schema.py::WorkflowStatusRebuildTest::test_the_four_status_release_upgrades_to_five tests/test_workflow.py::ParcelWorkflowTest::test_the_new_stage_is_stored_and_the_order_is_the_board_order -v
```

Expected: FAIL — `sqlite3.IntegrityError: CHECK constraint failed` on the first, `ValueError: Unknown contact status: in_discussion` on the second.

- [ ] **Step 3: Add the stage**

In `workflow.py`, replace the `CONTACT_STATUS_LABELS` dictionary with:

```python
#: Dictionary order is the acquisition board's column order, left to right —
#: the sequence a lead actually moves through. The codes are stable; only the
#: labels are display. `contacted` is shown as "Brief versandt" because that is
#: the step it has always meant in practice, and renaming the code would mean
#: rewriting stored values for a caption.
CONTACT_STATUS_LABELS = {
    "not_contacted": "Nicht kontaktiert",
    "contacted": "Brief versandt",
    "in_discussion": "Im Gespräch",
    "meeting_scheduled": "Termin vereinbart",
    "declined": "Abgelehnt",
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_schema.py tests/test_workflow.py -v
```

Expected: PASS.

- [ ] **Step 5: Check the label change did not break the result table**

`app.py` maps labels back to codes in the saved-leads editor. That code is deleted in Task 7, but must still work now.

```bash
.venv/bin/python -m pytest tests/test_app.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add workflow.py tests/test_schema.py tests/test_workflow.py
git commit -m "feat: a lead can be in discussion, not only written to"
```

---

## Task 4: Reading and writing the new fields

**Files:**
- Modify: `workflow.py` — imports, new constants, `load`, `update`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write the failing tests**

Add to `ParcelWorkflowTest` in `tests/test_workflow.py`:

```python
    def test_acquisition_fields_survive_a_round_trip(self):
        workflow.update(
            [(4001, "12")],
            owner_name="Erbengemeinschaft Weber",
            contact_person="Dr. T. Weber, Erbenvertreter",
            phone="+41 44 712 08 41",
            email="t.weber@example.ch",
            last_contact="2026-08-21",
            due_date="2026-09-04",
            next_step="Zweitgespräch, Wertindikation vorlegen",
            note="Drei Erben, einer im Ausland",
            db=self.database,
        )

        state = workflow.load(self.database).iloc[0]
        self.assertEqual(state["contact_person"], "Dr. T. Weber, Erbenvertreter")
        self.assertEqual(state["phone"], "+41 44 712 08 41")
        self.assertEqual(state["email"], "t.weber@example.ch")
        self.assertEqual(state["last_contact"], "2026-08-21")
        self.assertEqual(state["due_date"], "2026-09-04")
        self.assertEqual(state["note"], "Drei Erben, einer im Ausland")

    def test_an_omitted_field_keeps_its_stored_value(self):
        workflow.update(
            [(4001, "12")], note="Wohnsitz vermutlich Zug", db=self.database
        )
        workflow.update([(4001, "12")], due_date="2026-09-02", db=self.database)

        state = workflow.load(self.database).iloc[0]
        self.assertEqual(state["note"], "Wohnsitz vermutlich Zug")
        self.assertEqual(state["due_date"], "2026-09-02")

    def test_a_date_can_be_cleared_but_not_malformed(self):
        workflow.update([(4001, "12")], due_date="2026-09-02", db=self.database)
        workflow.update([(4001, "12")], due_date="", db=self.database)
        self.assertEqual(workflow.load(self.database).iloc[0]["due_date"], "")

        for bad in ("02.09.2026", "2026-9-2", "20260902", "morgen"):
            with self.assertRaises(ValueError, msg=bad):
                workflow.update([(4001, "12")], due_date=bad, db=self.database)

    def test_an_impossible_calendar_date_is_rejected(self):
        """Right shape, wrong day. A regex alone would let this through."""
        with self.assertRaises(ValueError):
            workflow.update([(4001, "12")], due_date="2026-02-30", db=self.database)

    def test_acquisition_text_is_trimmed_and_bounded(self):
        workflow.update(
            [(4001, "12")], next_step="  Brief aufsetzen  ", db=self.database
        )
        self.assertEqual(
            workflow.load(self.database).iloc[0]["next_step"], "Brief aufsetzen"
        )
        with self.assertRaises(ValueError):
            workflow.update([(4001, "12")], note="x" * 1001, db=self.database)
        with self.assertRaises(ValueError):
            workflow.update([(4001, "12")], phone="x" * 51, db=self.database)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_workflow.py -v
```

Expected: FAIL — `TypeError: update() got an unexpected keyword argument 'contact_person'`.

- [ ] **Step 3: Add imports and validation constants**

In `workflow.py`, the import block becomes — `re` before `sqlite3`, keeping the block alphabetical:

```python
import re
import sqlite3
from collections.abc import Iterable
from datetime import date
```

Then add below `DEFAULT_CONTACT_STATUS = "not_contacted"`:

```python
#: How long each free-text acquisition field may be. The limits are generous
#: enough that nobody meets them in normal use and small enough that a paste
#: accident cannot put a document into a database column.
TEXT_LIMITS = {
    "owner_name": 200,
    "contact_person": 200,
    "phone": 50,
    "email": 200,
    "next_step": 300,
    "note": 1000,
}

DATE_FIELDS = ("due_date", "last_contact")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _text(field: str, value: str) -> str:
    """Trim a free-text field and hold it to its documented length."""
    value = str(value).strip()
    limit = TEXT_LIMITS[field]
    if len(value) > limit:
        raise ValueError(f"{field} must be {limit} characters or fewer")
    return value


def _date(field: str, value: str) -> str:
    """An ISO date, or the empty string for "no date decided yet".

    Checked twice: the shape, so that a Swiss `02.09.2026` is refused rather
    than silently sorted as text, and then the calendar, so that `2026-02-30`
    is refused as well.
    """
    value = str(value).strip()
    if not value:
        return ""
    if not _ISO_DATE.match(value):
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD) or empty")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} is not a date on the calendar") from None
    return value
```

- [ ] **Step 4: Widen `load`**

In `workflow.py`, replace the `read_sql_query` call inside `load` with:

```python
        return pd.read_sql_query(
            "SELECT bfs, parcel, saved, hidden, owner_name, contact_status, "
            "due_date, last_contact, next_step, note, contact_person, "
            "phone, email, updated_at "
            "FROM parcel_workflow",
            connection,
        )
```

- [ ] **Step 5: Widen `update`**

Replace the signature and the validation-and-assignment block of `update` in `workflow.py`. The new signature:

```python
def update(
    keys: Iterable[tuple[int, str]],
    *,
    saved: bool | None = None,
    hidden: bool | None = None,
    owner_name: str | None = None,
    contact_status: str | None = None,
    due_date: str | None = None,
    last_contact: str | None = None,
    next_step: str | None = None,
    note: str | None = None,
    contact_person: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    db: str | None = None,
) -> int:
```

Replace the body's validation section — the lines from `if contact_status is not None and contact_status not in CONTACT_STATUS_LABELS:` through the `owner_name` length check — with:

```python
    if contact_status is not None and contact_status not in CONTACT_STATUS_LABELS:
        raise ValueError(f"Unknown contact status: {contact_status}")

    # Validated before a single write, so a bad date in one field cannot leave
    # the other fields of the same save applied.
    text = {
        "owner_name": owner_name,
        "next_step": next_step,
        "note": note,
        "contact_person": contact_person,
        "phone": phone,
        "email": email,
    }
    dates = {"due_date": due_date, "last_contact": last_contact}
    clean = {
        field: _text(field, value)
        for field, value in text.items()
        if value is not None
    }
    clean.update(
        {
            field: _date(field, value)
            for field, value in dates.items()
            if value is not None
        }
    )
```

Then replace the `if owner_name is not None:` assignment block (the one appending `owner_name = ?`) with:

```python
    for field, value in clean.items():
        assignments.append(f"{field} = ?")
        values.append(value)
```

Leave the `saved`, `hidden` and `contact_status` assignment blocks exactly as they are. The field names in that f-string are module constants, never caller input.

- [ ] **Step 6: Keep the existing wrapper honest**

`set_owner_name` already calls `update(keys, owner_name=...)`, so it now routes through `_text` and keeps its trimming and its 200-character limit. No change needed. Confirm by reading it.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_workflow.py -v
```

Expected: PASS, all tests including the pre-existing `test_owner_name_is_trimmed_and_bounded`.

- [ ] **Step 8: Commit**

```bash
git add workflow.py tests/test_workflow.py
git commit -m "feat: dates, notes and a contact behind every saved lead"
```

---

## Task 5: What the board decides

Pure functions over DataFrames. No Streamlit import is exercised by these tests.

**Files:**
- Create: `acquisition.py`
- Test: `tests/test_acquisition.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_acquisition.py`:

```python
import unittest

import pandas as pd

import acquisition
import workflow


def leads_frame(rows):
    """A saved-leads frame with the columns the board reads.

    Built by hand rather than through the database so that the ordering rules
    can be stated as data: the point of these tests is the sort, not the join.
    """
    return pd.DataFrame(
        rows,
        columns=[
            "bfs",
            "parcel",
            "municipality",
            "contact_status",
            "due_date",
        ],
    )


class OverdueTest(unittest.TestCase):
    def test_a_lead_due_today_is_not_yet_overdue(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", "2026-08-30"),
                (4002, "2", "Baden", "contacted", "2026-08-31"),
                (4003, "3", "Brugg", "contacted", "2026-09-02"),
            ]
        )

        due = acquisition.overdue(leads, "2026-08-31")

        self.assertEqual(list(due["parcel"]), ["1"])

    def test_a_lead_without_a_date_is_never_chased(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", ""),
                (4002, "2", "Baden", "contacted", "2026-08-01"),
            ]
        )

        due = acquisition.overdue(leads, "2026-08-31")

        self.assertEqual(list(due["parcel"]), ["2"])

    def test_overdue_leads_come_back_earliest_first(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", "2026-08-27"),
                (4002, "2", "Baden", "contacted", "2026-08-12"),
                (4003, "3", "Brugg", "contacted", "2026-08-21"),
            ]
        )

        due = acquisition.overdue(leads, "2026-08-31")

        self.assertEqual(list(due["due_date"]), ["2026-08-12", "2026-08-21", "2026-08-27"])

    def test_an_empty_shortlist_returns_an_empty_frame(self):
        due = acquisition.overdue(leads_frame([]), "2026-08-31")

        self.assertTrue(due.empty)


class ByStageTest(unittest.TestCase):
    def test_every_stage_is_present_in_board_order_even_when_empty(self):
        stages = acquisition.by_stage(leads_frame([]))

        self.assertEqual(list(stages), list(workflow.CONTACT_STATUS_LABELS))
        self.assertTrue(all(frame.empty for frame in stages.values()))

    def test_leads_land_in_their_own_stage(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "in_discussion", "2026-09-04"),
                (4002, "2", "Baden", "declined", ""),
                (4003, "3", "Brugg", "in_discussion", "2026-09-01"),
            ]
        )

        stages = acquisition.by_stage(leads)

        self.assertEqual(list(stages["in_discussion"]["parcel"]), ["3", "1"])
        self.assertEqual(list(stages["declined"]["parcel"]), ["2"])
        self.assertTrue(stages["not_contacted"].empty)

    def test_an_undated_lead_sorts_below_a_dated_one(self):
        """An empty date sorts before every real one as a string, which is the
        opposite of what the column wants: a lead nobody has scheduled is not
        more urgent than one that is due next week."""
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", ""),
                (4002, "2", "Baden", "contacted", "2026-09-10"),
                (4003, "3", "Brugg", "contacted", "2026-09-01"),
            ]
        )

        stages = acquisition.by_stage(leads)

        self.assertEqual(list(stages["contacted"]["parcel"]), ["3", "2", "1"])

    def test_an_unknown_stored_status_falls_back_to_the_first_stage(self):
        """A status written by a newer release and read by this one. The lead
        appears on the board rather than vanishing from it."""
        leads = leads_frame([(4001, "1", "Aarau", "gone_quiet", "")])

        stages = acquisition.by_stage(leads)

        self.assertEqual(list(stages["not_contacted"]["parcel"]), ["1"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_acquisition.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'acquisition'`.

- [ ] **Step 3: Create the module with its pure functions**

Create `acquisition.py`:

```python
"""The acquisition board — saved leads grouped by contact stage.

Kept out of `app.py` because the decisions behind the board are worth testing
on their own: which leads are due, which column a lead belongs in, and in what
order the cards sit. Those are the four functions below `render`, and none of
them touches Streamlit.

The board replaces a saved list grouped by municipality. A lead's municipality
is still on its card; what the user works through day to day is the stage.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import detail
import workflow as WF


def leads(parcels: pd.DataFrame, decisions: pd.DataFrame, field: str) -> pd.DataFrame:
    """Saved or hidden decisions joined back to the parcel facts they name.

    An inner join deliberately: a decision whose parcel is not in the current
    result set — a municipality that has not been recomputed yet — has nothing
    to put on a card, and a blank card would suggest the lead had been lost
    rather than merely not loaded.
    """
    if decisions.empty:
        return parcels.iloc[0:0].copy()
    chosen = decisions[decisions[field].fillna(0).astype(bool)]
    if chosen.empty:
        return parcels.iloc[0:0].copy()
    return chosen.merge(
        parcels, on=["bfs", "parcel"], how="inner", validate="one_to_one"
    )


def overdue(shortlist: pd.DataFrame, today: str) -> pd.DataFrame:
    """Leads whose follow-up date has passed, earliest first.

    `today` is an ISO string parameter rather than a call to `date.today()`, so
    the boundary is testable. A lead due today is not yet overdue. A lead with
    no date is not chased at all: an empty `due_date` means nobody has decided
    when to come back, which is a different state from being late.

    ISO dates compare as strings exactly as they compare as dates, so no
    parsing is needed to sort them.
    """
    if shortlist.empty:
        return shortlist
    due = shortlist["due_date"].fillna("").astype(str)
    return shortlist[(due != "") & (due < today)].sort_values(
        "due_date", kind="stable"
    )


def _board_order(frame: pd.DataFrame) -> pd.DataFrame:
    """Soonest follow-up first within a column; undated leads last."""
    if frame.empty:
        return frame
    due = frame["due_date"].fillna("").astype(str)
    return (
        frame.assign(_undated=(due == "").astype(int), _due=due)
        .sort_values(["_undated", "_due", "parcel"], kind="stable")
        .drop(columns=["_undated", "_due"])
    )


def by_stage(shortlist: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """One frame per contact stage, in board order, including empty stages.

    Every stage is present even when nothing is in it: the board draws five
    columns on an empty database, and a column that disappeared when its last
    lead moved on would make the board change shape under the user.

    A status this release does not know — written by a newer one against the
    same volume — falls back to the first stage rather than dropping the lead
    off the board entirely.
    """
    if shortlist.empty:
        return {stage: shortlist for stage in WF.CONTACT_STATUS_LABELS}
    # `list(...)` rather than the dict: `Series.isin` does match a mapping on
    # its keys, but saying so out loud costs nothing and does not depend on it.
    known = shortlist["contact_status"].where(
        shortlist["contact_status"].isin(list(WF.CONTACT_STATUS_LABELS)),
        WF.DEFAULT_CONTACT_STATUS,
    )
    return {
        stage: _board_order(shortlist[known == stage])
        for stage in WF.CONTACT_STATUS_LABELS
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_acquisition.py -v
```

Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add acquisition.py tests/test_acquisition.py
git commit -m "feat: which leads are due, and which column each one sits in"
```

---

## Task 6: The board itself

Rendering only. Nothing in this task changes what the previous tasks decided.

**Files:**
- Modify: `acquisition.py` (append)

- [ ] **Step 1: Append the rendering functions to `acquisition.py`**

```python
def _swiss(value: float) -> str:
    """1'740, not 1,740 — the separator the cadastre and the canton use."""
    return f"{value:,.0f}".replace(",", "’")


def _or_dash(value) -> str:
    return str(value) if pd.notna(value) and str(value).strip() else "—"


def render(parcels, decisions, db, today, price_of):
    """The acquisition board, and the recoverable list of hidden parcels.

    `price_of` is passed in rather than imported: resolving a land-price
    reference needs the loaded `land_prices.csv`, which belongs to `app.py`,
    and two lookups that disagreed about the most specific matching row would
    put one number in the table and another on the card.
    """
    st.divider()
    st.subheader("Akquisition — Eigentümerdialog")
    st.caption(
        "Kontaktstand je Parzelle und Eigentümerschaft. Das Wiedervorlagedatum "
        "steuert die Fälligkeit, die Stufe wird auf der Karte geändert. "
        "Eigentümerangaben werden weiterhin von Hand im AGIS nachgeschlagen."
    )

    shortlist = leads(parcels, decisions, "saved")
    if shortlist.empty:
        st.info("Noch keine Parzellen gespeichert.")
    else:
        _render_overdue(shortlist, today)
        _render_board(shortlist, db, price_of)

    _render_hidden(parcels, decisions, db)


def _render_overdue(shortlist, today):
    """Hidden entirely when nothing is due, rather than shown as an empty frame
    — an empty table reads as a broken query, not as a clear desk."""
    due = overdue(shortlist, today)
    if due.empty:
        return
    st.markdown(f"**Fällige Wiedervorlagen** · {len(due)} offen")
    st.dataframe(
        pd.DataFrame(
            {
                "Wiedervorlage": due["due_date"],
                "Adresse": due["address"].map(_or_dash),
                "Gemeinde": due["municipality"],
                "Parzelle": due["parcel"],
                "Eigentümerschaft": due["owner_name"].map(_or_dash),
                "Stufe": due["contact_status"].map(
                    lambda code: WF.CONTACT_STATUS_LABELS.get(
                        code, WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS]
                    )
                ),
                "Nächster Schritt": due["next_step"].map(_or_dash),
            }
        ),
        hide_index=True,
        width="stretch",
    )


def _render_board(shortlist, db, price_of):
    stages = by_stage(shortlist)
    columns = st.columns(len(WF.CONTACT_STATUS_LABELS))
    for column, (stage, label) in zip(columns, WF.CONTACT_STATUS_LABELS.items()):
        frame = stages[stage]
        with column:
            st.markdown(f"**{label}** · {len(frame)}")
            for _, row in frame.iterrows():
                _render_card(row, db, price_of)


def _render_card(row, db, price_of):
    key = int(row["bfs"]), str(row["parcel"])
    slug = f"{key[0]}_{key[1]}"
    reference = price_of(row)
    land_value = (
        row["area"] * reference.price_chf_m2
        if reference is not None and pd.notna(row["area"])
        else None
    )
    stored = (
        row["contact_status"]
        if row["contact_status"] in WF.CONTACT_STATUS_LABELS
        else WF.DEFAULT_CONTACT_STATUS
    )

    with st.container(border=True):
        st.markdown(f"**{_or_dash(row['address'])}**")
        st.caption(f"{row['municipality']} · {row['parcel']}")
        st.text(f"Potenzial  {_swiss(row['delta'])} m²")
        st.text(
            "Landwert   —"
            if land_value is None
            else f"Landwert   CHF {_swiss(land_value)}"
        )
        st.caption(
            str(row["owner_name"]).strip() or "Eigentümer nicht erfasst"
        )
        st.caption(f"{_or_dash(row['last_contact'])} → {_or_dash(row['due_date'])}")
        if str(row["next_step"]).strip():
            st.caption(row["next_step"])

        stage = st.selectbox(
            "Stufe",
            list(WF.CONTACT_STATUS_LABELS),
            index=list(WF.CONTACT_STATUS_LABELS).index(stored),
            format_func=lambda code: WF.CONTACT_STATUS_LABELS[code],
            key=f"stage_{slug}",
            label_visibility="collapsed",
        )
        if stage != stored:
            WF.update([key], contact_status=stage, db=db)
            st.rerun()

        if st.button("Analyse", key=f"open_{slug}", width="stretch"):
            detail.open_parcel(detail.parcel_id(row))
            st.rerun()

        with st.expander("Kontaktdetails"):
            _render_contact_form(row, key, slug, db)


def _render_contact_form(row, key, slug, db):
    """Dates are text rather than `st.date_input` because "no date yet" is a
    real and common state here, and a date picker has to invent a day to show.
    The format is stated in the placeholder and enforced by `workflow.update`,
    which reports what it refused."""
    with st.form(f"contact_{slug}"):
        owner_name = st.text_input(
            "Eigentümerschaft", value=str(row["owner_name"]), max_chars=200
        )
        contact_person = st.text_input(
            "Kontaktperson",
            value=str(row["contact_person"]),
            max_chars=200,
            placeholder="Name, Funktion",
        )
        phone = st.text_input(
            "Telefon", value=str(row["phone"]), max_chars=50, placeholder="+41 …"
        )
        email = st.text_input(
            "E-Mail",
            value=str(row["email"]),
            max_chars=200,
            placeholder="name@domain.ch",
        )
        last_contact = st.text_input(
            "Letzter Kontakt",
            value=str(row["last_contact"]),
            max_chars=10,
            placeholder="JJJJ-MM-TT",
        )
        due_date = st.text_input(
            "Wiedervorlage",
            value=str(row["due_date"]),
            max_chars=10,
            placeholder="JJJJ-MM-TT",
        )
        next_step = st.text_input(
            "Nächster Schritt", value=str(row["next_step"]), max_chars=300
        )
        note = st.text_area("Notiz", value=str(row["note"]), max_chars=1000)
        store = st.form_submit_button("Speichern")
        remove = st.form_submit_button("Von Merkliste entfernen")

    if remove:
        WF.set_saved([key], False, db)
        st.toast("Parzelle von der Merkliste entfernt.")
        st.rerun()
    if not store:
        return
    try:
        WF.update(
            [key],
            owner_name=owner_name,
            contact_person=contact_person,
            phone=phone,
            email=email,
            last_contact=last_contact,
            due_date=due_date,
            next_step=next_step,
            note=note,
            db=db,
        )
    except ValueError as error:
        st.error(str(error))
        return
    st.toast("Kontaktdaten gespeichert.")
    st.rerun()


def _render_hidden(parcels, decisions, db):
    """Carried across from the old panel unchanged. Hiding a parcel is the one
    destructive-looking action in the list, and it stays recoverable."""
    hidden = leads(parcels, decisions, "hidden")
    if hidden.empty:
        return
    ordered = hidden.sort_values(["municipality", "parcel"], kind="stable")
    options = [(int(row["bfs"]), str(row["parcel"])) for _, row in ordered.iterrows()]
    labels = {
        (int(row["bfs"]), str(row["parcel"])): (
            f"{row['municipality']} · Parzelle {row['parcel']} · "
            f"{_or_dash(row['address'])}"
        )
        for _, row in ordered.iterrows()
    }
    with st.expander(f"Ausgeblendete Parzellen · {len(hidden)}"):
        restore = st.multiselect(
            "Wieder in der Ergebnisliste anzeigen",
            options,
            format_func=lambda key: labels[key],
            key="restore_hidden_selection",
        )
        if st.button(
            "Auswahl wieder anzeigen",
            key="restore_hidden_button",
            disabled=not restore,
        ):
            WF.set_hidden(restore, False, db)
            st.toast(f"{len(restore)} Parzelle(n) wieder eingeblendet.")
            st.rerun()
```

- [ ] **Step 2: Verify the module still imports and the pure tests still pass**

```bash
.venv/bin/python -m pytest tests/test_acquisition.py -v
```

Expected: PASS, 9 tests. Rendering is not exercised here; Task 7's app test covers it.

- [ ] **Step 3: Commit**

```bash
git add acquisition.py
git commit -m "feat: the board, a card per lead and the contacts behind it"
```

---

## Task 7: Wire it into the page

**Files:**
- Modify: `app.py` — imports, delete `workflow_parcels` and `render_workflow`, two call sites

- [ ] **Step 1: Add the import**

In `app.py`, add to the local import block (alphabetical, so before `import detail`):

```python
import acquisition as ACQ
```

- [ ] **Step 2: Delete the two replaced functions**

Delete `def workflow_parcels(field):` and `def render_workflow():` entirely — everything from `def workflow_parcels(field):` down to the end of `render_workflow`, which finishes with the `st.rerun()` inside the hidden-parcels expander, immediately before the `if final.empty:` block.

Both are now in `acquisition.py`: `workflow_parcels` as `acquisition.leads`, the hidden expander as `acquisition._render_hidden`, and the saved list replaced by the board.

- [ ] **Step 3: Replace the early call site**

Find, in the `if final.empty:` block:

```python
if final.empty:
    st.info("Keine Parzelle erfüllt diese Kriterien.")
    render_workflow()
    st.stop()
```

Replace with:

```python
if final.empty:
    st.info("Keine Parzelle erfüllt diese Kriterien.")
    ACQ.render(parcels, parcel_workflow, DB, date.today().isoformat(), price_of)
    st.stop()
```

- [ ] **Step 4: Replace the late call site**

Find the bare `render_workflow()` call near the foot of the file, after the `st.caption(...)` about opening a single analysis. Replace it with:

```python
ACQ.render(parcels, parcel_workflow, DB, date.today().isoformat(), price_of)
```

`date` is already imported at the top of `app.py` (`from datetime import date`). `price_of` is defined above both call sites.

- [ ] **Step 5: Check nothing still refers to the deleted names**

```bash
grep -n "render_workflow\|workflow_parcels" app.py
```

Expected: no output.

- [ ] **Step 6: Run the whole suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS. If `tests/test_app.py` asserts on the old saved-leads editor, read the failure and update that assertion to the board — do not restore the old panel.

- [ ] **Step 7: Run the app and look at it**

```bash
.venv/bin/streamlit run app.py
```

Save a parcel from the result table, then check on the board: the card appears under *Nicht kontaktiert*, the stage dropdown moves it between columns, *Kontaktdetails* stores a date and a note, an overdue date makes the *Fällige Wiedervorlagen* list appear, and *Von Merkliste entfernen* takes the card away. Stop the server when done.

- [ ] **Step 8: Commit**

```bash
git add app.py
git commit -m "feat: the saved list becomes the acquisition board"
```

---

## Task 8: End-to-end regression

**Files:**
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Add this method to `AppRegressionTest` in `tests/test_app.py`:

```python
    def test_the_board_renders_a_saved_lead_with_its_acquisition_fields(self):
        """The whole path: a decision in `parcel_workflow`, joined to a parcel
        the cascade produced, drawn as a card. The join is `validate=
        "one_to_one"`, so a duplicate decision would raise here rather than
        double a lead on the board."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        key = [(int(first["bfs"]), str(first["parcel"]))]
        workflow.set_saved(key, True, self.database)
        workflow.update(
            key,
            contact_status="in_discussion",
            owner_name="Erbengemeinschaft Weber",
            due_date="2020-01-01",
            next_step="Zweitgespräch vereinbaren",
            db=self.database,
        )

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()

        self.assertFalse(app.exception)
        text = " ".join(element.value for element in app.markdown)
        self.assertIn("Akquisition", text)
        self.assertIn("Im Gespräch", text)
        # Due in 2020 and today is not: the overdue list must have drawn.
        overdue = [
            frame.value
            for frame in app.dataframe
            if "Wiedervorlage" in getattr(frame.value, "columns", [])
        ]
        self.assertEqual(len(overdue), 1)
        self.assertEqual(list(overdue[0]["Nächster Schritt"]), ["Zweitgespräch vereinbaren"])
```

- [ ] **Step 2: Run the test**

```bash
.venv/bin/python -m pytest tests/test_app.py::AppRegressionTest::test_the_board_renders_a_saved_lead_with_its_acquisition_fields -v
```

Expected: PASS if Tasks 1–7 are correct. If it fails on `app.exception`, print the exception first — `print(app.exception)` — and fix the cause in `acquisition.py`, not in the test.

If the assertion on `app.markdown` fails because Streamlit routes `st.subheader` elsewhere in this version, widen the search to `app.markdown + app.subheader` rather than deleting the assertion.

- [ ] **Step 3: Run the whole suite one final time**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS, no failures, no errors.

- [ ] **Step 4: Prove the migration test actually tests something**

A green suite is not evidence that the migration works — the tests would also pass if `_rebuild_workflow_check` never fired. Break it on purpose and watch the right test go red:

```bash
.venv/bin/python - <<'PY'
import re, pathlib
p = pathlib.Path("ingest.py")
s = p.read_text()
p.write_text(s.replace("    _rebuild_workflow_check(con)\n", "", 1))
PY
.venv/bin/python -m pytest tests/test_schema.py::WorkflowStatusRebuildTest -q
```

Expected: FAIL, with `CHECK constraint failed` — that is the deployed failure the whole migration exists to prevent. Now restore it:

```bash
git checkout ingest.py
.venv/bin/python -m pytest tests/test_schema.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_app.py
git commit -m "test: a saved lead reaches the board with its dates intact"
```

---

## Done

The branch `feat/akquisition-crm` now holds the acquisition board. Do not push or merge it — confirm with the user first.

What is deliberately not built, from the spec's scope section: the four-tab navigation, the Screening filter restyle, a separate Merkliste view, contact-list export and *Serienbrief*, the organisation and team modal, drag-and-drop between stages, and the canvas's Zürich sample data.
