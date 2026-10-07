"""Explicit-run public search metadata snapshots for human review.

The output is staging, never an amtsblatt_import batch or a planning store.
Only search HTML/JSON is fetched; article text and PDFs are not followed.
"""
import argparse
import fcntl
import http.client
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit

import amtsblatt as A

SCHEMA = "scope.planning-candidates"
VERSION = 1
MAX_PAGES = 50
MAX_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 40 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
AG_ORIGIN = "https://www.ag.ch"
CONSULTATION_PARENT = "/de/themen/staat-politik/anhoerungen-vernehmlassungen/"
CONSULTATION_PATHS = {
    "current": CONSULTATION_PARENT + "laufende-anhoerungen",
    "archive": CONSULTATION_PARENT + "archivierte-anhoerungen",
}
SEARCH_API = AG_ORIGIN + "/app/search-service/api/v1/dynamiccontent"
TAG_QUERY = {"filters": [{"filters": [
    {"field": "tagIds", "value": "063d5a66-595b-4a2b-9279-7e62b48d2815", "operator": "eq"},
    {"field": "tagIds", "value": "f0040d54-5b61-460d-9e8f-a4d8281d20a0", "operator": "eq"},
], "operator": "or"}], "operator": "and"}
_UUID_DE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_de")
_PUB_NR = re.compile(r"\d{2}\.\d{3}\.\d{3}")


class CollectionError(ValueError):
    """A snapshot failed validation; no output should be replaced."""


def _require(condition, message):
    if not condition:
        raise CollectionError(message)


def _url(url, origin, paths):
    """Strict fixed-origin/path check, before any request or accepted link."""
    _require(isinstance(url, str) and not re.search(r"[\x00-\x20\x7f\\]", url), "Unsafe source URL")
    _require(not re.search(r"[\x00-\x1f\x7f\\]", unquote(url)), "Unsafe encoded source URL")
    try:
        parts, expected = urlsplit(url), urlsplit(origin)
        _require(parts.scheme == "https" and parts.hostname == expected.hostname
                 and parts.port in (None, 443) and parts.username is None and parts.password is None
                 and not parts.fragment, "Source URL leaves the allowed HTTPS origin")
    except ValueError as exc:
        raise CollectionError("Malformed source URL") from exc
    _require(parts.path in paths, "Source URL leaves the allowed search path")
    return parts


def _json(raw, label):
    _require(isinstance(raw, (str, bytes)), f"{label}: missing JSON value")
    try:
        # Ambiguous duplicate JSON keys are schema errors, not last-value wins.
        def pairs(items):
            result = {}
            for key, value in items:
                _require(key not in result, f"{label}: duplicate JSON field")
                result[key] = value
            return result
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise CollectionError(f"{label}: invalid JSON") from exc


