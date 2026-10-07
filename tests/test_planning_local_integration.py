"""Offline pipeline, real process interruption, and operational date/budget edges.

Reviewer-supplied proof dates below are synthetic test inputs, not legal facts.
Child processes receive only test paths and fixture responses, never app secrets.
"""
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

import amtsblatt as A
import planning as PL
import planning_collect as C
import planning_preview as P
import planning_refresh as R
import scheduler
from test_planning_collect import ConnectionFactory, FakeResponse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "planning_sources"
NOW = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)
EMPTY_HTML = b'<div class="publication-search-result" data-total="0" data-page="1" data-limit="10"></div>'
GIESSI = P.NS_AMTSBLATT + ":00.055.965"
MITWIRKUNG = P.NS_AMTSBLATT + ":00.102.603"


def deny_network(*args, **kwargs):
    raise AssertionError("Real outbound requests are forbidden in local integration tests")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)


def fixture(name):
    return (FIXTURES / name).read_bytes()


def clock_at(start):
    current = start - timedelta(milliseconds=1)
    def clock():
        nonlocal current
        current += timedelta(milliseconds=1)
        return current
    return clock


def environment(path, *, names=("Seon",), pages=5, hour=8):
    return {P.ENV: str(path), R.PREFIX + "ENABLED": "1", R.PREFIX + "MUNICIPALITIES": ",".join(names),
            R.PREFIX + "MAX_PAGES": str(pages), R.PREFIX + "HOUR": str(hour),
            "SCOPE_PLANNING_REUSE_AUTHORIZED": "1", "SCOPE_PLANNING_REUSE_REFERENCE": "Offline fixture test only"}


class FixtureTransport:
    """Replace HTTP responses only; all collector URL/selection validators run."""
    def __init__(self, *, correction=False, fail_canton=False, empty=False, park=False):
        self.correction, self.fail_canton, self.empty, self.park = correction, fail_canton, empty, park
        self.calls = []

    def request(self, method, url, *, validate_url, body=None):
        validate_url(url)
        query = json.loads(body) if body else None
        self.calls.append((method, url, query))
        if self.park:
            print("BOUNDARY", flush=True)
            threading.Event().wait()  # The parent deliberately SIGKILLs this test child.
        parts = urlsplit(url)
        if parts.hostname == "amtsblatt.ag.ch":
            filters, paging = C._amtsblatt_selection(url)
            if self.empty:
                return EMPTY_HTML
            if filters == C._amtsblatt_selection(A.municipal_planning_link("Seon"))[0]:
                page = int(paging.get("page", "1"))
                raw = fixture(f"amtsblatt-seon-page{page}.html")
                if self.correction and page == 1:
                    anchor = 'Gestaltungsplan &quot;Giessi&quot;; öffentliche Auflage</a></h2>'.encode()
                    assert raw.count(anchor) == 1
                    raw = raw.replace(anchor, anchor + b'<span class="box-publication-date">Korrektur</span>')
                return raw
            if filters == C._amtsblatt_selection(A.municipal_planning_link("Menziken"))[0]:
                return b'<div class="publication-search-result" data-total="2" data-page="1" data-limit="10"></div>' + fixture("amtsblatt-menziken-versions.html")
            return EMPTY_HTML
        if method == "GET":
            assert url == C.AG_ORIGIN + C.CONSULTATION_PATHS["current"]
            return fixture("consultations-current-widget.html")
        assert method == "POST" and url == C.SEARCH_API
        if self.fail_canton:
            raise TimeoutError("Synthetic private provider body must not be saved")
        if self.empty:
            value = json.loads(fixture("consultations-current-page0.json"))
            value["dynamiccontent"] = []
            value["control"].update(numberOfElements=0, totalPages=0, totalElements=0, firstPage=True, lastPage=True)
            return json.dumps(value).encode()
        return fixture(f"consultations-current-page{query['pagination']['page']}.json")


def scheduled_pass(monkeypatch, env, when, transport):
    """Exercise the scheduler hook and actual refresh/collectors with controlled time."""
    actual_run = R.run_once
    result = []
    def run():
        result.append(actual_run(environ=env, clock=clock_at(when)))
        return result[-1]
    with monkeypatch.context() as scoped:
        scoped.setattr(C, "Transport", lambda **kwargs: transport)
        scoped.setattr(R, "run_once", run)
        scheduler.planning_tick()
    return result[0]


def scope_review(item, when, *, municipalities=("Seon",), action="include"):
    candidate = item["candidate"]
    value = {"source_id": candidate["source_id"], "fingerprint": item["fingerprint"], "action": action,
             "evidence_url": candidate["url"], "rationale": "Explicit offline reviewer test decision.", "reviewed_at": C._now(when)}
    if action == "include":
        value["municipalities"] = list(municipalities)
    return value


