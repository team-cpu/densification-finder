"""Local metadata preview with relevance/scope reviews, separate from legal stores.

Render-time reads are local only. Explicit import/sync/review commands own writes.
"""
import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import amtsblatt as A
import planning_collect as C
import planning as PL

ENV = "SCOPE_PLANNING_PREVIEW"
SCHEMA = "scope.planning-preview"
VERSION = 1
MAX_STORE_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 10000
# Two searches per known municipality plus shared canton source selections.
MAX_SELECTIONS = 400
STALE_DAYS = 7
NS_AMTSBLATT = "amtsblatt.ag.ch:pub_nr"
NS_CANTON = "www.ag.ch:dynamiccontent"


def _require(condition, message):
    if not condition:
        raise C.CollectionError(message)


def _keys(value, required, optional=()):
    _require(isinstance(value, dict) and required <= value.keys()
             and value.keys() <= required | set(optional), "Missing or unexpected metadata fields")


def _stamp(value):
    return datetime.fromisoformat(C._timestamp(value, "preview").replace("Z", "+00:00")).astimezone(timezone.utc)


def _time(now=None):
    return C._now(now)


def swiss_today(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/Zurich")).date()


def _municipality(value):
    _require(isinstance(value, str) and A.authorities_for(value), "Unknown municipality")
    return A.canonical_name(value)


def identity(source_id):
    _keys(source_id, {"namespace", "value"})
    namespace, value = source_id["namespace"], source_id["value"]
    _require(isinstance(value, str) and ((namespace == NS_AMTSBLATT and C._PUB_NR.fullmatch(value))
             or (namespace == NS_CANTON and C._UUID_DE.fullmatch(value))), "Invalid provider ID namespace/value")
    return namespace + ":" + value


def validate_candidate(candidate, mode=None):
    _require(isinstance(candidate, dict), "Candidate must be an object")
    key = identity(candidate.get("source_id"))
    namespace = candidate["source_id"]["namespace"]
    required = {"source_id", "title", "url", "authority"}
    required |= {"published_on", "rubric"} if namespace == NS_AMTSBLATT else {
        "publication_date", "cms_valid_from", "cms_valid_until"}
    _keys(candidate, required, {"publication_version_label"} if namespace == NS_AMTSBLATT else ())
    if "publication_version_label" in candidate:
        _require(candidate["publication_version_label"] in ("Korrektur", "ursprüngliche Version"), "Unknown publication version label")
    for name in ("title", "authority"):
        C._text(candidate[name], name)
    url, value = candidate["url"], candidate["source_id"]["value"]
    if namespace == NS_AMTSBLATT:
        C._text(candidate["rubric"], "rubric")
        parts = C._url(url, "https://amtsblatt.ag.ch", {f"/ekab/{value}/publikation/"})
        _require(not parts.query, "Publication URL does not match its ID")
        day = candidate["published_on"]
        _require(isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day), "Malformed publication date")
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise C.CollectionError("Malformed publication date") from exc
    else:
        paths = {C.CONSULTATION_PATHS[mode]} if mode else set(C.CONSULTATION_PATHS.values())
        parts = C._url(url, C.AG_ORIGIN, paths)
        _require(parse_qsl(parts.query, keep_blank_values=True) == [("dc", value)], "Consultation URL does not match its ID")
        C._timestamp(candidate["publication_date"], "publication_date")
        C._timestamp(candidate["cms_valid_from"], "cms_valid_from")
        C._timestamp(candidate["cms_valid_until"], "cms_valid_until", nullable=True)
    return key


def source_href(candidate):
    try:
        validate_candidate(candidate)
        return candidate["url"]
    except (C.CollectionError, ValueError, TypeError):
        return ""


