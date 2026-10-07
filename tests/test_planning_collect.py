"""Offline source snapshots and failure boundaries; no live service calls."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import amtsblatt as A
import amtsblatt_import as I
import planning_collect as C

FIXTURES = Path(__file__).parent / "fixtures" / "planning_sources"
NOW = datetime(2026, 10, 6, 5, 13, 52, 731000, tzinfo=timezone.utc)


def fixture(name):
    return (FIXTURES / name).read_bytes()


def json_fixture(name):
    return json.loads(fixture(name))


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, validate_url, body=None):
        validate_url(url)
        self.calls.append((method, url, json.loads(body) if body else None))
        assert self.responses, "Unexpected additional search fetch"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response).encode() if isinstance(response, dict) else response


def amts_responses():
    return [fixture("amtsblatt-seon-page1.html"), fixture("amtsblatt-seon-page2.html")]


def consultation_responses():
    return [fixture("consultations-current-widget.html"),
            json_fixture("consultations-current-page0.json"), json_fixture("consultations-current-page1.json")]


def test_two_page_amtsblatt_preserves_only_metadata_and_query_hint():
    transport = FakeTransport(amts_responses())
    snapshot = C.collect_amtsblatt("Seon", transport=transport, now=NOW)
    assert snapshot["schema"] == C.SCHEMA and snapshot["schema_version"] == 1
    assert snapshot["fetched_at"] == "2026-10-06T05:13:52.731Z"
    assert snapshot["traversal"]["total_candidates"] == 11
    assert [p["count"] for p in snapshot["traversal"]["pages"]] == [10, 1]
    assert len(transport.calls) == 2 and all(call[0] == "GET" for call in transport.calls)
    first, last = snapshot["candidates"][0], snapshot["candidates"][-1]
    assert first["source_id"] == {"namespace": "amtsblatt.ag.ch:pub_nr", "value": "00.102.603"}
    assert first["title"] == "Öffentliche Mitwirkung zum Gestaltungsplan «Heidegrabe»"
    assert first["published_on"] == "2026-09-10"
    assert last["source_id"]["value"] == "00.003.015"
    assert set(first) == {"source_id", "title", "url", "published_on", "authority", "rubric"}
    assert snapshot["parameters"]["municipality_query_hint"] == "Seon"
    assert set(snapshot["review_required"]) == {"historical_coverage", "legal_coverage", "planning_relevance", "territorial_scope", "legal_stage"}
    assert not {"since", "stand", "records", "municipalities"} & snapshot.keys()
    for candidate in snapshot["candidates"]:
        assert not {"municipality", "canton_wide", "verified", "stage", "effective_on", "text", "teaser"} & candidate.keys()


def test_titles_entities_and_body_are_handled_without_storing_teaser():
    page = fixture("amtsblatt-seon-page1.html").replace(b"</article>", b"<p>SECRET FULLTEXT &amp; teaser [...]</p></article>")
    snapshot = C.collect_amtsblatt("Seon", transport=FakeTransport([page, amts_responses()[1]]), now=NOW)
    assert 'Gestaltungsplan "Giessi"; Beschluss' in [r["title"] for r in snapshot["candidates"]]
    assert "SECRET" not in json.dumps(snapshot)


def zero_amtsblatt():
    return b'<div class="publication-search-result" data-total="0" data-page="1" data-limit="10"></div>'


def test_amtsblatt_zero_is_valid_only_with_explicit_result_metadata():
    result = C.collect_amtsblatt("Seon", transport=FakeTransport([zero_amtsblatt()]))
    assert result["candidates"] == [] and result["traversal"]["total_candidates"] == 0
    for response in (b"<html>Login</html>", b"<html>Technical error</html>", b""):
        with pytest.raises(C.CollectionError, match="metadata"):
            C.collect_amtsblatt("Seon", transport=FakeTransport([response]))


def test_multiple_office_filters_and_canton_approvals_use_existing_helpers():
    for name, kind, helper in [("Baden", "municipal", A.municipal_planning_link),
                               ("Beinwil am See", "canton-approvals", A.canton_approvals_link)]:
        transport = FakeTransport([zero_amtsblatt()])
        C.collect_amtsblatt(name, kind, transport=transport)
        assert transport.calls[0][1] == helper(name)
    selection, _ = C._amtsblatt_selection(A.municipal_planning_link("Baden"))
    next_query = [(f"filter[authority][{i}]", value) for i, value in enumerate(A.authorities_for("Baden"))]
    from urllib.parse import urlencode
    next_url = A.BASE + "?" + urlencode(next_query + [
        ("filter[category][0]", A.BNO), ("filter[type][0]", "tx_ekab_publication_domain_model_publication"),
        ("page", "2"), ("timerange[type]", "4"),
        ("tx_diamcore_publicationsearchresult[action]", "resultAjax"),
        ("tx_diamcore_publicationsearchresult[controller]", "PublicationSearch")])
    C._amtsblatt_validator(selection, 2)(next_url)
    with pytest.raises(C.CollectionError, match="filters"):
        C._amtsblatt_validator(selection, 2)(next_url.replace("10%2C151", "10%2C398"))
    with pytest.raises(C.CollectionError, match="Unknown municipality"):
        C.collect_amtsblatt("Invented village", transport=FakeTransport([]))


@pytest.mark.parametrize("old,new,match", [
    (b'data-total="11"', b'data-total="12"', "count"),
    (b'data-page="1"', b'data-page="2"', "page/limit"),
    (b'data-limit="10"', b'data-limit="20"', "page/limit"),
    (b'data-total="11"', b'data-total="eleven"', "data-total"),
    (b'publication-summary__title', b'new-title-class', "title"),
    (b'10.09.2026', b'32.09.2026', "date"),
    (b'<b>Stelle:</b>', b'<b>Office:</b>', "authority"),
    (b'<b>Rubrik:</b>', b'<b>Category:</b>', "rubric"),
    (b'/ekab/00.102.603/publikation/', b'/ekab/00.102.604/publikation/', "path"),
    (b'<b>Publ.-Nr.:</b>', b'<b>Number:</b>', "number"),
])
def test_amtsblatt_card_and_pagination_drift_aborts(old, new, match):
    responses = amts_responses()
    responses[0] = responses[0].replace(old, new)
    with pytest.raises(C.CollectionError, match=match):
        C.collect_amtsblatt("Seon", transport=FakeTransport(responses))


@pytest.mark.parametrize("url", [
    "https://evil.invalid/publikationen/?page=2", "//evil.invalid/publikationen/?page=2",
    "/publikationen/../private/?page=2", "/publikationen/%2e%2e/private/?page=2",
    "/publikationen/?page=1", "/publikationen/?filter[category][0]=162,175&page=2",
    "https://user@amtsblatt.ag.ch/publikationen/?page=2", "https://amtsblatt.ag.ch:444/publikationen/?page=2",
    "/publikationen/?page=2&#10;", "/publikationen/?page=2%0a", "/publikationen/\\other?page=2",
])
def test_unsafe_changed_filter_or_nonadvancing_next_links_abort(url):
    page = fixture("amtsblatt-seon-page1.html")
    import re
    page = re.sub(rb'(<a class="control-pagebrowser__next-page" href=")[^"]+', lambda m: m[1] + url.encode(), page)
    with pytest.raises(C.CollectionError):
        C.collect_amtsblatt("Seon", transport=FakeTransport([page]))


def test_amtsblatt_missing_extra_next_duplicate_conflicting_and_changed_total():
    page1, page2 = amts_responses()
    with pytest.raises(C.CollectionError, match="next-page"):
        C.collect_amtsblatt("Seon", transport=FakeTransport([page1.split(b'<a class="control-pagebrowser__next-page"')[0]]))
    with pytest.raises(C.CollectionError, match="next-page"):
        C.collect_amtsblatt("Seon", transport=FakeTransport([page1, page2 + b'<a class="control-pagebrowser__next-page" href="/publikationen/">extra</a>']))
    for changed in (page2.replace(b"00.003.015", b"00.102.603"),
                    page2 + b'<div class="publication-search-result" data-total="12" data-page="2" data-limit="10"></div>',
                    page2 + page2, page2[:-10]):
        with pytest.raises(C.CollectionError):
            C.collect_amtsblatt("Seon", transport=FakeTransport([page1, changed]))
    with pytest.raises(C.CollectionError, match="max-pages"):
        C.collect_amtsblatt("Seon", max_pages=1, transport=FakeTransport([page1]))


def aarau_four_pages():
    """Actual sanitized first two pages, synthetic distinct later test cards."""
    import re
    page1, page2 = (fixture(f"amtsblatt-aarau-page{page}.html") for page in (1, 2))
    ids = [C._amtsblatt_card(card)["source_id"]["value"] for card in C._html(page2).cards]
    chunks = re.split(rb'(?=<div class="publication-list__item publication-list__item--publication)', page2.strip())
    assert not chunks[0] and len(chunks) == 11
    page3, page4 = page2, b"".join(chunks[1:4])
    for index, value in enumerate(ids):
        page3 = page3.replace(value.encode(), f"00.901.{index:03d}".encode())
        page4 = page4.replace(value.encode(), f"00.902.{index:03d}".encode())
    return [page1, page2, page3, page4]


def test_cards_only_ajax_advances_observed_url_and_completes_four_pages():
    from urllib.parse import parse_qsl, urlsplit
    transport = FakeTransport(aarau_four_pages())
    snapshot = C.collect_amtsblatt("Aarau", max_pages=50, transport=transport, now=NOW)
    assert snapshot["traversal"]["total_candidates"] == 33
    assert [page["count"] for page in snapshot["traversal"]["pages"]] == [10, 10, 10, 3]
    assert len({candidate["source_id"]["value"] for candidate in snapshot["candidates"]}) == 33
    ajax = [urlsplit(call[1]) for call in transport.calls[1:]]
    queries = [dict(parse_qsl(parts.query)) for parts in ajax]
    assert [query.pop("page") for query in queries] == ["2", "3", "4"]
    assert queries[0] == queries[1] == queries[2]
    assert all((parts.scheme, parts.netloc, parts.path) == ("https", "amtsblatt.ag.ch", "/publikationen/") for parts in ajax)


@pytest.mark.parametrize("failure", ["metadata", "truncated", "duplicate", "extra-final"])
def test_cards_only_pagination_keeps_count_metadata_and_final_link_guards(failure):
    pages = aarau_four_pages()
    if failure == "metadata":
        pages[1] += b'<div class="publication-search-result" data-total="33" data-page="2" data-limit="10"></div>'
    elif failure == "truncated":
        pages[3] = pages[3].split(b'<div class="publication-list__item publication-list__item--publication')[0]
    elif failure == "duplicate":
        pages[2] = pages[1]
    else:
        pages[3] += b'<a class="control-pagebrowser__next-page" href="/publikationen/">extra</a>'
    with pytest.raises(C.CollectionError):
        C.collect_amtsblatt("Aarau", max_pages=50, transport=FakeTransport(pages))


def test_manual_fifty_page_limit_remains_bounded_before_fetching_more_pages():
    C.collect_amtsblatt("Aarau", max_pages=50, transport=FakeTransport([zero_amtsblatt()]))
    oversized = fixture("amtsblatt-aarau-page1.html").replace(b'data-total="33"', b'data-total="501"')
    transport = FakeTransport([oversized])
    with pytest.raises(C.CollectionError, match="max-pages"):
        C.collect_amtsblatt("Aarau", max_pages=50, transport=transport)
    assert len(transport.calls) == 1


def test_more_manual_pages_do_not_expand_existing_snapshot_byte_limit(tmp_path):
    path = tmp_path / "oversized-candidates.json"
    with path.open("wb") as handle:
        handle.truncate(40 * 1024 * 1024 + 1)
    with patch.object(C, "_json", side_effect=AssertionError("Oversized snapshot must not be read")):
        with pytest.raises(C.CollectionError, match="too large"):
            C._destination(path)


def test_canton_two_pages_have_distinct_uuid_namespace_and_no_legal_inference():
    transport = FakeTransport(consultation_responses())
    snapshot = C.collect_consultations(transport=transport, now=NOW)
    assert len(snapshot["candidates"]) == 11
    assert [p["count"] for p in snapshot["traversal"]["pages"]] == [10, 1]
    assert [call[0] for call in transport.calls] == ["GET", "POST", "POST"]
    expected = json_fixture("consultations-current-query.json")
    assert transport.calls[1][2] == expected
    expected["pagination"]["page"] = 1
    assert transport.calls[2][2] == expected
    first = snapshot["candidates"][0]
    assert first["source_id"]["namespace"] == "www.ag.ch:dynamiccontent"
    assert "laufende-anhoerungen?dc=" in first["url"]
    assert set(first) == {"source_id", "title", "url", "authority", "publication_date", "cms_valid_from", "cms_valid_until"}
    assert first["publication_date"] == "2026-10-02T06:55:00Z"
    assert "Programm Invasive Neobiota" in first["title"]  # No automatic planning-only relevance claim.
    assert not {"pub_nr", "municipality", "canton_wide", "stage", "effective_on", "verified", "teaser"} & first.keys()


def zero_canton():
    return {"collectionName": "dynamiccontent", "dynamiccontent": [], "control": {
        "firstPage": True, "lastPage": True, "numberOfElements": 0, "pageNumber": 0,
        "pageSize": 10, "totalPages": 0, "totalElements": 0}}


def test_canton_archive_query_zero_and_matching_source_urls():
    timestamp = datetime(2026, 10, 6, 5, 16, 42, 986000, tzinfo=timezone.utc)
    transport = FakeTransport([fixture("consultations-archive-widget.html"), zero_canton()])
    snapshot = C.collect_consultations(mode="archive", term="Richtplan", transport=transport, now=timestamp)
    assert transport.calls[1][2] == json_fixture("consultations-archive-query.json")
    assert snapshot["candidates"] == []
    rows = consultation_responses()[1:]
    snapshot = C.collect_consultations(mode="archive", transport=FakeTransport([fixture("consultations-archive-widget.html"), *rows]))
    assert all("archivierte-anhoerungen?dc=" in r["url"] for r in snapshot["candidates"])


@pytest.mark.parametrize("old,new", [
    (b'data-api="https://www.ag.ch', b'data-api="https://evil.invalid'),
    (b'data-results-key="dynamiccontent"', b'data-results-key="other"'),
    (b'data-limit-validity="valid"', b'data-limit-validity="invalid"'),
    (b'data-pagesize="10"', b'data-pagesize="20"'),
    (b'validFrom', b'validUntil'), (b'063d5a66', b'xxxxxxxx'),
])
def test_canton_widget_category_and_configuration_drift_fails(old, new):
    widget = fixture("consultations-current-widget.html").replace(old, new)
    with pytest.raises(C.CollectionError):
        C.collect_consultations(transport=FakeTransport([widget]))


@pytest.mark.parametrize("key,value", [
    ("firstPage", False), ("lastPage", True), ("numberOfElements", 9), ("pageNumber", 1),
    ("pageSize", 20), ("totalPages", 3), ("totalElements", 9), ("totalElements", True),
    ("totalPages", "2"), ("totalElements", -1),
])
def test_canton_pagination_metadata_mismatch_fails(key, value):
    responses = consultation_responses()
    responses[1]["control"][key] = value
    with pytest.raises(C.CollectionError):
        C.collect_consultations(transport=FakeTransport(responses))


@pytest.mark.parametrize("key,value", [
    ("id", "00.102.603"), ("queryId", "other_de"), ("title", ""), ("organisation", None),
    ("publicationDate", "2026-10-02"), ("validFrom", "2026-99-99T06:55:00Z"),
    ("validUntil", "not a date"), ("deactivated", True), ("deactivated", "false"),
    ("urls", []), ("urls", [{"type": "current", "url": "https://evil.invalid/?dc=wrong"}]),
])
def test_canton_missing_malformed_or_deactivated_item_fails(key, value):
    responses = consultation_responses()
    responses[1]["dynamiccontent"][0][key] = value
    with pytest.raises(C.CollectionError):
        C.collect_consultations(transport=FakeTransport(responses))


def test_canton_missing_fields_duplicate_ids_and_changed_total_fail():
    for key in ("publicationDate", "validFrom", "validUntil", "queryId", "urls"):
        responses = consultation_responses()
        del responses[1]["dynamiccontent"][0][key]
        with pytest.raises(C.CollectionError):
            C.collect_consultations(transport=FakeTransport(responses))
    responses = consultation_responses()
    responses[2]["dynamiccontent"][0] = copy.deepcopy(responses[1]["dynamiccontent"][0])
    responses[2]["dynamiccontent"][0]["title"] = "Conflicting title"
    with pytest.raises(C.CollectionError, match="ID"):
        C.collect_consultations(transport=FakeTransport(responses))
    responses = consultation_responses()
    responses[2]["control"]["totalElements"] = 12
    with pytest.raises(C.CollectionError, match="totals"):
        C.collect_consultations(transport=FakeTransport(responses))
    with pytest.raises(C.CollectionError, match="max-pages"):
        C.collect_consultations(max_pages=1, transport=FakeTransport(consultation_responses()))
    for payload in ({}, zero_canton() | {"dynamiccontent": None}, b'<html>error</html>', b'{"control":{},"control":{}}'):
        with pytest.raises(C.CollectionError):
            C.collect_consultations(transport=FakeTransport([fixture("consultations-current-widget.html"), payload]))


def test_future_cms_start_and_null_end_are_source_metadata_only():
    responses = consultation_responses()
    first = responses[1]["dynamiccontent"][0]
    first["validFrom"], first["validUntil"] = "2027-10-06T01:00:00Z", None
    first["teaser"] = "Private full text not retained"
    first["topicIds"] = ["useless-placeholder"]
    snapshot = C.collect_consultations(transport=FakeTransport(responses))
    assert snapshot["candidates"][0]["cms_valid_from"] == "2027-10-06T01:00:00Z"
    assert snapshot["candidates"][0]["cms_valid_until"] is None
    assert "Private full text" not in json.dumps(snapshot)
    assert "topicIds" not in json.dumps(snapshot)


@pytest.mark.parametrize("bad", [0, 51, True, "2"])
def test_page_cap_input_is_strict(bad):
    with pytest.raises(C.CollectionError, match="max-pages"):
        C.collect_consultations(max_pages=bad, transport=FakeTransport([]))


def test_atomic_output_and_staging_cannot_be_imported_even_with_explicit_coverage(tmp_path):
    path = tmp_path / "candidates.json"
    snapshot = C.collect_amtsblatt("Seon", transport=FakeTransport(amts_responses()))
    C.write_snapshot(path, snapshot)
    assert json.loads(path.read_bytes()) == snapshot
    batch = I.read_batch(str(path), covers_from=datetime(2019, 1, 1).date(),
                         covers_until=NOW.date(), municipalities=["Seon"])
    assert batch.errors
    result = I.import_batch(str(tmp_path / "store.json"), batch, today=NOW.date())
    assert not result.ok and not (tmp_path / "store.json").exists()
    snapshot["parameters"]["kind"] = "new snapshot"
    C.write_snapshot(path, snapshot)
    assert json.loads(path.read_text())["parameters"]["kind"] == "new snapshot"
    assert not list(tmp_path.glob(".candidates.json.*"))


def test_failed_and_partial_fetch_preserves_prior_output_byte_for_byte(tmp_path, capsys):
    path = tmp_path / "candidates.json"
    C.write_snapshot(path, C.collect_amtsblatt("Seon", transport=FakeTransport(amts_responses())))
    before = path.read_bytes()
    for failure in (C.CollectionError("Source request timed out"), b"<html>Login</html>", b""):
        assert C.main(["amtsblatt", "--municipality", "Seon", "--output", str(path)],
                      transport=FakeTransport([amts_responses()[0], failure])) == 1
        assert path.read_bytes() == before
    assert "Collection failed" in capsys.readouterr().err
    assert C.main(["amtsblatt", "--municipality", "Seon", "--max-pages", "1", "--output", str(path)],
                  transport=FakeTransport(amts_responses())) == 1
    assert path.read_bytes() == before


def test_store_batch_unrelated_file_and_symlink_destinations_refused_before_fetch(tmp_path):
    for value in ({"since": "2020-01-01", "stand": "2026-10-06", "records": []},
                  {"records": []}, {"publications": []}, {"unrelated": "file"}):
        path = tmp_path / "production.json"
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        assert C.main(["consultations", "--output", str(path)], transport=FakeTransport([])) == 1
        assert path.read_bytes() == before
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(C.CollectionError, match="symlinks"):
        C.write_snapshot(link, {})
    assert path.read_bytes() == before
    dangling = tmp_path / "dangling.json"
    dangling.symlink_to(tmp_path / "missing.json")
    with pytest.raises(C.CollectionError, match="symlinks"):
        C.write_snapshot(dangling, {})


def test_atomic_rename_failure_keeps_previous_snapshot_and_cleans_temporary(tmp_path):
    path = tmp_path / "candidates.json"
    snapshot = C.collect_amtsblatt("Seon", transport=FakeTransport([zero_amtsblatt()]))
    C.write_snapshot(path, snapshot)
    before = path.read_bytes()
    with patch.object(C.os, "replace", side_effect=OSError("disk write failed")):
        with pytest.raises(OSError):
            C.write_snapshot(path, snapshot)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".candidates.json.*"))


def test_cli_success_and_invalid_bounds(tmp_path, capsys):
    path = tmp_path / "candidates.json"
    assert C.main(["consultations", "--output", str(path)], transport=FakeTransport(consultation_responses()), now=NOW) == 0
    assert len(json.loads(path.read_text())["candidates"]) == 11
    assert "human review required" in capsys.readouterr().out
    before = path.read_bytes()
    assert C.main(["amtsblatt", "--municipality", "Seon", "--timeout", "nan", "--output", str(path)]) == 1
    assert path.read_bytes() == before


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class FakeResponse:
    def __init__(self, status=200, body=b"OK", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class ConnectionFactory:
    def __init__(self, responses, request_error=None):
        self.responses, self.request_error, self.instances = list(responses), request_error, []

    def __call__(self, host, *, port, timeout):
        parent = self

        class Connection:
            def __init__(self):
                self.sock, self.closed = FakeSocket(), False
                self.host, self.port, self.timeout = host, port, timeout
                self.calls = []

            def request(self, method, path, body, headers):
                self.calls.append((method, path, body, headers))
                if parent.request_error:
                    raise parent.request_error

            def getresponse(self):
                return parent.responses.pop(0)

            def close(self):
                self.closed = True
        instance = Connection()
        self.instances.append(instance)
        return instance


def test_transport_bounds_deadline_no_cookies_no_proxy_and_http_errors(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://evil.invalid")
    url = A.municipal_planning_link("Seon")
    validator = C._amtsblatt_validator(C._amtsblatt_selection(url)[0], 1)
    factory = ConnectionFactory([FakeResponse(headers={"Set-Cookie": "secret cookie"})])
    assert C.Transport(connection_factory=factory).request("GET", url, validate_url=validator) == b"OK"
    connection = factory.instances[0]
    assert connection.host == "amtsblatt.ag.ch" and connection.port == 443 and connection.closed
    assert connection.sock.timeouts and all(0 < t <= C.DEFAULT_TIMEOUT for t in connection.sock.timeouts)
    assert "Cookie" not in connection.calls[0][3] and "Authorization" not in connection.calls[0][3]
    for response, match in [(FakeResponse(body=b"x" * 11), "size"),
                             (FakeResponse(headers={"Content-Length": "11"}), "size"),
                             (FakeResponse(headers={"Content-Length": "3"}), "Incomplete"),
                             (FakeResponse(status=503, body=b"SECRET BODY"), "HTTP 503"),
                             (FakeResponse(headers={"Content-Encoding": "gzip"}), "encoding")]:
        factory = ConnectionFactory([response])
        with pytest.raises(C.CollectionError, match=match) as error:
            C.Transport(max_bytes=10, connection_factory=factory).request("GET", url, validate_url=validator)
        assert "SECRET BODY" not in str(error.value) and all(c.closed for c in factory.instances)
    factory = ConnectionFactory([], request_error=TimeoutError("SECRET network details"))
    with pytest.raises(C.CollectionError, match="TimeoutError") as error:
        C.Transport(connection_factory=factory).request("GET", url, validate_url=validator)
    assert "SECRET" not in str(error.value)
    factory = ConnectionFactory([FakeResponse()])
    with patch.object(C.time, "monotonic", side_effect=[0, 0, 21]):
        with pytest.raises(C.CollectionError, match="timed out"):
            C.Transport(connection_factory=factory).request("GET", url, validate_url=validator)


@pytest.mark.parametrize("location", ["https://evil.invalid/publikationen/", "/private/", "/publikationen/../private/",
                                       "/publikationen/%2e%2e/private/", "https://amtsblatt.ag.ch:444/publikationen/",
                                       "https://@amtsblatt.ag.ch/publikationen/", "http://amtsblatt.ag.ch/publikationen/",
                                       "/publikationen/\\other", "/publikationen/?filter[category][]=other"])
def test_transport_redirect_origin_path_and_filters_are_checked(location):
    url = A.municipal_planning_link("Seon")
    factory = ConnectionFactory([FakeResponse(status=302, headers={"Location": location})])
    with pytest.raises(C.CollectionError):
        C.Transport(connection_factory=factory).request("GET", url, validate_url=C._amtsblatt_validator(C._amtsblatt_selection(url)[0], 1))
    assert len(factory.instances) == 1 and factory.instances[0].closed


def test_transport_same_origin_redirect_cycle_and_post_method_preservation():
    url = A.municipal_planning_link("Seon")
    validator = C._amtsblatt_validator(C._amtsblatt_selection(url)[0], 1)
    encoded = url.replace("filter[authority][]", "filter%5Bauthority%5D%5B%5D")
    factory = ConnectionFactory([FakeResponse(status=302, headers={"Location": encoded}), FakeResponse(body=b"safe")])
    assert C.Transport(connection_factory=factory).request("GET", url, validate_url=validator) == b"safe"
    factory = ConnectionFactory([FakeResponse(status=302, headers={"Location": url})])
    with pytest.raises(C.CollectionError, match="cycle"):
        C.Transport(connection_factory=factory).request("GET", url, validate_url=validator)
    for status in (301, 302, 303):
        factory = ConnectionFactory([FakeResponse(status=status, headers={"Location": C.SEARCH_API})])
        with pytest.raises(C.CollectionError, match="POST to GET"):
            C.Transport(connection_factory=factory).request("POST", C.SEARCH_API, body=b"{}", validate_url=C._consultation_validator("current", api=True))
    # An equivalent same-path redirect may preserve POST on 307/308.
    for status in (307, 308):
        equivalent = C.SEARCH_API.replace("www.ag.ch/", "www.ag.ch:443/")
        factory = ConnectionFactory([FakeResponse(status=status, headers={"Location": equivalent}), FakeResponse(body=b"{}")])
        assert C.Transport(connection_factory=factory).request("POST", C.SEARCH_API, body=b"{}", validate_url=C._consultation_validator("current", api=True)) == b"{}"
        assert all(c.calls[0][0] == "POST" and c.calls[0][2] == b"{}" for c in factory.instances)


def test_transport_cannot_use_arbitrary_origin_even_with_weak_injected_validator():
    factory = ConnectionFactory([])
    with pytest.raises(C.CollectionError):
        C.Transport(connection_factory=factory).request("GET", "https://evil.invalid/publikationen/", validate_url=lambda url: None)
    assert not factory.instances


def test_html_valueless_attrs_and_deep_card_markup_fail_cleanly():
    page1, page2 = amts_responses()
    valueless = page1.replace(b'<div class="row">', b'<div class>')
    assert len(C.collect_amtsblatt("Seon", transport=FakeTransport([valueless, page2]))["candidates"]) == 11
    nested = page1.replace(b'<article class="publication-summary">', b'<article class="publication-summary">' + b'<div>' * 64, 1)
    with pytest.raises(C.CollectionError, match="nesting"):
        C.collect_amtsblatt("Seon", transport=FakeTransport([nested]))


def test_output_changed_during_fetch_is_preserved(tmp_path):
    path = tmp_path / "candidates.json"
    snapshot = C.collect_amtsblatt("Seon", transport=FakeTransport([zero_amtsblatt()]))
    C.write_snapshot(path, snapshot)
    concurrent = copy.deepcopy(snapshot)
    concurrent["parameters"]["municipality_query_hint"] = "Baden"

    class ConcurrentTransport(FakeTransport):
        def request(self, *args, **kwargs):
            C.write_snapshot(path, concurrent)
            return super().request(*args, **kwargs)

    assert C.main(["amtsblatt", "--municipality", "Seon", "--output", str(path)],
                  transport=ConcurrentTransport([zero_amtsblatt()])) == 1
    assert json.loads(path.read_text()) == concurrent
    assert not list(tmp_path.glob(".candidates.json.*"))


def test_output_created_during_fetch_is_preserved(tmp_path):
    path = tmp_path / "candidates.json"
    snapshot = C.collect_amtsblatt("Seon", transport=FakeTransport([zero_amtsblatt()]))

    class ConcurrentTransport(FakeTransport):
        def request(self, *args, **kwargs):
            C.write_snapshot(path, snapshot)
            return super().request(*args, **kwargs)

    assert C.main(["amtsblatt", "--municipality", "Seon", "--output", str(path)],
                  transport=ConcurrentTransport([zero_amtsblatt()])) == 1
    assert json.loads(path.read_text()) == snapshot


def test_cooperating_writers_serialize_destination_check_and_replace(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    path = tmp_path / "candidates.json"
    snapshot = C.collect_amtsblatt("Seon", transport=FakeTransport([zero_amtsblatt()]))
    C.write_snapshot(path, snapshot)
    expected = C._destination(path)
    original_replace = C.os.replace
    replacing, release, second_locking = Event(), Event(), Event()
    original_flock = C.fcntl.flock
    calls = []

    def gated_replace(*args):
        calls.append(args)
        replacing.set()
        assert release.wait(5), "Writer test timed out"
        original_replace(*args)

    def observed_flock(*args):
        if replacing.is_set():
            second_locking.set()
        return original_flock(*args)

    with patch.object(C.os, "replace", side_effect=gated_replace), patch.object(C.fcntl, "flock", side_effect=observed_flock):
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(C.write_snapshot, path, snapshot, expected_destination=expected)
            assert replacing.wait(5)
            second = executor.submit(C.write_snapshot, path, snapshot, expected_destination=expected)
            assert second_locking.wait(5)
            assert not second.done()
            release.set()
            first.result(timeout=5)
            with pytest.raises(C.CollectionError, match="changed during collection"):
                second.result(timeout=5)
    assert len(calls) == 1
    assert json.loads(path.read_text()) == snapshot


def test_output_lock_symlink_is_refused_and_target_preserved(tmp_path):
    path = tmp_path / "candidates.json"
    secret = tmp_path / "other-file"
    secret.write_bytes(b"Do not access or replace")
    path.with_name(path.name + ".collect.lock").symlink_to(secret)
    snapshot = C.collect_amtsblatt("Seon", transport=FakeTransport([zero_amtsblatt()]))
    with pytest.raises(OSError):
        C.write_snapshot(path, snapshot)
    assert not path.exists() and secret.read_bytes() == b"Do not access or replace"


def test_valueless_required_html_attributes_fail_as_schema_errors():
    for response in (zero_amtsblatt().replace(b'data-total="0"', b'data-total'),
                     zero_amtsblatt().replace(b'data-page="1"', b'data-page')):
        with pytest.raises(C.CollectionError, match="Missing or malformed"):
            C.collect_amtsblatt("Seon", transport=FakeTransport([response]))
    widget = fixture("consultations-current-widget.html")
    import re
    widget = re.sub(rb'data-sort="[^"]*"', b'data-sort', widget)
    with pytest.raises(C.CollectionError, match="missing JSON"):
        C.collect_consultations(transport=FakeTransport([widget]))


def test_duplicate_indexed_filter_cannot_change_multioffice_backend_selection():
    from urllib.parse import urlencode
    query = [("filter[authority][0]", office) for office in A.authorities_for("Baden")]
    url = A.BASE + "?" + urlencode(query + [("filter[category][0]", A.BNO), ("timerange[type]", "4")])
    with pytest.raises(C.CollectionError, match="Duplicate indexed"):
        C._amtsblatt_selection(url)


def test_observed_menziken_version_markers_are_distinct_from_real_dates():
    parsed = C._html(fixture("amtsblatt-menziken-versions.html"))
    rows = [C._amtsblatt_card(card) for card in parsed.cards]
    assert [(r["source_id"]["value"], r["published_on"], r["publication_version_label"]) for r in rows] == [
        ("00.049.005", "2024-02-15", "Korrektur"), ("00.039.905", "2023-08-17", "ursprüngliche Version")]
    assert all(not {"verified", "stage", "effective_on", "supersedes"} & r.keys() for r in rows)
    markup = fixture("amtsblatt-menziken-versions.html")
    duplicate = markup.replace(b'</article>', b'<div class="box-publication-date">15.02.2024</div></article>', 1)
    with pytest.raises(C.CollectionError, match="duplicate publication date"):
        C._amtsblatt_card(C._html(duplicate).cards[0])
    unknown = markup.replace(b'Korrektur', b'Unknown version label', 1)
    with pytest.raises(C.CollectionError, match="version marker"):
        C._amtsblatt_card(C._html(unknown).cards[0])
