"""Scheduled metadata collection is bounded, authorized and durable."""
import fcntl
import json
import os
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import patch

import pytest

import planning_collect as C
import planning_preview as P
import planning_refresh as R
import scheduler
from test_planning_preview import NOW, amts_snapshot, canton_snapshot, review


def environment(tmp_path, **changes):
    return {P.ENV: str(tmp_path / "preview.json"), R.PREFIX + "ENABLED": "1",
            R.PREFIX + "MUNICIPALITIES": "Seon,Menziken", R.PREFIX + "HOUR": "8",
            "SCOPE_PLANNING_REUSE_AUTHORIZED": "1", "SCOPE_PLANNING_REUSE_REFERENCE": "Explicit test approval",
            **changes}


def ticking(start=NOW):
    current = start - timedelta(milliseconds=1)
    def clock():
        nonlocal current
        current += timedelta(milliseconds=1)
        return current
    return clock


def collectors(calls, *, fail=None, titles=False):
    def municipal(name, kind, **kwargs):
        calls.append((name, kind, kwargs["now"]))
        if fail == (name, kind):
            raise C.CollectionError("Private provider body with token SECRET")
        snapshot = amts_snapshot(name=name, kind=kind, stamp=kwargs["now"])
        if titles:
            snapshot["candidates"][0]["title"] += f" {name} {kind}"  # Same ID changes across selections.
        return snapshot
    def canton(**kwargs):
        calls.append(("canton", "current", kwargs["now"]))
        if fail == ("canton", "current"):
            raise RuntimeError("Private provider body with token SECRET")
        return canton_snapshot(stamp=kwargs["now"])
    return municipal, canton


def test_default_off_never_reads_writes_or_contacts_sources(tmp_path):
    calls = []
    with patch.object(R, "check", side_effect=AssertionError("No file read")):
        assert R.run_once(environ={}, collectors=collectors(calls)) == {"status": "off"}
    assert not calls and not list(tmp_path.iterdir())
    # Stale optional fields are harmless while explicitly disabled.
    assert R.configuration({R.PREFIX + "ENABLED": "0", P.ENV: "invalid", "SCOPE_PLANNING_REUSE_AUTHORIZED": "1"}).enabled is False


@pytest.mark.parametrize("changes", [
    {R.PREFIX + "ENABLED": "true"}, {"SCOPE_PLANNING_REUSE_AUTHORIZED": "0"},
    {"SCOPE_PLANNING_REUSE_REFERENCE": ""}, {P.ENV: "preview.json"},
    {R.PREFIX + "MUNICIPALITIES": ""}, {R.PREFIX + "MUNICIPALITIES": "Unknown"},
    {R.PREFIX + "MUNICIPALITIES": "Hausen AG,Hausen (AG)"},
    {R.PREFIX + "HOUR": "24"}, {R.PREFIX + "MAX_PAGES": "21"},
    {R.PREFIX + "MUNICIPALITIES": "Seon,Menziken,Baden,Reinach,Oberrohrdorf,Hausen AG", R.PREFIX + "MAX_PAGES": "10"},
    {R.PREFIX + "TIMEOUT": "nan"}, {R.PREFIX + "TIMEOUT": "31"},
])
def test_invalid_or_unauthorized_configuration_never_makes_requests(tmp_path, changes):
    calls = []
    with pytest.raises(R.ConfigError):
        R.run_once(environ=environment(tmp_path, **changes), collectors=collectors(calls), clock=ticking())
    assert not calls and not list(tmp_path.iterdir())


def test_check_is_readonly_and_rejects_unrelated_store_state_and_symlinks(tmp_path):
    config = R.configuration(environment(tmp_path))
    assert R.check(config)["status"] == "configured"
    assert not list(tmp_path.iterdir())
    config.store.write_text('{"records":[]}')
    before = config.store.read_bytes()
    with pytest.raises(C.CollectionError):
        R.check(config)
    assert config.store.read_bytes() == before and not config.lock_path.exists()
    config.store.unlink()
    target = tmp_path / "target.json"
    target.write_text(json.dumps(P._empty()))
    config.store.symlink_to(target)
    with pytest.raises(C.CollectionError, match="symlink"):
        R.check(config)
    config.store.unlink()
    config.state_path.write_text('{"schema":"other"}')
    with pytest.raises(C.CollectionError):
        R.check(config)
    config.state_path.unlink()
    config.lock_path.symlink_to(target)
    with pytest.raises(C.CollectionError, match="symlink"):
        R.check(config)