def expired_proof(item, when):
    return {"source_id": item["candidate"]["source_id"], "fingerprint": item["fingerprint"], "municipality": "Seon",
            "verified": {"stage": "auflage", "source": item["candidate"]["url"].replace("/publikation/", "/pdf/"),
                         "verified_on": P.swiss_today(when).isoformat(), "method": "Synthetic offline reviewer-supplied proof dates.",
                         "auflage_from": "2024-06-27", "auflage_to": "2024-07-26"}}


def test_offline_scheduler_to_review_proof_correction_failure_and_recovery(tmp_path, monkeypatch):
    path = tmp_path / "preview.json"
    env = environment(path, names=("Seon", "Menziken"))
    initial = FixtureTransport()
    assert scheduled_pass(monkeypatch, env, NOW, initial) == {"status": "success", "sources_succeeded": 5, "sources_failed": 0}
    store = P.validate_store(P._read(path))
    assert len(store["items"]) == 24 and len(store["selections"]) == 5
    assert not P.state_for("Seon", path, today=NOW.date()).items
    assert all("legal" not in item and P.review_state(item) == "pending" for item in store["items"].values())
    corrected_id = P.NS_AMTSBLATT + ":00.049.005"
    assert store["items"][corrected_id]["candidate"]["publication_version_label"] == "Korrektur"
    canton = [item for item in store["items"].values() if item["candidate"]["source_id"]["namespace"] == P.NS_CANTON]
    when = NOW + timedelta(minutes=1)
    P.apply_reviews(path, [scope_review(store["items"][MITWIRKUNG], when), scope_review(store["items"][GIESSI], when),
                          scope_review(store["items"][corrected_id], when, municipalities=("Menziken",)),
                          scope_review(canton[0], when), scope_review(canton[1], when, action="exclude")], now=when)
    P.apply_verifications(path, [expired_proof(store["items"][GIESSI], when)], now=when)
    seon = P.state_for("Seon", path, today=NOW.date())
    assert len(seon.items) == 3 and len(P.state_for("Menziken", path, today=NOW.date()).items) == 1
    assert not P.state_for("Baden", path, today=NOW.date()).items
    assert all(PL.stage_badge(item, NOW.date())[0] is None for item in seon.items)
    mitwirkung = next(item for item in seon.items if P.identity(item.candidate["source_id"]) == MITWIRKUNG)
    assert mitwirkung.verified is None  # An actual Mitwirkung title cannot supply Auflage.
    assert all(item.verified is None for item in P.state_for("Menziken", path, today=NOW.date()).items)

    next_day = NOW + timedelta(days=1)
    changed = FixtureTransport(correction=True, fail_canton=True)
    assert scheduled_pass(monkeypatch, env, next_day, changed)["status"] == "partial"
    changed_store = P.validate_store(P._read(path))
    assert P.review_state(changed_store["items"][GIESSI]) == "changed"
    assert "invalidated_at" in changed_store["items"][GIESSI]["legal"]["Seon"]
    assert len(P.state_for("Seon", path, today=next_day.date()).items) == 2
    canton_source = next(source for source in changed_store["selections"].values() if source["selection"]["provider"] == "www.ag.ch")
    assert canton_source["error"] and P._stamp(canton_source["last_success_at"]).date() == NOW.date()
    assert "Synthetic private provider body" not in path.read_text()
    assert all(PL.stage_badge(item, next_day.date())[0] is None for item in P.state_for("Seon", path, today=next_day.date()).items)

    recovery_at = next_day + timedelta(hours=1, minutes=1)
    recovery = FixtureTransport(correction=True)
    assert scheduled_pass(monkeypatch, env, recovery_at, recovery)["status"] == "success"
    recovered = P.validate_store(P._read(path))
    assert not next(source for source in recovered["selections"].values() if source["selection"]["provider"] == "www.ag.ch")["error"]
    assert P.review_state(recovered["items"][GIESSI]) == "changed"  # Fetch recovery is not a review.
    reviewed_at = recovery_at + timedelta(minutes=1)
    P.apply_reviews(path, [scope_review(recovered["items"][GIESSI], reviewed_at)], now=reviewed_at)
    row = next(item for item in P.state_for("Seon", path, today=next_day.date()).items if P.identity(item.candidate["source_id"]) == GIESSI)
    assert row.verified is None  # Relevance alone cannot resurrect the old legal proof.
    P.apply_verifications(path, [expired_proof(recovered["items"][GIESSI], reviewed_at)], now=reviewed_at)
    assert scheduled_pass(monkeypatch, env, recovery_at + timedelta(hours=2), FixtureTransport())["status"] == "not-due"
    # A later source reversion to the original hash still invalidates both decisions.
    assert scheduled_pass(monkeypatch, env, NOW + timedelta(days=2), FixtureTransport())["status"] == "success"
    reverted = P.validate_store(P._read(path))["items"][GIESSI]
    assert reverted["fingerprint"] == store["items"][GIESSI]["fingerprint"] and P.review_state(reverted) == "changed"
    assert "invalidated_at" in reverted["legal"]["Seon"]


