import io
import sqlite3
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest
from streamlit.testing.v1 import AppTest

import ingest
import organisation
import scope_auth as auth
import workflow
import searches


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = str(tmp_path / "scope.sqlite")
    with sqlite3.connect(database) as con:
        ingest.schema(con)
    monkeypatch.setenv("SCOPE_AUTH_MODE", "personal")
    monkeypatch.setenv("SCOPE_OWNER_EMAIL", "owner@example.com")
    auth.bootstrap_owner(database)
    return database


def user(email="owner@example.com", uid="scope-user-1"):
    return {"id": uid, "email": email, "email_confirmed_at": "2026-09-10T00:00:00Z"}


def test_only_configured_owner_bootstrapped_and_never_repromoted(db, monkeypatch):
    monkeypatch.setenv("SCOPE_OWNER_EMAIL", "someone@example.com")
    auth.bootstrap_owner(db)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT email,role FROM organisation_members").fetchall() == [("owner@example.com", "Inhaber")]


def test_identity_binding_rejects_uninvited_unverified_and_different_identity(db):
    with pytest.raises(auth.AuthError):
        auth.verified_member(user("outsider@example.com"), db, bind=True)
    with pytest.raises(auth.AuthError):
        auth.verified_member(user() | {"email_confirmed_at": None}, db, bind=True)
    auth.verified_member(user(), db, bind=True)
    assert auth.verified_member(user(), db)["role"] == "Inhaber"
    with pytest.raises(auth.AuthError):
        auth.verified_member(user(uid="other-project-user"), db, bind=True)


def test_expired_and_revoked_invites_cannot_bind(db):
    with sqlite3.connect(db) as con:
        con.execute("UPDATE scope_access SET invited_until=0")
    with pytest.raises(auth.AuthError):
        auth.verified_member(user(), db, bind=True)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scope_access")
    with pytest.raises(auth.AuthError):
        auth.verified_member(user(), db, bind=True)


def test_legacy_member_does_not_gain_access(db):
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO organisation_members(email,role,status) VALUES ('legacy@example.com','Inhaber','active')")
    with pytest.raises(auth.AuthError):
        auth.verified_member(user("legacy@example.com"), db, bind=True)


def test_requests_throttled_across_sessions_and_unknown_email_does_not_send(db, monkeypatch):
    api = Mock(return_value={})
    monkeypatch.setattr(auth, "api", api)
    auth.request_code("unknown@example.com", db)
    api.assert_not_called()
    auth.request_code("owner@example.com", db)
    auth.request_code("owner@example.com", db)
    assert api.call_count == 1


def test_verification_checks_provider_user_before_binding(db, monkeypatch):
    api = Mock(side_effect=[{"access_token": "test-token"}, user()])
    monkeypatch.setattr(auth, "api", api)
    assert auth.verify_code("owner@example.com", "123456", db) == "test-token"
    assert api.call_args_list[1].args == ("user",)
    assert api.call_args_list[1].kwargs == {"token": "test-token"}


def test_editor_cannot_change_team_or_settings(db, monkeypatch):
    monkeypatch.setattr(auth, "current", lambda db=None: {"role": "Bearbeiter", "id": 42})
    for operation in [lambda: organisation.invite_member("new@example.com", db=db),
                      lambda: organisation.update_profile({"name": "Changed"}, db),
                      lambda: organisation.set_member_role(1, "Bearbeiter", db),
                      lambda: organisation.remove_member(1, db),
                      lambda: organisation.revoke_invite(1, db)]:
        with pytest.raises(auth.AuthError):
            operation()


def test_reader_mutations_stop_before_database_write(db, monkeypatch):
    monkeypatch.setattr(auth, "current", lambda db=None: {"role": "Leseweise", "id": 42})
    for operation in [lambda: workflow.update([(1, "1")], saved=True, db=db),
                      lambda: searches.save("test", {}, db), lambda: searches.delete("test", db)]:
        with pytest.raises(auth.AuthError):
            operation()
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM parcel_workflow").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM saved_searches").fetchone()[0] == 0


