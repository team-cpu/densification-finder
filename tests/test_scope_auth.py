import io
import json
import sqlite3
from unittest.mock import Mock, call
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
    monkeypatch.setenv("SCOPE_NORMIQ_AUTH_URL", "https://normiq.example.com")
    monkeypatch.delenv("SCOPE_NORMIQ_PROVISIONING_SECRET", raising=False)
    auth.bootstrap_owner(database)
    return database


PROVISIONING_SECRET = "scope-provisioning-test-secret-0123456789abcdef"


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
    normiq_api = Mock(return_value={})
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    auth.request_code("unknown@example.com", db)
    normiq_api.assert_not_called()
    auth.request_code("owner@example.com", db)
    auth.request_code("owner@example.com", db)
    assert normiq_api.call_count == 1


def test_passcode_request_uses_normiq_api_with_signup_disabled(db, monkeypatch):
    """Login codes come from Normiq's own passcode API: the request must use
    `isSignup: false`, which only permits existing Normiq users, so Scope can
    never silently register strangers in Normiq's auth directory."""
    normiq_api = Mock(return_value={})
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    auth.request_code("owner@example.com", db)
    assert normiq_api.call_args_list == [
        call("request-passcode", {"email": "owner@example.com", "isSignup": False})
    ]


def test_first_code_provisions_identity_when_configured(db, monkeypatch):
    """Phase 2: an invited member without a Normiq account gets an identity
    from Normiq's provisioning endpoint first — never via `isSignup: true`,
    which would create a regular Normiq customer account with meeting notes."""
    monkeypatch.setenv("SCOPE_NORMIQ_PROVISIONING_SECRET", PROVISIONING_SECRET)
    normiq_api = Mock(return_value={})
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    auth.request_code("owner@example.com", db)
    assert normiq_api.call_args_list == [
        call("scope/provision", {"email": "owner@example.com"}),
        call("request-passcode", {"email": "owner@example.com", "isSignup": False}),
    ]


def test_bound_member_is_never_provisioned_again(db, monkeypatch):
    monkeypatch.setenv("SCOPE_NORMIQ_PROVISIONING_SECRET", PROVISIONING_SECRET)
    auth.verified_member(user(), db, bind=True)
    normiq_api = Mock(return_value={})
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    auth.request_code("owner@example.com", db)
    assert normiq_api.call_args_list == [
        call("request-passcode", {"email": "owner@example.com", "isSignup": False})
    ]


def test_uninvited_expired_and_revoked_emails_are_never_provisioned(db, monkeypatch):
    monkeypatch.setenv("SCOPE_NORMIQ_PROVISIONING_SECRET", PROVISIONING_SECRET)
    monkeypatch.setattr(auth, "current", lambda db=None: {"role": "Inhaber", "id": 1})
    expired = organisation.invite_member("expired@example.com", db=db)
    revoked = organisation.invite_member("revoked@example.com", db=db)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE scope_access SET invited_until=0 WHERE member_id=?", (expired,))
    organisation.revoke_invite(revoked, db)
    normiq_api = Mock(return_value={})
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    for email in ["stranger@example.com", "expired@example.com", "revoked@example.com"]:
        auth.request_code(email, db)
    normiq_api.assert_not_called()


def test_failed_provisioning_sends_no_code(db, monkeypatch):
    monkeypatch.setenv("SCOPE_NORMIQ_PROVISIONING_SECRET", PROVISIONING_SECRET)
    normiq_api = Mock(side_effect=auth.AuthTransientError("down"))
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    with pytest.raises(auth.AuthTransientError):
        auth.request_code("owner@example.com", db)
    assert normiq_api.call_args_list == [call("scope/provision", {"email": "owner@example.com"})]


@pytest.mark.parametrize("raw", ["short", "x" * 31, "with space " + "x" * 32, "x" * 32 + "é", "x" * 513])
def test_malformed_provisioning_secret_is_a_config_fault(db, monkeypatch, raw):
    monkeypatch.setenv("SCOPE_NORMIQ_PROVISIONING_SECRET", raw)
    normiq_api = Mock(return_value={})
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    with pytest.raises(auth.AuthError) as error:
        auth.request_code("owner@example.com", db)
    assert "nicht konfiguriert" in str(error.value)
    normiq_api.assert_not_called()


