import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import bootstrap
import ingest
import paths


class SchemaMigrationTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        # Production had this pre-Street-View schema on its persistent volume.
        self.con.executescript(
            """
            CREATE TABLE parcel_results (
                bfs INTEGER NOT NULL,
                municipality TEXT,
                parcel TEXT NOT NULL,
                egrid TEXT,
                address TEXT,
                built TEXT,
                use_class TEXT,
                zone TEXT,
                az REAL,
                metric TEXT,
                unconvertible REAL,
                e REAL,
                n REAL,
                area REAL,
                buildable REAL,
                zone_share REAL,
                buildings INTEGER,
                existing REAL,
                delta REAL,
                heritage TEXT,
                design_plan INTEGER,
                calculated_at TEXT,
                PRIMARY KEY (bfs, parcel)
            );
            CREATE TABLE runs (
                bfs INTEGER PRIMARY KEY,
                municipality TEXT,
                parcels INTEGER,
                assessed INTEGER,
                candidates INTEGER,
                no_az INTEGER,
                seconds REAL,
                finished_at TEXT
            );
            CREATE TABLE oereb_cache (
                egrid TEXT PRIMARY KEY,
                hard TEXT,
                notable TEXT,
                error TEXT,
                checked_at TEXT
            );
            CREATE TABLE parcel_workflow (
                bfs INTEGER NOT NULL,
                parcel TEXT NOT NULL,
                saved INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (bfs, parcel)
            );
            INSERT INTO parcel_results
                (bfs, municipality, parcel, e, n, area, buildings, delta)
            VALUES (4001, 'Aarau', '1', 2648000, 1249000, 500, 1, 250);
            """
        )

    def tearDown(self):
        self.con.close()

    def test_schema_widens_old_database_without_losing_rows(self):
        ingest.schema(self.con)

        parcel_columns = {
            row[1] for row in self.con.execute("PRAGMA table_info(parcel_results)")
        }
        run_columns = {
            row[1] for row in self.con.execute("PRAGMA table_info(runs)")
        }
        workflow_columns = {
            row[1] for row in self.con.execute("PRAGMA table_info(parcel_workflow)")
        }
        self.assertTrue({name for name, _ in ingest.COLUMNS} <= parcel_columns)
        self.assertIn("sv_e", parcel_columns)
        self.assertIn("sv_n", parcel_columns)
        self.assertIn("transport_share", parcel_columns)
        self.assertIn("reasons", run_columns)
        self.assertTrue(
            {name for name, _ in ingest.WORKFLOW_COLUMNS} <= workflow_columns
        )
        self.assertEqual(
            self.con.execute(
                "SELECT municipality, parcel, delta, sv_e, sv_n "
                "FROM parcel_results"
            ).fetchone(),
            ("Aarau", "1", 250.0, None, None),
        )

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

    def test_schema_migration_is_idempotent(self):
        ingest.schema(self.con)
        ingest.schema(self.con)

        count = self.con.execute("SELECT COUNT(*) FROM parcel_results").fetchone()[0]
        self.assertEqual(count, 1)
        workflow_tables = self.con.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'parcel_workflow'"
        ).fetchone()[0]
        self.assertEqual(workflow_tables, 1)


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


