"""Opt-in daily metadata refresh; no relevance decisions, legal promotion or mail.

The scheduler requires explicit operational source-reuse authorization. Manual
planning_preview sync remains a separate, explicit local command.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import planning_collect as C
import planning_preview as P

PREFIX = "SCOPE_PLANNING_REFRESH_"
SCHEMA = "scope.planning-refresh"
MAX_STATE_BYTES = 16384
MAX_MUNICIPALITIES = 10
# Scheduled operations retain their smaller bound than explicit manual reads.
MAX_SOURCE_PAGES = 20
MAX_TOTAL_PAGES = 100
RETRY_SECONDS = 3600


class ConfigError(ValueError):
    """A fixed, safe operator-facing configuration error."""


def _config_require(condition, message):
    if not condition:
        raise ConfigError(message)


def _flag(env, name):
    raw = env.get(name, "").strip()
    _config_require(raw in ("", "0", "1"), f"{name} must be 0 or 1")
    return raw == "1"


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    store: Path | None = None
    municipalities: tuple = ()
    hour: int = 8
    max_pages: int = 5
    timeout: float = 20.0
    reference: str = ""

    @property
    def state_path(self):
        return Path(str(self.store) + ".refresh.json")

    @property
    def lock_path(self):
        return Path(str(self.store) + ".refresh.lock")

    @property
    def digest(self):
        values = [str(self.store), self.municipalities, self.hour, self.max_pages, self.timeout, self.reference]
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()


def configuration(environ=None):
    env = os.environ if environ is None else environ
    if not _flag(env, PREFIX + "ENABLED"):
        return Config()
    _config_require(_flag(env, "SCOPE_PLANNING_REUSE_AUTHORIZED"), "Scheduled refresh requires SCOPE_PLANNING_REUSE_AUTHORIZED=1")
    reference = env.get("SCOPE_PLANNING_REUSE_REFERENCE", "").strip()
    _config_require(bool(reference) and len(reference) <= 200 and not re.search(r"[\x00-\x1f\x7f]", reference),
                    "Scheduled refresh requires a short explicit SCOPE_PLANNING_REUSE_REFERENCE")
    raw_store = env.get(P.ENV, "").strip()
    store = Path(raw_store)
    _config_require(bool(raw_store) and store.is_absolute() and ".." not in store.parts, "Scheduled refresh requires an absolute SCOPE_PLANNING_PREVIEW file")
    raw_names = env.get(PREFIX + "MUNICIPALITIES", "").split(",")
    _config_require(1 <= len(raw_names) <= MAX_MUNICIPALITIES and all(name.strip() for name in raw_names),
                    "Scheduled refresh requires 1–10 explicitly configured municipalities")
    try:
        names = tuple(sorted(P._municipality(name.strip()) for name in raw_names))
    except C.CollectionError as exc:
        raise ConfigError("Unknown refresh municipality") from exc
    _config_require(len(set(names)) == len(names), "Duplicate refresh municipality")
    try:
        hour = int(env.get(PREFIX + "HOUR", "8"))
        pages = int(env.get(PREFIX + "MAX_PAGES", "5"))
        timeout = float(env.get(PREFIX + "TIMEOUT", "20"))
    except (ValueError, TypeError) as exc:
        raise ConfigError("Invalid refresh hour/pages/timeout") from exc
    _config_require(0 <= hour <= 23, "Refresh hour must be 0–23 (Europe/Zurich)")
    _config_require(1 <= pages <= MAX_SOURCE_PAGES and (2 * len(names) + 1) * pages <= MAX_TOTAL_PAGES,
                    "Refresh page budget must be 1–20 per source and at most 100 pages per pass")
    _config_require(math.isfinite(timeout) and 0 < timeout <= 30, "Refresh timeout must be greater than 0 and at most 30 seconds")
    try:
        ZoneInfo("Europe/Zurich")
    except ZoneInfoNotFoundError as exc:
        raise ConfigError("Europe/Zurich timezone data is required") from exc
    return Config(True, store, names, hour, pages, timeout, reference)


def _state(value):
    P._keys(value, {"schema", "schema_version", "config_hash", "day", "status", "last_attempt_at", "last_finished_at",
                    "retry_at", "successful_date", "successes", "failures"})
    P._require(value["schema"] == SCHEMA and type(value["schema_version"]) is int and value["schema_version"] == 1,
               "Refusing unrelated refresh state")
    P._require(isinstance(value["config_hash"], str) and re.fullmatch(r"[0-9a-f]{64}", value["config_hash"]), "Invalid refresh configuration hash")
    for name in ("day", "successful_date"):
        raw = value[name]
        P._require((name == "successful_date" and raw is None) or (isinstance(raw, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw)), "Invalid refresh day")
        if raw is not None:
            date.fromisoformat(raw)
    attempt, retry = P._stamp(value["last_attempt_at"]), P._stamp(value["retry_at"])
    P._require(retry >= attempt + timedelta(seconds=RETRY_SECONDS), "Invalid refresh retry cooldown")
    P._require(value["status"] in ("running", "success", "partial", "error"), "Invalid refresh status")
    finished = P._stamp(value["last_finished_at"]) if value["last_finished_at"] is not None else None
    P._require((value["status"] == "running") == (finished is None) and (finished is None or attempt <= finished <= retry), "Invalid refresh completion date")
    P._require(all(type(value[k]) is int and 0 <= value[k] <= 2 * MAX_MUNICIPALITIES + 1 for k in ("successes", "failures")), "Invalid refresh counts")
    P._require(value["status"] != "success" or (value["successful_date"] == value["day"] and value["failures"] == 0), "Invalid daily refresh success")
    return value


def _read_state(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    P._require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_STATE_BYTES, "Refresh state must be a bounded regular file; symlinks refused")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        P._require(os.fstat(handle.fileno()).st_ino == info.st_ino, "Refresh state changed during inspection")
        raw = handle.read(MAX_STATE_BYTES + 1)
    P._require(len(raw) <= MAX_STATE_BYTES, "Refresh state exceeds size limit")
    try:
        return _state(C._json(raw, "refresh state"))
    except RecursionError as exc:
        raise C.CollectionError("Refresh state exceeds nesting limit") from exc


def check(config):
    """Read-only validation: no lock creation, provider requests or directory writes."""
    if not config.enabled:
        return {"status": "off"}
    _config_require(config.store.parent.is_dir(), "Refresh store directory must already exist")
    P.validate_store(P._read(config.store, missing=True))
    _read_state(config.state_path)
    for path in (config.lock_path, Path(str(config.store) + ".lock")):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        P._require(stat.S_ISREG(info.st_mode), "Refresh/preview locks must be regular files; symlinks refused")
    return {"status": "configured", "municipalities": len(config.municipalities), "maximum_pages": (2 * len(config.municipalities) + 1) * config.max_pages}


def _write_state(path, value, expected):
    _state(value)
    raw = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    P._require(len(raw) <= MAX_STATE_BYTES, "Refresh state exceeds size limit")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        P._require(_read_state(path) == expected, "Refresh state changed during update")
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _selections(config):
    return [{"provider": "amtsblatt.ag.ch", "kind": kind, "municipality": name}
            for name in config.municipalities for kind in ("municipal", "canton-approvals")] + [
                {"provider": "www.ag.ch", "mode": "current", "term": ""}]


def run_once(*, environ=None, clock=None, collectors=None):
    """Only enabled, authorized, due passes contact providers; claims survive restarts."""
    config = configuration(environ)
    if not config.enabled:
        return {"status": "off"}
    check(config)  # Refuse unsafe stores and bookkeeping before making any request.
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = P._stamp(P._time(clock()))
    local = now.astimezone(ZoneInfo("Europe/Zurich"))
    if local.hour < config.hour:
        return {"status": "not-due"}
    lock = os.open(config.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        P._require(stat.S_ISREG(os.fstat(lock).st_mode), "Refresh lock must be a regular file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        check(config)
        previous = _read_state(config.state_path)
        day = local.date().isoformat()
        if previous:
            if previous["status"] == "success" and previous["day"] == day and previous["config_hash"] == config.digest:
                return {"status": "not-due"}
            if now < P._stamp(previous["retry_at"]):
                return {"status": "cooldown"}
        claim = {"schema": SCHEMA, "schema_version": 1, "config_hash": config.digest, "day": day, "status": "running",
                 "last_attempt_at": P._time(now), "last_finished_at": None, "retry_at": P._time(now + timedelta(seconds=RETRY_SECONDS)),
                 "successful_date": previous["successful_date"] if previous else None, "successes": 0, "failures": 0}
        _write_state(config.state_path, claim, previous)
        municipal, canton = collectors or (C.collect_amtsblatt, C.collect_consultations)
        transport = C.Transport(timeout=config.timeout) if collectors is None else None
        successes, failures, fatal = 0, 0, False
        try:
            for selection in _selections(config):
                try:
                    fetched = clock()  # Each source gets its own observation time, not one batch timestamp.
                    if selection["provider"] == "amtsblatt.ag.ch":
                        snapshot = municipal(selection["municipality"], selection["kind"], max_pages=config.max_pages, transport=transport, now=fetched)
                    else:
                        snapshot = canton(max_pages=config.max_pages, transport=transport, now=fetched)
                    P._require(P.validate_snapshot(snapshot) == selection, "Collected snapshot selection mismatch")
                    completed = P._time(clock())
                    P._transaction(config.store, lambda current: P._merge(current, [snapshot], [], completed))
                    successes += 1
                except Exception as exc:
                    failures += 1
                    # Provider exceptions may carry bodies/URLs: store only their type.
                    error = f"Scheduled source failed ({type(exc).__name__})"
                    completed = P._time(clock())
                    P._transaction(config.store, lambda current: P._merge(current, [], [(selection, error)], completed))
        except Exception:
            fatal = True  # A local write failed; the durable pre-fetch claim still prevents immediate retries.
        finished = max(now, P._stamp(P._time(clock())))
        status = "error" if fatal else ("partial" if failures else "success")
        result = {**claim, "status": status, "last_finished_at": P._time(finished),
                  "retry_at": P._time(finished + timedelta(seconds=RETRY_SECONDS)), "successes": successes, "failures": failures}
        if status == "success":
            result["successful_date"] = day
        _write_state(config.state_path, result, claim)
        return {"status": status, "sources_succeeded": successes, "sources_failed": failures}
    finally:
        os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Validate configuration without requests or disk mutation")
    action.add_argument("--once", action="store_true", help="Run only when enabled, authorized and due")
    args = parser.parse_args(argv)
    try:
        result = check(configuration()) if args.check else run_once()
        print(json.dumps(result, sort_keys=True))
        return 1 if result["status"] in ("partial", "error") else 0
    except ConfigError as exc:
        print(f"Planning refresh configuration: {exc}", file=sys.stderr)
    except (C.CollectionError, OSError, ValueError, TypeError) as exc:
        print(f"Planning refresh failed ({type(exc).__name__}); inspect local configuration/state", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