def test_shared_provider_identity_without_scope_access_remains_denied(db):
    """A valid Normiq Auth identity is authenticated, but without a local
    `scope_access` invitation it must never enter Scope."""
    normiq_user = user(
        "member@example.com", uid="normiq-user-0000-0000-000000000001"
    )
    with pytest.raises(auth.AuthError):
        auth.verified_member(normiq_user, db, bind=True)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO organisation_members(email,role,status) "
            "VALUES ('member@example.com','Bearbeiter','pending')"
        )
        member_id = con.execute(
            "SELECT id FROM organisation_members WHERE email='member@example.com'"
        ).fetchone()[0]
        auth.grant(con, member_id)
    bound = auth.verified_member(normiq_user, db, bind=True)
    assert bound["role"] == "Bearbeiter"
    with sqlite3.connect(db) as con:
        bound_id = con.execute("SELECT auth_id FROM scope_access a JOIN organisation_members m "
                               "ON m.id=a.member_id WHERE m.email='member@example.com'").fetchone()[0]
    assert bound_id == normiq_user["id"]  # Immutable provider UUID is bound.
    with pytest.raises(auth.AuthError):
        auth.verified_member(user("member@example.com", uid="different-normiq-uuid"), db)


def test_verification_checks_provider_user_before_binding(db, monkeypatch):
    normiq_api = Mock(return_value={"access_token": "test-token"})
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    api = Mock(return_value=user())
    monkeypatch.setattr(auth, "api", api)
    assert auth.verify_code("owner@example.com", "123456", db) == "test-token"
    assert normiq_api.call_args_list == [
        call("verify-passcode", {"email": "owner@example.com", "passcode": "123456", "rememberMe": False})
    ]
    assert api.call_args_list[0].args == ("user",)
    assert api.call_args_list[0].kwargs == {"token": "test-token"}
    # Only the locally invited member is bound: an authenticated Normiq
    # identity without `scope_access` never reaches token validation.
    api.reset_mock()
    with pytest.raises(auth.AuthError):
        auth.verify_code("outsider@example.com", "123456", db)
    api.assert_not_called()
    normiq_api.assert_called_once()


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


#: Verbatim answer of the live provider (Supabase Auth) to GET /auth/v1/user
#: with a real session's access token 15 s and 75 s after its `exp` claim,
#: captured 2026-09-18 (docs/2026-09-18-jwt-expiry-real-provider.md). A plain
#: JWT expiry is reported as bad_jwt, never as session_expired.
REAL_EXPIRED_JWT_BODY = (b'{"code":403,"error_code":"bad_jwt","msg":"invalid JWT: unable to parse or '
                         b'verify signature, token has invalid claims: token is expired"}')


def test_http_real_expired_jwt_answer_is_typed_expired(monkeypatch):
    _provider_opener(monkeypatch, _http_error(403, REAL_EXPIRED_JWT_BODY))
    with pytest.raises(auth.AuthSessionExpired) as error:
        auth.api("user", token="test-token")
    assert "abgelaufen" in str(error.value)
    assert "invalid JWT" not in str(error.value)  # Provider wording is matched, never shown.