def fingerprint(candidate):
    validate_candidate(candidate)
    encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def selection_key(selection):
    return json.dumps(selection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _selection(selection):
    _require(isinstance(selection, dict), "Missing source selection")
    if selection.get("provider") == "amtsblatt.ag.ch":
        _keys(selection, {"provider", "kind", "municipality"})
        _require(selection["kind"] in ("municipal", "canton-approvals"), "Unknown Amtsblatt selection kind")
        _require(_municipality(selection["municipality"]) == selection["municipality"], "Noncanonical municipality selection")
    elif selection.get("provider") == "www.ag.ch":
        _keys(selection, {"provider", "mode", "term"})
        _require(selection["mode"] in C.CONSULTATION_PATHS and isinstance(selection["term"], str)
                 and len(selection["term"]) <= 200 and not re.search(r"[\x00-\x1f\x7f]", selection["term"]), "Invalid consultation selection")
    else:
        raise C.CollectionError("Unknown source provider")
    return selection_key(selection)


def validate_snapshot(snapshot):
    _keys(snapshot, {"schema", "schema_version", "source", "parameters", "fetched_at",
                     "review_required", "review_note", "traversal", "candidates"})
    _require(snapshot["schema"] == C.SCHEMA and type(snapshot["schema_version"]) is int
             and snapshot["schema_version"] == C.VERSION, "Not a planning-candidates staging snapshot")
    _stamp(snapshot["fetched_at"])
    review = snapshot["review_required"]
    _require(isinstance(review, list) and len(review) == 5 and all(isinstance(value, str) for value in review) and set(review) == {
        "historical_coverage", "legal_coverage", "planning_relevance", "territorial_scope", "legal_stage"}, "Missing snapshot review boundary")
    C._text(snapshot["review_note"], "review note")
    source, parameters = snapshot["source"], snapshot["parameters"]
    _require(isinstance(source, dict) and isinstance(parameters, dict), "Missing source/query metadata")
    if source.get("provider") == "amtsblatt.ag.ch":
        _keys(source, {"provider", "search_url"})
        _keys(parameters, {"kind", "municipality_query_hint", "max_pages"})
        name = _municipality(parameters["municipality_query_hint"])
        _require(name == parameters["municipality_query_hint"], "Noncanonical municipality query hint")
        selection = {"provider": source["provider"], "kind": parameters["kind"], "municipality": name}
        _selection(selection)
        url = (A.municipal_planning_link if selection["kind"] == "municipal" else A.canton_approvals_link)(name)
        filters = C._amtsblatt_selection(url)[0]
        C._amtsblatt_validator(filters, 1)(source["search_url"])
        start, namespace, mode = 1, NS_AMTSBLATT, None
    else:
        _keys(source, {"provider", "search_url", "endpoint"})
        _keys(parameters, {"mode", "term", "cms_validity_as_of", "max_pages"})
        selection = {"provider": source["provider"], "mode": parameters["mode"], "term": parameters["term"]}
        _selection(selection)
        mode = selection["mode"]
        _require(source["search_url"] == C.AG_ORIGIN + C.CONSULTATION_PATHS[mode]
                 and source["endpoint"] == C.SEARCH_API and parameters["cms_validity_as_of"] == snapshot["fetched_at"],
                 "Consultation query/source metadata mismatch")
        start, namespace = 0, NS_CANTON
    C._pages_limit(parameters["max_pages"])
    traversal = snapshot["traversal"]
    _keys(traversal, {"complete_search_snapshot", "total_candidates", "pages_fetched", "pages"})
    candidates, total, pages = snapshot["candidates"], traversal["total_candidates"], traversal["pages"]
    _require(traversal["complete_search_snapshot"] is True and type(total) is int and total >= 0
             and isinstance(candidates, list) and len(candidates) == total, "Partial snapshot or candidate count mismatch")
    count = max(1, math.ceil(total / 10))
    _require(type(traversal["pages_fetched"]) is int and traversal["pages_fetched"] == count
             and isinstance(pages, list) and len(pages) == count and count <= parameters["max_pages"], "Snapshot page count/cap mismatch")
    for index, page in enumerate(pages):
        _keys(page, {"page", "count", "url"})
        _require(type(page["page"]) is int and page["page"] == index + start
                 and type(page["count"]) is int and page["count"] == min(10, max(0, total - index * 10)), "Snapshot traversal metadata mismatch")
        if namespace == NS_AMTSBLATT:
            C._amtsblatt_validator(filters, index + 1)(page["url"])
            if index == 0:
                _require(page["url"] == source["search_url"], "Snapshot initial search URL mismatch")
        else:
            _require(page["url"] == C.SEARCH_API, "Snapshot consultation endpoint mismatch")
    ids = set()
    for candidate in candidates:
        key = validate_candidate(candidate, mode)
        _require(candidate["source_id"]["namespace"] == namespace and key not in ids, "Mismatched namespace or duplicate candidate ID")
        ids.add(key)
    return selection


def _evidence(url, candidate):
    # Scope decisions must cite this source item, not an unrelated publication.
    value = candidate["source_id"]["value"]
    if candidate["source_id"]["namespace"] == NS_AMTSBLATT:
        parts = C._url(url, "https://amtsblatt.ag.ch", {f"/ekab/{value}/publikation/", f"/ekab/{value}/pdf/"})
        _require(not parts.query, "Review evidence publication ID mismatch")
    else:
        parts = C._url(url, C.AG_ORIGIN, set(C.CONSULTATION_PATHS.values()))
        _require(parse_qsl(parts.query, keep_blank_values=True) == [("dc", value)], "Review evidence consultation ID mismatch")


def _review(review, candidate):
    _keys(review, {"fingerprint", "action", "evidence_url", "rationale", "reviewed_at"}, {"municipalities", "canton_wide"})
    _require(isinstance(review["fingerprint"], str) and re.fullmatch(r"[0-9a-f]{64}", review["fingerprint"]), "Malformed review fingerprint")
    _require(review["action"] in ("include", "exclude", "hold"), "Unknown review action")
    _stamp(review["reviewed_at"])
    rationale = C._text(review["rationale"], "review rationale")
    _require(len(rationale) <= 500, "Review rationale must be short (at most 500 characters)")
    _evidence(review["evidence_url"], candidate)
    names, wide = review.get("municipalities", []), review.get("canton_wide", False)
    _require(isinstance(names, list) and type(wide) is bool, "Review scope must use municipalities or a boolean canton_wide")
    canonical = [_municipality(name) for name in names]
    _require(len(set(canonical)) == len(canonical), "Duplicate review municipality")
    if review["action"] == "include":
        _require(bool(canonical) != wide, "Include requires exact municipalities OR explicit canton-wide scope")
    else:
        _require(not canonical and not wide, "Hold/exclude decisions must not assert territory")
    return {**review, "municipalities": sorted(canonical), "canton_wide": wide, "rationale": rationale}


def _proof(raw, candidate):
    """Use the production verifier, with a stricter same-source-item boundary."""
    _keys(raw, {"stage", "source", "verified_on", "method"}, PL._STAGE_DATES)
    _require(raw["stage"] in PL.STAGES, "Unsupported verified legal stage")
    method = C._text(raw["method"], "verification method")
    _require(len(method) <= 500, "Verification method must be short")
    _evidence(raw["source"], candidate)
    for name in ("verified_on", *PL._STAGE_DATES):
        if name in raw:
            _require(isinstance(raw[name], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw[name]), "Malformed legal verification date")
    verified, error = PL._verified(raw)
    _require(verified is not None and not error, error or "Invalid legal verification")
    normalized = {"stage": verified.stage, "source": verified.source,
                  "verified_on": verified.verified_on.isoformat(), "method": method}
    normalized.update({name: getattr(verified, name).isoformat() for name in PL._STAGE_DATES if getattr(verified, name) is not None})
    return normalized, verified


def _legal_scope(item, scope):
    if review_state(item) != "included":
        return False
    review = item["review"]
    return review["canton_wide"] if scope == "*" else _scope(review, scope)


def _invalidate_legal(item, stamp, *, removed_only=False):
    for scope, proof in item.get("legal", {}).items():
        if not removed_only or not _legal_scope(item, scope):
            proof["invalidated_at"] = stamp


def _legal_for(item, municipality, today):
    for scope in (municipality, "*"):
        entry = item.get("legal", {}).get(scope)
        if entry and "invalidated_at" not in entry and entry["fingerprint"] == item["fingerprint"] and _legal_scope(item, scope):
            verified = _proof(entry["verified"], item["candidate"])[1]
            if verified.verified_on <= today:
                return verified
    return None


def _empty():
    return {"schema": SCHEMA, "schema_version": VERSION, "updated_at": None, "items": {}, "selections": {}}


def validate_store(store):
    _keys(store, {"schema", "schema_version", "updated_at", "items", "selections"})
    _require(store["schema"] == SCHEMA and type(store["schema_version"]) is int
             and store["schema_version"] == VERSION, "Refusing unrelated file or production store")
    items, selections = store["items"], store["selections"]
    _require(isinstance(items, dict) and len(items) <= MAX_ITEMS and isinstance(selections, dict)
             and len(selections) <= MAX_SELECTIONS, "Invalid or oversized preview store")
    updated = _stamp(store["updated_at"]) if store["updated_at"] is not None else None
    _require(updated is not None or not (items or selections), "Preview data has no update timestamp")
    for key, source in selections.items():
        _keys(source, {"selection", "last_attempt_at", "last_success_at", "error", "members"})
        _require(key == _selection(source["selection"]), "Source selection key mismatch")
        attempt = _stamp(source["last_attempt_at"])
        success = _stamp(source["last_success_at"]) if source["last_success_at"] is not None else None
        _require((success is None or success <= attempt) and attempt <= updated, "Invalid source attempt/data date")
        _require(isinstance(source["error"], str) and len(source["error"]) <= 240, "Invalid source error metadata")
        members = source["members"]
        _require(isinstance(members, list) and all(isinstance(k, str) for k in members)
                 and len(members) == len(set(members)) and all(k in items for k in members), "Source membership is invalid")
        _require(success is not None or (not members and bool(source["error"])), "Missing source success/error state")
    for key, item in items.items():
        _keys(item, {"candidate", "fingerprint", "metadata_at", "last_seen_at", "seen_in", "audit"}, {"review", "review_invalidated_at", "legal"})
        _require(validate_candidate(item["candidate"]) == key and fingerprint(item["candidate"]) == item["fingerprint"], "Item identity/fingerprint mismatch")
        _require(_stamp(item["metadata_at"]) <= _stamp(item["last_seen_at"]) <= updated, "Invalid item metadata dates")
        seen = item["seen_in"]
        _require(isinstance(seen, list) and seen and all(isinstance(k, str) and k in selections for k in seen)
                 and len(seen) == len(set(seen)), "Invalid item source history")
        expected_provider = "amtsblatt.ag.ch" if item["candidate"]["source_id"]["namespace"] == NS_AMTSBLATT else "www.ag.ch"
        _require(all(selections[source]["selection"]["provider"] == expected_provider for source in seen), "Item/source provider mismatch")
        _require(isinstance(item["audit"], list) and len(item["audit"]) <= 5, "Invalid item audit history")
        for event in item["audit"]:
            _keys(event, {"fingerprint", "changed_at", "review_action"})
            _require(isinstance(event["fingerprint"], str) and re.fullmatch(r"[0-9a-f]{64}", event["fingerprint"])
                     and event["review_action"] in (None, "include", "exclude", "hold"), "Invalid audit event")
            _stamp(event["changed_at"])
        if "review_invalidated_at" in item:
            _require("review" in item and _stamp(item["review_invalidated_at"]) <= updated, "Invalid review invalidation metadata")
        if "review" in item:
            normalized = _review(item["review"], item["candidate"])
            _require(normalized == item["review"] and _stamp(normalized["reviewed_at"]) <= updated, "Noncanonical or future stored review")
        legal = item.get("legal", {})
        _require(isinstance(legal, dict) and len(legal) <= 201, "Invalid legal review collection")
        for scope, entry in legal.items():
            _require(scope == "*" or _municipality(scope) == scope, "Noncanonical legal review municipality")
            _keys(entry, {"fingerprint", "verified"}, {"invalidated_at"})
            _require(isinstance(entry["fingerprint"], str) and re.fullmatch(r"[0-9a-f]{64}", entry["fingerprint"]), "Invalid legal fingerprint")
            normalized, verified = _proof(entry["verified"], item["candidate"])
            _require(normalized == entry["verified"] and verified.verified_on <= swiss_today(updated), "Noncanonical or future legal review")
            if "invalidated_at" in entry:
                _require(verified.verified_on <= swiss_today(_stamp(entry["invalidated_at"]))
                         and _stamp(entry["invalidated_at"]) <= updated, "Invalid legal invalidation date")
            else:
                _require(entry["fingerprint"] == item["fingerprint"] and _legal_scope(item, scope)
                         and verified.verified_on >= swiss_today(_stamp(item["metadata_at"])), "Legal proof is stale or outside reviewed territory")
    for key, source in selections.items():
        _require(all(key in items[member]["seen_in"] for member in source["members"]), "Inconsistent source/item membership")
    return store


def _read(path, *, missing=False):
    path = Path(path).absolute()
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return _empty()
        raise C.CollectionError("Preview file does not exist")
    _require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_STORE_BYTES, "Preview must be a bounded regular file; symlinks are refused")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        _require(os.fstat(handle.fileno()).st_ino == info.st_ino, "Preview file changed during inspection")
        raw = handle.read(MAX_STORE_BYTES + 1)
    _require(len(raw) <= MAX_STORE_BYTES, "Preview file exceeds size limit")
    try:
        return C._json(raw, "local preview file")
    except RecursionError as exc:
        raise C.CollectionError("Preview JSON exceeds nesting limit") from exc