class Transport:
    """Fixed HTTPS destinations, no ambient proxies, total per-request deadline.

    Redirects are inspected manually. Validators also enforce search filters;
    POST redirects that would turn a read-only search into GET are refused.
    """
    def __init__(self, timeout=DEFAULT_TIMEOUT, max_bytes=MAX_BYTES, connection_factory=None):
        _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
                 and math.isfinite(timeout) and 0 < timeout <= 120, "timeout must be 0–120 seconds (exclusive of 0)")
        _require(type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES, "Invalid response size limit")
        self.timeout, self.max_bytes = timeout, max_bytes
        self.connection_factory = connection_factory or http.client.HTTPSConnection

    def request(self, method, url, *, validate_url, body=None):
        _require(method in ("GET", "POST"), "Only read-only search GET/POST is supported")
        deadline, visited = time.monotonic() + self.timeout, set()

        def remaining():
            seconds = deadline - time.monotonic()
            _require(seconds > 0, "Source request timed out")
            return seconds

        try:
            for hop in range(4):
                host = urlsplit(url).hostname
                if host == "amtsblatt.ag.ch":
                    _url(url, "https://amtsblatt.ag.ch", {"/publikationen/"})
                    _require(method == "GET", "Amtsblatt search only supports GET")
                else:
                    parts = _url(url, AG_ORIGIN, {*CONSULTATION_PATHS.values(), urlsplit(SEARCH_API).path})
                    _require((method == "POST") == (parts.path == urlsplit(SEARCH_API).path),
                             "Wrong method for consultation search path")
                validate_url(url)
                _require(url not in visited, "Source redirect cycle")
                visited.add(url)
                parts = urlsplit(url)
                connection = self.connection_factory(parts.hostname, port=443, timeout=remaining())
                try:
                    headers = {"Accept": "application/json" if method == "POST" else "text/html",
                               "Accept-Encoding": "identity", "User-Agent": "Scope-local-metadata-review/1"}
                    if method == "POST":
                        headers["Content-Type"] = "application/json; charset=utf-8"
                    connection.request(method, parts.path + ("?" + parts.query if parts.query else ""),
                                       body=body, headers=headers)
                    sock = connection.sock
                    if sock is not None:
                        sock.settimeout(remaining())
                    response = connection.getresponse()
                    if response.status in (301, 302, 303, 307, 308):
                        _require(hop < 3, "Too many source redirects")
                        _require(method != "POST" or response.status in (307, 308),
                                 "Source redirect would change POST to GET")
                        location = response.getheader("Location")
                        _require(bool(location), "Source redirect has no Location")
                        # Validate the raw reference before urljoin can normalize traversal.
                        _require(not re.search(r"[\x00-\x20\x7f\\]", location)
                                 and not re.search(r"[\x00-\x1f\x7f\\]", unquote(location))
                                 and not any(unquote(p) in (".", "..") for p in urlsplit(location).path.split("/")),
                                 "Unsafe redirect reference")
                        url = urljoin(url, location)
                        validate_url(url)
                        continue
                    _require(response.status == 200, f"Source returned HTTP {response.status}")
                    _require(response.getheader("Content-Encoding", "identity").lower() in ("", "identity"),
                             "Unsupported source content encoding")
                    length = response.getheader("Content-Length")
                    if length is not None:
                        _require(length.isdigit() and int(length) <= self.max_bytes, "Source response exceeds size limit")
                    chunks, size = [], 0
                    while True:
                        if sock is not None:
                            sock.settimeout(remaining())
                        chunk = response.read1(min(65536, self.max_bytes + 1 - size))
                        remaining()
                        if not chunk:
                            break
                        size += len(chunk)
                        _require(size <= self.max_bytes, "Source response exceeds size limit")
                        chunks.append(chunk)
                    _require(length is None or size == int(length), "Incomplete source response")
                    return b"".join(chunks)
                finally:
                    connection.close()
        except CollectionError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise CollectionError(f"Source request failed ({type(exc).__name__})") from exc
        raise CollectionError("Too many source redirects")


@dataclass
class _Node:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)

    def text(self):
        return " ".join(" ".join(c.text() if isinstance(c, _Node) else c for c in self.children).split())

    def find(self, *, tag=None, css=None):
        result = []
        for child in self.children:
            if isinstance(child, _Node):
                if (tag is None or child.tag == tag) and (css is None or css in (child.attrs.get("class") or "").split()):
                    result.append(child)
                result.extend(child.find(tag=tag, css=css))
        return result