def test_daily_success_restarts_skip_and_shared_canton_is_fetched_once(tmp_path):
    env, calls = environment(tmp_path), []
    result = R.run_once(environ=env, collectors=collectors(calls), clock=ticking())
    assert result == {"status": "success", "sources_succeeded": 5, "sources_failed": 0}
    assert len(calls) == 5 and sum(name == "canton" for name, _, _ in calls) == 1
    assert len({stamp for _, _, stamp in calls}) == 5
    path = R.configuration(env).store
    stored = P.validate_store(json.loads(path.read_text()))
    assert len(stored["selections"]) == 5 and len(stored["items"]) == 2
    assert all(P.review_state(item) == "pending" and "legal" not in item for item in stored["items"].values())
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(NOW + timedelta(hours=2))) == {"status": "not-due"}
    assert len(calls) == 5
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(NOW + timedelta(days=1)))["status"] == "success"
    assert len(calls) == 10


def test_swiss_schedule_hour_uses_dst_and_does_not_create_lock_when_early(tmp_path):
    env, calls = environment(tmp_path), []
    summer = datetime(2026, 7, 1, 5, 59, tzinfo=timezone.utc)  # Zurich07:59
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(summer)) == {"status": "not-due"}
    assert not calls and not list(tmp_path.iterdir())
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(summer + timedelta(minutes=1)))["status"] == "success"


def test_partial_failure_preserves_review_data_and_retries_only_after_cooldown(tmp_path):
    env, calls = environment(tmp_path), []
    config = R.configuration(env)
    R.run_once(environ=env, collectors=collectors(calls), clock=ticking())
    stored = P.validate_store(json.loads(config.store.read_text()))
    candidate = next(item["candidate"] for item in stored["items"].values() if item["candidate"]["source_id"]["namespace"] == P.NS_AMTSBLATT)
    P.apply_reviews(config.store, [review(candidate, municipalities=["Seon"], stamp=NOW + timedelta(minutes=1))], now=NOW + timedelta(minutes=1))
    before = P.validate_store(json.loads(config.store.read_text()))
    retry_day = NOW + timedelta(days=1)
    result = R.run_once(environ=env, collectors=collectors(calls, fail=("Seon", "municipal")), clock=ticking(retry_day))
    assert result["status"] == "partial" and result["sources_failed"] == 1
    key = P.selection_key({"provider": "amtsblatt.ag.ch", "kind": "municipal", "municipality": "Seon"})
    after = P.validate_store(json.loads(config.store.read_text()))
    assert after["selections"][key]["last_success_at"] == before["selections"][key]["last_success_at"]
    assert after["selections"][key]["members"] == before["selections"][key]["members"]
    assert P.state_for("Seon", config.store).items[0].candidate == candidate
    assert "SECRET" not in config.store.read_text() + config.state_path.read_text()
    count = len(calls)
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(retry_day + timedelta(minutes=59))) == {"status": "cooldown"}
    assert len(calls) == count
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(retry_day + timedelta(hours=1, seconds=1)))["status"] == "success"


def test_claim_precedes_requests_and_crash_restart_obeys_cooldown(tmp_path):
    env, calls = environment(tmp_path), []
    config = R.configuration(env)
    class Crash(BaseException):
        pass
    def crashed(*args, **kwargs):
        claim = R._read_state(config.state_path)
        assert claim["status"] == "running" and claim["last_finished_at"] is None
        raise Crash()
    with pytest.raises(Crash):
        R.run_once(environ=env, collectors=(crashed, crashed), clock=ticking())
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(NOW + timedelta(minutes=30))) == {"status": "cooldown"}
    assert not calls
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(NOW + timedelta(hours=1, seconds=1)))["status"] == "success"


def test_process_lock_is_nonblocking_and_preserves_state(tmp_path):
    env, calls = environment(tmp_path), []
    config = R.configuration(env)
    fd = os.open(config.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking()) == {"status": "busy"}
        assert not calls and not config.state_path.exists() and not config.store.exists()
    finally:
        os.close(fd)


def test_source_versions_commit_separately_with_fresh_timestamps(tmp_path):
    calls = []
    result = R.run_once(environ=environment(tmp_path), collectors=collectors(calls, titles=True), clock=ticking())
    assert result["status"] == "success"
    item = next(item for item in json.loads((tmp_path / "preview.json").read_text())["items"].values() if item["candidate"]["source_id"]["namespace"] == P.NS_AMTSBLATT)
    assert len(item["audit"]) == 3 and item["candidate"]["title"].endswith("Seon canton-approvals")


