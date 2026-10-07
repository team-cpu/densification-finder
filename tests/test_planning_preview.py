"""Local preview boundaries: source metadata, operator scope and update history."""
import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import amtsblatt as A
import planning_collect as C
import planning_preview as P
import planning as PL

FIXTURES = Path(__file__).parent / "fixtures" / "planning_sources"
NOW = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)


def amts_snapshot(*, name="Seon", kind="municipal", stamp=NOW, count=1):
    parsed = C._html((FIXTURES / "amtsblatt-seon-page1.html").read_bytes())
    candidates = [C._amtsblatt_card(card) for card in parsed.cards[:count]]
    url = (A.municipal_planning_link if kind == "municipal" else A.canton_approvals_link)(name)
    return C._snapshot({"provider": "amtsblatt.ag.ch", "search_url": url},
                       {"kind": kind, "municipality_query_hint": A.canonical_name(name), "max_pages": 5},
                       C._now(stamp), candidates, [{"page": 1, "count": count, "url": url}], count)


def canton_snapshot(*, stamp=NOW, count=1):
    data = json.loads((FIXTURES / "consultations-current-page0.json").read_text())
    candidates = [C._consultation_card(item, "current") for item in data["dynamiccontent"][:count]]
    return C._snapshot({"provider": "www.ag.ch", "search_url": C.AG_ORIGIN + C.CONSULTATION_PATHS["current"], "endpoint": C.SEARCH_API},
                       {"mode": "current", "term": "", "cms_validity_as_of": C._now(stamp), "max_pages": 5},
                       C._now(stamp), candidates, [{"page": 0, "count": count, "url": C.SEARCH_API}], count)


def review(candidate, *, municipalities=None, canton_wide=False, action="include", stamp=None):
    value = {"source_id": candidate["source_id"], "fingerprint": P.fingerprint(candidate), "action": action,
             "evidence_url": candidate["url"], "rationale": "Source reviewed for planning relevance and named municipality.",
             "reviewed_at": C._now(stamp or NOW + timedelta(minutes=1))}
    if action == "include":
        value.update(municipalities=municipalities or [], canton_wide=canton_wide)
    return value


def seed(tmp_path, snapshots=None):
    path = tmp_path / "preview.json"
    snapshots = snapshots or [amts_snapshot(), canton_snapshot()]
    P.import_snapshots(path, snapshots, now=NOW)
    return path


def test_success_off_empty_unqueried_failure_and_stale_are_distinct(tmp_path, monkeypatch):
    monkeypatch.delenv(P.ENV, raising=False)
    assert P.state_for("Seon").status == "off"
    assert P.state_for("Seon", tmp_path / "missing.json").status == "error"
    path = seed(tmp_path, [amts_snapshot(count=0)])
    view = P.state_for("Seon", path, today=NOW.date())
    assert view.status == "ok" and view.municipality_queried and not view.items and view.sources[0]["empty"]
    assert not view.sources[0]["stale"]
    assert not P.state_for("Baden", path).municipality_queried
    assert not P.state_for("Baden", path).sources
    assert P.state_for("Seon", path, today=date(2026, 10, 14)).sources[0]["stale"]
    monkeypatch.setenv(P.ENV, str(path))
    assert P.state_for("Seon").status == "ok"


def test_unreviewed_scope_and_publisher_never_include_automatically(tmp_path):
    path = seed(tmp_path)
    view = P.state_for("Seon", path, today=NOW.date())
    assert not view.items and view.pending == 2 and view.changed == 0
    assert P.state_for("Baden", path).pending == 1  # Shared canton queue, not a scope claim.
    assert not P.state_for("Baden", path).items
    rows = P.queue(path)
    assert len(rows) == 2 and all(row["state"] == "pending" for row in rows)
    assert all(len(row["fingerprint"]) == 64 and row["url"].startswith("https://") for row in rows)


def test_explicit_multimunicipality_canton_and_excluded_reviews(tmp_path):
    municipal, canton = amts_snapshot(count=2), canton_snapshot()
    path = seed(tmp_path, [municipal, canton])
    reviews = [review(municipal["candidates"][0], municipalities=["Seon", "Hausen AG"]),
               review(municipal["candidates"][1], action="exclude"),
               review(canton["candidates"][0], canton_wide=True)]
    P.apply_reviews(path, reviews, now=NOW + timedelta(minutes=1))
    seon = P.state_for("Seon", path)
    assert len(seon.items) == 2
    assert len(P.state_for("Hausen (AG)", path).items) == 2
    assert len(P.state_for("Baden", path).items) == 1
    assert seon.pending == 0 and not P.queue(path)
    assert [row["state"] for row in P.queue(path, all_items=True)].count("excluded") == 1
    stored = json.loads(path.read_text())
    scoped = stored["items"][P.identity(municipal["candidates"][0]["source_id"])]["review"]
    assert scoped["municipalities"] == ["Hausen (AG)", "Seon"]
    assert not {"records", "since", "stand"} & stored.keys()
    assert all(not {"verified", "stage", "effective_on"} & item["candidate"].keys() for item in stored["items"].values())