class _SearchHTML(HTMLParser):
    """Capture cards and widget configuration only; navigation is discarded."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards, self.metadata, self.next_links, self.widgets, self.stack = [], [], [], [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        relevant = (self.stack or "publication-list__item--publication" in classes
                    or "publication-search-result" in classes or "control-pagebrowser__next-page" in classes
                    or "data-api" in values)
        if relevant:
            _require(len(values) == len(attrs), "HTML search has duplicate attributes")
        if "publication-search-result" in classes:
            self.metadata.append(values)
        if "control-pagebrowser__next-page" in classes:
            _require(tag == "a" and bool(values.get("href")), "Malformed next-page link")
            self.next_links.append(values["href"])
        if "data-api" in values:
            self.widgets.append(values)
        if self.stack:
            _require(len(self.stack) < 64, "Publication card markup exceeds nesting limit")
            node = _Node(tag, values)
            self.stack[-1].children.append(node)
            if tag not in self.VOID:
                self.stack.append(node)
        elif "publication-list__item--publication" in classes:
            _require(tag == "div", "Malformed publication card")
            node = _Node(tag, values)
            self.cards.append(node)
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.stack:
            _require(self.stack[-1].tag == tag, "Unbalanced publication card markup")
            self.stack.pop()

    def handle_data(self, data):
        if self.stack:
            self.stack[-1].children.append(data)


def _html(raw):
    _require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, "Invalid or oversized HTML response")
    try:
        parser = _SearchHTML()
        parser.feed(raw.decode("utf-8"))
        parser.close()
        _require(not parser.stack, "Truncated publication card markup")
        return parser
    except UnicodeError as exc:
        raise CollectionError("Source HTML is not UTF-8") from exc


def _one(nodes, message):
    _require(len(nodes) == 1, message)
    return nodes[0]


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= 10000
             and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value), f"Missing or malformed {label}")
    return value.strip()


def _amtsblatt_card(card):
    article = _one(card.find(tag="article", css="publication-summary"), "Missing publication summary")
    title = _one(article.find(tag="a", css="publication-summary__title"), "Missing or duplicate publication title")
    date_nodes = article.find(css="box-publication-date")
    _require(all(node.tag in ("div", "span") for node in date_nodes), "Unknown publication date/version markup")
    date_node = _one([node for node in date_nodes if node.tag == "div"], "Missing or duplicate publication date")
    markers = [node.text() for node in date_nodes if node.tag == "span"]
    _require(len(markers) <= 1 and all(marker in ("Korrektur", "ursprüngliche Version") for marker in markers),
             "Unknown or duplicate publication version marker")
    day = date_node.text()
    _require(re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", day), "Malformed publication date")
    try:
        published = datetime.strptime(day, "%d.%m.%Y").date().isoformat()
    except ValueError as exc:
        raise CollectionError("Malformed publication date") from exc
    fields = {}
    for item in article.find(tag="li"):
        labels = item.find(css="col-sm-4")
        if not labels:
            continue
        label = _one(labels, "Malformed publication label").text()
        if label in ("Publ.-Nr.:", "Stelle:", "Rubrik:"):
            _require(label not in fields, "Duplicate publication field")
            fields[label] = _one(item.find(css="col-sm-8"), "Missing publication field value").text()
    number = fields.get("Publ.-Nr.:", "")
    _require(_PUB_NR.fullmatch(number), "Missing or malformed publication number")
    href = title.attrs.get("href")
    _require(isinstance(href, str), "Missing publication URL")
    source_url = urljoin(A.BASE, href)
    parts = _url(source_url, "https://amtsblatt.ag.ch", {f"/ekab/{number}/publikation/"})
    _require(not parts.query and href in (parts.path, source_url), "Publication URL does not match its number")
    candidate = {"source_id": {"namespace": "amtsblatt.ag.ch:pub_nr", "value": number},
            "title": _text(title.text(), "publication title"), "url": source_url,
            "published_on": published, "authority": _text(fields.get("Stelle:"), "publication authority"),
            "rubric": _text(fields.get("Rubrik:"), "publication rubric")}
    if markers:
        candidate["publication_version_label"] = markers[0]
    return candidate


def _amtsblatt_selection(url):
    parts = _url(url, "https://amtsblatt.ag.ch", {"/publikationen/"})
    selection, paging, indexed_keys = {}, {}, set()
    try:
        parameters = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise CollectionError("Malformed search URL query") from exc
    for key, value in parameters:
        if key in ("page", "tx_diamcore_publicationsearchresult[action]", "tx_diamcore_publicationsearchresult[controller]"):
            _require(key not in paging, "Duplicate search pagination parameter")
            paging[key] = value
        elif key in ("searchQuery", "timerange[type]") or re.fullmatch(r"filter\[(authority|category|type)\]\[(\d*)\]", key):
            if re.search(r"\[\d+\]$", key):
                _require(key not in indexed_keys, "Duplicate indexed search filter")
                indexed_keys.add(key)
            canonical = re.sub(r"\[\d*\]$", "[]", key) if key.startswith("filter[") else key
            selection.setdefault(canonical, []).append(value)
        else:
            raise CollectionError("Unexpected search URL parameter")
    for values in selection.values():
        _require(len(values) == len(set(values)), "Duplicate search selection filter")
        values.sort()
    _require(selection.get("filter[type][]", ["tx_ekab_publication_domain_model_publication"])
             == ["tx_ekab_publication_domain_model_publication"], "Changed publication type filter")
    selection.pop("filter[type][]", None)  # Ajax adds this explicit equivalent filter.
    return selection, paging


def _amtsblatt_validator(expected, page):
    def validate(url):
        selection, paging = _amtsblatt_selection(url)
        _require(selection == expected, "Search pagination changed selection filters")
        if page == 1:
            _require(not paging, "Unexpected first-page search pagination")
        else:
            _require(paging == {"page": str(page), "tx_diamcore_publicationsearchresult[action]": "resultAjax",
                                "tx_diamcore_publicationsearchresult[controller]": "PublicationSearch"},
                     "Search page must advance monotonically with the observed Ajax parameters")
    return validate


def _pages_limit(value):
    _require(type(value) is int and 1 <= value <= MAX_PAGES, f"max-pages must be 1–{MAX_PAGES}")


def _snapshot(source, parameters, fetched_at, candidates, pages, total):
    return {"schema": SCHEMA, "schema_version": VERSION, "source": source,
            "parameters": parameters, "fetched_at": fetched_at,
            "review_required": ["historical_coverage", "legal_coverage", "planning_relevance", "territorial_scope", "legal_stage"],
            "review_note": "A complete search snapshot is not a complete legal evidence period. Search hints and CMS validity do not establish legal scope or stage.",
            "traversal": {"complete_search_snapshot": True, "total_candidates": total, "pages_fetched": len(pages), "pages": pages},
            "candidates": candidates}


def _now(now=None):
    value = now or datetime.now(timezone.utc)
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
             "Snapshot time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def collect_amtsblatt(municipality, kind="municipal", *, max_pages=5, transport=None, now=None):
    _pages_limit(max_pages)
    _require(A.authorities_for(municipality), "Unknown municipality; a bounded known-municipality search is required")
    municipality = A.canonical_name(municipality)
    _require(kind in ("municipal", "canton-approvals"), "Unknown Amtsblatt search kind")
    url = (A.municipal_planning_link if kind == "municipal" else A.canton_approvals_link)(municipality)
    selection, _ = _amtsblatt_selection(url)
    transport, fetched_at = transport or Transport(), _now(now)
    candidates, pages, ids, visited, total, limit = [], [], set(), set(), None, None
    ajax_template = None
    for page in range(1, max_pages + 1):
        validator = _amtsblatt_validator(selection, page)
        validator(url)
        _require(url not in visited, "Search pagination cycle")
        visited.add(url)
        parsed = _html(transport.request("GET", url, validate_url=validator))
        if page == 1 or parsed.metadata:
            meta = _one(parsed.metadata, "Missing or duplicate search-result metadata (HTML may have changed)")
            numbers = []
            for key in ("data-total", "data-page", "data-limit"):
                value = meta.get(key, "")
                _require(isinstance(value, str) and re.fullmatch(r"\d+", value), f"Missing or malformed search-result {key}")
                numbers.append(int(value))
            found_total, found_page, found_limit = numbers
            _require(found_page == page and found_limit == 10, "Search page/limit metadata mismatch")
            if total is None:
                total, limit = found_total, found_limit
                _require(max(1, math.ceil(total / limit)) <= max_pages, "Search exceeds max-pages; snapshot aborted")
            _require(found_total == total and found_limit == limit, "Search totals changed during traversal")
        expected_count = min(limit, max(0, total - (page - 1) * limit))
        _require(len(parsed.cards) == expected_count, "Search publication count does not match declared total")
        for card in parsed.cards:
            candidate = _amtsblatt_card(card)
            identity = candidate["source_id"]["value"]
            _require(identity not in ids, "Duplicate or conflicting publication ID across search pages")
            ids.add(identity)
            candidates.append(candidate)
        pages.append({"page": page, "count": len(parsed.cards), "url": url})
        more = len(candidates) < total
        cards_only = page > 1 and not parsed.metadata and not parsed.next_links
        _require(len(parsed.next_links) == (1 if more else 0) or (more and cards_only),
                 "Missing or extra search next-page link")
        if not more:
            return _snapshot({"provider": "amtsblatt.ag.ch", "search_url": pages[0]["url"]},
                             {"kind": kind, "municipality_query_hint": municipality, "max_pages": max_pages},
                             fetched_at, candidates, pages, total)
        if parsed.next_links:
            href = parsed.next_links[0]
            _require(not re.search(r"[\x00-\x20\x7f\\]", href)
                     and not re.search(r"[\x00-\x1f\x7f\\]", unquote(href)), "Unsafe next-page reference")
            _require(not any(unquote(p) in (".", "..") for p in urlsplit(href).path.split("/")), "Unsafe next-page path")
            url = urljoin(url, href)
        else:
            # Observed Ajax fragments contain cards only. Advance the page in
            # the first page's validated URL; retain every selection parameter.
            _require(ajax_template is not None, "Missing observed Ajax pagination URL")
            parts = urlsplit(ajax_template)
            query = [(key, str(page + 1) if key == "page" else value)
                     for key, value in parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)]
            url = parts._replace(query=urlencode(query)).geturl()
        _amtsblatt_validator(selection, page + 1)(url)
        if page == 1:
            ajax_template = url
    raise CollectionError("Search exceeds max-pages; snapshot aborted")


def _consultation_validator(mode, *, api=False):
    path = urlsplit(SEARCH_API).path if api else CONSULTATION_PATHS[mode]
    def validate(url):
        parts = _url(url, AG_ORIGIN, {path})
        _require(not parts.query, "Unexpected consultation search URL query")
    return validate


def _consultation_widget(raw, mode):
    attrs = _one(_html(raw).widgets, "Missing or duplicate consultation widget (HTML may have changed)")
    expected = {"data-api": SEARCH_API, "data-results-key": "dynamiccontent",
                "data-limit-validity": "valid" if mode == "current" else "invalid", "data-pagesize": "10"}
    _require(all(attrs.get(key) == value for key, value in expected.items()), "Consultation widget configuration changed")
    _require(_json(attrs.get("data-sort", ""), "widget sort") == [{"property": "validFrom", "method": "desc"}],
             "Consultation widget sort changed")
    _require(_json(attrs.get("data-query", ""), "widget category query") == TAG_QUERY, "Consultation category filters changed")
    return attrs


def consultation_query(mode, term, page, timestamp):
    """The site's CMS validity expression, unrelated to legal effectiveness."""
    validity = {"operator": "lt", "field": "validUntil", "value": timestamp}
    if mode == "current":
        validity = {"operator": "or", "filters": [
            {"operator": "gt", "field": "validUntil", "value": timestamp},
            {"operator": "and", "filters": [
                {"operator": "eq", "field": "validUntil", "value": None},
                {"operator": "lt", "field": "validFrom", "value": timestamp}]}]}
    return {"term": term, "pagination": {"page": page, "size": 10},
            "sort": [{"field": "validFrom", "direction": "desc"}],
            "filter": {"operator": "and", "filters": [validity, TAG_QUERY]}}


