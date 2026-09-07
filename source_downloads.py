"""On-demand, bounded ZIP downloads of public legal-source PDFs.

Only the three public document endpoints below are fetched. Other valid web
references remain links in the archive index; this is not a general URL proxy.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
import http.client
import io
import queue
import socket
import ssl
import threading
import time
from urllib.parse import unquote, urlsplit, urlunsplit
import zipfile


MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_ARCHIVE_SIZE = 50 * 1024 * 1024
MAX_DOWNLOADS = 40
MAX_URL_LENGTH = 2048
MAX_TITLE_LENGTH = 300
DOWNLOAD_TIMEOUT = 5.0
TOTAL_TIMEOUT = 25.0
_CHUNK_SIZE = 64 * 1024
_PDF_ENDPOINTS = {
    "oereblex.ag.ch": "/api/attachments/",
    "gesetzessammlungen.ag.ch": "/api/de/versions/",
    "data.geo.admin.ch": "/",
}


@dataclass(frozen=True, slots=True)
class Source:
    title: str
    url: str


class _DownloadFailure(Exception):
    """A safe, user-facing failure description, never a raw network error."""


def _web_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        return None
    if any(ord(char) < 33 or 127 <= ord(char) <= 159 for char in value):
        return None
    if "\\" in value:
        return None
    decoded = unquote(value)
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in decoded):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        parts.port  # Reject malformed/out-of-range ports even for index links.
    except ValueError:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))


def _source(title: object, url: object) -> Source | None:
    clean_url = _web_url(url)
    if clean_url is None:
        return None
    clean_title = str(title or "Dokument").strip()[:MAX_TITLE_LENGTH] or "Dokument"
    return Source(clean_title, clean_url)


def references(extract: Mapping | None) -> tuple[Source, ...]:
    """Deduplicated web links from the normalized ``oereb.details`` extract."""
    if not isinstance(extract, Mapping):
        return ()
    result, seen = [], set()
    for key in ("provisions", "laws"):
        documents = extract.get(key) or []
        if not isinstance(documents, (list, tuple)):
            continue
        for document in documents:
            if not isinstance(document, Mapping):
                continue
            urls = document.get("urls") or []
            if not isinstance(urls, (list, tuple)):
                continue
            for url in urls:
                item = _source(document.get("title"), url)
                if item is not None and item.url not in seen:
                    seen.add(item.url)
                    result.append(item)
    return tuple(result)


def _pdf_endpoint(url: str) -> bool:
    parts = urlsplit(url)
    prefix = _PDF_ENDPOINTS.get(parts.hostname or "")
    if parts.scheme != "https" or parts.port not in (None, 443) or prefix is None:
        return False
    # Encoded separators/dot segments must not escape the approved path prefix.
    decoded_path = unquote(parts.path)
    if decoded_path != parts.path or any(part in {".", ".."} for part in parts.path.split("/")):
        return False
    return parts.path.startswith(prefix)


def _fetch_pdf(url: str, deadline: float) -> bytes:
    """Fetch with an absolute per-file deadline, including slow HTTP headers.

    A daemon worker also bounds DNS resolution from the caller's perspective.
    On timeout its socket is interrupted; if DNS is still pending it checks the
    cancellation flag before sending a request once resolution returns.
    HTTPSConnection has neither proxy-environment support nor redirect following.
    """
    budget = min(DOWNLOAD_TIMEOUT, deadline - time.monotonic())
    if budget <= 0:
        raise _DownloadFailure("Gesamtzeitlimit erreicht; nicht heruntergeladen.")
    parts = urlsplit(url)
    connection = http.client.HTTPSConnection(
        parts.hostname, port=443, timeout=budget, context=ssl.create_default_context(),
    )
    cancelled = threading.Event()
    result = queue.Queue(maxsize=1)
    file_deadline = time.monotonic() + budget

    def check_time():
        if cancelled.is_set() or time.monotonic() >= file_deadline:
            raise _DownloadFailure("Zeitlimit beim Herunterladen erreicht.")

    def worker():
        try:
            connection.connect()
            check_time()
            target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            connection.request("GET", target, headers={"Accept": "application/pdf"})
            check_time()
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise _DownloadFailure("Weiterleitung blockiert; Original-Link prüfen.")
            if response.status != 200:
                raise _DownloadFailure(f"Download fehlgeschlagen (HTTP {response.status}).")
            length = response.getheader("Content-Length")
            declared_size = None
            if length is not None:
                try:
                    declared_size = int(length)
                except ValueError:
                    raise _DownloadFailure("Ungültige Dateigrösse gemeldet.") from None
                if declared_size < 0 or declared_size > MAX_FILE_SIZE:
                    raise _DownloadFailure("PDF überschreitet das Dateilimit von 10 MiB.")
            data = bytearray()
            while True:
                check_time()
                chunk = response.read1(min(_CHUNK_SIZE, MAX_FILE_SIZE - len(data) + 1))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_FILE_SIZE:
                    raise _DownloadFailure("PDF überschreitet das Dateilimit von 10 MiB.")
            check_time()
            if declared_size is not None and len(data) != declared_size:
                raise _DownloadFailure("PDF wurde unvollständig übertragen; nur Original-Link enthalten.")
            if not data.startswith(b"%PDF-"):
                raise _DownloadFailure("Antwort ist keine PDF-Datei; nur Original-Link enthalten.")
            result.put(bytes(data))
        except _DownloadFailure as error:
            result.put(error)
        except (OSError, http.client.HTTPException, ValueError):
            result.put(_DownloadFailure("Download fehlgeschlagen; Original-Link prüfen."))
        finally:
            connection.close()

    threading.Thread(target=worker, daemon=True, name="legal-pdf-download").start()
    try:
        value = result.get(timeout=budget)
    except queue.Empty:
        cancelled.set()
        active_socket = connection.sock
        if active_socket is not None:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        raise _DownloadFailure("Zeitlimit beim Herunterladen erreicht.") from None
    if isinstance(value, _DownloadFailure):
        raise value
    return value


def _index(entries: list[tuple[Source, str, str]]) -> bytes:
    rows = []
    for source, filename, status in entries:
        local = f' · <a href="{escape(filename, quote=True)}">PDF im Archiv</a>' if filename else ""
        rows.append(
            f"<li><strong>{escape(source.title)}</strong><br>"
            f'<a href="{escape(source.url, quote=True)}" rel="noreferrer">Originalquelle</a>'
            f"{local}<br>{escape(status)}</li>"
        )
    included = sum(bool(filename) for _, filename, _ in entries)
    return (
        '<!doctype html><html lang="de"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer"><title>Rechtsgrundlagen – Quellen</title>'
        '<h1>Rechtsgrundlagen – Quellen</h1>'
        f'<p>{included} PDF-Dateien enthalten · {len(entries)} Referenzen.</p>'
        '<p>Nicht enthaltene Dokumente sind unten mit ihrem Grund markiert. '
        'Webseiten werden nicht als PDF heruntergeladen. Grenzen: 40 Downloads, '
        '10 MiB je Datei, 50 MiB je Archiv und 25 Sekunden Gesamtzeit.</p>'
        '<ul>' + "".join(rows) + '</ul></html>'
    ).encode("utf-8")


def build_archive(sources: Sequence[Source]) -> bytes:
    """Build a ZIP only when requested; failures remain visible in Quellen.html."""
    unique, seen = [], set()
    for source in sources:
        if not isinstance(source, Source):
            raise ValueError("Ungültige Quellenreferenz.")
        item = _source(source.title, source.url)
        if item is None:
            raise ValueError("Ungültiger Quellen-Link.")
        if item.url not in seen:
            seen.add(item.url)
            unique.append(item)

    # Reserve enough index space for every source and a bounded status message.
    entries = [(source, "", "Noch nicht heruntergeladen.") for source in unique]
    reserve = len(_index(entries)) + 1024 * (len(entries) + 1)
    if reserve >= MAX_ARCHIVE_SIZE:
        raise ValueError("Zu viele Quellen für das Archivlimit.")
    output = io.BytesIO()
    deadline = time.monotonic() + TOTAL_TIMEOUT
    attempts = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for index, source in enumerate(unique):
            filename = ""
            if not _pdf_endpoint(source.url):
                status = "Webseite / nicht freigegebener PDF-Endpunkt: nur Original-Link enthalten."
            elif attempts >= MAX_DOWNLOADS:
                status = "Downloadlimit von 40 Dokumenten erreicht; nur Original-Link enthalten."
            elif time.monotonic() >= deadline:
                status = "Gesamtzeitlimit erreicht; nur Original-Link enthalten."
            else:
                attempts += 1
                try:
                    data = _fetch_pdf(source.url, deadline)
                except _DownloadFailure as error:
                    status = str(error)
                else:
                    if output.tell() + len(data) + reserve > MAX_ARCHIVE_SIZE:
                        status = "Archivlimit von 50 MiB erreicht; nur Original-Link enthalten."
                    else:
                        filename = f"Dokument-{index + 1:03d}.pdf"
                        archive.writestr(filename, data)
                        status = "PDF erfolgreich heruntergeladen."
            entries[index] = (source, filename, status)
        archive.writestr("Quellen.html", _index(entries))
    if output.tell() > MAX_ARCHIVE_SIZE:
        raise ValueError("Archivlimit überschritten.")
    return output.getvalue()