def test_incomplete_config_is_closed_in_ui(db, monkeypatch):
    monkeypatch.delenv("SCOPE_SUPABASE_URL", raising=False)
    app = AppTest.from_string(f"import scope_auth\nscope_auth.gate({db!r})\nimport streamlit as st\nst.write('PRIVATE DATA')").run()
    assert not app.exception
    assert "noch nicht konfiguriert" in app.error[0].value
    assert not app.markdown


def test_login_form_does_not_send_on_render(db, monkeypatch):
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    api = Mock()
    monkeypatch.setattr(auth, "api", api)
    app = AppTest.from_string(f"import scope_auth\nscope_auth.gate({db!r})").run()
    assert not app.exception
    assert [item.label for item in app.text_input] == ["E-Mail-Adresse", "Anmeldecode"]
    api.assert_not_called()


def test_identity_is_verified_once_per_run_and_never_reused_across_runs(db, monkeypatch):
    """Four callers in one rerun share one provider lookup; a later rerun re-verifies."""
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    api = Mock(return_value=user())
    monkeypatch.setattr(auth, "api", api)

    app = AppTest.from_string(
        "import scope_auth, streamlit as st\n"
        "st.session_state['scope_access_token']='test-token'\n"
        "for _ in range(4):\n"
        f"    scope_auth.current({db!r})\n"
    )
    app.session_state["scope_access_token"] = "test-token"
    app.run()
    assert not app.exception
    assert api.call_count == 1

    # A snapshot left over from an earlier run must not authorize a later one.
    # gate() clears the memo, so a revoked member is rejected on the next run
    # instead of reusing the identity that was cached before the revocation.
    api.reset_mock()
    auth._verified_identities["test-token"] = {"role": "Inhaber", "id": 1}
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scope_access")
    app2 = AppTest.from_string(
        "import scope_auth, streamlit as st\n"
        "st.session_state['scope_access_token']='test-token'\n"
        f"scope_auth.gate({db!r})\n"
    )
    app2.session_state["scope_access_token"] = "test-token"
    app2.run()
    assert not app2.exception
    # The stale snapshot was discarded, so the run verified the token again
    # against the provider and then logged the now-unauthorized session out.
    assert [call.args[0] for call in api.call_args_list] == ["user", "logout"]
    assert "test-token" not in auth._verified_identities


def test_auth_transport_rejects_redirects_and_redacts_errors(monkeypatch):
    monkeypatch.setenv("SCOPE_SUPABASE_URL", "https://scope.supabase.co")
    monkeypatch.setenv("SCOPE_SUPABASE_ANON_KEY", "test-key")
    opener = Mock()
    opener.open.return_value = io.BytesIO(b'{"id":"u"}')
    monkeypatch.setattr(auth, "build_opener", lambda handler: opener)
    assert auth.api("user", token="test-token") == {"id": "u"}
    assert opener.open.call_args.args[0].full_url == "https://scope.supabase.co/auth/v1/user"
    assert auth._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.example") is None
    opener.open.side_effect = OSError("secret-test-token")
    with pytest.raises(auth.AuthError) as error:
        auth.api("user", token="test-token")
    assert "secret-test-token" not in str(error.value)


def _http_error(code, body=b""):
    return HTTPError("https://scope.supabase.co/auth/v1/user", code, "error", {}, io.BytesIO(body))


def _provider_opener(monkeypatch, side_effect):
    monkeypatch.setenv("SCOPE_SUPABASE_URL", "https://scope.supabase.co")
    monkeypatch.setenv("SCOPE_SUPABASE_ANON_KEY", "test-key")
    opener = Mock()
    opener.open.side_effect = side_effect
    monkeypatch.setattr(auth, "build_opener", lambda handler: opener)
    return opener