@pytest.mark.parametrize("changes", [
    {"municipalities": []}, {"municipalities": ["Unknown"]}, {"municipalities": ["Seon"], "canton_wide": True},
    {"municipalities": "Seon"}, {"canton_wide": "true"}, {"municipalities": ["Hausen AG", "Hausen (AG)"]},
    {"evidence_url": "javascript:alert(1)"}, {"evidence_url": "https://evil.invalid/notice"},
    {"evidence_url": "https://amtsblatt.ag.ch/ekab/00.999.999/publikation/"},
    {"reviewed_at": "2026-10-06"}, {"rationale": ""}, {"rationale": "x" * 501},
    {"action": "include", "stage": "in_kraft"}, {"fingerprint": "a" * 64},
])
def test_invalid_or_stale_review_batch_changes_nothing(tmp_path, changes):
    snapshot = amts_snapshot()
    path = seed(tmp_path, [snapshot])
    before = path.read_bytes()
    decision = review(snapshot["candidates"][0], municipalities=["Seon"])
    decision.update(changes)
    with pytest.raises(C.CollectionError):
        P.apply_reviews(path, [decision], now=NOW + timedelta(minutes=1))
    assert path.read_bytes() == before


def test_all_review_changes_validate_before_replacement(tmp_path):
    snapshot = amts_snapshot(count=2)
    path = seed(tmp_path, [snapshot])
    before = path.read_bytes()
    valid = review(snapshot["candidates"][0], municipalities=["Seon"])
    invalid = review(snapshot["candidates"][1], municipalities=["Unknown"])
    with pytest.raises(C.CollectionError):
        P.apply_reviews(path, [valid, invalid], now=NOW + timedelta(minutes=1))
    assert path.read_bytes() == before
    with pytest.raises(C.CollectionError, match="repeated"):
        P.apply_reviews(path, [valid, valid], now=NOW + timedelta(minutes=1))
    assert path.read_bytes() == before


def test_duplicate_import_and_overlapping_selections_preserve_review(tmp_path):
    municipal, approvals = amts_snapshot(), amts_snapshot(kind="canton-approvals")
    path = seed(tmp_path, [municipal])
    P.apply_reviews(path, [review(municipal["candidates"][0], municipalities=["Seon"])], now=NOW + timedelta(minutes=1))
    store = P.import_snapshots(path, [municipal, approvals], now=NOW + timedelta(minutes=2))
    assert len(store["items"]) == 1 and len(store["selections"]) == 2
    item = next(iter(store["items"].values()))
    assert len(item["seen_in"]) == 2 and P.review_state(item) == "included"
    P.import_snapshots(path, [municipal, approvals], now=NOW + timedelta(minutes=3))
    assert len(P.state_for("Seon", path).items) == 1
    assert not P.queue(path)


def test_corrected_metadata_invalidates_inclusion_and_stale_decisions(tmp_path):
    original = amts_snapshot()
    path = seed(tmp_path, [original])
    decision = review(original["candidates"][0], municipalities=["Seon"])
    P.apply_reviews(path, [decision], now=NOW + timedelta(minutes=1))
    correction = amts_snapshot(stamp=NOW + timedelta(hours=1))
    correction["candidates"][0]["title"] += "; Berichtigung"
    store = P.import_snapshots(path, [correction], now=NOW + timedelta(hours=1))
    item = next(iter(store["items"].values()))
    assert len(item["audit"]) == 1 and item["audit"][0]["review_action"] == "include"
    assert P.review_state(item) == "changed"
    assert not P.state_for("Seon", path).items and P.state_for("Seon", path).changed == 1
    before = path.read_bytes()
    with pytest.raises(C.CollectionError, match="Stale"):
        P.apply_reviews(path, [decision], now=NOW + timedelta(hours=1, minutes=1))
    assert path.read_bytes() == before
    # Reverting to an older identical fingerprint must still require a new review.
    reverted = amts_snapshot(stamp=NOW + timedelta(hours=2))
    P.import_snapshots(path, [reverted], now=NOW + timedelta(hours=2))
    assert P.state_for("Seon", path).changed == 1 and not P.state_for("Seon", path).items
    P.apply_reviews(path, [review(reverted["candidates"][0], municipalities=["Seon"], stamp=NOW + timedelta(hours=2, minutes=1))], now=NOW + timedelta(hours=2, minutes=1))
    assert len(P.state_for("Seon", path).items) == 1


def test_disappearance_is_visible_without_changing_legal_stage(tmp_path):
    snapshot = amts_snapshot()
    path = seed(tmp_path, [snapshot])
    P.apply_reviews(path, [review(snapshot["candidates"][0], municipalities=["Seon"])], now=NOW + timedelta(minutes=1))
    P.import_snapshots(path, [amts_snapshot(count=0, stamp=NOW + timedelta(hours=1))], now=NOW + timedelta(hours=1))
    view = P.state_for("Seon", path)
    assert len(view.items) == 1 and view.items[0].absent and view.sources[0]["empty"]
    assert P.queue(path, all_items=True)[0]["state"] == "included"