CHILD = r'''
import json, socket, sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_planning_local_integration import FixtureTransport, clock_at, deny_network, environment
import planning_collect as C
import planning_preview as P
import planning_refresh as R
socket.getaddrinfo = deny_network
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
mode, raw_path, raw_time = sys.argv[1:]
path, when = Path(raw_path), datetime.fromisoformat(raw_time)
transport = FixtureTransport(park=mode == "refresh-claim")
if mode.startswith("refresh"):
    with patch.object(C, "Transport", return_value=transport):
        result = R.run_once(environ=environment(path), clock=clock_at(when))
    print(json.dumps({"result": result, "requests": len(transport.calls)}), flush=True)
else:
    snapshot = C.collect_amtsblatt("Seon", transport=FixtureTransport(correction=True), now=when)
    replace = P.os.replace
    def boundary(source, destination):
        if mode == "preview-after":
            replace(source, destination)
        print("BOUNDARY", flush=True)
        import threading
        threading.Event().wait()
    with patch.object(P.os, "replace", side_effect=boundary):
        P.import_snapshots(path, [snapshot], now=when + timedelta(seconds=1))
'''


def child_environment():
    return {**{key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT") if key in os.environ},
            "PYTHONDONTWRITEBYTECODE": "1"}


@contextmanager
def parked_child(tmp_path, mode, path, when):
    error_path = tmp_path / f"{mode}.stderr"
    with error_path.open("w") as errors:
        process = subprocess.Popen([sys.executable, "-u", "-c", CHILD, mode, str(path), when.isoformat()],
                                   cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=errors, text=True)
        try:
            readable, _, _ = select.select([process.stdout], [], [], 15)
            assert readable and process.stdout.readline().strip() == "BOUNDARY", error_path.read_text()
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()


def refresh_child(path, when):
    result = subprocess.run([sys.executable, "-u", "-c", CHILD, "refresh-once", str(path), when.isoformat()],
                            cwd=ROOT, env=child_environment(), text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(os.name != "posix", reason="Refresh uses POSIX advisory locking")
def test_sigkill_after_durable_refresh_claim_releases_lock_and_restart_cooldown(tmp_path):
    path = tmp_path / "preview.json"
    with parked_child(tmp_path, "refresh-claim", path, NOW) as process:
        state_path = R.configuration(environment(path)).state_path
        claim = R._read_state(state_path)
        assert claim["status"] == "running" and not path.exists()
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
    restarted = refresh_child(path, NOW + timedelta(minutes=30))
    assert restarted == {"result": {"status": "cooldown"}, "requests": 0}  # Not busy: OS released the killed owner's lock.
    assert R._read_state(state_path) == claim and not path.exists()
    retried = refresh_child(path, NOW + timedelta(hours=1, seconds=1))
    assert retried["result"] == {"status": "success", "sources_succeeded": 3, "sources_failed": 0}
    assert retried["requests"] == 6 and len(P.validate_store(P._read(path))["items"]) == 22
    assert R._read_state(state_path)["status"] == "success"


@pytest.mark.skipif(os.name != "posix", reason="Test deliberately SIGKILLs a POSIX child")
@pytest.mark.parametrize("boundary", ["before", "after"])
def test_sigkill_at_atomic_preview_commit_keeps_complete_old_or_new_store(tmp_path, boundary):
    path = tmp_path / "preview.json"
    original = C.collect_amtsblatt("Seon", transport=FixtureTransport(), now=NOW)
    P.import_snapshots(path, [original], now=NOW)
    before = path.read_bytes()
    when = NOW + timedelta(days=1)
    with parked_child(tmp_path, "preview-" + boundary, path, when) as process:
        current = P.validate_store(P._read(path))
        if boundary == "before":
            assert path.read_bytes() == before
        else:
            assert path.read_bytes() != before and current["items"][GIESSI]["candidate"]["publication_version_label"] == "Korrektur"
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
    # The killed owner cannot retain the preview lock; the next commit can recover.
    changed = C.collect_amtsblatt("Seon", transport=FixtureTransport(correction=True), now=when + timedelta(hours=1))
    recovered = P.import_snapshots(path, [changed], now=when + timedelta(hours=1))
    assert len(recovered["items"]) == 11 and recovered["items"][GIESSI]["candidate"]["publication_version_label"] == "Korrektur"


def test_ten_municipality_pass_and_first_excess_page_budget_are_bounded(tmp_path, monkeypatch):
    names = ("Seon", "Menziken", "Baden", "Reinach", "Oberrohrdorf", "Hausen (AG)", "Beinwil am See", "Aarau", "Egliswil", "Dintikon")
    path = tmp_path / "preview.json"
    env = environment(path, names=names, pages=4)
    assert R.check(R.configuration(env))["maximum_pages"] == 84
    transport = FixtureTransport(empty=True)
    assert scheduled_pass(monkeypatch, env, NOW, transport) == {"status": "success", "sources_succeeded": 21, "sources_failed": 0}
    assert len(transport.calls) == 22  # 20 town searches, one shared widget and one empty canton search.
    sources = P.validate_store(P._read(path))["selections"].values()
    assert len(sources) == 21 and all(not source["members"] and not source["error"] for source in sources)
    before = path.read_bytes()
    with pytest.raises(R.ConfigError, match="100 pages"):
        R.run_once(environ=env | {R.PREFIX + "MAX_PAGES": "5"}, clock=clock_at(NOW))
    with pytest.raises(R.ConfigError, match="1–10"):
        R.configuration(environment(path, names=names + ("Rupperswil",), pages=4))
    assert len(transport.calls) == 22 and path.read_bytes() == before


def test_swiss_spring_missing_hour_runs_at_next_existing_hour(tmp_path, monkeypatch):
    env = environment(tmp_path / "preview.json", hour=2)
    transport = FixtureTransport(empty=True)
    before_gap = datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)
    assert scheduled_pass(monkeypatch, env, before_gap, transport)["status"] == "not-due"
    assert not transport.calls and not list(tmp_path.iterdir())
    after_gap = datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)  # Zurich03:00; 02:00 does not exist.
    assert scheduled_pass(monkeypatch, env, after_gap, transport)["status"] == "success"
    count = len(transport.calls)
    assert scheduled_pass(monkeypatch, env, after_gap + timedelta(minutes=30), transport)["status"] == "not-due"
    assert len(transport.calls) == count