def test_cli_check_does_not_mutate_and_configuration_errors_are_visible(tmp_path, monkeypatch, capsys):
    for name, value in environment(tmp_path).items():
        monkeypatch.setenv(name, value)
    with patch.object(C, "Transport", side_effect=AssertionError("No request client")):
        assert R.main(["--check"]) == 0
    assert "configured" in capsys.readouterr().out and not list(tmp_path.iterdir())
    monkeypatch.setenv("SCOPE_PLANNING_REUSE_REFERENCE", "")
    assert R.main(["--once"]) == 1
    assert "REUSE_REFERENCE" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_scheduler_email_loop_continues_while_one_planning_worker_is_blocked(monkeypatch):
    started, release, finished = Event(), Event(), Event()
    planning_calls, email_calls, sleeps = [], [], []
    def blocked():
        planning_calls.append(1)
        started.set()
        try:
            assert release.wait(5)
            return {"status": "success", "sources_succeeded": 1, "sources_failed": 0}
        finally:
            finished.set()
    class EndLoop(Exception):
        pass
    def sleep(_):
        sleeps.append(1)
        assert started.wait(5)
        assert not finished.is_set() and len(planning_calls) == 1
        if len(sleeps) == 2:
            raise EndLoop()
    monkeypatch.setattr(R, "run_once", blocked)
    monkeypatch.setattr(scheduler, "run_once", lambda db: email_calls.append(db) or {})
    monkeypatch.setattr(scheduler.time, "sleep", sleep)
    try:
        with pytest.raises(EndLoop):
            scheduler.serve(interval=1)
        assert len(email_calls) == 2 and len(planning_calls) == 1
    finally:
        release.set()
        assert finished.wait(5)


def test_scheduler_planning_config_errors_and_provider_bodies_are_not_silent_or_logged(capsys):
    with patch.object(R, "run_once", side_effect=R.ConfigError("Scheduled refresh requires authorization")):
        scheduler.planning_tick()
    assert "requires authorization" in capsys.readouterr().err
    with patch.object(R, "run_once", side_effect=RuntimeError("SECRET body")):
        scheduler.planning_tick()
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "SECRET" not in err


def test_attempt_cap_limits_passes_per_day_then_resets_next_day(tmp_path):
    env, calls = environment(tmp_path), []
    fail = ("Seon", "municipal")  # A deterministic failure (e.g. search exceeds max-pages).
    for attempt in range(R.MAX_ATTEMPTS_PER_DAY):
        when = NOW + timedelta(hours=2 * attempt, seconds=1)
        result = R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(when))
        assert result["status"] == "partial" and result["sources_failed"] == 1
    assert len(calls) == 5 * R.MAX_ATTEMPTS_PER_DAY
    count = len(calls)
    capped = NOW + timedelta(hours=2 * R.MAX_ATTEMPTS_PER_DAY, seconds=1)
    assert R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(capped)) == {"status": "not-due"}
    assert len(calls) == count  # Capped: no provider contacted.
    tomorrow = R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(NOW + timedelta(days=1)))
    assert tomorrow["status"] == "partial" and len(calls) == count + 5


def test_successful_pass_remains_not_due_for_rest_of_day_well_under_the_cap(tmp_path):
    env, calls = environment(tmp_path), []
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking())["status"] == "success"
    assert R._read_state(R.configuration(env).state_path)["attempts"] == 1
    assert R.run_once(environ=env, collectors=collectors(calls), clock=ticking(NOW + timedelta(hours=2))) == {"status": "not-due"}
    assert len(calls) == 5  # Only the first, successful pass contacted providers.


def test_config_change_after_cap_runs_again_same_day_with_a_fresh_count(tmp_path):
    env, calls = environment(tmp_path), []
    fail = ("Seon", "municipal")
    for attempt in range(R.MAX_ATTEMPTS_PER_DAY):
        when = NOW + timedelta(hours=2 * attempt, seconds=1)
        assert R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(when))["status"] == "partial"
    capped = NOW + timedelta(hours=2 * R.MAX_ATTEMPTS_PER_DAY, seconds=1)
    assert R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(capped)) == {"status": "not-due"}
    changed = environment(tmp_path, **{R.PREFIX + "MAX_PAGES": "4"})  # Any config change hashes differently.
    after = capped + timedelta(hours=2)  # Clear any cooldown left over from the capped config's last claim.
    result = R.run_once(environ=changed, collectors=collectors(calls, fail=fail), clock=ticking(after))
    assert result["status"] == "partial"
    assert R._read_state(R.configuration(changed).state_path)["attempts"] == 1


def test_crash_after_claim_counts_toward_attempt_cap(tmp_path):
    env, calls = environment(tmp_path), []
    config = R.configuration(env)
    fail = ("Seon", "municipal")

    class Crash(BaseException):
        pass

    def crashed(*args, **kwargs):
        raise Crash()

    with pytest.raises(Crash):
        R.run_once(environ=env, collectors=(crashed, crashed), clock=ticking())
    assert R._read_state(config.state_path)["attempts"] == 1
    for attempt in (1, 2):
        when = NOW + timedelta(hours=2 * attempt, seconds=1)
        result = R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(when))
        assert result["status"] == "partial"
    assert R._read_state(config.state_path)["attempts"] == R.MAX_ATTEMPTS_PER_DAY
    count = len(calls)
    capped = NOW + timedelta(hours=2 * (R.MAX_ATTEMPTS_PER_DAY + 1), seconds=1)
    assert R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(capped)) == {"status": "not-due"}
    assert len(calls) == count