def test_conflicting_versions_batch_and_older_source_or_item_do_not_mutate(tmp_path):
    path = tmp_path / "preview.json"
    current = amts_snapshot(stamp=NOW + timedelta(hours=1))
    P.import_snapshots(path, [current], now=NOW + timedelta(hours=1))
    before = path.read_bytes()
    with pytest.raises(C.CollectionError, match="Out-of-order source"):
        P.import_snapshots(path, [amts_snapshot()], now=NOW + timedelta(hours=2))
    conflicting = amts_snapshot(kind="canton-approvals")
    conflicting["candidates"][0]["title"] = "Older conflicting metadata"
    with pytest.raises(C.CollectionError, match="Out-of-order.*item"):
        P.import_snapshots(path, [conflicting], now=NOW + timedelta(hours=2))
    later = amts_snapshot(kind="canton-approvals", stamp=NOW + timedelta(hours=2))
    later["candidates"][0]["title"] = "Conflicting version in same batch"
    with pytest.raises(C.CollectionError, match="Conflicting candidate"):
        P.import_snapshots(path, [current, later], now=NOW + timedelta(hours=2))
    assert path.read_bytes() == before


def test_partial_sync_failure_keeps_membership_and_success_dates(tmp_path):
    municipal, approvals, canton = amts_snapshot(), amts_snapshot(kind="canton-approvals"), canton_snapshot()
    path = seed(tmp_path, [municipal, approvals, canton])
    P.apply_reviews(path, [review(municipal["candidates"][0], municipalities=["Seon"])], now=NOW + timedelta(minutes=1))
    earlier = json.loads(path.read_text())
    stamp = NOW + timedelta(days=1)

    def fake_amts(name, kind, **kwargs):
        if kind == "municipal":
            raise C.CollectionError("Source request timed out")
        return amts_snapshot(kind=kind, stamp=stamp)

    def fake_canton(**kwargs):
        return canton_snapshot(stamp=stamp, count=0)

    store, errors = P.sync(path, "Seon", collectors=(fake_amts, fake_canton), now=stamp)
    assert len(errors) == 1
    key = P.selection_key({"provider": "amtsblatt.ag.ch", "kind": "municipal", "municipality": "Seon"})
    source = store["selections"][key]
    assert source["members"] == earlier["selections"][key]["members"]
    assert source["last_success_at"] == earlier["selections"][key]["last_success_at"]
    assert source["last_attempt_at"] == C._now(stamp) and source["error"]
    view = P.state_for("Seon", path)
    assert len(view.items) == 1 and any(s["error"] for s in view.sources)


def test_first_sync_failure_has_error_without_fabricated_success(tmp_path):
    path = tmp_path / "preview.json"

    def failure(*args, **kwargs):
        raise RuntimeError("Response body must never be stored")

    store, errors = P.sync(path, "Seon", collectors=(failure, failure), now=NOW)
    assert len(errors) == 3 and not store["items"]
    assert all(s["last_success_at"] is None and not s["members"] and s["error"] == "Source failed (RuntimeError)" for s in store["selections"].values())
    view = P.state_for("Seon", path)
    assert not view.municipality_queried and len(view.sources) == 3
    assert "Response body" not in path.read_text()


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(schema="production"),
    lambda s: s["traversal"].update(complete_search_snapshot=False),
    lambda s: s["traversal"].update(total_candidates=2),
    lambda s: s["traversal"].update(pages_fetched=2),
    lambda s: s["traversal"]["pages"][0].update(page=2),
    lambda s: s["source"].update(search_url=A.municipal_planning_link("Baden")),
    lambda s: s["parameters"].pop("municipality_query_hint"),
    lambda s: s["parameters"].update(max_pages=51),
    lambda s: s["candidates"][0].update(stage="in_kraft"),
    lambda s: s["candidates"][0].update(url="javascript:alert(1)"),
    lambda s: s["candidates"][0].update(url="https://amtsblatt.ag.ch/ekab/00.999.999/publikation/"),
    lambda s: s["candidates"][0].update(published_on="2026-02-30"),
    lambda s: s["candidates"][0]["source_id"].update(namespace=P.NS_CANTON),
])
def test_corrupt_partial_or_unsafe_snapshots_cannot_replace_store(tmp_path, mutate):
    path = seed(tmp_path, [amts_snapshot()])
    before = path.read_bytes()
    snapshot = amts_snapshot()
    mutate(snapshot)
    with pytest.raises(C.CollectionError):
        P.import_snapshots(path, [snapshot], now=NOW + timedelta(hours=1))
    assert path.read_bytes() == before


def test_canton_snapshot_query_identity_and_dates_are_validated(tmp_path):
    for change in (lambda s: s["parameters"].update(cms_validity_as_of="2026-10-06T00:00:00Z"),
                   lambda s: s["source"].update(endpoint="https://evil.invalid/search"),
                   lambda s: s["candidates"][0].update(url=C.AG_ORIGIN + C.CONSULTATION_PATHS["current"] + "?dc=wrong"),
                   lambda s: s["candidates"][0].update(cms_valid_until="tomorrow")):
        snapshot = canton_snapshot()
        change(snapshot)
        with pytest.raises(C.CollectionError):
            P.import_snapshots(tmp_path / "preview.json", [snapshot], now=NOW)
    assert not (tmp_path / "preview.json").exists()


def test_duplicate_candidates_and_missing_count_metadata_are_rejected(tmp_path):
    snapshot = amts_snapshot(count=2)
    snapshot["candidates"][1] = copy.deepcopy(snapshot["candidates"][0])
    with pytest.raises(C.CollectionError, match="duplicate"):
        P.import_snapshots(tmp_path / "preview.json", [snapshot], now=NOW)
    snapshot = amts_snapshot()
    del snapshot["traversal"]["pages"][0]["count"]
    with pytest.raises(C.CollectionError):
        P.validate_snapshot(snapshot)


