"""Regression tests for the Bandit remediation (docs/2026-09-29-bandit-review.md).

Covers the two new helpers — `sqlquote.ident` (B608) and `http_fetch` (B310) —
against a real in-memory SQLite database and plain-urllib objects. Every
residual annotated Bandit finding in the production modules cites these tests
as its provenance.
"""
import sqlite3
import unittest
import urllib.error
import urllib.request
from unittest import mock

import http_fetch
import oereb
from sqlquote import ident

import economics as E
import ingest


class IdentQuotingTest(unittest.TestCase):
    def test_rejects_non_string_empty_and_nul(self):
        for bad in (None, 42, b"name", "", 'nul\x00name'):
            with self.assertRaises((TypeError, ValueError)):
                ident(bad)

    def test_embedded_quote_is_doubled(self):
        self.assertEqual(ident('a"b'), '"a""b"')

    def test_odd_but_legal_names_work_on_real_sqlite(self):
        # Spaces, a leading digit, unicode, and an embedded double quote are
        # all legitimate SQLite identifiers once delimited — the whole reason
        # quoting must not be an ASCII-restricted regex.
        db = sqlite3.connect(":memory:")
        for name in ('my table', '7tablé', 'quo"te', 'tabelle «ä»'):
            with self.subTest(name=name):
                db.execute(f"CREATE TABLE {ident(name)} (val INTEGER)")
                db.execute(f"INSERT INTO {ident(name)} (val) VALUES (1)")
                (val,) = db.execute(f"SELECT val FROM {ident(name)}").fetchone()
                self.assertEqual(val, 1)

    def test_malicious_looking_name_cannot_inject(self):
        # A name that LOOKS like an injection attempt must become a boring
        # table name; the neighbouring table has to survive untouched.
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE safe (val INTEGER)")
        attacker = 'x"; DROP TABLE safe; --'
        db.execute(f"CREATE TABLE {ident(attacker)} (val INTEGER)")
        db.execute(f"INSERT INTO {ident(attacker)} (val) VALUES (2)")
        db.execute("INSERT INTO safe (val) VALUES (1)")
        rows = db.execute(
            "SELECT val FROM safe ORDER BY val"
        ).fetchall()
        self.assertEqual(rows, [(1,)])
        (val,) = db.execute(
            f"SELECT val FROM {ident(attacker)}"
        ).fetchone()
        self.assertEqual(val, 2)

    def test_select_with_where_shape_used_by_constraints(self):
        # The exact statement shape constraints.py builds: identifier from
        # gpkg_contents, rest static, values bound.
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE zones (SHAPE TEXT, KTBez TEXT)")
        db.execute("INSERT INTO zones VALUES ('a', 'Gebäude mit Substanzschutz')")
        t = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone()[0]
        rows = list(db.execute(
            f"SELECT SHAPE, KTBez FROM {ident(t)} WHERE KTBez LIKE 'Geb%schutz'"
        ))
        self.assertEqual(rows, [("a", "Gebäude mit Substanzschutz")])

    def test_ingest_insert_statement_is_parameter_bound(self):
        # The parcel_results INSERT in ingest.{recompute,main} joins the
        # COLUMNS names into the statement but binds every VALUE as a named
        # parameter — names come from the fixed COLUMNS table, values never
        # reach SQL text. Replay it against an in-memory database.
        db = sqlite3.connect(":memory:")
        cols = ", ".join(ident(n) for n, _ in ingest.COLUMNS)
        db.execute(f"CREATE TABLE parcel_results ({cols})")
        names = [n for n, _ in ingest.COLUMNS]
        row = {n: None for n in names}
        row.update({"bfs": 4001, "parcel": "1234", "egrid": "CH1"})
        db.execute(
            f"INSERT OR REPLACE INTO parcel_results ({','.join(names)}) "
            f"VALUES ({','.join(':' + n for n in names)})",
            row,
        )
        got = db.execute(
            "SELECT bfs, parcel, egrid FROM parcel_results"
        ).fetchone()
        self.assertEqual(got, (4001, "1234", "CH1"))


