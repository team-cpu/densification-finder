"""Explicit parcel snapshots for the single Scope organisation.

Drafts remain session-local. A revision check prevents overwriting a teammate's
newer snapshot. Reads and writes require personal, granted membership.
"""
import json
import math
import sqlite3

import economics
import scope_auth

LIMITS = {
    'gf': (0, None), 'unit': (10, None), 'sale': (0, None),
    'share': (10, 100), 'build': (0, None), 'ancillary': (0, 100),
    'demolition': (0, None), 'financing': (0, 100), 'reserve': (0, 100),
}


def schema(con):
    con.execute('''CREATE TABLE IF NOT EXISTS shared_calculations (
        parcel_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
        revision INTEGER NOT NULL, updated_by INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {'inputs', 'overrides'}:
        raise ValueError('Ungültige Kalkulation.')
    inputs = payload['inputs']
    if not isinstance(inputs, dict) or set(inputs) != set(LIMITS) | {'demolish'}:
        raise ValueError('Unvollständige Kalkulation.')
    for key, (minimum, maximum) in LIMITS.items():
        value = inputs[key]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or value < minimum or (maximum is not None and value > maximum)):
            raise ValueError('Ungültige Annahme: ' + key)
    if type(inputs['demolish']) is not bool:
        raise ValueError('Ungültige Abbruchentscheidung.')
    overrides = payload['overrides']
    if not isinstance(overrides, dict) or set(overrides) - set(economics.OVERRIDE_KEYS):
        raise ValueError('Ungültige Überschreibung.')
    for value in overrides.values():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Ungültiger Betrag.')
    economics.validate_overrides(overrides)
    return json.dumps(payload, allow_nan=False, sort_keys=True)


def _enabled(con):
    row = con.execute('SELECT shared_calculations FROM organisation_profile WHERE id=1').fetchone()
    if not row or not row[0]:
        raise scope_auth.AuthError('Team-Kalkulationen sind deaktiviert.')


def _read_access(con, member):
    scope_auth._require_second_factor(member, bool(con.execute(
        'SELECT enforce_2fa FROM organisation_profile WHERE id=1').fetchone()[0]))
    row = con.execute("SELECT 1 FROM organisation_members m JOIN scope_access a ON a.member_id=m.id "
                      "WHERE m.id=? AND m.status='active' AND a.auth_id IS NOT NULL AND a.auth_id<>''",
                      (member['id'],)).fetchone()
    if not row:
        raise scope_auth.AuthError('Zugriff wurde entzogen.')
    _enabled(con)


def load(db, pid):
    if not scope_auth.enabled():
        raise scope_auth.AuthError('Persönlicher Zugang erforderlich.')
    member = scope_auth.current(db)
    with sqlite3.connect(db) as con:
        con.execute('BEGIN')
        _read_access(con, member)
        row = con.execute('SELECT payload,revision,updated_at FROM shared_calculations WHERE parcel_id=?', (pid,)).fetchone()
    if not row:
        return None
    payload = json.loads(row[0])
    validate(payload)
    return {'payload': payload, 'revision': row[1], 'updated_at': row[2]}


def save(db, pid, payload, expected_revision):
    if not scope_auth.enabled():
        raise scope_auth.AuthError('Persönlicher Zugang erforderlich.')
    actor = scope_auth.require_write(db)
    encoded = validate(payload)
    if not isinstance(pid, str) or not pid or len(pid) > 200:
        raise ValueError('Ungültige Parzelle.')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('Ungültige Version.')
    with sqlite3.connect(db) as con:
        scope_auth.check_transaction(con, actor)
        _read_access(con, actor)
        row = con.execute('SELECT revision FROM shared_calculations WHERE parcel_id=?', (pid,)).fetchone()
        revision = row[0] if row else 0
        if revision != expected_revision:
            raise ValueError('Die Team-Version wurde geändert. Bitte zuerst die aktuelle Team-Version laden.')
        con.execute('INSERT INTO shared_calculations(parcel_id,payload,revision,updated_by) VALUES(?,?,?,?) '
                    'ON CONFLICT(parcel_id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision,'
                    'updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP',
                    (pid, encoded, revision + 1, actor['id']))
    return revision + 1