def test_store_corruption_production_and_symlinks_refused_without_mutation(tmp_path):
    path = seed(tmp_path)
    good = json.loads(path.read_text())
    variants = [good | {"schema": "other"}, {"since": "2020-01-01", "stand": "2026-10-06", "records": []},
                {"random": "unrelated"}]
    bad = copy.deepcopy(good)
    next(iter(bad["items"].values()))["fingerprint"] = "a" * 64
    variants.append(bad)
    bad = copy.deepcopy(good)
    next(iter(bad["selections"].values()))["members"] = ["invented"]
    variants.append(bad)
    for value in variants:
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        assert P.state_for("Seon", path).status == "error"
        with pytest.raises(C.CollectionError):
            P.import_snapshots(path, [amts_snapshot()], now=NOW)
        assert path.read_bytes() == before
    target = tmp_path / "other.json"
    target.write_text(json.dumps(good))
    path.unlink()
    path.symlink_to(target)
    before = target.read_bytes()
    with pytest.raises(C.CollectionError, match="symlinks"):
        P.import_snapshots(path, [amts_snapshot()], now=NOW)
    assert target.read_bytes() == before


def test_failed_atomic_replacement_preserves_previous_store(tmp_path):
    path = seed(tmp_path)
    before = path.read_bytes()
    with patch.object(P.os, "replace", side_effect=OSError("Write failure")):
        with pytest.raises(OSError):
            P.import_snapshots(path, [amts_snapshot()], now=NOW)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".preview.json.*"))


def test_cli_import_queue_review_and_failed_sync_exit_codes(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(P, "_time", lambda now=None: C._now(NOW + timedelta(hours=1)))
    path = tmp_path / "preview.json"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(amts_snapshot()))
    assert P.main(["import", str(path), str(snapshot)]) == 0
    assert P.main(["queue", str(path)]) == 0
    assert "fingerprint" in capsys.readouterr().out
    batch = tmp_path / "reviews.json"
    decision = review(amts_snapshot()["candidates"][0], municipalities=["Seon"])
    batch.write_text(json.dumps([decision]))
    assert P.main(["review", str(path), str(batch)]) == 0
    assert len(P.state_for("Seon", path).items) == 1
    before = path.read_bytes()
    batch.write_text(json.dumps([decision | {"fingerprint": "a" * 64}]))
    assert P.main(["review", str(path), str(batch)]) == 1
    assert path.read_bytes() == before
    with patch.object(P.C, "collect_amtsblatt", side_effect=C.CollectionError("Network unavailable")), patch.object(P.C, "collect_consultations", side_effect=C.CollectionError("Network unavailable")):
        assert P.main(["sync", str(path), "--municipality", "Seon"]) == 1
    assert len(P.state_for("Seon", path).items) == 1


def test_older_other_selection_cannot_replace_more_recent_unchanged_observation(tmp_path):
    path = seed(tmp_path, [amts_snapshot()])
    unchanged = amts_snapshot(stamp=NOW + timedelta(hours=3))
    P.import_snapshots(path, [unchanged], now=NOW + timedelta(hours=3))
    before = path.read_bytes()
    older = amts_snapshot(kind="canton-approvals", stamp=NOW + timedelta(hours=2))
    older["candidates"][0]["title"] = "An older conflicting version"
    with pytest.raises(C.CollectionError, match="Out-of-order.*item"):
        P.import_snapshots(path, [older], now=NOW + timedelta(hours=4))
    assert path.read_bytes() == before


def test_only_successful_municipal_search_marks_municipality_as_queried(tmp_path):
    path = seed(tmp_path, [amts_snapshot(kind="canton-approvals"), canton_snapshot()])
    assert not P.state_for("Seon", path).municipality_queried


def test_publication_version_label_is_reviewed_metadata_and_invalidates_old_review(tmp_path):
    snapshot = amts_snapshot()
    path = seed(tmp_path, [snapshot])
    P.apply_reviews(path, [review(snapshot["candidates"][0], municipalities=["Seon"])], now=NOW + timedelta(minutes=1))
    newer = amts_snapshot(stamp=NOW + timedelta(hours=1))
    newer["candidates"][0]["publication_version_label"] = "Korrektur"
    P.import_snapshots(path, [newer], now=NOW + timedelta(hours=1))
    assert P.state_for("Seon", path).changed == 1
    assert P.queue(path)[0]["fingerprint"] != P.fingerprint(snapshot["candidates"][0])


def test_absence_from_one_successful_selection_is_visible_even_if_another_retains_membership(tmp_path):
    municipal, approvals = amts_snapshot(), amts_snapshot(kind="canton-approvals")
    path = seed(tmp_path, [municipal, approvals])
    P.apply_reviews(path, [review(municipal["candidates"][0], municipalities=["Seon"])], now=NOW + timedelta(minutes=1))
    P.import_snapshots(path, [amts_snapshot(count=0, stamp=NOW + timedelta(hours=1))], now=NOW + timedelta(hours=1))
    view = P.state_for("Seon", path)
    assert len(view.items) == 1 and view.items[0].absent
    assert sum(source["empty"] for source in view.sources) == 1


