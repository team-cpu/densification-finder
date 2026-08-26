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


class DatabaseBootstrapTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