def _timestamp(value, label, nullable=False):
    if value is None and nullable:
        return None
    _require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value),
             f"Missing or malformed {label} timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(parsed.utcoffset() is not None, f"Missing timezone in {label}")
    except ValueError as exc:
        raise CollectionError(f"Malformed {label} timestamp") from exc
    return value


def _consultation_card(item, mode):
    _require(isinstance(item, dict), "Consultation item is not an object")
    identity = item.get("id")
    _require(isinstance(identity, str) and _UUID_DE.fullmatch(identity) and item.get("queryId") == identity,
             "Missing or conflicting consultation ID")
    _require(item.get("deactivated") is False, "Consultation item is deactivated or lacks activation metadata")
    urls = item.get("urls")
    _require(isinstance(urls, list) and urls, "Missing consultation URLs")
    selected = []
    for link in urls:
        _require(isinstance(link, dict) and isinstance(link.get("type"), str)
                 and isinstance(link.get("url"), str), "Malformed consultation URL entry")
        href = link["url"]
        url = urljoin(AG_ORIGIN, href)
        parts = _url(url, AG_ORIGIN, set(CONSULTATION_PATHS.values()))
        _require(parse_qsl(parts.query, keep_blank_values=True) == [("dc", identity)], "Consultation URL ID mismatch")
        _require(href in (parts.path + "?" + parts.query, url), "Noncanonical consultation URL")
        if parts.path == CONSULTATION_PATHS[mode]:
            selected.append(url)
    url = _one(selected, "Missing or duplicate consultation URL for requested mode")
    for key in ("publicationDate", "validFrom", "validUntil"):
        _require(key in item, f"Missing consultation {key}")
    return {"source_id": {"namespace": "www.ag.ch:dynamiccontent", "value": identity},
            "title": _text(item.get("title"), "consultation title"), "url": url,
            "authority": _text(item.get("organisation"), "consultation organisation"),
            "publication_date": _timestamp(item.get("publicationDate"), "publicationDate"),
            "cms_valid_from": _timestamp(item.get("validFrom"), "validFrom"),
            "cms_valid_until": _timestamp(item.get("validUntil"), "validUntil", nullable=True)}