def test_swiss_repeated_autumn_hour_does_not_repeat_daily_success(tmp_path, monkeypatch):
    env = environment(tmp_path / "preview.json", hour=2)
    transport = FixtureTransport(empty=True)
    first = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)  # Zurich02:30+02
    repeated = first + timedelta(hours=1)  # Zurich02:30+01
    assert scheduled_pass(monkeypatch, env, first, transport)["status"] == "success"
    count = len(transport.calls)
    assert scheduled_pass(monkeypatch, env, repeated, transport)["status"] == "not-due"
    assert len(transport.calls) == count
    assert scheduled_pass(monkeypatch, env, repeated + timedelta(days=1), transport)["status"] == "success"
    assert len(transport.calls) == count * 2


class Elapsed:
    def __init__(self):
        self.seconds = 0
    def __call__(self):
        return self.seconds


def test_dribbling_response_cannot_reset_total_request_deadline(monkeypatch):
    elapsed = Elapsed()
    class Dribble(FakeResponse):
        def read1(self, size):
            elapsed.seconds += 6
            return super().read1(1)  # Each individual read fits 10s, together they exceed it.
    factory = ConnectionFactory([Dribble(body=b"secret body")])
    url = A.municipal_planning_link("Seon")
    validator = C._amtsblatt_validator(C._amtsblatt_selection(url)[0], 1)
    with monkeypatch.context() as scoped:
        scoped.setattr(C.time, "monotonic", elapsed)
        with pytest.raises(C.CollectionError, match="timed out"):
            C.Transport(timeout=10, connection_factory=factory).request("GET", url, validate_url=validator)
    assert factory.instances[0].closed and factory.instances[0].sock.timeouts[-1] == 4


def test_redirect_cannot_reset_total_request_deadline(monkeypatch):
    elapsed = Elapsed()
    url = A.municipal_planning_link("Seon")
    parts = urlsplit(url)
    redirect = urlunsplit(parts._replace(query=urlencode(list(reversed(parse_qsl(parts.query))))))
    assert redirect != url  # Same approved filters, a distinct permitted URL.
    class DelayedFactory(ConnectionFactory):
        def __call__(self, *args, **kwargs):
            connection = super().__call__(*args, **kwargs)
            request = connection.request
            def delayed(*args, **kwargs):
                elapsed.seconds += 6
                return request(*args, **kwargs)
            connection.request = delayed
            return connection
    factory = DelayedFactory([FakeResponse(status=302, headers={"Location": redirect}), FakeResponse()])
    validator = C._amtsblatt_validator(C._amtsblatt_selection(url)[0], 1)
    with monkeypatch.context() as scoped:
        scoped.setattr(C.time, "monotonic", elapsed)
        with pytest.raises(C.CollectionError, match="timed out"):
            C.Transport(timeout=10, connection_factory=factory).request("GET", url, validate_url=validator)
    assert len(factory.instances) == 2 and factory.instances[1].timeout == 4
    assert all(connection.closed for connection in factory.instances)