def test_http_expired_jwt_is_typed_and_never_leaks_provider_body(monkeypatch):
    _provider_opener(monkeypatch, _http_error(
        403, b'{"code":403,"error_code":"session_expired","msg":"secret-test-token is expired"}'))
    with pytest.raises(auth.AuthSessionExpired) as error:
        auth.api("user", token="test-token")
    assert "abgelaufen" in str(error.value)
    assert "secret-test-token" not in str(error.value)
    assert isinstance(error.value, auth.AuthError)


def test_http_invalid_token_is_invalid_session_not_expired_or_transient(monkeypatch):
    for body in [b'{"code":401,"error_code":"bad_jwt","msg":"secret-test-token"}',
                 b'{"code":403,"msg":"secret-test-token"}',
                 b'not json at all']:
        _provider_opener(monkeypatch, _http_error(401, body))
        with pytest.raises(auth.AuthError) as error:
            auth.api("user", token="test-token")
        assert type(error.value) is auth.AuthError
        assert "secret-test-token" not in str(error.value)


def test_http_5xx_429_and_network_failures_are_transient(monkeypatch):
    for failure in [_http_error(500), _http_error(503, b'{"msg":"secret-test-token"}'),
                    _http_error(429), URLError("secret-test-token"), OSError("down")]:
        _provider_opener(monkeypatch, failure)
        with pytest.raises(auth.AuthTransientError) as error:
            auth.api("user", token="test-token")
        assert "erneut versuchen" in str(error.value)
        assert "secret-test-token" not in str(error.value)


def _gate_app(db):
    return AppTest.from_string(
        f"import scope_auth\nscope_auth.gate({db!r})\nimport streamlit as st\nst.write('PRIVATE DATA')")