def test_concurrent_review_updates_preserve_both_operators_decisions(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    snapshot = amts_snapshot(count=2)
    path = seed(tmp_path, [snapshot])
    reviews = [review(candidate, municipalities=["Seon"]) for candidate in snapshot["candidates"]]
    original_replace, original_flock = P.os.replace, P.fcntl.flock
    replacing, release, second_locking = Event(), Event(), Event()
    calls = []

    def gated_replace(*args):
        calls.append(args)
        if len(calls) == 1:
            replacing.set()
            assert release.wait(5)
        return original_replace(*args)

    def observed_flock(*args):
        if replacing.is_set():
            second_locking.set()
        return original_flock(*args)

    with patch.object(P.os, "replace", side_effect=gated_replace), patch.object(P.fcntl, "flock", side_effect=observed_flock):
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(P.apply_reviews, path, [reviews[0]], now=NOW + timedelta(minutes=1))
            assert replacing.wait(5)
            second = executor.submit(P.apply_reviews, path, [reviews[1]], now=NOW + timedelta(minutes=1))
            assert second_locking.wait(5) and not second.done()
            release.set()
            first.result(timeout=5)
            second.result(timeout=5)
    assert len(P.state_for("Seon", path).items) == 2
    assert all(row["state"] == "included" for row in P.queue(path, all_items=True))


def legal(candidate, *, municipality="Seon", canton_wide=False, stage="auflage", **dates):
    scope = {"canton_wide": True} if canton_wide else {"municipality": municipality}
    proof = {"stage": stage, "source": candidate["url"], "verified_on": "2026-10-06",
             "method": "Exact source item and municipal section checked."}
    proof.update(dates or {"auflage_from": "2026-10-01", "auflage_to": "2026-10-10"})
    return {"source_id": candidate["source_id"], "fingerprint": P.fingerprint(candidate), **scope, "verified": proof}


def included(tmp_path, *, canton=False, municipalities=None, canton_wide=False):
    snapshot = canton_snapshot() if canton else amts_snapshot()
    path = seed(tmp_path, [snapshot])
    candidate = snapshot["candidates"][0]
    P.apply_reviews(path, [review(candidate, municipalities=municipalities or ["Seon"], canton_wide=canton_wide)], now=NOW + timedelta(minutes=1))
    return path, candidate


def test_optional_legal_proofs_are_explicit_and_municipality_specific(tmp_path):
    path, candidate = included(tmp_path, municipalities=["Seon", "Baden"])
    assert P.state_for("Seon", path).items[0].verified is None  # Existing schema v1 remains valid.
    P.apply_verifications(path, [legal(candidate)], now=NOW + timedelta(minutes=2))
    proof = P.state_for("Seon", path, today=NOW.date()).items[0].verified
    assert isinstance(proof, PL.Verified) and proof.stage == "auflage"
    assert P.state_for("Baden", path, today=NOW.date()).items[0].verified is None
    assert not P.state_for("Menziken", path).items
    assert P.state_for("Seon", path, today=date(2026, 10, 5)).items[0].verified is None
    assert PL.stage_badge(P.state_for("Seon", path, today=date(2026, 10, 11)).items[0], date(2026, 10, 11))[0] is None
    assert P.queue(path, all_items=True)[0]["reviewed_scope"]["municipalities"] == ["Baden", "Seon"]


def test_full_local_municipality_coverage_persists_reviews_and_bounded_sources(tmp_path, monkeypatch):
    path, candidate = included(tmp_path)
    P.apply_verifications(path, [legal(candidate)], now=NOW + timedelta(minutes=2))
    before = P.validate_store(P._read(path))
    item_key = P.identity(candidate["source_id"])
    names = ["Seon", *[name for name in A.AUTHORITIES if name != "Seon"]][:163]
    observed = NOW + timedelta(minutes=3)
    snapshots = [amts_snapshot(name=name, kind=kind, stamp=observed,
                              count=int(name == "Seon" and kind == "municipal"))
                 for name in names for kind in ("municipal", "canton-approvals")]
    snapshots.append(canton_snapshot(stamp=observed, count=0))
    P.import_snapshots(path, snapshots, now=observed)
    stored = P.validate_store(P._read(path))
    assert len(stored["selections"]) == 327
    assert stored["items"][item_key]["review"] == before["items"][item_key]["review"]
    assert stored["items"][item_key]["legal"] == before["items"][item_key]["legal"]
    for name in names:
        view = P.state_for(name, path, today=NOW.date())
        assert view.status == "ok" and view.municipality_queried
        assert sum(source["selection"].get("municipality") == name for source in view.sources) == 2
        assert not any(source["error"] for source in view.sources)
        if name == "Seon":
            assert len(view.items) == 1 and isinstance(view.items[0].verified, PL.Verified)
        else:
            assert not view.items and all(source["empty"] for source in view.sources)

    def archive(index):
        return C._snapshot(
            {"provider": "www.ag.ch", "search_url": C.AG_ORIGIN + C.CONSULTATION_PATHS["archive"], "endpoint": C.SEARCH_API},
            {"mode": "archive", "term": f"capacity-{index}", "cms_validity_as_of": C._now(observed), "max_pages": 5},
            C._now(observed), [], [{"page": 0, "count": 0, "url": C.SEARCH_API}], 0)

    P.import_snapshots(path, [archive(index) for index in range(73)], now=observed)
    assert len(P.validate_store(P._read(path))["selections"]) == 400
    last_good = path.read_bytes()
    extra = archive(73)
    assert P.validate_snapshot(extra)["term"] == "capacity-73"
    with pytest.raises(C.CollectionError, match="oversized preview store"):
        P.import_snapshots(path, [extra], now=observed)
    assert path.read_bytes() == last_good

    # A bounded scheduled subset must also operate on an already-wide store.
    import planning_refresh as R
    from test_planning_collect import ConnectionFactory, FakeResponse, zero_amtsblatt

    wide = P.validate_store(P._read(path))
    refreshed = {P.selection_key({"provider": "amtsblatt.ag.ch", "kind": kind, "municipality": "Seon"})
                 for kind in ("municipal", "canton-approvals")}
    refreshed.add(P.selection_key({"provider": "www.ag.ch", "mode": "current", "term": ""}))
    empty_canton = json.loads((FIXTURES / "consultations-current-page0.json").read_text())
    empty_canton["dynamiccontent"] = []
    empty_canton["control"].update(numberOfElements=0, totalPages=0, totalElements=0, firstPage=True, lastPage=True)
    bodies = [(FIXTURES / f"amtsblatt-seon-page{page}.html").read_bytes() for page in (1, 2)]
    bodies += [zero_amtsblatt(), (FIXTURES / "consultations-current-widget.html").read_bytes(), json.dumps(empty_canton).encode()]
    connections = ConnectionFactory([FakeResponse(body=body) for body in bodies])
    transport = C.Transport(connection_factory=connections)
    monkeypatch.setattr(C, "Transport", lambda **kwargs: transport)
    env = {P.ENV: str(path), R.PREFIX + "ENABLED": "1", R.PREFIX + "MUNICIPALITIES": "Seon",
           "SCOPE_PLANNING_REUSE_AUTHORIZED": "1", "SCOPE_PLANNING_REUSE_REFERENCE": "Offline fixture test only"}
    ticks = iter(observed + timedelta(minutes=1, seconds=index) for index in range(20))
    with patch("socket.socket.connect", side_effect=AssertionError("No real network in fixture test")):
        assert R.run_once(environ=env, clock=lambda: next(ticks)) == {
            "status": "success", "sources_succeeded": 3, "sources_failed": 0}
    after = P.validate_store(P._read(path))
    assert len(connections.instances) == 5 and not connections.responses
    assert len(after["selections"]) == 400
    assert all(source == after["selections"][key] for key, source in wide["selections"].items() if key not in refreshed)
    assert after["items"][item_key]["review"] == wide["items"][item_key]["review"]
    assert after["items"][item_key]["legal"] == wide["items"][item_key]["legal"]
    assert all(P.review_state(item) == "pending" and not item.get("legal")
               for key, item in after["items"].items() if key != item_key)


@pytest.mark.parametrize("own_query", [False, True])
def test_foreign_queries_do_not_expand_scoped_sources_or_pending_queue(tmp_path, own_query):
    foreign = amts_snapshot(kind="canton-approvals", count=2)
    path = seed(tmp_path, [foreign, canton_snapshot()])
    candidate = foreign["candidates"][0]
    P.apply_reviews(path, [review(candidate, municipalities=["Baden", "Menziken"])], now=NOW + timedelta(minutes=1))
    P.apply_verifications(path, [legal(candidate, municipality="Baden")], now=NOW + timedelta(minutes=2))
    if own_query:
        P.import_snapshots(path, [amts_snapshot(name="Baden", stamp=NOW + timedelta(minutes=3))],
                           now=NOW + timedelta(minutes=3))
    before = path.read_bytes()
    view = P.state_for("Baden", path, today=NOW.date())
    assert view.status == "ok" and view.municipality_queried is own_query
    assert {source["selection"].get("municipality") for source in view.sources} == ({"Baden", None} if own_query else {None})
    assert view.pending == 1 and view.changed == 0  # Only the shared canton candidate.
    assert len(view.items) == 1 and isinstance(view.items[0].verified, PL.Verified)
    other = P.state_for("Menziken", path, today=NOW.date())
    assert not other.municipality_queried and other.pending == 1
    assert len(other.items) == 1 and other.items[0].verified is None
    assert P.state_for("Seon", path, today=NOW.date()).pending == 2
    assert path.read_bytes() == before


def test_foreign_scoped_item_keeps_proof_and_actual_absence_without_foreign_status(tmp_path):
    foreign = amts_snapshot(kind="canton-approvals")
    path = seed(tmp_path, [foreign])
    candidate = foreign["candidates"][0]
    P.apply_reviews(path, [review(candidate, municipalities=["Baden"])], now=NOW + timedelta(minutes=1))
    P.apply_verifications(path, [legal(candidate, municipality="Baden")], now=NOW + timedelta(minutes=2))
    foreign_selection = P.validate_snapshot(foreign)
    own_selection = {"provider": "amtsblatt.ag.ch", "kind": "municipal", "municipality": "Baden"}
    P._transaction(path, lambda store: P._merge(store, [], [(foreign_selection, "Source timed out"),
                                                           (own_selection, "Source timed out")], C._now(NOW + timedelta(minutes=3))))
    view = P.state_for("Baden", path, today=NOW.date())
    assert not view.municipality_queried and view.pending == 0
    assert len(view.sources) == 1 and view.sources[0]["selection"] == own_selection
    assert view.sources[0]["error"] and view.sources[0]["last_success_at"] is None
    assert len(view.items) == 1 and not view.items[0].absent and view.items[0].verified is not None
    P.import_snapshots(path, [amts_snapshot(kind="canton-approvals", count=0, stamp=NOW + timedelta(minutes=4))],
                       now=NOW + timedelta(minutes=4))
    absent = P.state_for("Baden", path, today=NOW.date())
    assert absent.items[0].absent and absent.items[0].verified is not None
    assert not absent.municipality_queried and len(absent.sources) == 1
    assert not P.state_for("Menziken", path, today=NOW.date()).items


def test_changed_scoped_include_remains_visible_as_warning_without_own_query(tmp_path):
    foreign = amts_snapshot(kind="canton-approvals", count=2)
    path = seed(tmp_path, [foreign, canton_snapshot()])
    candidate = foreign["candidates"][0]
    P.apply_reviews(path, [review(candidate, municipalities=["Baden"])], now=NOW + timedelta(minutes=1))
    P.apply_verifications(path, [legal(candidate, municipality="Baden")], now=NOW + timedelta(minutes=2))
    changed = amts_snapshot(kind="canton-approvals", count=2, stamp=NOW + timedelta(hours=1))
    changed["candidates"][0]["title"] += "; Korrektur"
    P.import_snapshots(path, [changed], now=NOW + timedelta(hours=1))
    view = P.state_for("Baden", path, today=NOW.date())
    assert not view.municipality_queried and not view.items
    assert view.changed == 1 and view.pending == 1
    assert len(view.sources) == 1 and view.sources[0]["selection"]["provider"] == "www.ag.ch"
    reverted = amts_snapshot(kind="canton-approvals", count=2, stamp=NOW + timedelta(hours=2))
    P.import_snapshots(path, [reverted], now=NOW + timedelta(hours=2))
    assert P.state_for("Baden", path, today=NOW.date()).changed == 1
    assert not P.state_for("Baden", path, today=NOW.date()).items


@pytest.mark.parametrize("change", [
    {"municipality": "Unknown"}, {"municipality": "Baden"}, {"canton_wide": True},
    {"fingerprint": "a" * 64}, {"verified": {"stage": "mitwirkung"}},
])
def test_legal_scope_and_stale_batches_are_atomic(tmp_path, change):
    path, candidate = included(tmp_path)
    before = path.read_bytes()
    with pytest.raises(C.CollectionError):
        P.apply_verifications(path, [legal(candidate) | change], now=NOW + timedelta(minutes=2))
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"source": "https://evil.invalid/legal.pdf"},
    {"source": "https://amtsblatt.ag.ch/ekab/00.999.999/pdf/"},
    {"verified_on": "2026-10-05"}, {"verified_on": "2026-10-07"},
    {"verified_on": "2026-10-06T08:00:00Z"}, {"method": ""},
    {"auflage_to": "2026-09-01"}, {"auflage_from": None},
    {"effective_on": "invalid"}, {"unrelated_pdf": "https://example.com/proof"},
    {"stage": "in_kraft", "effective_on": "2026-10-07"},
    {"stage": "genehmigt", "approved_on": "2026-10-07"},
    {"stage": "genehmigt", "approved_on": "2026-10-02", "appeal_until": "2026-10-01"},
])
def test_legal_proof_dates_fields_and_same_source_evidence(tmp_path, change):
    path, candidate = included(tmp_path)
    batch = legal(candidate)
    batch["verified"].update(change)
    before = path.read_bytes()
    with pytest.raises(C.CollectionError):
        P.apply_verifications(path, [batch], now=NOW + timedelta(minutes=2))
    assert path.read_bytes() == before


