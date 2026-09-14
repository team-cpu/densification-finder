import sqlite3
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import ingest
import organisation
import scope_auth as auth

QR = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = str(tmp_path / "scope.sqlite")
    with sqlite3.connect(database) as con:
        ingest.schema(con)
    monkeypatch.setenv("SCOPE_AUTH_MODE", "personal")
    monkeypatch.setenv("SCOPE_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    auth.bootstrap_owner(database)
    return database


def user(factors=None, email="owner@example.com", uid="scope-user-1"):
    return {"id": uid, "email": email, "email_confirmed_at": "2026-09-10T00:00:00Z",
            "factors": factors or []}


def enforce(db, on=True):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR IGNORE INTO organisation_profile (id, name) VALUES (1, '')")
        con.execute("UPDATE organisation_profile SET enforce_2fa=? WHERE id=1", (int(on),))


def gate_app(db):
    app = AppTest.from_string(
        "import scope_auth, streamlit as st\n"
        f"scope_auth.gate({db!r})\n"
        "st.write('PRIVATE DATA')\n"
    )
    app.session_state["scope_access_token"] = "aal1-token"
    return app


def _private(app):
    return any(item.value == "PRIVATE DATA" for item in app.markdown)


def test_transport_allows_factor_paths_only(monkeypatch):
    seen = []

    class Opener:
        def open(self, request, timeout=None):
            seen.append((request.get_method(), request.full_url))
            import io
            return io.BytesIO(b"{}")
    monkeypatch.setattr(auth, "build_opener", lambda handler: Opener())
    monkeypatch.setenv("SCOPE_SUPABASE_URL", "https://scope.supabase.co")
    monkeypatch.setenv("SCOPE_SUPABASE_ANON_KEY", "test-key")
    auth.api("factors", {"factor_type": "totp"}, "t")
    auth.api("factors/abc-123/challenge", {}, "t")
    auth.api("factors/abc-123/verify", {"challenge_id": "c", "code": "123456"}, "t")
    auth.api("factors/abc-123", token="t", method="DELETE")
    assert [m for m, _ in seen] == ["POST", "POST", "POST", "DELETE"]
    assert seen[-1][1] == "https://scope.supabase.co/auth/v1/factors/abc-123"
    for bad in ("factors/../admin", "factors/abc/unenroll", "admin/users", "factors/"):
        with pytest.raises(auth.AuthError):
            auth.api(bad, token="t")


def test_member_carries_verified_totp_factor(db):
    factors = [{"id": "f-old", "factor_type": "totp", "status": "unverified"},
               {"id": "f-ok", "factor_type": "totp", "status": "verified"},
               {"id": "p-1", "factor_type": "phone", "status": "verified"}]
    member = auth.verified_member(user(factors), db, bind=True)
    assert member["mfa_factor"] == "f-ok"
    assert member["mfa_unverified"] == ["f-old"]
    assert auth.verified_member(user(), db)["mfa_factor"] is None


def test_gate_without_enforcement_needs_no_second_factor(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "api", Mock(return_value=user()))
    app = gate_app(db).run()
    assert not app.exception
    assert _private(app)
    assert "scope_mfa" not in app.session_state


def test_enforced_gate_enrols_then_verifies_and_replaces_token(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    enforce(db)
    calls = []

    def api(endpoint, payload=None, token=None, method=None):
        calls.append((endpoint, method or "POST" if endpoint != "user" else "GET", token))
        if endpoint == "user":
            return user([{"id": "f-old", "factor_type": "totp", "status": "unverified"}]
                        if token == "aal1-token" else
                        [{"id": "f-new", "factor_type": "totp", "status": "verified"}])
        if endpoint == "factors/f-old":
            return {}
        if endpoint == "factors":
            assert payload == {"factor_type": "totp", "friendly_name": "Scope", "issuer": "Scope"}
            return {"id": "f-new", "type": "totp",
                    "totp": {"qr_code": QR, "secret": "JBSWY3DPEHPK3PXP", "uri": "otpauth://totp/Scope:owner@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Scope"}}
        if endpoint == "factors/f-new/challenge":
            return {"id": "ch-1", "expires_at": 9999999999}
        if endpoint == "factors/f-new/verify":
            assert payload == {"challenge_id": "ch-1", "code": "246810"}
            return {"access_token": "aal2-token", "user": user()}
        raise AssertionError(endpoint)
    monkeypatch.setattr(auth, "api", api)

    app = gate_app(db).run()
    assert not app.exception
    assert not _private(app)
    # Abandoned enrolment removed, fresh secret shown for manual entry.
    assert ("factors/f-old", "DELETE", "aal1-token") in calls
    html = " ".join(node.proto.body for node in app.get("html"))
    assert "JBSW Y3DP EHPK 3PXP" in html
    assert app.get("image")
    code = next(item for item in app.text_input if item.label == "Code aus der App")
    code.set_value("246810")
    next(button for button in app.button if button.label == "Aktivieren").click().run()
    assert not app.exception
    assert app.session_state["scope_access_token"] == "aal2-token"
    assert app.session_state["scope_mfa"] is True
    assert "scope_mfa_enrol" not in app.session_state
    assert _private(app)


def test_enforced_gate_challenges_existing_factor_and_limits_attempts(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    enforce(db)
    verify = Mock(side_effect=auth.AuthError("Anmeldung konnte nicht bestätigt werden. Bitte erneut versuchen."))

    def api(endpoint, payload=None, token=None, method=None):
        if endpoint == "user":
            return user([{"id": "f-ok", "factor_type": "totp", "status": "verified"}])
        if endpoint == "factors/f-ok/challenge":
            return {"id": "ch-1"}
        if endpoint == "factors/f-ok/verify":
            return verify(payload)
        raise AssertionError(endpoint)
    monkeypatch.setattr(auth, "api", api)

    app = gate_app(db).run()
    assert not app.exception
    assert not _private(app)
    assert not app.get("image")  # no enrolment for a member who already has a factor
    for attempt in range(6):
        app.text_input[0].set_value("000000")
        next(button for button in app.button if button.label == "Bestätigen").click().run()
        assert not app.exception
    assert verify.call_count == 5
    assert "Zu viele Versuche" in app.error[-1].value

    verify.side_effect = None
    verify.return_value = {"access_token": "aal2-token"}
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scope_auth_verifications")
    app.text_input[0].set_value("135790")
    next(button for button in app.button if button.label == "Bestätigen").click().run()
    assert not app.exception
    assert app.session_state["scope_access_token"] == "aal2-token"
    assert _private(app)


def test_reset_unenrols_with_second_factor_session(db, monkeypatch):
    calls = []

    def api(endpoint, payload=None, token=None, method=None):
        calls.append((endpoint, method, token))
        return {}
    monkeypatch.setattr(auth, "api", api)
    monkeypatch.setattr(auth, "current", lambda db=None: {"id": 1, "role": "Inhaber", "mfa_factor": "f-ok"})
    app = AppTest.from_string(
        "import scope_auth, streamlit as st\n"
        "st.session_state['scope_access_token'] = 'aal2-token'\n"
        "st.session_state['scope_mfa'] = True\n"
        f"scope_auth.mfa_reset({db!r})\n"
    ).run()
    assert not app.exception
    assert calls == [("factors/f-ok", "DELETE", "aal2-token")]
    assert "scope_mfa" not in app.session_state
    assert app.session_state["scope_access_token"] == "aal2-token"


def test_enforce_toggle_is_live_for_owners_in_personal_mode(db, monkeypatch):
    import paths
    monkeypatch.setattr(auth, "current", lambda db=None: {"id": 1, "role": "Inhaber", "mfa_factor": None})
    monkeypatch.setattr(paths, "DB", db)
    app = AppTest.from_string(
        "import organisation, streamlit as st\n"
        f"st.session_state[{organisation.DIALOG_OPEN!r}] = True\n"
        f"st.session_state[{organisation.DIALOG_VIEW!r}] = 'settings'\n"
        "organisation.open_if_requested('2026-09-11')\n"
    ).run()
    assert not app.exception
    toggle = app.toggle(key="org_profile_enforce_2fa")
    assert not toggle.disabled and toggle.value is False
    assert not app.toggle(key="org_profile_shared_calculations").disabled
    toggle.set_value(True).run()
    assert not app.exception
    assert organisation.load_profile(db)["enforce_2fa"] is True
    assert auth.mfa_required(db) is True
    assert any("zweiten Faktor" in error.value for error in app.info)


def test_transport_reads_large_enrolment_answers(monkeypatch):
    import io
    import json
    body = json.dumps({"id": "f-1", "totp": {"qr_code": "<svg>" + "x" * 200_000 + "</svg>", "secret": "S", "uri": "otpauth://x"}}).encode()
    assert len(body) > 65536

    class Opener:
        def open(self, request, timeout=None):
            return io.BytesIO(body)
    monkeypatch.setattr(auth, "build_opener", lambda handler: Opener())
    monkeypatch.setenv("SCOPE_SUPABASE_URL", "https://scope.supabase.co")
    monkeypatch.setenv("SCOPE_SUPABASE_ANON_KEY", "test-key")
    assert auth.api("factors", {"factor_type": "totp"}, "t")["id"] == "f-1"


def test_qr_code_is_accepted_bare_or_as_data_url():
    from urllib.parse import quote
    import base64 as b64
    assert auth._qr_svg(QR) == QR
    assert auth._qr_svg('<?xml version="1.0"?>' + QR) == QR
    assert auth._qr_svg("data:image/svg+xml;utf-8," + quote(QR)) == QR
    assert auth._qr_svg("data:image/svg+xml;base64," + b64.b64encode(QR.encode()).decode()) == QR
    assert auth._qr_svg("") == "" and auth._qr_svg("data:image/png;base64,AAAA") == ""


def _factor_api(calls, verified=True):
    def api(endpoint, payload=None, token=None, method=None):
        calls.append(endpoint)
        if endpoint == "user":
            return user([{"id": "f-ok", "factor_type": "totp", "status": "verified"}] if verified else [])
        if endpoint == "factors":
            return {"id": "f-new", "type": "totp", "totp": {"qr_code": QR, "secret": "JBSWY3DPEHPK3PXP", "uri": "otpauth://x"}}
        if endpoint.endswith("/challenge"):
            return {"id": "ch-1"}
        if endpoint.endswith("/verify"):
            return {"access_token": "aal2-token"}
        if method == "DELETE":
            return {}
        raise AssertionError(endpoint)
    return api


def test_voluntary_factor_is_challenged_even_without_enforcement(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "api", _factor_api([]))
    app = gate_app(db).run()
    assert not app.exception
    assert not _private(app)
    assert any(button.label == "Bestätigen" for button in app.button)
    app.text_input[0].set_value("123456")
    next(button for button in app.button if button.label == "Bestätigen").click().run()
    assert not app.exception
    assert _private(app) and app.session_state["scope_mfa"] is True


def test_member_can_set_up_a_second_factor_voluntarily_or_postpone_it(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "api", _factor_api([], verified=False))
    app = gate_app(db)
    app.session_state["scope_mfa_setup"] = True
    app.run()
    assert not app.exception
    assert not _private(app)
    assert app.get("image")  # enrolment card
    later = next(button for button in app.button if button.label == "Später")
    later.click().run()
    assert not app.exception
    assert _private(app)
    assert "scope_mfa_setup" not in app.session_state and "scope_mfa" not in app.session_state

    app.session_state["scope_mfa_setup"] = True
    app.run()
    next(item for item in app.text_input if item.label == "Code aus der App").set_value("123456")
    next(button for button in app.button if button.label == "Aktivieren").click().run()
    assert not app.exception
    assert _private(app) and app.session_state["scope_mfa"] is True
    assert "scope_mfa_setup" not in app.session_state


def test_enforced_enrolment_offers_no_postponement(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    enforce(db)
    monkeypatch.setattr(auth, "api", _factor_api([], verified=False))
    app = gate_app(db).run()
    assert not app.exception
    assert app.get("image")
    assert not any(button.label == "Später" for button in app.button)


def test_account_menu_offers_setup_or_reset(db, monkeypatch):
    import shell
    for factor, flag, expected in ((None, False, "2FA einrichten"), ("f-ok", True, "2FA zurücksetzen")):
        monkeypatch.setattr(auth, "current", lambda db=None, f=factor: {"id": 1, "role": "Inhaber", "name": "Owner",
                                                                        "email": "owner@example.com", "mfa_factor": f})
        import paths
        monkeypatch.setattr(paths, "DB", db)
        app = AppTest.from_string(
            "import shell, streamlit as st\n"
            f"st.session_state['scope_mfa'] = {flag!r}\n"
            "shell._account_chip()\n"
        ).run()
        assert not app.exception
        labels = {button.label for button in app.button}
        assert expected in labels
        assert not ({"2FA einrichten", "2FA zurücksetzen"} - {expected}) & labels


def test_write_guards_recheck_second_factor_policy(db, monkeypatch):
    member = auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "current", lambda db=None: member)
    monkeypatch.setattr(auth.st, "session_state", {})
    enforce(db)
    with pytest.raises(auth.AuthError, match="zweiten Faktor"):
        auth.require_owner(db)
    with sqlite3.connect(db) as con:
        with pytest.raises(auth.AuthError, match="zweiten Faktor"):
            auth.check_transaction(con, member, owner=True)
    auth.st.session_state['scope_mfa'] = True
    assert auth.require_owner(db)['id'] == member['id']
    with sqlite3.connect(db) as con:
        auth.check_transaction(con, member, owner=True)