class HttpFetchPolicyTest(unittest.TestCase):
    def test_allowed_endpoints_accepted(self):
        for url in (
            "https://geodienste.ch/db/av_0/deu?SERVICE=WFS",
            "https://api.geo.ag.ch/v2/oereb/extract/json/?EGRID=CH1",
            "https://oereblex.ag.ch/api/edicts.json",
            "https://geodienste.ch:443/db/av_0/deu",
        ):
            with self.subTest(url=url):
                self.assertEqual(http_fetch._check_url(url), urlsplit_host(url))

    def test_rejected_urls(self):
        for url in (
            "http://geodienste.ch/db/av_0/deu",          # plain http
            "ftp://geodienste.ch/x",                      # other scheme
            "https://evil.ch/x",                          # wrong host
            "https://notgeodienste.ch/x",                 # look-alike host
            "https://sub.geodienste.ch/x",                # subdomain ≠ endpoint
            "https://geodienste.ch:8443/x",               # wrong port
            "https://user:pass@geodienste.ch/x",          # credentials
            'https://geodienste.ch/x\nHost: evil.ch',     # control character
            "https://geodienste.ch/\\@evil.ch",           # backslash
            "",                                           # empty
            None,                                         # non-string
        ):
            with self.subTest(url=url):
                with self.assertRaises((TypeError, ValueError)):
                    http_fetch._check_url(url)

    def test_same_origin_redirect_followed(self):
        handler = http_fetch._SameOriginRedirect("geodienste.ch")
        req = urllib.request.Request("https://geodienste.ch/db/av_0/deu?x=1")
        new = handler.redirect_request(
            req, None, 302, "Found", {}, "https://geodienste.ch/moved?y=2"
        )
        self.assertIsNotNone(new)
        self.assertEqual(new.full_url, "https://geodienste.ch/moved?y=2")

    def test_cross_host_redirect_refused(self):
        handler = http_fetch._SameOriginRedirect("geodienste.ch")
        req = urllib.request.Request("https://geodienste.ch/db/av_0/deu")
        for target in (
            "https://evil.ch/x",
            "http://geodienste.ch/x",        # scheme downgrade on same host
            "https://oereblex.ag.ch/x",      # another ALLOWED host, still cross-origin
        ):
            with self.subTest(target=target):
                with self.assertRaises((urllib.error.URLError, ValueError)):
                    handler.redirect_request(req, None, 302, "Found", {}, target)

    def test_ambient_proxies_disabled(self):
        # build_opener installs a ProxyHandler populated from getproxies() by
        # default. Passing ProxyHandler({}) suppresses that default; the empty
        # handler itself contributes no open methods, so the opener resolves
        # no proxies at all and requests go direct.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            http_fetch._SameOriginRedirect("geodienste.ch"),
        )
        installed = [
            h for h in opener.handlers
            if isinstance(h, urllib.request.ProxyHandler)
        ]
        self.assertTrue(all(not h.proxies for h in installed))
        self.assertNotIn(
            urllib.request.getproxies(),
            [h.proxies for h in installed],
        )

    def test_oereb_egrid_is_urlencoded(self):
        # The EGRID arrives from parcel XML; it must stay one opaque query
        # value, never string-interpolated into the URL.
        seen = {}

        def fake_get(url, timeout):
            seen["url"] = url
            seen["timeout"] = timeout
            return b"{}"

        with mock.patch.object(oereb.http_fetch, "get", fake_get):
            doc = oereb.fetch("CH1&EGRID=evil", timeout=7)
        self.assertEqual(doc, {})
        self.assertEqual(
            seen["url"],
            "https://api.geo.ag.ch/v2/oereb/extract/json/?EGRID=CH1%26EGRID%3Devil",
        )
        self.assertEqual(seen["timeout"], 7)


class EconomicsFormulaAuditTest(unittest.TestCase):
    def test_formulas_reference_only_known_symbols(self):
        """The B307 suppression on economics.evaluate holds only while every
        PATH expression resolves exclusively to the declared inputs and the
        outputs of earlier rules — nothing that could reach a builtin."""
        known = set(E.INPUTS)
        for rule in E.PATH:
            for symbol in E._NAME.findall(rule.expr):
                self.assertIn(
                    symbol,
                    known,
                    f"{rule.key}: unknown symbol {symbol!r} in {rule.expr!r}",
                )
            known.add(rule.key)


def urlsplit_host(url):
    import urllib.parse
    return urllib.parse.urlsplit(url).hostname


if __name__ == "__main__":
    unittest.main()
