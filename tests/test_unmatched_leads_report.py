"""Tests for scripts/report_unmatched_leads.py.

The script lists saved leads whose (bfs, parcel) is absent from the seed's
parcel_results — the leads `acquisition.leads()` inner-joins away after a
reseed. These tests build temporary SQLite files, run the CLI's main() and
check the CSV, the read-only behaviour and the failure modes.
"""
import csv
import hashlib
import importlib.util
import os
import sqlite3

import pytest

import ingest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "report_unmatched_leads", os.path.join(ROOT, "scripts", "report_unmatched_leads.py")
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def make_live(path):
    con = sqlite3.connect(path)
    ingest.schema(con)
    con.executescript(
        """
        INSERT INTO parcel_workflow
            (bfs, parcel, saved, hidden, owner_name, contact_status, updated_at,
             due_date, last_contact, next_step, note, contact_person,
             owner_address, phone, email)
        VALUES
            (4001, '1', 1, 0, 'Muster AG', 'contacted', '2026-09-01',
             '2026-10-01', '2026-09-01', 'Angebot schicken', 'kennt das Grundstück',
             'E. Muster', 'Hauptstrasse 1', '+41 79 555 00 00', 'muster@example.com'),
            (4002, '9', 1, 0, '=HYPERLINK("http://evil")', 'not_contacted', '',
             '', '', '', '=-2+3', '', '', '-lead@example.com', '@mention'),
            (4003, '12', 0, 1, '', 'not_contacted', '', '', '', '', '', '', '', '', ''),
            (4004, '15', 1, 0, 'Verbleibt', 'declined', '', '', '', '', '', '', '', '', '');
        """
    )
    con.commit()
    con.close()


def make_seed(path, rows=((4001, "1"), (4003, "12"))):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE parcel_results (bfs INTEGER NOT NULL, parcel TEXT NOT NULL, "
        "PRIMARY KEY (bfs, parcel))"
    )
    con.executemany("INSERT INTO parcel_results VALUES (?, ?)", rows)
    con.commit()
    con.close()


@pytest.fixture
def dbs(tmp_path):
    live, seed = str(tmp_path / "live.sqlite"), str(tmp_path / "seed.sqlite")
    make_live(live)
    make_seed(seed)
    return live, seed


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def test_zero_unmatched_leads_writes_header_only(tmp_path, capsys):
    live, seed = str(tmp_path / "live.sqlite"), str(tmp_path / "seed.sqlite")
    make_live(live)
    make_seed(seed, rows=((4001, "1"), (4002, "9"), (4003, "12"), (4004, "15")))
    output = str(tmp_path / "report.csv")

    assert report.main(["--live-db", live, "--seed-db", seed, "--output", output]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == f"0 unmatched saved lead(s) -> {output}"
    table = read_csv(output)
    assert table[0][:3] == ["bfs", "parcel", "saved"]
    assert len(table) == 1  # header only


def test_reports_exact_keys_and_contact_fields(tmp_path, dbs, capsys):
    live, seed = dbs
    output = str(tmp_path / "report.csv")

    assert report.main(["--live-db", live, "--seed-db", seed, "--output", output]) == 0

    table = read_csv(output)
    header = table[0]
    assert header[:3] == ["bfs", "parcel", "saved"]
    # every entered contact/workflow field rides along
    for field in (
        "owner_name", "contact_status", "due_date", "last_contact", "next_step",
        "note", "contact_person", "owner_address", "phone", "email",
    ):
        assert field in header
    # exactly the two saved leads whose parcel is missing from the seed
    assert [(row[0], row[1]) for row in table[1:]] == [("4002", "9"), ("4004", "15")]
    # stdout carries the count and path only — no PII
    out = capsys.readouterr().out
    assert "2 unmatched saved lead(s)" in out and output in out
    assert "Muster" not in out and "muster@example.com" not in out
    # and the untouched lead's details are nowhere in the CSV either
    assert "Muster AG" not in open(output, encoding="utf-8").read()


def test_csv_contents_escape_formula_injection(tmp_path, dbs):
    live, seed = dbs
    output = str(tmp_path / "report.csv")
    with sqlite3.connect(live) as con:
        con.execute(
            "UPDATE parcel_workflow SET note = ? WHERE bfs = 4004",
            ("  =HYPERLINK(\"http://evil\")",),
        )

    report.main(["--live-db", live, "--seed-db", seed, "--output", output])

    table = read_csv(output)
    by_key = {row[1]: dict(zip(table[0], row)) for row in table[1:]}
    assert by_key["9"]["owner_name"] == "'=HYPERLINK(\"http://evil\")"
    assert by_key["9"]["note"] == "'=-2+3"
    assert by_key["9"]["phone"] == "'-lead@example.com"
    assert by_key["9"]["email"] == "'@mention"
    assert by_key["15"]["note"] == "'  =HYPERLINK(\"http://evil\")"


def test_databases_are_not_modified(tmp_path, dbs):
    live, seed = dbs
    before = {p: digest(p) for p in (live, seed)}
    output = str(tmp_path / "report.csv")

    report.main(["--live-db", live, "--seed-db", seed, "--output", output])

    assert {p: digest(p) for p in (live, seed)} == before
    # read-only access leaves no WAL/SHM sidecars behind
    for path in (live, seed):
        assert not os.path.exists(path + "-wal")
        assert not os.path.exists(path + "-shm")


def test_refuses_to_overwrite_existing_output(tmp_path, dbs, capsys):
    live, seed = dbs
    output = str(tmp_path / "report.csv")
    with open(output, "w", encoding="utf-8") as fh:
        fh.write("precious")

    assert report.main(["--live-db", live, "--seed-db", seed, "--output", output]) == 2

    assert open(output, encoding="utf-8").read() == "precious"
    assert "refusing to overwrite" in capsys.readouterr().err


def test_output_permissions_are_0600(tmp_path, dbs):
    live, seed = dbs
    output = str(tmp_path / "report.csv")

    report.main(["--live-db", live, "--seed-db", seed, "--output", output])

    assert os.stat(output).st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "kind",
    ["missing live", "garbage live", "missing workflow table", "legacy workflow"],
)
def test_invalid_live_database_fails_clearly(tmp_path, capsys, kind):
    live, seed = str(tmp_path / "live.sqlite"), str(tmp_path / "seed.sqlite")
    make_seed(seed)
    if kind == "missing live":
        pass  # never created
    elif kind == "garbage live":
        with open(live, "wb") as fh:
            fh.write(b"this is not a sqlite file at all")
    elif kind == "missing workflow table":
        con = sqlite3.connect(live)
        con.execute("CREATE TABLE something_else (id INTEGER)")
        con.close()
    else:  # legacy workflow without the saved column
        con = sqlite3.connect(live)
        con.execute(
            "CREATE TABLE parcel_workflow (bfs INTEGER, parcel TEXT, PRIMARY KEY (bfs, parcel))"
        )
        con.close()
    output = str(tmp_path / "report.csv")

    assert report.main(["--live-db", live, "--seed-db", seed, "--output", output]) == 2

    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert not os.path.exists(output)


