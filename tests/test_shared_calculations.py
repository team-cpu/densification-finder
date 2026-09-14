import copy
import sqlite3
import pytest
import ingest
import scope_auth as auth
import shared_calculations as shared


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = str(tmp_path / 'test.sqlite')
    with sqlite3.connect(db) as con:
        ingest.schema(con)
    monkeypatch.setenv('SCOPE_AUTH_MODE', 'personal')
    monkeypatch.setenv('SCOPE_OWNER_EMAIL', 'owner@example.com')
    auth.bootstrap_owner(db)
    member = auth.verified_member({'id':'owner-id','email':'owner@example.com',
                                  'email_confirmed_at':'2026-09-12'}, db, bind=True)
    monkeypatch.setattr(auth, 'current', lambda db=None: member)
    monkeypatch.setattr(auth.st, 'session_state', {})
    with sqlite3.connect(db) as con:
        con.execute('UPDATE organisation_profile SET shared_calculations=1')
    payload = {'inputs': {k: float(max(lo, 20)) for k,(lo,hi) in shared.LIMITS.items()}, 'overrides': {}}
    payload['inputs']['demolish'] = True
    return db, member, payload


def test_roundtrip_conflict_and_parcel_isolation(setup):
    db, member, payload = setup
    assert shared.load(db, '1:2') is None
    assert shared.save(db, '1:2', payload, 0) == 1
    assert shared.load(db, '1:2')['payload'] == payload
    assert shared.load(db, '1:3') is None
    changed = copy.deepcopy(payload)
    changed['inputs']['sale'] = 5000
    with pytest.raises(ValueError, match='Team-Version'):
        shared.save(db, '1:2', changed, 0)
    assert shared.load(db, '1:2')['payload'] == payload
    assert shared.save(db, '1:2', changed, 1) == 2


def test_reader_loads_but_cannot_save_and_revocation_blocks_read(setup):
    db, member, payload = setup
    shared.save(db, '1:2', payload, 0)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE organisation_members SET role='Leseweise' WHERE id=?", (member['id'],))
    # Cached owner role cannot bypass the write transaction check.
    assert shared.load(db, '1:2')['revision'] == 1
    with pytest.raises(auth.AuthError):
        shared.save(db, '1:2', payload, 1)
    with sqlite3.connect(db) as con:
        con.execute('DELETE FROM scope_access')
    with pytest.raises(auth.AuthError):
        shared.load(db, '1:2')


def test_editor_can_save(setup):
    db, member, payload = setup
    member['role'] = 'Bearbeiter'
    with sqlite3.connect(db) as con:
        con.execute("UPDATE organisation_members SET role='Bearbeiter' WHERE id=?", (member['id'],))
    assert shared.save(db, '1:2', payload, 0) == 1


def test_policy_and_mfa_rechecked(setup):
    db, member, payload = setup
    with sqlite3.connect(db) as con:
        con.execute('UPDATE organisation_profile SET enforce_2fa=1')
    with pytest.raises(auth.AuthError):
        shared.load(db, '1:2')
    with pytest.raises(auth.AuthError):
        shared.save(db, '1:2', payload, 0)
    auth.st.session_state['scope_mfa'] = True
    shared.save(db, '1:2', payload, 0)
    with sqlite3.connect(db) as con:
        con.execute('UPDATE organisation_profile SET shared_calculations=0')
    with pytest.raises(auth.AuthError):
        shared.load(db, '1:2')
    with pytest.raises(auth.AuthError):
        shared.save(db, '1:2', payload, 1)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, True, '12'])
def test_invalid_input_is_rejected(setup, value):
    db, _, payload = setup
    payload['inputs']['sale'] = value
    with pytest.raises(ValueError):
        shared.save(db, '1:2', payload, 0)
    assert shared.load(db, '1:2') is None


def test_migration_preserves_owner_choice(setup):
    db, _, _ = setup
    with sqlite3.connect(db) as con:
        ingest.schema(con)
        assert con.execute('SELECT shared_calculations FROM organisation_profile').fetchone()[0] == 1


def test_widgets_load_saved_values_and_save_new_revision(setup, monkeypatch):
    from streamlit.testing.v1 import AppTest
    db, _, payload = setup
    shared.save(db, '1:2', payload, 0)
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy
    monkeypatch.setattr(auth.st, "session_state", SessionStateProxy())
    script = f'''
import streamlit as st
import detail
active = detail._team_load('1:2', {db!r})
for name in detail.OWN + ('gf',):
    detail._number(st, name, '1:2', name, 25.0, step=1.0)
detail._remember('1:2', 'demolish', st.checkbox('demolish', value=True, key=detail._widget_key('1:2', 'demolish')))
if active:
    detail._team_save('1:2', {db!r})
'''
    app = AppTest.from_string(script).run()
    assert not app.exception
    app.button(key='team_load::1:2').click().run()
    assert not app.exception
    assert app.number_input(key='own::sale').value == 20.0
    app.number_input(key='own::sale').set_value(8500.0).run()
    app.button(key='team_save::1:2').click().run()
    assert not app.exception
    saved = shared.load(db, '1:2')
    assert saved['revision'] == 2
    assert saved['payload']['inputs']['sale'] == 8500.0