def test_gate_keeps_session_and_blocks_content_on_transient_failure(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    api = Mock(side_effect=auth.AuthTransientError(
        "Anmeldedienst ist vorübergehend nicht erreichbar. Bitte erneut versuchen."))
    monkeypatch.setattr(auth, "api", api)
    app = _gate_app(db)
    app.session_state["scope_access_token"] = "test-token"
    app.run()
    assert not app.exception
    assert not app.markdown  # Protected content is never rendered.
    assert app.session_state["scope_access_token"] == "test-token"  # Token retained.
    assert "nicht erreichbar" in app.error[0].value
    # The retry button re-runs the gate; once the provider recovers, access resumes.
    api.side_effect = lambda endpoint, payload=None, token=None, method=None: user()
    next(button for button in app.button if button.label == "Erneut versuchen").click().run()
    assert not app.exception
    assert any(item.value == "PRIVATE DATA" for item in app.markdown)


def test_gate_clears_expired_session_without_provider_logout(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    calls = []

    def api(endpoint, payload=None, token=None, method=None):
        calls.append(endpoint)
        if endpoint == "user":
            raise auth.AuthSessionExpired("Sitzung abgelaufen. Bitte erneut anmelden.")
        return {}
    monkeypatch.setattr(auth, "api", api)
    app = _gate_app(db)
    app.session_state["scope_access_token"] = "test-token"
    app.run()
    assert not app.exception
    assert "scope_access_token" not in app.session_state
    assert calls == ["user"]  # No pointless logout call for a dead token.
    assert "abgelaufen" in app.warning[0].value
    assert not app.markdown


def test_gate_reports_revoked_access_and_clears_session(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    calls = []

    def api(endpoint, payload=None, token=None, method=None):
        calls.append(endpoint)
        return user() if endpoint == "user" else {}
    monkeypatch.setattr(auth, "api", api)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scope_access")
    app = _gate_app(db)
    app.session_state["scope_access_token"] = "test-token"
    app.run()
    assert not app.exception
    assert "scope_access_token" not in app.session_state
    assert calls == ["user", "logout"]  # Live provider session is still closed.
    assert "entzogen" in app.warning[0].value
    assert not app.markdown


def test_gate_clears_invalid_session_with_accurate_notice(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    api = Mock(side_effect=auth.AuthError("Anmeldung konnte nicht bestätigt werden. Bitte erneut versuchen."))
    monkeypatch.setattr(auth, "api", api)
    app = _gate_app(db)
    app.session_state["scope_access_token"] = "test-token"
    app.run()
    assert not app.exception
    assert "scope_access_token" not in app.session_state
    assert "nicht mehr gültig" in app.warning[0].value
    assert "abgelaufen" not in app.warning[0].value
    assert not app.markdown


def test_wrong_codes_are_rate_limited_across_sessions(db, monkeypatch):
    api = Mock(side_effect=auth.AuthError("Invalid code"))
    monkeypatch.setattr(auth, "api", api)
    for _ in range(6):
        with pytest.raises(auth.AuthError):
            auth.verify_code("owner@example.com", "123456", db)
    assert api.call_count == 5


def test_member_removal_revokes_old_session(db, monkeypatch):
    auth.verified_member(user(), db, bind=True)
    monkeypatch.setattr(auth, "current", lambda db=None: {"role": "Inhaber", "id": 1})
    member_id = organisation.invite_member("member@example.com", db=db)
    member_user = user("member@example.com", "member-2")
    auth.verified_member(member_user, db, bind=True)
    assert organisation.remove_member(member_id, db)
    with pytest.raises(auth.AuthError):
        auth.verified_member(member_user, db)
    new_id = organisation.invite_member("member@example.com", db=db)
    assert new_id != member_id
    assert auth.verified_member(member_user, db, bind=True)["id"] == new_id


def test_invitation_notification_is_deduplicated_and_revocable(db, monkeypatch):
    import email_outbox
    monkeypatch.setattr(auth, "current", lambda db=None: {"role": "Inhaber", "id": 1})
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "scope@example.com")
    monkeypatch.setenv("SCOPE_PUBLIC_URL", "https://scope.example.com")
    send = Mock(return_value="provider-id")
    monkeypatch.setattr(email_outbox, "send_email", send)
    member_id = organisation.invite_member("member@example.com", db=db)
    assert organisation.send_invitation(member_id, db) == "accepted"
    organisation.resend_invite(member_id, db)
    assert organisation.send_invitation(member_id, db) == "accepted"
    assert send.call_count == 1
    assert "test-key" not in send.call_args.kwargs["text"]
    organisation.revoke_invite(member_id, db)
    with pytest.raises(auth.AuthError):
        auth.verified_member(user("member@example.com", "member-2"), db, bind=True)


def test_stale_owner_authorization_is_rechecked_inside_write_transaction(db, monkeypatch):
    monkeypatch.setattr(auth, "current", lambda db=None: {"role": "Inhaber", "id": 1})
    with sqlite3.connect(db) as con:
        con.execute("UPDATE organisation_members SET role='Leseweise' WHERE id=1")
    with pytest.raises(auth.AuthError):
        organisation.invite_member("new@example.com", db=db)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM organisation_members").fetchone()[0] == 1


def test_login_and_logout_through_widgets_clear_private_session(db, monkeypatch):
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    def api(endpoint, payload=None, token=None):
        if endpoint == "verify":
            return {"access_token": "test-session-token"}
        if endpoint == "user":
            return user()
        return {}
    monkeypatch.setattr(auth, "api", api)
    app = AppTest.from_string(
        f"import scope_auth\nimport streamlit as st\nscope_auth.gate({db!r})\n"
        "st.write('PRIVATE DATA')\nst.session_state['private_draft']='draft'\n"
        "if st.button('Logout', key='qa_logout'):\n"
        "    scope_auth.logout()\n    st.rerun()\n"
    ).run()
    app.text_input[0].set_value("owner@example.com")
    app.text_input[1].set_value("123456")
    next(button for button in app.button if button.label == "Anmelden").click().run()
    assert not app.exception
    assert app.session_state["scope_access_token"] == "test-session-token"
    assert any(item.value == "PRIVATE DATA" for item in app.markdown)
    app.button(key="qa_logout").click().run()
    assert not app.exception
    assert "scope_access_token" not in app.session_state
    assert "private_draft" not in app.session_state
    assert not app.markdown


def test_revoked_invite_is_flagged_and_reinvite_regrants(db, monkeypatch):
    import time
    monkeypatch.setattr(auth, "current", lambda db=None: {"role": "Inhaber", "id": 1})
    member_id = organisation.invite_member("member@example.com", db=db)
    assert organisation.load_members(db)[1]["revoked"] is False
    organisation.revoke_invite(member_id, db)
    assert organisation.load_members(db)[1]["revoked"] is True
    organisation.resend_invite(member_id, db)
    assert organisation.load_members(db)[1]["revoked"] is False
    with sqlite3.connect(db) as con:
        granted_until = con.execute("SELECT invited_until FROM scope_access WHERE member_id=?", (member_id,)).fetchone()[0]
    assert granted_until > time.time()


def test_team_dialog_uses_personal_wording_and_revoked_invite_offers_reinvite_only(db, monkeypatch):
    import email_outbox
    import paths
    monkeypatch.setattr(auth, "current", lambda db=None: {"role": "Inhaber", "id": 1})
    monkeypatch.setattr(paths, "DB", db)
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "scope@example.com")
    monkeypatch.setenv("SCOPE_PUBLIC_URL", "https://scope.example.com")
    send = Mock(return_value="provider-id")
    monkeypatch.setattr(email_outbox, "send_email", send)
    member_id = organisation.invite_member("member@example.com", db=db)

    app = AppTest.from_string(
        "import organisation, streamlit as st\n"
        f"st.session_state[{organisation.DIALOG_OPEN!r}] = True\n"
        f"st.session_state[{organisation.DIALOG_VIEW!r}] = 'team'\n"
        "organisation.open_if_requested('2026-09-11')\n"
    ).run()
    assert not app.exception
    html = " ".join(node.proto.body for node in app.get("html"))
    assert "Persönliche Konten" in html
    assert "Gemeinsamer Zugang" not in html
    assert "benötigt persönliche Benutzerkonten" not in html
    assert "Anmeldecode" in html
    assert html.count("Einladung offen") == 2
    keys = {button.key for button in app.button}
    assert {f"org_resend_{member_id}", f"org_revoke_{member_id}"} <= keys
    assert f"org_reinvite_{member_id}" not in keys

    organisation.revoke_invite(member_id, db)
    app.run()
    assert not app.exception
    keys = {button.key for button in app.button}
    assert f"org_reinvite_{member_id}" in keys
    assert not keys & {f"org_resend_{member_id}", f"org_revoke_{member_id}"}
    # The never-logged-in owner keeps its open badge; the revoked row loses it.
    html = " ".join(node.proto.body for node in app.get("html"))
    assert html.count("Einladung offen") == 1

    app.button(key=f"org_reinvite_{member_id}").click().run()
    assert not app.exception
    member = organisation.load_members(db)[1]
    assert member["revoked"] is False
    assert member["activity"] == "E-Mail von Resend angenommen"
    assert send.call_count == 1
    keys = {button.key for button in app.button}
    assert {f"org_resend_{member_id}", f"org_revoke_{member_id}"} <= keys
    assert f"org_reinvite_{member_id}" not in keys


@pytest.mark.parametrize('body', [b'{"error_code":[]}', b'{"error_code":{}}', b'{"error_code":null}'])
def test_malformed_provider_error_code_is_safe(body):
    from urllib.error import HTTPError
    error = HTTPError('https://scope.supabase.co/auth/v1/user', 401, 'error', {}, io.BytesIO(body))
    assert type(auth._http_error(error)) is auth.AuthError
