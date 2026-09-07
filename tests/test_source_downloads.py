import dataclasses
import io
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

import source_downloads as downloads


PDF_URL = "https://oereblex.ag.ch/api/attachments/1289"
PDF = b"%PDF-1.7\npublic source\n%%EOF\n"


class Response:
    def __init__(self, body=PDF, status=200, headers=None):
        self.body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def getheader(self, name):
        return self.headers.get(name)

    def read1(self, size):
        return self.body.read(size)


class Connection:
    def __init__(self, response=None):
        self.response = response or Response()
        self.sock = Mock()
        self.requests = []
        self.connected = False
        self.closed = threading.Event()

    def connect(self):
        self.connected = True

    def request(self, method, target, headers):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed.set()


def contents(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


class ReferencesTest(unittest.TestCase):
    def test_normalized_extract_deduplicates_links_and_is_immutable(self):
        sources = downloads.references({
            "provisions": [{"title": "Plan", "urls": [PDF_URL, PDF_URL + "#page=2"]}],
            "laws": [{"title": "Duplicate", "urls": [PDF_URL]},
                     {"title": "Law", "urls": ["http://www.ag.ch/law"]}],
        })
        self.assertEqual(sources, (
            downloads.Source("Plan", PDF_URL),
            downloads.Source("Law", "http://www.ag.ch/law"),
        ))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            sources[0].url = "https://example.com"

    def test_invalid_schemes_credentials_controls_and_long_urls_are_rejected(self):
        invalid = [
            "file:///etc/passwd", "javascript:alert(1)",
            "https://user:password@oereblex.ag.ch/api/attachments/1",
            "https://oereblex.ag.ch/api/attachments/1\n",
            "https://oereblex.ag.ch/api/attachments/1%0d%0aHost:evil",
            "https://oereblex.ag.ch:bad/api/attachments/1",
            "https://oereblex.ag.ch:99999/api/attachments/1",
            "https://oereblex.ag.ch\\@example.com/x",
            "https://example.com/" + "a" * downloads.MAX_URL_LENGTH,
        ]
        sources = downloads.references({"laws": [{"urls": invalid + [PDF_URL]}]})
        self.assertEqual(sources, (downloads.Source("Dokument", PDF_URL),))

    def test_missing_and_malformed_extract_entries_are_ignored(self):
        for extract in (None, {}, {"laws": "invalid"}, {"laws": [None, {"urls": "invalid"}]}):
            self.assertEqual(downloads.references(extract), ())


class ArchiveTest(unittest.TestCase):
    def test_successful_pdf_uses_generated_filename_and_verified_direct_https(self):
        connection = Connection()
        with patch("source_downloads.http.client.HTTPSConnection", return_value=connection) as connect:
            files = contents(downloads.build_archive((downloads.Source("../../Plan", PDF_URL),)))
        self.assertEqual(files["Dokument-001.pdf"], PDF)
        self.assertEqual(set(files), {"Dokument-001.pdf", "Quellen.html"})
        self.assertIn("PDF erfolgreich heruntergeladen", files["Quellen.html"].decode())
        self.assertEqual(connect.call_args.args, ("oereblex.ag.ch",))
        self.assertEqual(connect.call_args.kwargs["port"], 443)
        self.assertTrue(connect.call_args.kwargs["context"].check_hostname)
        self.assertLessEqual(connect.call_args.kwargs["timeout"], 5)
        self.assertEqual(connection.requests, [
            ("GET", "/api/attachments/1289", {"Accept": "application/pdf"}),
        ])
        self.assertTrue(connection.closed.wait(1))

    def test_all_approved_hosts_can_supply_pdfs(self):
        urls = [PDF_URL,
                "https://gesetzessammlungen.ag.ch:443/api/de/versions/3985/pdf_file_with_annexes",
                "https://data.geo.admin.ch/ch.test/source.pdf"]
        with patch("source_downloads.http.client.HTTPSConnection", side_effect=lambda *a, **k: Connection()) as connect:
            files = contents(downloads.build_archive(tuple(downloads.Source("PDF", url) for url in urls)))
        self.assertEqual(connect.call_count, 3)
        self.assertEqual(sum(name.endswith(".pdf") for name in files), 3)

    def test_other_web_links_are_indexed_escaped_and_never_fetched(self):
        urls = [
            "https://example.com/file.pdf?x=1&y=2",
            "http://oereblex.ag.ch/api/attachments/1",
            "https://oereblex.ag.ch.example.com/api/attachments/1",
            "https://oereblex.ag.ch:8443/api/attachments/1",
            "https://oereblex.ag.ch/private/file.pdf",
            "https://oereblex.ag.ch/api/attachments/../private/file.pdf",
            "https://oereblex.ag.ch/api/attachments/%2e%2e/private/file.pdf",
            "http://127.0.0.1/private",
        ]
        with patch("source_downloads.http.client.HTTPSConnection") as connect:
            files = contents(downloads.build_archive(tuple(
                downloads.Source('<script>alert("x")</script>', url) for url in urls
            )))
        connect.assert_not_called()
        self.assertEqual(set(files), {"Quellen.html"})
        index = files["Quellen.html"].decode()
        self.assertIn("&lt;script&gt;", index)
        self.assertNotIn("<script>", index)
        self.assertIn("x=1&amp;y=2", index)
        self.assertIn("Webseite / nicht freigegebener PDF-Endpunkt", index)
        self.assertIn("0 PDF-Dateien enthalten", index)

    def test_direct_archive_inputs_are_validated_and_deduplicated(self):
        with patch("source_downloads.http.client.HTTPSConnection", side_effect=lambda *a, **k: Connection()) as connect:
            files = contents(downloads.build_archive((
                downloads.Source("One", PDF_URL), downloads.Source("Two", PDF_URL),
            )))
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(len(files), 2)
        with self.assertRaises(ValueError):
            downloads.build_archive((downloads.Source("Invalid", "file:///etc/passwd"),))

    def test_redirects_are_not_followed_even_to_an_approved_host(self):
        connection = Connection(Response(status=302, headers={"Location": PDF_URL + "2"}))
        with patch("source_downloads.http.client.HTTPSConnection", return_value=connection) as connect:
            files = contents(downloads.build_archive((downloads.Source("Plan", PDF_URL),)))
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(len(connection.requests), 1)
        self.assertEqual(set(files), {"Quellen.html"})
        self.assertIn("Weiterleitung blockiert", files["Quellen.html"].decode())

    def test_non_pdf_is_not_misrepresented_as_a_download(self):
        connection = Connection(Response(b"<html>login or error</html>"))
        with patch("source_downloads.http.client.HTTPSConnection", return_value=connection):
            files = contents(downloads.build_archive((downloads.Source("Plan", PDF_URL),)))
        self.assertEqual(set(files), {"Quellen.html"})
        self.assertIn("keine PDF-Datei", files["Quellen.html"].decode())

    def test_oversized_declared_and_streamed_files_are_rejected(self):
        responses = [Response(headers={"Content-Length": "101"}), Response(b"%PDF-" + b"x" * 100)]
        for response in responses:
            with self.subTest(headers=response.headers):
                with patch.object(downloads, "MAX_FILE_SIZE", 100), patch(
                    "source_downloads.http.client.HTTPSConnection", return_value=Connection(response),
                ):
                    files = contents(downloads.build_archive((downloads.Source("Plan", PDF_URL),)))
                self.assertEqual(set(files), {"Quellen.html"})
                self.assertIn("Dateilimit", files["Quellen.html"].decode())

    def test_download_count_limit_is_reported_without_more_network_calls(self):
        sources = tuple(downloads.Source("Plan", PDF_URL + str(i)) for i in range(3))
        with patch.object(downloads, "MAX_DOWNLOADS", 2), patch(
            "source_downloads.http.client.HTTPSConnection", side_effect=lambda *a, **k: Connection(),
        ) as connect:
            files = contents(downloads.build_archive(sources))
        self.assertEqual(connect.call_count, 2)
        self.assertIn("Downloadlimit", files["Quellen.html"].decode())

    def test_truncated_pdf_is_not_reported_as_successful(self):
        connection = Connection(Response(PDF, headers={"Content-Length": str(len(PDF) + 10)}))
        with patch("source_downloads.http.client.HTTPSConnection", return_value=connection):
            files = contents(downloads.build_archive((downloads.Source("Plan", PDF_URL),)))
        self.assertEqual(set(files), {"Quellen.html"})
        self.assertIn("unvollständig übertragen", files["Quellen.html"].decode())

    def test_archive_size_limit_does_not_silently_drop_a_document(self):
        sources = (downloads.Source("First", PDF_URL), downloads.Source("Second", PDF_URL + "2"))
        large_pdf = b"%PDF-" + b"x" * 4995
        with patch.object(downloads, "MAX_ARCHIVE_SIZE", 10000), patch(
            "source_downloads.http.client.HTTPSConnection",
            side_effect=lambda *a, **k: Connection(Response(large_pdf)),
        ):
            archive = downloads.build_archive(sources)
        self.assertLessEqual(len(archive), 10000)
        files = contents(archive)
        self.assertEqual(sum(name.endswith(".pdf") for name in files), 1)
        self.assertIn("Archivlimit", files["Quellen.html"].decode())

    def test_http_failure_status_is_clear_and_does_not_expose_error_details(self):
        connection = Connection(Response(status=404))
        with patch("source_downloads.http.client.HTTPSConnection", return_value=connection):
            files = contents(downloads.build_archive((downloads.Source("Plan", PDF_URL),)))
        self.assertIn("HTTP 404", files["Quellen.html"].decode())
        connection = Connection()
        connection.connect = Mock(side_effect=OSError("sensitive diagnostic detail"))
        with patch("source_downloads.http.client.HTTPSConnection", return_value=connection):
            files = contents(downloads.build_archive((downloads.Source("Plan", PDF_URL),)))
        index = files["Quellen.html"].decode()
        self.assertIn("Download fehlgeschlagen", index)
        self.assertNotIn("sensitive diagnostic detail", index)

    def test_absolute_timeout_bounds_stalled_header_and_total_download_time(self):
        release = threading.Event()
        connection = Connection()
        connection.getresponse = lambda: (release.wait(1), Response())[1]
        sources = (downloads.Source("First", PDF_URL), downloads.Source("Second", PDF_URL + "2"))
        started = time.monotonic()
        try:
            with patch.object(downloads, "TOTAL_TIMEOUT", .05), patch.object(downloads, "DOWNLOAD_TIMEOUT", .05), patch(
                "source_downloads.http.client.HTTPSConnection", return_value=connection,
            ) as connect:
                files = contents(downloads.build_archive(sources))
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(connect.call_count, 1)
            connection.sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
            self.assertIn("Zeitlimit", files["Quellen.html"].decode())
            self.assertIn("Gesamtzeitlimit", files["Quellen.html"].decode())
        finally:
            release.set()
            self.assertTrue(connection.closed.wait(1))

    def test_cancelled_resolution_cannot_start_request_after_caller_times_out(self):
        release = threading.Event()
        connection = Connection()
        connection.connect = lambda: release.wait(1)
        try:
            with patch.object(downloads, "DOWNLOAD_TIMEOUT", .02), patch(
                "source_downloads.http.client.HTTPSConnection", return_value=connection,
            ):
                files = contents(downloads.build_archive((downloads.Source("Plan", PDF_URL),)))
            self.assertIn("Zeitlimit", files["Quellen.html"].decode())
        finally:
            release.set()
            self.assertTrue(connection.closed.wait(1))
        self.assertEqual(connection.requests, [])


if __name__ == "__main__":
    unittest.main()