def collect_consultations(*, mode="current", term="", max_pages=5, transport=None, now=None):
    _pages_limit(max_pages)
    _require(mode in CONSULTATION_PATHS, "Unknown consultation mode")
    _require(isinstance(term, str) and len(term) <= 200 and not re.search(r"[\x00-\x1f\x7f]", term), "Invalid consultation search term")
    transport, fetched_at = transport or Transport(), _now(now)
    source_url = AG_ORIGIN + CONSULTATION_PATHS[mode]
    _consultation_widget(transport.request("GET", source_url, validate_url=_consultation_validator(mode)), mode)
    candidates, pages, ids, totals = [], [], set(), None
    for page in range(max_pages):
        body = json.dumps(consultation_query(mode, term, page, fetched_at), ensure_ascii=False).encode("utf-8")
        raw = transport.request("POST", SEARCH_API, body=body, validate_url=_consultation_validator(mode, api=True))
        _require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, "Invalid or oversized JSON response")
        payload = _json(raw, "consultation search")
        _require(isinstance(payload, dict) and payload.get("collectionName") == "dynamiccontent", "Consultation response schema changed")
        items, control = payload.get("dynamiccontent"), payload.get("control")
        _require(isinstance(items, list) and isinstance(control, dict), "Missing consultation items or pagination")
        for key in ("numberOfElements", "pageNumber", "pageSize", "totalPages", "totalElements"):
            _require(type(control.get(key)) is int and control[key] >= 0, f"Malformed consultation control.{key}")
        total, count_pages = control["totalElements"], control["totalPages"]
        _require(count_pages == (math.ceil(total / 10) if total else 0), "Consultation totalPages mismatch")
        _require(control["pageNumber"] == page and control["pageSize"] == 10, "Consultation page metadata mismatch")
        _require(max(1, count_pages) <= max_pages, "Consultation search exceeds max-pages; snapshot aborted")
        if totals is None:
            totals = (total, count_pages)
        _require(totals == (total, count_pages), "Consultation totals changed during traversal")
        expected_count = min(10, max(0, total - page * 10))
        last = page == max(0, count_pages - 1)
        _require(control.get("firstPage") is (page == 0) and control.get("lastPage") is last,
                 "Consultation first/last page mismatch")
        _require(control["numberOfElements"] == len(items) == expected_count, "Consultation item count mismatch")
        for item in items:
            candidate = _consultation_card(item, mode)
            identity = candidate["source_id"]["value"]
            _require(identity not in ids, "Duplicate or conflicting consultation ID across pages")
            ids.add(identity)
            candidates.append(candidate)
        pages.append({"page": page, "count": len(items), "url": SEARCH_API})
        if last:
            return _snapshot({"provider": "www.ag.ch", "search_url": source_url, "endpoint": SEARCH_API},
                             {"mode": mode, "term": term, "cms_validity_as_of": fetched_at, "max_pages": max_pages},
                             fetched_at, candidates, pages, total)
    raise CollectionError("Consultation search exceeds max-pages; snapshot aborted")