class DatabaseBootstrapTest(unittest.TestCase):
    def test_current_schema_needs_no_writes_and_preserves_opt_ins(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        ingest.schema(con)
        con.execute("UPDATE organisation_profile SET name='Saved', weekly_digest=1, "
                    "due_reminders=1, shared_calculations=1")
        con.commit()
        before = con.iterdump()
        before = list(before)
        con.execute("PRAGMA query_only=ON")

        ingest.schema(con)

        self.assertEqual(list(con.iterdump()), before)

    def test_current_schema_can_be_checked_while_another_connection_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "current.sqlite")
            con = sqlite3.connect(database, timeout=0.1)
            holder = sqlite3.connect(database)
            try:
                ingest.schema(con)
                holder.execute("BEGIN IMMEDIATE")
                holder.execute("UPDATE organisation_profile SET name='In progress'")

                ingest.schema(con)

                self.assertEqual(con.execute("SELECT name FROM organisation_profile").fetchone(), ("",))
                self.assertTrue(holder.in_transaction)
            finally:
                holder.close()
                con.close()

    def test_bootstrap_migrates_a_persistent_legacy_database(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = os.path.join(directory, "seed.sqlite")
            database = os.path.join(directory, "volume.sqlite")
            for path in (seed, database):
                con = sqlite3.connect(path)
                con.executescript(
                    """
                    CREATE TABLE parcel_results (
                        bfs INTEGER NOT NULL,
                        parcel TEXT NOT NULL,
                        delta REAL,
                        PRIMARY KEY (bfs, parcel)
                    );
                    CREATE TABLE runs (bfs INTEGER PRIMARY KEY);
                    CREATE TABLE oereb_cache (egrid TEXT PRIMARY KEY);
                    """
                )
                con.close()

            with mock.patch.object(paths, "DB", database), mock.patch.object(
                paths, "SEED_DB", seed
            ), mock.patch.object(bootstrap.paths, "DB", database):
                self.assertFalse(bootstrap.prepare_database())

            con = sqlite3.connect(database)
            columns = {
                row[1] for row in con.execute("PRAGMA table_info(parcel_results)")
            }
            con.close()
            self.assertIn("sv_e", columns)
            self.assertIn("sv_n", columns)

    def test_requested_reseed_replaces_then_migrates_database(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = os.path.join(directory, "seed.sqlite")
            database = os.path.join(directory, "volume.sqlite")
            for path, municipality in ((seed, "New"), (database, "Old")):
                con = sqlite3.connect(path)
                con.executescript(
                    """
                    CREATE TABLE parcel_results (
                        bfs INTEGER NOT NULL,
                        municipality TEXT,
                        parcel TEXT NOT NULL,
                        delta REAL,
                        PRIMARY KEY (bfs, parcel)
                    );
                    CREATE TABLE runs (bfs INTEGER PRIMARY KEY);
                    CREATE TABLE oereb_cache (egrid TEXT PRIMARY KEY);
                    """
                )
                con.execute(
                    "INSERT INTO parcel_results "
                    "(bfs, municipality, parcel, delta) VALUES (4001, ?, '1', 1)",
                    (municipality,),
                )
                con.commit()
                con.close()

            with mock.patch.object(paths, "DB", database), mock.patch.object(
                paths, "SEED_DB", seed
            ), mock.patch.dict(os.environ, {"DENSIFICATION_RESEED": "1"}):
                self.assertTrue(bootstrap.prepare_database())

            con = sqlite3.connect(database)
            municipality = con.execute(
                "SELECT municipality FROM parcel_results"
            ).fetchone()[0]
            columns = {
                row[1] for row in con.execute("PRAGMA table_info(parcel_results)")
            }
            con.close()
            self.assertEqual(municipality, "New")
            self.assertIn("sv_e", columns)

    def test_requested_reseed_keeps_everything_people_entered(self):
        """A data refresh replaces the parcel data and nothing else.

        The volume's file also holds the team and its invitations, saved leads
        and contact progress, settings, saved searches and shared calculations.
        Copying the image's file over it used to wipe all of that.
        """
        with tempfile.TemporaryDirectory() as directory:
            seed = os.path.join(directory, "seed.sqlite")
            database = os.path.join(directory, "volume.sqlite")

            con = sqlite3.connect(seed)
            con.executescript(
                """
                CREATE TABLE parcel_results (
                    bfs INTEGER NOT NULL,
                    municipality TEXT,
                    parcel TEXT NOT NULL,
                    delta REAL,
                    PRIMARY KEY (bfs, parcel)
                );
                CREATE INDEX parcel_results_by_municipality
                    ON parcel_results (municipality);
                CREATE TABLE runs (bfs INTEGER PRIMARY KEY, municipality TEXT);
                CREATE TABLE oereb_cache (egrid TEXT PRIMARY KEY, hard TEXT);
                CREATE TABLE parcel_workflow (
                    bfs INTEGER NOT NULL, parcel TEXT NOT NULL,
                    PRIMARY KEY (bfs, parcel)
                );
                INSERT INTO parcel_results VALUES (4001, 'New', '1', 2), (4002, 'New', '9', 3);
                INSERT INTO runs VALUES (4001, 'New'), (4002, 'New');
                INSERT INTO oereb_cache VALUES ('CH-both', 'seed answer'), ('CH-seed', '');
                """
            )
            con.close()

            con = sqlite3.connect(database)
            ingest.schema(con)
            con.executescript(
                """
                INSERT INTO parcel_results (bfs, municipality, parcel) VALUES (4001, 'Old', '1');
                INSERT INTO runs (bfs, municipality) VALUES (4001, 'Old');
                INSERT INTO oereb_cache (egrid, hard) VALUES ('CH-both', 'paid answer'), ('CH-live', '');
                INSERT INTO parcel_workflow (bfs, parcel, saved, contact_status)
                    VALUES (4001, '1', 1, 'contacted');
                UPDATE organisation_profile SET name = 'Firma AG' WHERE id = 1;
                INSERT INTO organisation_members (name, email, role, status)
                    VALUES ('Inhaberin', 'owner@example.com', 'Inhaber', 'active');
                INSERT INTO scope_access (member_id, auth_id, invited_until) VALUES (1, 'provider-id', 0);
                INSERT INTO scope_auth_setup VALUES (1);
                INSERT INTO saved_searches (name, filters) VALUES ('Aarau', '{}');
                INSERT INTO shared_calculations (parcel_id, payload, revision, updated_by)
                    VALUES ('4001/1', '{}', 1, 1);
                """
            )
            con.commit()
            kept = [
                name for (name,) in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT IN ('parcel_results', 'runs', 'oereb_cache') "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            ]
            before = {
                name: con.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()
                for name in kept
            }
            con.close()

            with mock.patch.object(paths, "DB", database), mock.patch.object(
                paths, "SEED_DB", seed
            ), mock.patch.object(bootstrap.paths, "DB", database), mock.patch.dict(
                os.environ, {"DENSIFICATION_RESEED": "1"}
            ):
                self.assertTrue(bootstrap.prepare_database())

            con = sqlite3.connect(database)
            after = {
                name: con.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()
                for name in kept
            }
            parcels = con.execute(
                "SELECT bfs, municipality, parcel FROM parcel_results ORDER BY bfs"
            ).fetchall()
            runs = con.execute("SELECT bfs, municipality FROM runs ORDER BY bfs").fetchall()
            oereb = dict(con.execute("SELECT egrid, hard FROM oereb_cache"))
            index = con.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'parcel_results_by_municipality'"
            ).fetchone()
            columns = {row[1] for row in con.execute("PRAGMA table_info(parcel_results)")}
            con.close()

            self.assertIn("scope_access", kept)
            self.assertIn("organisation_members", kept)
            self.assertIn("parcel_workflow", kept)
            self.assertEqual(after, before)
            self.assertEqual(parcels, [(4001, "New", "1"), (4002, "New", "9")])
            self.assertEqual(runs, [(4001, "New"), (4002, "New")])
            self.assertIsNotNone(index)
            self.assertEqual(oereb, {"CH-both": "paid answer", "CH-live": "", "CH-seed": ""})
            self.assertIn("sv_e", columns)

    def test_requested_reseed_of_an_empty_volume_copies_the_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = os.path.join(directory, "seed.sqlite")
            database = os.path.join(directory, "volume.sqlite")
            con = sqlite3.connect(seed)
            con.executescript(
                """
                CREATE TABLE parcel_results (
                    bfs INTEGER NOT NULL, municipality TEXT, parcel TEXT NOT NULL,
                    PRIMARY KEY (bfs, parcel)
                );
                INSERT INTO parcel_results VALUES (4001, 'New', '1');
                """
            )
            con.close()
            open(database, "w").close()  # the zero-byte file a stray connect leaves

            with mock.patch.object(paths, "DB", database), mock.patch.object(
                paths, "SEED_DB", seed
            ), mock.patch.dict(os.environ, {"DENSIFICATION_RESEED": "1"}):
                self.assertTrue(paths.ensure_db())

            con = sqlite3.connect(database)
            rows = con.execute("SELECT municipality FROM parcel_results").fetchall()
            con.close()
            self.assertEqual(rows, [("New",)])


class ReseedSqlGuardTest(unittest.TestCase):
    """The reseed's SQL is fixed statements plus whitelisted identifiers.

    Bandit B608 flagged the original f-string construction; the guards below
    pin the replacement behaviour: unknown schemas and tables are refused,
    hostile identifiers never reach a statement, and the ÖREB merge tolerates
    the two files having different column sets.
    """

    def test_quote_ident_accepts_plain_names_and_rejects_the_rest(self):
        self.assertEqual(paths._quote_ident("oereb_cache"), '"oereb_cache"')
        for hostile in ('x"; DROP TABLE t; --', "1abc", "x;y", "x y", "", "-x"):
            with self.assertRaises(ValueError):
                paths._quote_ident(hostile)

    def test_has_table_refuses_an_unknown_schema(self):
        con = sqlite3.connect(":memory:")
        with self.assertRaises(ValueError):
            paths._has_table(con, 'main"; ATTACH DATABASE "/tmp/x" AS seed; --', "t")
        con.close()

    def test_replace_refuses_a_table_the_seed_does_not_own(self):
        """`parcel_workflow` holds what people entered; a swap of it would be
        a data-loss bug, so the lookup turning up no statement must raise."""
        con = sqlite3.connect(":memory:")
        with self.assertRaises(ValueError):
            paths._replace_with_seed_table(con, "parcel_workflow")
        con.close()

    def test_oereb_merge_with_divergent_column_sets(self):
        """An unmigrated volume has fewer columns than the seed, an older seed
        fewer than the volume: the merge names the shared columns and both
        files keep their own shape."""
        with tempfile.TemporaryDirectory() as directory:
            seed = os.path.join(directory, "seed.sqlite")
            database = os.path.join(directory, "volume.sqlite")

            con = sqlite3.connect(seed)
            con.executescript(
                """
                CREATE TABLE parcel_results (
                    bfs INTEGER NOT NULL, parcel TEXT NOT NULL,
                    PRIMARY KEY (bfs, parcel)
                );
                CREATE TABLE runs (bfs INTEGER PRIMARY KEY);
                CREATE TABLE oereb_cache (egrid TEXT PRIMARY KEY, hard TEXT);
                INSERT INTO parcel_results VALUES (4001, '1');
                INSERT INTO oereb_cache VALUES ('CH-seed', '');
                """
            )
            con.close()

            con = sqlite3.connect(database)
            ingest.schema(con)
            con.executescript(
                """
                INSERT INTO parcel_results (bfs, parcel) VALUES (4001, '1');
                INSERT INTO oereb_cache (egrid, hard, checked_at)
                    VALUES ('CH-seed', 'paid answer', '2026-09-01'),
                           ('CH-live', '', '2026-09-02');
                """
            )
            con.commit()
            con.close()

            with mock.patch.object(paths, "DB", database), mock.patch.object(
                paths, "SEED_DB", seed
            ), mock.patch.dict(os.environ, {"DENSIFICATION_RESEED": "1"}):
                self.assertTrue(paths.ensure_db())

            con = sqlite3.connect(database)
            rows = dict(
                con.execute("SELECT egrid, hard FROM oereb_cache ORDER BY egrid")
            )
            live_row = con.execute(
                "SELECT checked_at FROM oereb_cache WHERE egrid = 'CH-seed'"
            ).fetchone()
            columns = {
                row[1] for row in con.execute("PRAGMA table_info(oereb_cache)")
            }
            con.close()
            # The paid answer wins; the seed's copy of that row adds nothing.
            self.assertEqual(rows, {"CH-live": "", "CH-seed": "paid answer"})
            # And the volume's own column survives alongside the seed's row.
            self.assertEqual(live_row, ("2026-09-01",))
            self.assertIn("checked_at", columns)

    def test_oereb_merge_inserts_seed_rows_nulling_live_only_columns(self):
        """An old seed with fewer columns: its new row must land with the
        shared values and NULL in the live-only columns.

        When both schema listings silently read `main`, the SELECT's missing
        seed columns fell back to SQLite's double-quoted-literal quirk and the
        merged row stored the column *names* as text — an INSERT that "worked"
        while corrupting the row.
        """
        with tempfile.TemporaryDirectory() as directory:
            seed = os.path.join(directory, "seed.sqlite")
            database = os.path.join(directory, "volume.sqlite")

            con = sqlite3.connect(seed)
            con.executescript(
                """
                CREATE TABLE parcel_results (
                    bfs INTEGER NOT NULL, parcel TEXT NOT NULL,
                    PRIMARY KEY (bfs, parcel)
                );
                CREATE TABLE runs (bfs INTEGER PRIMARY KEY);
                CREATE TABLE oereb_cache (egrid TEXT PRIMARY KEY, hard TEXT);
                INSERT INTO oereb_cache VALUES ('CH-new', 'seed answer');
                """
            )
            con.close()

            con = sqlite3.connect(database)
            ingest.schema(con)
            con.execute(
                "INSERT INTO oereb_cache (egrid, hard) VALUES ('CH-live', '')"
            )
            con.commit()
            con.close()

            with mock.patch.object(paths, "DB", database), mock.patch.object(
                paths, "SEED_DB", seed
            ), mock.patch.dict(os.environ, {"DENSIFICATION_RESEED": "1"}):
                self.assertTrue(paths.ensure_db())

            con = sqlite3.connect(database)
            rows = {
                egrid: (hard, checked_at, details)
                for egrid, hard, checked_at, details in con.execute(
                    "SELECT egrid, hard, checked_at, details FROM oereb_cache"
                )
            }
            columns = {
                row[1] for row in con.execute("PRAGMA table_info(oereb_cache)")
            }
            con.close()
            self.assertEqual(rows["CH-new"], ("seed answer", None, None))
            self.assertEqual(rows["CH-live"], ("", None, None))
            self.assertIn("checked_at", columns)

    def test_oereb_merge_survives_a_seed_only_column(self):
        """An unmigrated volume with fewer columns than the seed: the merge
        must insert on the shared columns instead of erroring with
        "no such column" (the broken listing read `main` for the seed too)."""
        with tempfile.TemporaryDirectory() as directory:
            seed = os.path.join(directory, "seed.sqlite")
            database = os.path.join(directory, "volume.sqlite")

            con = sqlite3.connect(seed)
            con.executescript(
                """
                CREATE TABLE parcel_results (
                    bfs INTEGER NOT NULL, parcel TEXT NOT NULL,
                    PRIMARY KEY (bfs, parcel)
                );
                CREATE TABLE runs (bfs INTEGER PRIMARY KEY);
                CREATE TABLE oereb_cache (
                    egrid TEXT PRIMARY KEY, hard TEXT, details TEXT
                );
                INSERT INTO oereb_cache VALUES ('CH-new', 'seed answer', 'seed details');
                """
            )
            con.close()

            con = sqlite3.connect(database)
            con.executescript(
                """
                CREATE TABLE parcel_results (
                    bfs INTEGER NOT NULL, parcel TEXT NOT NULL,
                    PRIMARY KEY (bfs, parcel)
                );
                CREATE TABLE runs (bfs INTEGER PRIMARY KEY);
                CREATE TABLE oereb_cache (egrid TEXT PRIMARY KEY, hard TEXT);
                INSERT INTO oereb_cache VALUES ('CH-live', 'live answer');
                """
            )
            con.commit()
            con.close()

            with mock.patch.object(paths, "DB", database), mock.patch.object(
                paths, "SEED_DB", seed
            ), mock.patch.dict(os.environ, {"DENSIFICATION_RESEED": "1"}):
                self.assertTrue(paths.ensure_db())

            con = sqlite3.connect(database)
            rows = dict(con.execute("SELECT egrid, hard FROM oereb_cache"))
            con.close()
            self.assertEqual(
                rows, {"CH-live": "live answer", "CH-new": "seed answer"}
            )

    def test_reseed_leaves_live_table_alone_when_seed_lacks_it(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = os.path.join(directory, "seed.sqlite")
            database = os.path.join(directory, "volume.sqlite")

            con = sqlite3.connect(seed)
            con.executescript(
                """
                CREATE TABLE parcel_results (
                    bfs INTEGER NOT NULL, parcel TEXT NOT NULL,
                    PRIMARY KEY (bfs, parcel)
                );
                -- an old seed with no runs table at all
                INSERT INTO parcel_results VALUES (4001, '1');
                """
            )
            con.close()

            con = sqlite3.connect(database)
            con.executescript(
                """
                CREATE TABLE parcel_results (
                    bfs INTEGER NOT NULL, parcel TEXT NOT NULL,
                    PRIMARY KEY (bfs, parcel)
                );
                CREATE TABLE runs (bfs INTEGER PRIMARY KEY, municipality TEXT);
                INSERT INTO parcel_results VALUES (4001, '1'), (4002, '9');
                INSERT INTO runs VALUES (4001, 'Old'), (4002, 'Old');
                """
            )
            con.commit()
            con.close()

            with mock.patch.object(paths, "DB", database), mock.patch.object(
                paths, "SEED_DB", seed
            ), mock.patch.dict(os.environ, {"DENSIFICATION_RESEED": "1"}):
                self.assertTrue(paths.ensure_db())

            con = sqlite3.connect(database)
            runs = con.execute("SELECT bfs, municipality FROM runs ORDER BY bfs").fetchall()
            con.close()
            self.assertEqual(runs, [(4001, "Old"), (4002, "Old")])


if __name__ == "__main__":
    unittest.main()