@pytest.mark.parametrize("action", ["exclude", "hold"])
def test_legal_proofs_require_current_relevance_and_never_resurrect_after_reinclusion(tmp_path, action):
    path, candidate = included(tmp_path)
    P.apply_verifications(path, [legal(candidate)], now=NOW + timedelta(minutes=2))
    P.apply_reviews(path, [review(candidate, action=action, stamp=NOW + timedelta(minutes=3))], now=NOW + timedelta(minutes=3))
    with pytest.raises(C.CollectionError, match="included territorial"):
        P.apply_verifications(path, [legal(candidate)], now=NOW + timedelta(minutes=4))
    P.apply_reviews(path, [review(candidate, municipalities=["Seon"], stamp=NOW + timedelta(minutes=5))], now=NOW + timedelta(minutes=5))
    assert P.state_for("Seon", path).items[0].verified is None
    # A separate explicit verification, after the invalidation, may restore proof.
    P.apply_verifications(path, [legal(candidate)], now=NOW + timedelta(minutes=6))
    assert P.state_for("Seon", path).items[0].verified is not None


def test_scoped_removal_invalidates_only_removed_proof_and_expired_proof_stays_expired(tmp_path):
    path, candidate = included(tmp_path, municipalities=["Seon", "Baden"])
    P.apply_verifications(path, [legal(candidate), legal(candidate, municipality="Baden")], now=NOW + timedelta(minutes=2))
    P.apply_reviews(path, [review(candidate, municipalities=["Seon"], stamp=NOW + timedelta(minutes=3))], now=NOW + timedelta(minutes=3))
    P.apply_reviews(path, [review(candidate, municipalities=["Seon", "Baden"], stamp=NOW + timedelta(days=10))], now=NOW + timedelta(days=10))
    assert P.state_for("Baden", path).items[0].verified is None
    seon = P.state_for("Seon", path, today=date(2026, 10, 16)).items[0]
    assert seon.verified is not None and PL.stage_badge(seon, date(2026, 10, 16))[0] is None