def test_http_invalid_token_is_invalid_session_not_expired_or_transient(monkeypatch):
    for body in [b'{"code":401,"error_code":"bad_jwt","msg":"secret-test-token"}',
                 # A tampered token is bad_jwt too; only the expiry wording marks expiry.
                 b'{"code":403,"error_code":"bad_jwt","msg":"invalid JWT: unable to parse or verify '
                 b'signature, signature is invalid"}',
                 # Expiry wording without the bad_jwt code is not expiry evidence.
                 b'{"code":403,"msg":"token has invalid claims: token is expired"}',
                 b'{"code":403,"error_code":"session_not_found","msg":"token is expired"}',
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
    normiq_api = Mock(side_effect=auth.AuthError("Invalid code"))
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    for _ in range(6):
        with pytest.raises(auth.AuthError):
            auth.verify_code("owner@example.com", "123456", db)
    assert normiq_api.call_count == 5


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
    monkeypatch.setattr(auth, "normiq_api",
                        lambda action, payload: {"access_token": "test-session-token"})
    def api(endpoint, payload=None, token=None, method=None):
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


# --- Normiq custom passcode API ----------------------------------------------


@pytest.mark.parametrize('raw', [
    "",
    "not a url",
    "ftp://normiq.example.com",
    "http://normiq.example.com",                      # plain http off localhost
    "http://192.168.1.10:3000",                       # plain http off localhost
    "https://normiq.example.com/path",
    "https://normiq.example.com/?x=1",
    "https://normiq.example.com/#frag",
    "https://user@normiq.example.com",                # userinfo
    "https://user:pw@normiq.example.com",
    "https://normiq.example.com:444",
    "http://localhost",                               # local dev needs a port
    "http://127.0.0.1:notaport",
    "http://127.0.0.1:99999",
    "https://.example.com",
])
def test_invalid_normiq_auth_url_is_rejected(db, monkeypatch, raw):
    monkeypatch.setenv("SCOPE_NORMIQ_AUTH_URL", raw)
    with pytest.raises(auth.AuthError):
        auth.normiq_configuration()
    with pytest.raises(auth.AuthError):
        auth.request_code("owner@example.com", db)


@pytest.mark.parametrize('raw,origin', [
    ("https://normiq.example.com", "https://normiq.example.com"),
    ("https://normiq.example.com/", "https://normiq.example.com"),
    ("https://Normiq.Example.COM", "https://normiq.example.com"),
    ("http://127.0.0.1:3000", "http://127.0.0.1:3000"),
    ("http://localhost:3000", "http://localhost:3000"),
])
def test_valid_normiq_auth_url_forms(db, monkeypatch, raw, origin):
    monkeypatch.setenv("SCOPE_NORMIQ_AUTH_URL", raw)
    assert auth.normiq_configuration() == origin


def _normiq_opener(monkeypatch, response, url="https://normiq.example.com"):
    monkeypatch.setenv("SCOPE_NORMIQ_AUTH_URL", url)
    opener = Mock()
    if isinstance(response, Exception):
        opener.open.side_effect = response
    else:
        opener.open.return_value = response
    monkeypatch.setattr(auth, "build_opener", lambda handler: opener)
    return opener


def test_normiq_api_posts_json_to_passcode_endpoint(monkeypatch):
    opener = _normiq_opener(monkeypatch, io.BytesIO(b'{"access_token":"t"}'))
    assert auth.normiq_api("verify-passcode", {"email": "e@x.ch", "passcode": "123456",
                                               "rememberMe": False}) == {"access_token": "t"}
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://normiq.example.com/api/auth/verify-passcode"
    assert request.method == "POST"
    assert json.loads(request.data) == {"email": "e@x.ch", "passcode": "123456", "rememberMe": False}
    assert request.headers["Content-type"] == "application/json"


def test_normiq_api_rejects_unknown_action(monkeypatch):
    _normiq_opener(monkeypatch, io.BytesIO(b"{}"))
    with pytest.raises(auth.AuthError):
        auth.normiq_api("anything-else", {})


def test_provisioning_secret_travels_only_on_the_provision_call(monkeypatch):
    monkeypatch.setenv("SCOPE_NORMIQ_PROVISIONING_SECRET", PROVISIONING_SECRET)
    opener = _normiq_opener(monkeypatch, io.BytesIO(b'{"ok":true}'))
    assert auth.normiq_api("scope/provision", {"email": "e@x.ch"}) == {"ok": True}
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://normiq.example.com/api/auth/scope/provision"
    assert request.method == "POST"
    assert json.loads(request.data) == {"email": "e@x.ch"}
    assert request.headers["Authorization"] == f"Bearer {PROVISIONING_SECRET}"

    for action, payload in [("request-passcode", {"email": "e@x.ch", "isSignup": False}),
                            ("verify-passcode", {"email": "e@x.ch", "passcode": "123456", "rememberMe": False})]:
        opener = _normiq_opener(monkeypatch, io.BytesIO(b"{}"))
        auth.normiq_api(action, payload)
        request = opener.open.call_args.args[0]
        assert "Authorization" not in request.headers
        assert PROVISIONING_SECRET not in json.dumps(request.header_items())


def test_provision_without_secret_is_refused_before_any_request(monkeypatch):
    monkeypatch.delenv("SCOPE_NORMIQ_PROVISIONING_SECRET", raising=False)
    opener = _normiq_opener(monkeypatch, io.BytesIO(b"{}"))
    with pytest.raises(auth.AuthError):
        auth.normiq_api("scope/provision", {"email": "e@x.ch"})
    opener.open.assert_not_called()


def test_provision_errors_are_generic_and_never_echo_the_secret(monkeypatch):
    monkeypatch.setenv("SCOPE_NORMIQ_PROVISIONING_SECRET", PROVISIONING_SECRET)
    for failure in [_http_error(401, f'{{"error":"{PROVISIONING_SECRET}"}}'.encode()),
                    _http_error(404, b'{"error":"Not found"}'),
                    _http_error(400, b'{"error":"A valid email is required"}')]:
        _normiq_opener(monkeypatch, failure)
        with pytest.raises(auth.AuthError) as error:
            auth.normiq_api("scope/provision", {"email": "e@x.ch"})
        assert type(error.value) is auth.AuthError
        assert PROVISIONING_SECRET not in str(error.value)
    for failure in [_http_error(500), _http_error(429), URLError(PROVISIONING_SECRET)]:
        _normiq_opener(monkeypatch, failure)
        with pytest.raises(auth.AuthTransientError) as error:
            auth.normiq_api("scope/provision", {"email": "e@x.ch"})
        assert PROVISIONING_SECRET not in str(error.value)


def test_normiq_verify_missing_access_token_is_invalid(db, monkeypatch):
    normiq_api = Mock(return_value={"unexpected": "shape"})
    monkeypatch.setattr(auth, "normiq_api", normiq_api)
    api = Mock()
    monkeypatch.setattr(auth, "api", api)
    with pytest.raises(auth.AuthError):
        auth.verify_code("owner@example.com", "123456", db)
    api.assert_not_called()  # A token that was never issued is never validated.


def test_normiq_verify_error_is_generic_and_never_leaks_body(db, monkeypatch):
    for failure in [_http_error(400, b'{"error":"user not found: secret-test-token"}'),
                    _http_error(401, b'{"error":"secret-test-token"}'),
                    _http_error(410, b'{"error":"expired secret-test-token"}')]:
        _normiq_opener(monkeypatch, failure)
        with pytest.raises(auth.AuthError) as error:
            auth.normiq_api("verify-passcode", {"email": "e@x.ch", "passcode": "123456",
                                                "rememberMe": False})
        assert type(error.value) is auth.AuthError
        assert "nicht gültig oder abgelaufen" in str(error.value)
        assert "secret-test-token" not in str(error.value)


def test_normiq_request_error_is_generic_for_unknown_users(db, monkeypatch):
    """Normiq only permits existing users; the failure must not reveal whether
    an email is a Normiq account (no enumeration oracle)."""
    _normiq_opener(monkeypatch, _http_error(404, b'{"error":"no such Normiq user"}'))
    with pytest.raises(auth.AuthError) as error:
        auth.normiq_api("request-passcode", {"email": "e@x.ch", "isSignup": False})
    assert type(error.value) is auth.AuthError
    assert "Normiq" not in str(error.value)
    assert "no such" not in str(error.value)


def test_normiq_429_5xx_and_network_failures_are_transient(monkeypatch):
    for failure in [_http_error(429), _http_error(500, b'{"msg":"secret-test-token"}'),
                    _http_error(503), URLError("secret-test-token"), OSError("down")]:
        _normiq_opener(monkeypatch, failure)
        with pytest.raises(auth.AuthTransientError) as error:
            auth.normiq_api("request-passcode", {"email": "e@x.ch", "isSignup": False})
        assert "erneut versuchen" in str(error.value)
        assert "secret-test-token" not in str(error.value)


def test_normiq_api_never_follows_redirects(monkeypatch):
    _normiq_opener(monkeypatch, _http_error(307, b""))
    with pytest.raises(auth.AuthError) as error:
        auth.normiq_api("request-passcode", {"email": "e@x.ch", "isSignup": False})
    assert type(error.value) is auth.AuthError  # A redirect is a config fault, not transient.
    assert "307" not in str(error.value)
    # _NoRedirect is installed, so urllib refuses the redirect before it fires.
    assert auth._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.example") is None