@pytest.mark.parametrize("kind", ["missing seed", "seed without parcel_results"])
def test_invalid_seed_database_fails_clearly(tmp_path, capsys, kind):
    live, seed = str(tmp_path / "live.sqlite"), str(tmp_path / "seed.sqlite")
    make_live(live)
    if kind == "seed without parcel_results":
        con = sqlite3.connect(seed)
        con.execute("CREATE TABLE runs (bfs INTEGER PRIMARY KEY)")
        con.close()
    output = str(tmp_path / "report.csv")

    assert report.main(["--live-db", live, "--seed-db", seed, "--output", output]) == 2

    assert capsys.readouterr().err.startswith("error:")
    assert not os.path.exists(output)


def test_seed_results_missing_parcel_fails_before_query_and_output(tmp_path, capsys):
    """A seed whose parcel_results lacks `parcel` (divergent schema) must be
    rejected by the column check, not by a raw "no such column" from the
    anti-join after the real query started — and no CSV may appear."""
    live, seed = str(tmp_path / "live.sqlite"), str(tmp_path / "seed.sqlite")
    make_live(live)
    con = sqlite3.connect(seed)
    con.execute("CREATE TABLE parcel_results (bfs INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO parcel_results VALUES (4001)")
    con.commit()
    con.close()
    output = str(tmp_path / "report.csv")

    assert report.main(["--live-db", live, "--seed-db", seed, "--output", output]) == 2

    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "'parcel'" in err and "missing required column" in err
    assert not os.path.exists(output)


def test_seed_results_with_extra_columns_reports_normally(tmp_path, capsys):
    """Reverse divergence: a newer seed carries columns the live workflow
    lacks. The column check must read the seed's own schema and the report
    must still run on the workflow's columns."""
    live, seed = str(tmp_path / "live.sqlite"), str(tmp_path / "seed.sqlite")
    make_live(live)
    con = sqlite3.connect(seed)
    con.execute(
        "CREATE TABLE parcel_results (bfs INTEGER NOT NULL, parcel TEXT NOT NULL, "
        "delta REAL, PRIMARY KEY (bfs, parcel))"
    )
    con.execute("INSERT INTO parcel_results VALUES (4001, '1', 2.5)")
    con.commit()
    con.close()
    output = str(tmp_path / "report.csv")

    assert report.main(["--live-db", live, "--seed-db", seed, "--output", output]) == 0

    table = read_csv(output)
    assert [(row[0], row[1]) for row in table[1:]] == [("4002", "9"), ("4004", "15")]
    assert capsys.readouterr().out.strip() == f"2 unmatched saved lead(s) -> {output}"