def test_metadata_change_then_hash_reversion_requires_fresh_legal_verification(tmp_path):
    path, candidate = included(tmp_path)
    P.apply_verifications(path, [legal(candidate)], now=NOW + timedelta(minutes=2))
    corrected = amts_snapshot(stamp=NOW + timedelta(hours=1))
    corrected["candidates"][0]["title"] += " Korrektur"
    P.import_snapshots(path, [corrected], now=NOW + timedelta(hours=1))
    reverted = amts_snapshot(stamp=NOW + timedelta(hours=2))
    P.import_snapshots(path, [reverted], now=NOW + timedelta(hours=2))
    P.apply_reviews(path, [review(candidate, municipalities=["Seon"], stamp=NOW + timedelta(hours=2, minutes=1))], now=NOW + timedelta(hours=2, minutes=1))
    assert P.state_for("Seon", path).items[0].verified is None
    P.apply_verifications(path, [legal(candidate)], now=NOW + timedelta(hours=2, minutes=2))
    assert P.state_for("Seon", path).items[0].verified is not None


def test_late_metadata_and_scope_updates_invalidate_at_processing_time(tmp_path):
    earlier = NOW - timedelta(days=5)
    snapshot = amts_snapshot(stamp=earlier)
    path = seed(tmp_path, [snapshot])
    candidate = snapshot["candidates"][0]
    P.apply_reviews(path, [review(candidate, municipalities=["Seon"], stamp=earlier)], now=NOW)
    P.apply_verifications(path, [legal(candidate)], now=NOW)
    # Source observed Oct2, imported Oct7 after the Oct6 legal verification.
    corrected = amts_snapshot(stamp=earlier + timedelta(days=1))
    corrected["candidates"][0]["title"] += " Berichtigung"
    updated = P.import_snapshots(path, [corrected], now=NOW + timedelta(days=1))
    item = next(iter(updated["items"].values()))
    assert item["legal"]["Seon"]["invalidated_at"] == C._now(NOW + timedelta(days=1))
    assert not P.state_for("Seon", path).items
    # Also accept a late exclusion decision without backdating proof invalidation.
    second_path = tmp_path / "late-scope.json"
    P.import_snapshots(second_path, [snapshot], now=NOW)
    P.apply_reviews(second_path, [review(candidate, municipalities=["Seon"], stamp=earlier)], now=NOW)
    P.apply_verifications(second_path, [legal(candidate)], now=NOW)
    store = P.apply_reviews(second_path, [review(candidate, action="exclude", stamp=earlier + timedelta(days=1))], now=NOW + timedelta(days=1))
    assert next(iter(store["items"].values()))["legal"]["Seon"]["invalidated_at"] == C._now(NOW + timedelta(days=1))


