"""Prepare the persistent database before the web server becomes healthy."""

import sqlite3

import ingest
import paths


def prepare_database():
    """Seed an empty volume and migrate any database from an older release."""
    seeded = paths.ensure_db()
    con = sqlite3.connect(paths.DB)
    try:
        ingest.schema(con)
    finally:
        con.close()
    return seeded


if __name__ == "__main__":
    copied = prepare_database()
    action = "seeded and migrated" if copied else "migrated"
    print(f"Database {action}: {paths.DB}")