def _transaction(path, mutate):
    path = Path(path).absolute()
    validate_store(_read(path, missing=True))  # Refuse unrelated/symlink files before lock creation.
    lock = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    temporary = None
    try:
        _require(stat.S_ISREG(os.fstat(lock).st_mode), "Preview lock must be a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = validate_store(_read(path, missing=True))
        result = mutate(copy.deepcopy(current))
        validate_store(result)
        encoded = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        _require(len(encoded) <= MAX_STORE_BYTES, "Preview store exceeds size limit")
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck non-cooperating updates, including replacement by a symlink.
        _require(validate_store(_read(path, missing=True)) == current, "Preview changed during update")
        os.replace(temporary, path)
        temporary = None
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
        return result
    finally:
        if temporary is not None:
            os.unlink(temporary)
        os.close(lock)


def _merge(store, snapshots, attempts, operation_at):
    versions, source_versions = {}, {}
    for snapshot in snapshots:
        selection = validate_snapshot(snapshot)
        key, stamp = selection_key(selection), snapshot["fetched_at"]
        _require(_stamp(stamp) <= _stamp(operation_at), "Snapshot is dated after this update")
        content = [(identity(c["source_id"]), fingerprint(c)) for c in snapshot["candidates"]]
        previous = source_versions.get(key)
        _require(previous is None or previous == (stamp, sorted(content)), "Conflicting snapshots for the same selection in one batch")
        source_versions[key] = (stamp, sorted(content))
        for candidate in snapshot["candidates"]:
            item_key, fp = identity(candidate["source_id"]), fingerprint(candidate)
            _require(item_key not in versions or versions[item_key] == fp, "Conflicting candidate versions in one import batch")
            versions[item_key] = fp
    for snapshot in snapshots:
        selection = validate_snapshot(snapshot)
        key, stamp = selection_key(selection), snapshot["fetched_at"]
        source = store["selections"].get(key)
        if source:
            _require(_stamp(operation_at) >= _stamp(source["last_attempt_at"]), "Out-of-order source attempt")
            if source["last_success_at"]:
                _require(_stamp(stamp) >= _stamp(source["last_success_at"]), "Out-of-order source snapshot")
                if _stamp(stamp) == _stamp(source["last_success_at"]):
                    _require(sorted(identity(c["source_id"]) for c in snapshot["candidates"]) == sorted(source["members"]), "Conflicting source membership at the same timestamp")
        members = []
        for candidate in snapshot["candidates"]:
            item_key, fp = identity(candidate["source_id"]), fingerprint(candidate)
            item = store["items"].get(item_key)
            if item:
                if fp != item["fingerprint"]:
                    _require(_stamp(stamp) > _stamp(item["last_seen_at"]), "Out-of-order or conflicting item metadata")
                    item["audit"] = (item["audit"] + [{"fingerprint": item["fingerprint"], "changed_at": stamp,
                                                       "review_action": item.get("review", {}).get("action")}])[-5:]
                    item.update(candidate=copy.deepcopy(candidate), fingerprint=fp, metadata_at=stamp)
                    if "review" in item:
                        item["review_invalidated_at"] = stamp
                    _invalidate_legal(item, operation_at)
                item["last_seen_at"] = max(item["last_seen_at"], stamp, key=_stamp)
                item["seen_in"] = sorted(set(item["seen_in"]) | {key})
            else:
                item = {"candidate": copy.deepcopy(candidate), "fingerprint": fp, "metadata_at": stamp,
                        "last_seen_at": stamp, "seen_in": [key], "audit": []}
                store["items"][item_key] = item
            members.append(item_key)
        store["selections"][key] = {"selection": selection, "last_attempt_at": operation_at,
                                     "last_success_at": stamp, "error": "", "members": sorted(members)}
    for selection, error in attempts:
        key = _selection(selection)
        source = store["selections"].get(key)
        _require(source is None or _stamp(operation_at) >= _stamp(source["last_attempt_at"]), "Out-of-order source attempt")
        if source is None:
            source = {"selection": selection, "last_success_at": None, "members": []}
            store["selections"][key] = source
        source.update(last_attempt_at=operation_at, error=error)
    store["updated_at"] = operation_at
    return store


def import_snapshots(path, snapshots, *, now=None):
    _require(isinstance(snapshots, list) and snapshots, "At least one snapshot is required")
    operation_at = _time(now)
    return _transaction(path, lambda store: _merge(store, snapshots, [], operation_at))


def sync(path, municipality, *, max_pages=5, timeout=C.DEFAULT_TIMEOUT, collectors=None, now=None):
    municipality = _municipality(municipality)
    validate_store(_read(path, missing=True))
    C._pages_limit(max_pages)
    now = now or datetime.now(timezone.utc)
    operation_at = _time(now)
    transport = C.Transport(timeout=timeout) if collectors is None else None
    municipal, canton = collectors or (C.collect_amtsblatt, C.collect_consultations)
    selections = [{"provider": "amtsblatt.ag.ch", "kind": kind, "municipality": municipality}
                  for kind in ("municipal", "canton-approvals")] + [{"provider": "www.ag.ch", "mode": "current", "term": ""}]
    snapshots, errors = [], []
    for selection in selections:
        try:
            if selection["provider"] == "amtsblatt.ag.ch":
                snapshot = municipal(municipality, selection["kind"], max_pages=max_pages, transport=transport, now=now)
            else:
                snapshot = canton(max_pages=max_pages, transport=transport, now=now)
            _require(validate_snapshot(snapshot) == selection, "Collected snapshot does not match requested selection")
            snapshots.append(snapshot)
        except Exception as exc:
            errors.append((selection, (str(exc) if isinstance(exc, C.CollectionError) else f"Source failed ({type(exc).__name__})")[:240]))
    store = _transaction(path, lambda current: _merge(current, snapshots, errors, operation_at))
    return store, errors


def apply_reviews(path, reviews, *, now=None):
    _require(isinstance(reviews, list) and reviews, "Review batch must be a nonempty list")
    operation_at = _time(now)

    def update(store):
        seen = set()
        for raw in reviews:
            _keys(raw, {"source_id", "fingerprint", "action", "evidence_url", "rationale", "reviewed_at"}, {"municipalities", "canton_wide"})
            key = identity(raw["source_id"])
            _require(key not in seen and key in store["items"], "Unknown or repeated review item")
            seen.add(key)
            item = store["items"][key]
            review = _review({k: v for k, v in raw.items() if k != "source_id"}, item["candidate"])
            _require(review["fingerprint"] == item["fingerprint"], "Stale metadata fingerprint; inspect the current queue")
            _require(_stamp(item["metadata_at"]) <= _stamp(review["reviewed_at"]) <= _stamp(operation_at), "Review date is outside the current metadata period")
            previous = item.get("review")
            _require(previous is None or _stamp(review["reviewed_at"]) >= _stamp(previous["reviewed_at"]), "Out-of-order review decision")
            if previous and previous != review:
                item["audit"] = (item["audit"] + [{"fingerprint": previous["fingerprint"], "changed_at": review["reviewed_at"],
                                                   "review_action": previous["action"]}])[-5:]
            item["review"] = review
            item.pop("review_invalidated_at", None)
            _invalidate_legal(item, operation_at, removed_only=True)
        store["updated_at"] = operation_at
        return store
    return _transaction(path, update)


def apply_verifications(path, verifications, *, now=None):
    """Explicit legal evidence, independently entered for each reviewed territory."""
    _require(isinstance(verifications, list) and verifications, "Legal verification batch must be a nonempty list")
    operation_at = _time(now)

    def update(store):
        seen = set()
        for raw in verifications:
            _keys(raw, {"source_id", "fingerprint", "verified"}, {"municipality", "canton_wide"})
            key = identity(raw["source_id"])
            _require(key in store["items"], "Unknown legal verification item")
            named, wide = "municipality" in raw, raw.get("canton_wide", False)
            _require(type(wide) is bool and named != wide, "Legal proof requires one municipality OR explicit canton-wide scope")
            scope = _municipality(raw["municipality"]) if named else "*"
            _require((key, scope) not in seen, "Repeated legal verification item/territory")
            seen.add((key, scope))
            item = store["items"][key]
            _require(raw["fingerprint"] == item["fingerprint"], "Stale legal metadata fingerprint")
            _require(_legal_scope(item, scope), "Legal proof requires current included territorial scope")
            proof, verified = _proof(raw["verified"], item["candidate"])
            _require(swiss_today(_stamp(item["metadata_at"])) <= verified.verified_on <= swiss_today(_stamp(operation_at)),
                     "Legal verification date is outside the current metadata period")
            previous = item.get("legal", {}).get(scope)
            if previous:
                earliest = date.fromisoformat(previous["verified"]["verified_on"])
                if "invalidated_at" in previous:
                    earliest = max(earliest, swiss_today(_stamp(previous["invalidated_at"])))
                _require(verified.verified_on >= earliest, "Out-of-order legal verification")
            item.setdefault("legal", {})[scope] = {"fingerprint": item["fingerprint"], "verified": proof}
        store["updated_at"] = operation_at
        return store
    return _transaction(path, update)


def review_state(item):
    review = item.get("review")
    if not review:
        return "pending"
    if review["fingerprint"] != item["fingerprint"] or "review_invalidated_at" in item:
        return "changed"
    return {"include": "included", "exclude": "excluded", "hold": "held"}[review["action"]]


def queue(path, *, all_items=False):
    store = validate_store(_read(path))
    rows = []
    for key, item in store["items"].items():
        state = review_state(item)
        if not all_items and state not in ("pending", "changed", "held"):
            continue
        candidate = item["candidate"]
        suggestion = "planning-like" if re.search(r"bau|nutzungs|richtplan|raumplan|gestaltungs|überbau|erschliess|lärm|planungs", candidate["title"], re.I) else "needs-review"
        rows.append({"source_id": candidate["source_id"], "fingerprint": item["fingerprint"], "title": candidate["title"],
                     "url": candidate["url"], "state": state, "suggested_relevance": suggestion})
        if all_items and "review" in item:
            rows[-1]["reviewed_scope"] = {"municipalities": item["review"]["municipalities"], "canton_wide": item["review"]["canton_wide"]}
    return sorted(rows, key=lambda row: (row["source_id"]["namespace"], row["source_id"]["value"]))


@dataclass
class PreviewItem:
    candidate: dict
    absent: bool = False
    verified: PL.Verified | None = None


@dataclass
class View:
    status: str = "off"
    error: str = ""
    items: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    pending: int = 0
    changed: int = 0
    municipality_queried: bool = False


def _scope(review, municipality):
    return review.get("action") == "include" and (review.get("canton_wide") is True or municipality in review.get("municipalities", []))


def state_for(municipality, path=None, *, today=None):
    path = (os.environ.get(ENV, "") if path is None else str(path or "")).strip()
    if not path:
        return View()
    try:
        municipality = _municipality(municipality)
        store = validate_store(_read(path))
        today = today or swiss_today()
        source_keys = {key for key, source in store["selections"].items()
                       if source["selection"]["provider"] == "www.ag.ch" or source["selection"].get("municipality") == municipality}
        view = View(status="ok")
        for key in sorted(source_keys):
            source = store["selections"][key]
            selection = source["selection"]
            success = _stamp(source["last_success_at"]) if source["last_success_at"] else None
            if selection.get("municipality") == municipality and selection.get("kind") == "municipal" and success:
                view.municipality_queried = True
            view.sources.append({"selection": selection, "last_success_at": success,
                                 "last_attempt_at": _stamp(source["last_attempt_at"]), "error": source["error"],
                                 "stale": bool(success and (today - success.date()).days > STALE_DAYS),
                                 "empty": success is not None and not source["members"]})
        for key, item in store["items"].items():
            state = review_state(item)
            queried = bool(source_keys.intersection(item["seen_in"]))
            scoped = _scope(item.get("review", {}), municipality)
            view.pending += queried and state in ("pending", "held")
            view.changed += state == "changed" and (queried or scoped)
            if state == "included" and scoped:
                absent = any(store["selections"][source]["last_success_at"] is not None
                             and key not in store["selections"][source]["members"] for source in item["seen_in"])
                view.items.append(PreviewItem(copy.deepcopy(item["candidate"]), absent=absent, verified=_legal_for(item, municipality, today)))
        view.items.sort(key=lambda item: item.candidate.get("published_on", item.candidate.get("publication_date", "")), reverse=True)
        return view
    except (C.CollectionError, OSError, TypeError, ValueError):
        return View(status="error", error="Lokale Vorschau nicht lesbar. Die Quellen müssen erneut geprüft werden.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("import", help="Import validated local candidate snapshots")
    load.add_argument("store")
    load.add_argument("snapshots", nargs="+")
    refresh = commands.add_parser("sync", help="Explicit bounded local source refresh")
    refresh.add_argument("store")
    refresh.add_argument("--municipality", required=True)
    refresh.add_argument("--max-pages", type=int, default=5)
    refresh.add_argument("--timeout", type=float, default=C.DEFAULT_TIMEOUT)
    pending = commands.add_parser("queue", help="List IDs/fingerprints for operator review")
    pending.add_argument("store")
    pending.add_argument("--all", action="store_true", dest="all_items")
    review = commands.add_parser("review", help="Apply metadata-pinned relevance/scope decisions")
    review.add_argument("store")
    review.add_argument("batch")
    verify = commands.add_parser("verify", help="Apply explicit source-proven legal verification per reviewed territory")
    verify.add_argument("store")
    verify.add_argument("batch")
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            store = import_snapshots(args.store, [_read(path) for path in args.snapshots])
        elif args.command == "review":
            store = apply_reviews(args.store, _read(args.batch))
        elif args.command == "verify":
            store = apply_verifications(args.store, _read(args.batch))
        elif args.command == "queue":
            print(json.dumps(queue(args.store, all_items=args.all_items), ensure_ascii=False, indent=2))
            return 0
        else:
            # Refuse unsafe/unrelated stores before any source request.
            validate_store(_read(args.store, missing=True))
            store, errors = sync(args.store, args.municipality, max_pages=args.max_pages, timeout=args.timeout)
            if errors:
                print(f"Local preview updated; {len(errors)} source(s) failed. Last good data retained.", file=sys.stderr)
                return 1
        print(f"Local preview: {len(store['items'])} candidates, {len(store['selections'])} source selections. Scope reviews are not legal-stage verification.")
        return 0
    except (C.CollectionError, OSError, TypeError, ValueError) as exc:
        print(f"Preview update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