def test_cantonwide_legal_proof_requires_cantonwide_relevance_and_duplicates_are_refused(tmp_path):
    snapshot = canton_snapshot()
    path = seed(tmp_path, [snapshot])
    candidate = snapshot["candidates"][0]
    P.apply_reviews(path, [review(candidate, canton_wide=True)], now=NOW + timedelta(minutes=1))
    proof = legal(candidate, canton_wide=True)
    with pytest.raises(C.CollectionError, match="Repeated"):
        P.apply_verifications(path, [proof, proof], now=NOW + timedelta(minutes=2))
    with pytest.raises(C.CollectionError):
        P.apply_verifications(path, [proof | {"verified": proof["verified"] | {"source": candidate["url"].replace(candidate["source_id"]["value"], "00000000-0000-0000-0000-000000000000_de")}}], now=NOW + timedelta(minutes=2))
    P.apply_verifications(path, [proof], now=NOW + timedelta(minutes=2))
    assert P.state_for("Seon", path).items[0].verified is not None
    assert P.state_for("Baden", path).items[0].verified is not None
    P.apply_reviews(path, [review(candidate, municipalities=["Seon"], stamp=NOW + timedelta(minutes=3))], now=NOW + timedelta(minutes=3))
    assert P.state_for("Seon", path).items[0].verified is None


def test_legal_verification_cli_uses_same_item_pdf_and_swiss_day(tmp_path, monkeypatch, capsys):
    path, candidate = included(tmp_path)
    monkeypatch.setattr(P, "_time", lambda now=None: C._now(NOW + timedelta(minutes=2)))
    batch = legal(candidate)
    batch["verified"]["source"] = candidate["url"].replace("/publikation/", "/pdf/")
    source = tmp_path / "legal.json"
    source.write_text(json.dumps([batch]))
    assert P.main(["verify", str(path), str(source)]) == 0
    assert "candidates" in capsys.readouterr().out
    assert P.state_for("Seon", path).items[0].verified.source.endswith("/pdf/")
    assert P.swiss_today(datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)) == date(2026, 7, 2)