def _destination(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    _require(stat.S_ISREG(info.st_mode), "Output must be a regular file; symlinks are refused")
    _require(info.st_size <= MAX_SNAPSHOT_BYTES, "Existing output is too large to inspect safely")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            _require((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                     == (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                     "Output changed during inspection")
            current = _json(handle.read(MAX_SNAPSHOT_BYTES + 1), "existing output")
    except OSError as exc:
        raise CollectionError("Cannot inspect existing output safely") from exc
    _require(isinstance(current, dict) and not any(key in current for key in ("since", "stand", "records", "publications", "covers_from", "covers_until")),
             "Refusing to overwrite a production store or importer batch")
    _require(current.get("schema") == SCHEMA and current.get("schema_version") == VERSION,
             "Existing output is not a planning-candidates staging file")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


_DESTINATION_UNSET = object()


def write_snapshot(path, snapshot, *, expected_destination=_DESTINATION_UNSET):
    """Atomic replacement; serialize cooperating writers' check and rename."""
    path = Path(path).absolute()
    _destination(path)  # Refuse unsafe targets before creating the advisory lock.
    lock_path = path.with_name(path.name + ".collect.lock")
    lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        _require(stat.S_ISREG(os.fstat(lock).st_mode), "Output lock must be a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        _write_snapshot_locked(path, snapshot, expected_destination)
    finally:
        os.close(lock)


def _write_snapshot_locked(path, snapshot, expected_destination):
    before = _destination(path)
    _require(expected_destination is _DESTINATION_UNSET or before == expected_destination,
             "Output changed during collection; refusing to overwrite it")
    _require(isinstance(snapshot, dict) and snapshot.get("schema") == SCHEMA
             and snapshot.get("schema_version") == VERSION and isinstance(snapshot.get("candidates"), list)
             and snapshot.get("traversal", {}).get("complete_search_snapshot") is True,
             "Only a validated staging snapshot can be written")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _require(_destination(path) == before, "Output changed during collection; refusing to overwrite it")
        os.replace(temporary, path)
        temporary = None
        # Rename committed the snapshot. Some filesystems cannot fsync a
        # directory; do not report a failed collection after replacement.
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main(argv=None, *, transport=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="source", required=True)
    amtsblatt = subparsers.add_parser("amtsblatt", help="One known municipality's filtered Amtsblatt search")
    amtsblatt.add_argument("--municipality", required=True)
    amtsblatt.add_argument("--kind", choices=("municipal", "canton-approvals"), default="municipal")
    consultations = subparsers.add_parser("consultations", help="Canton consultation search metadata")
    consultations.add_argument("--mode", choices=("current", "archive"), default="current")
    consultations.add_argument("--term", default="")
    for command in (amtsblatt, consultations):
        command.add_argument("--output", required=True)
        command.add_argument("--max-pages", type=int, default=5)
        command.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    try:
        destination = _destination(Path(args.output).absolute())  # Fail before network when destination is unsafe.
        _pages_limit(args.max_pages)
        client = transport or Transport(timeout=args.timeout)
        if args.source == "amtsblatt":
            snapshot = collect_amtsblatt(args.municipality, args.kind, max_pages=args.max_pages, transport=client, now=now)
        else:
            snapshot = collect_consultations(mode=args.mode, term=args.term, max_pages=args.max_pages, transport=client, now=now)
        write_snapshot(args.output, snapshot, expected_destination=destination)
    except (CollectionError, OSError) as exc:
        # Transport/parser exceptions never include response text or headers.
        print(f"Collection failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(snapshot['candidates'])} candidates from {snapshot['traversal']['pages_fetched']} search pages to {args.output}; human review required.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