def test_version_one_state_is_read_as_one_attempt_and_cap_still_applies(tmp_path):
    env, calls = environment(tmp_path), []
    config = R.configuration(env)
    fail = ("Seon", "municipal")
    assert R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking())["status"] == "partial"
    stored = json.loads(config.state_path.read_text())
    assert stored["schema_version"] == 2 and stored.pop("attempts") == 1
    stored["schema_version"] = 1  # A pre-cap state file, as left by an older deployment.
    config.state_path.write_text(json.dumps(stored, indent=2) + "\n")
    assert R._read_state(config.state_path)["attempts"] == 1
    for attempt in (1, 2):
        when = NOW + timedelta(hours=2 * attempt, seconds=1)
        result = R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(when))
        assert result["status"] == "partial"
    count = len(calls)
    capped = NOW + timedelta(hours=2 * 3, seconds=1)
    assert R.run_once(environ=env, collectors=collectors(calls, fail=fail), clock=ticking(capped)) == {"status": "not-due"}
    assert len(calls) == count


@pytest.mark.parametrize("bad", [-1, 4, "2", None])
def test_malformed_state_attempts_are_refused(tmp_path, bad):
    env, calls = environment(tmp_path), []
    config = R.configuration(env)
    R.run_once(environ=env, collectors=collectors(calls), clock=ticking())
    stored = json.loads(config.state_path.read_text())
    stored["attempts"] = bad
    config.state_path.write_text(json.dumps(stored, indent=2) + "\n")
    with pytest.raises(C.CollectionError):
        R._read_state(config.state_path)


@pytest.mark.parametrize("raw, expected", [
    (None, R.KINDS),
    ("", R.KINDS),
    ("municipal", ("municipal",)),
    (" municipal ", ("municipal",)),
    ("municipal,canton-approvals", ("municipal", "canton-approvals")),
    ("canton-approvals,municipal", ("municipal", "canton-approvals")),
])
def test_kinds_parsing_accepts_canonical_subsets(tmp_path, raw, expected):
    changes = {} if raw is None else {R.PREFIX + "KINDS": raw}
    assert R.configuration(environment(tmp_path, **changes)).kinds == expected


@pytest.mark.parametrize("raw", ["foo", "municipal,", "municipal,municipal"])
def test_kinds_parsing_rejects_unknown_blank_and_duplicate_entries(tmp_path, raw):
    with pytest.raises(R.ConfigError):
        R.configuration(environment(tmp_path, **{R.PREFIX + "KINDS": raw}))


def test_kinds_municipal_only_skips_canton_approvals_and_narrows_selections(tmp_path):
    env, calls = environment(tmp_path, **{R.PREFIX + "KINDS": "municipal"}), []
    config = R.configuration(env)
    selections = R._selections(config)
    assert len(selections) == 3  # Seon + Menziken (municipal only) + the shared consultation selection.
    assert all(selection.get("kind") != "canton-approvals" for selection in selections)
    result = R.run_once(environ=env, collectors=collectors(calls), clock=ticking())
    assert result == {"status": "success", "sources_succeeded": 3, "sources_failed": 0}
    assert all(kind != "canton-approvals" for _, kind, _ in calls)


def test_page_budget_scales_with_configured_kinds(tmp_path):
    names = "Seon,Menziken,Baden,Reinach,Oberrohrdorf,Hausen AG,Beinwil am See,Aarau,Egliswil,Dintikon"
    base = environment(tmp_path, **{R.PREFIX + "MUNICIPALITIES": names})
    assert R.configuration({**base, R.PREFIX + "MAX_PAGES": "4"}).max_pages == 4
    with pytest.raises(R.ConfigError):
        R.configuration({**base, R.PREFIX + "MAX_PAGES": "5"})
    only_municipal = {**base, R.PREFIX + "KINDS": "municipal"}
    assert R.configuration({**only_municipal, R.PREFIX + "MAX_PAGES": "9"}).max_pages == 9
    with pytest.raises(R.ConfigError):
        R.configuration({**only_municipal, R.PREFIX + "MAX_PAGES": "10"})


def test_digest_differs_between_kinds_settings(tmp_path):
    env = environment(tmp_path)
    default_digest = R.configuration(env).digest
    municipal_only_digest = R.configuration({**env, R.PREFIX + "KINDS": "municipal"}).digest
    assert default_digest != municipal_only_digest
