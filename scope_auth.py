"""Separate Scope identities, verified by the configured Scope Supabase project.

Legacy shared access is retained only when personal mode is explicitly disabled.
The local access table, not Supabase signup or user metadata, grants Scope access.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import sqlite3
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import HTTPRedirectHandler, Request, build_opener

import streamlit as st
import login_page
import paths


class AuthError(ValueError):
    pass


class AuthTransientError(AuthError):
    """Provider unavailable; retain the session but do not authorize access."""


class AuthSessionExpired(AuthError):
    """Provider explicitly reports the token/session as expired."""


class AuthAccessRevoked(AuthError):
    """The local access table no longer grants this identity access."""


#: Identity verified from the provider token, kept for one script run only.
#: Streamlit reruns the whole script on every interaction and the header, the
#: member list and each write guard all need the same identity, so without this
#: memo a single rerun costs several provider round trips. `gate()` empties it
#: at the start of every run, so an entry never outlives the run that made it.
#:
#: This memo covers identity only. It does not re-read local roles, so a member
#: revoked mid-run still sees the page that is already open, exactly as
#: `docs/scope-accounts.md` describes. Every write calls `check_transaction`,
#: which re-checks role and membership under the write's own lock.
_verified_identities: dict[str, dict] = {}


def enabled() -> bool:
    mode = os.environ.get("SCOPE_AUTH_MODE", "shared")
    if mode not in ("shared", "personal"):
        raise AuthError("Ungültiger Anmeldemodus.")
    return mode == "personal"


def configuration() -> tuple[str, str]:
    url = os.environ.get("SCOPE_SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SCOPE_SUPABASE_ANON_KEY", "")
    if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url) or not key or any(c in key for c in "\r\n"):
        raise AuthError("Scope-Anmeldung ist noch nicht konfiguriert.")
    return url, key


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


#: The only provider paths the app may call: e-mail code login, session, and
#: the TOTP factor lifecycle (enrol, challenge, verify, unenrol). Factor ids
#: are provider UUIDs; anything else is refused before a request is built.
_ENDPOINTS = re.compile(r"(otp|verify|user|logout|factors|factors/[A-Za-z0-9-]{1,64}(/challenge|/verify)?)")


#: Provider (GoTrue) error codes the app may act on. Only allowlisted codes
#: from the provider's error JSON are interpreted; the body's raw message is
#: never surfaced, since it can echo request data such as the token itself.
_EXPIRED_CODES = frozenset({"session_expired"})


def _provider_code(error: HTTPError) -> str | None:
    """The allowlisted provider error code from a bounded, JSON-only body."""
    try:
        raw = error.read(1 << 16)  # Error bodies are tiny; cap regardless.
        data = json.loads(raw) if raw else {}
    except (OSError, HTTPException, ValueError):
        return None
    code = data.get("error_code") if isinstance(data, dict) else None
    return code if isinstance(code, str) and code in _EXPIRED_CODES else None


def _http_error(error: HTTPError) -> AuthError:
    """Classify a provider HTTP answer without ever surfacing its body."""
    if error.code == 429 or error.code >= 500:
        return AuthTransientError("Anmeldedienst ist vorübergehend nicht erreichbar. Bitte erneut versuchen.")
    # Expiry is marked only on explicit provider evidence; every other 4xx is
    # an invalid session, never a transient fault and never authenticated.
    if error.code in (401, 403) and _provider_code(error) == "session_expired":
        return AuthSessionExpired("Sitzung abgelaufen. Bitte erneut anmelden.")
    return AuthError("Anmeldung konnte nicht bestätigt werden. Bitte erneut versuchen.")


def api(endpoint: str, payload: dict | None = None, token: str | None = None,
        method: str | None = None) -> dict:
    url, key = configuration()
    if not _ENDPOINTS.fullmatch(endpoint) or method not in (None, "GET", "POST", "DELETE"):
        raise AuthError("Ungültige Authentifizierungsanfrage.")
    headers = {"apikey": key, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{url}/auth/v1/{endpoint}", headers=headers,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      method=method or ("GET" if endpoint == "user" else "POST"))
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            # Session and user records are a few KB; a TOTP enrolment answer
            # carries the provider's QR code as inline SVG, well past 64 KB.
            raw = response.read(1 << 20)
        result = json.loads(raw) if raw else {}
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except HTTPError as error:
        raise _http_error(error) from None
    except (URLError, OSError, HTTPException, ValueError):
        raise AuthTransientError("Verbindung zum Anmeldedienst fehlgeschlagen. Bitte erneut versuchen.") from None


def schema(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE IF NOT EXISTS scope_access (
            member_id INTEGER PRIMARY KEY,
            auth_id TEXT UNIQUE,
            invited_until REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scope_auth_setup (id INTEGER PRIMARY KEY CHECK(id=1));
        CREATE TABLE IF NOT EXISTS scope_auth_requests (email TEXT PRIMARY KEY, requested_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS scope_auth_verifications (
            email TEXT PRIMARY KEY, window_start REAL NOT NULL, attempts INTEGER NOT NULL
        );
    """)


def clean_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 200 or not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", email):
        raise AuthError("Bitte eine gültige E-Mail-Adresse eingeben.")
    return email


def bootstrap_owner(db: str) -> None:
    """One-time grant from server configuration; never promote an arbitrary user."""
    with sqlite3.connect(db) as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM scope_auth_setup").fetchone():
            return
        email = clean_email(os.environ.get("SCOPE_OWNER_EMAIL", ""))
        con.execute("INSERT INTO organisation_members (name,email,role,status,activity) "
                    "VALUES (?,?,'Inhaber','pending','Scope-Konto') ON CONFLICT(email) DO UPDATE SET role='Inhaber'",
                    (email.split("@")[0], email))
        member_id = con.execute("SELECT id FROM organisation_members WHERE email=?", (email,)).fetchone()[0]
        grant(con, member_id)
        con.execute("INSERT INTO scope_auth_setup VALUES (1)")


def grant(con: sqlite3.Connection, member_id: int) -> None:
    con.execute("INSERT INTO scope_access(member_id,invited_until) VALUES (?,?) "
                "ON CONFLICT(member_id) DO UPDATE SET invited_until=excluded.invited_until "
                "WHERE scope_access.invited_until <= ? AND scope_access.auth_id IS NULL",
                (member_id, time.time() + 7 * 86400, time.time()))


def request_code(email: str, db: str) -> None:
    email = clean_email(email)
    now = time.time()
    with sqlite3.connect(db) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT a.auth_id,a.invited_until FROM scope_access a "
                          "JOIN organisation_members m ON m.id=a.member_id WHERE m.email=?", (email,)).fetchone()
        if not row or (not row[0] and row[1] <= now):
            return  # Same UI response for unknown/expired invitations.
        previous = con.execute("SELECT requested_at FROM scope_auth_requests WHERE email=?", (email,)).fetchone()
        if previous and now - previous[0] < 60:
            return
        con.execute("INSERT INTO scope_auth_requests VALUES (?,?) ON CONFLICT(email) "
                    "DO UPDATE SET requested_at=excluded.requested_at", (email, now))
    # Signup can create a provider identity, but never grants local access.
    api("otp", {"email": email, "create_user": True})


def verified_member(user: dict, db: str, *, bind: bool = False) -> dict:
    email = clean_email(str(user.get("email", "")))
    auth_id = user.get("id")
    if not auth_id or not user.get("email_confirmed_at"):
        raise AuthError("Kein gültiger Scope-Zugang.")
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT m.*,a.auth_id,a.invited_until FROM organisation_members m "
                          "JOIN scope_access a ON a.member_id=m.id WHERE m.email=?", (email,)).fetchone()
        if not row or (row["auth_id"] and row["auth_id"] != auth_id):
            raise AuthAccessRevoked("Kein gültiger Scope-Zugang.")
        if row["auth_id"] and row["status"] != "active":
            raise AuthAccessRevoked("Kein gültiger Scope-Zugang.")
        if not row["auth_id"]:
            if not bind or row["invited_until"] <= time.time():
                raise AuthError("Einladung ist nicht gültig.")
            con.execute("UPDATE scope_access SET auth_id=? WHERE member_id=?", (auth_id, row["id"]))
            con.execute("UPDATE organisation_members SET status='active',activity='Angemeldet' WHERE id=?", (row["id"],))
        member = dict(row)
    # TOTP factors come with the provider's user record, so the gate learns
    # whether a second factor exists without an extra round trip.
    totp = [f for f in (user.get("factors") or []) if isinstance(f, dict) and f.get("factor_type") == "totp"]
    member["mfa_factor"] = next((f["id"] for f in totp if f.get("status") == "verified" and f.get("id")), None)
    member["mfa_unverified"] = [f["id"] for f in totp if f.get("status") != "verified" and f.get("id")]
    return member


def _count_attempt(con: sqlite3.Connection, key: str, now: float) -> None:
    """Five attempts per five minutes per key, counted before the provider is asked."""
    attempt = con.execute("SELECT window_start,attempts FROM scope_auth_verifications WHERE email=?", (key,)).fetchone()
    if attempt and now - attempt[0] < 300 and attempt[1] >= 5:
        raise AuthError("Zu viele Versuche. Bitte in fünf Minuten erneut versuchen.")
    if not attempt or now - attempt[0] >= 300:
        con.execute("INSERT INTO scope_auth_verifications VALUES (?,?,1) ON CONFLICT(email) "
                    "DO UPDATE SET window_start=excluded.window_start,attempts=1", (key, now))
    else:
        con.execute("UPDATE scope_auth_verifications SET attempts=attempts+1 WHERE email=?", (key,))


def _clear_attempts(db: str, key: str) -> None:
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scope_auth_verifications WHERE email=?", (key,))


def verify_code(email: str, code: str, db: str) -> str:
    if not re.fullmatch(r"\d{6}", code):
        raise AuthError("Bitte den sechsstelligen Code eingeben.")
    email = clean_email(email)
    now = time.time()
    with sqlite3.connect(db) as con:
        con.execute("BEGIN IMMEDIATE")
        access = con.execute("SELECT a.auth_id,a.invited_until FROM scope_access a "
                             "JOIN organisation_members m ON m.id=a.member_id WHERE m.email=?", (email,)).fetchone()
        if not access or (not access[0] and access[1] <= now):
            raise AuthError("Anmeldecode ist nicht gültig oder abgelaufen.")
        _count_attempt(con, email, now)
    result = api("verify", {"email": email, "token": code, "type": "email"})
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise AuthError("Kein gültiger Scope-Zugang.")
    # Never decode an unverified JWT or trust profile metadata for permissions.
    verified_member(api("user", token=token), db, bind=True)
    _clear_attempts(db, email)
    return token


# --- Second factor (TOTP via the provider's MFA) -----------------------------
#
# The app never reads the token's `aal` claim. `scope_mfa` in session state is
# set only after the provider answered `factors/<id>/verify` with a new
# session, and that session's token replaces the one in session state.


def mfa_required(db: str) -> bool:
    """Owners switch enforcement in Einstellungen (`organisation_profile.enforce_2fa`)."""
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT enforce_2fa FROM organisation_profile WHERE id=1").fetchone()
    return bool(row and row[0])


def mfa_enrol(token: str, member: dict) -> dict:
    """Start a fresh TOTP enrolment; the secret is shown once and never stored."""
    for factor_id in member.get("mfa_unverified") or []:
        api(f"factors/{factor_id}", token=token, method="DELETE")
    result = api("factors", {"factor_type": "totp", "friendly_name": "Scope", "issuer": "Scope"}, token)
    totp = result.get("totp") if isinstance(result.get("totp"), dict) else {}
    factor_id, secret, uri, qr = result.get("id"), totp.get("secret"), totp.get("uri"), totp.get("qr_code")
    if not all(isinstance(v, str) and v for v in (factor_id, secret, uri)):
        raise AuthError("Zweiter Faktor konnte nicht eingerichtet werden. Bitte erneut versuchen.")
    return {"id": factor_id, "secret": secret, "uri": uri, "qr": qr if isinstance(qr, str) else ""}


def mfa_verify(token: str, factor_id: str, code: str, email: str, db: str) -> str:
    """Challenge and verify one TOTP code; return the provider's second-factor token."""
    if not re.fullmatch(r"\d{6}", code):
        raise AuthError("Bitte den sechsstelligen Code aus der Authenticator-App eingeben.")
    key = f"mfa:{email}"
    with sqlite3.connect(db) as con:
        con.execute("BEGIN IMMEDIATE")
        _count_attempt(con, key, time.time())
    challenge = api(f"factors/{factor_id}/challenge", {}, token)
    challenge_id = challenge.get("id")
    if not isinstance(challenge_id, str) or not challenge_id:
        raise AuthError("Zweiter Faktor konnte nicht geprüft werden. Bitte erneut versuchen.")
    try:
        session = api(f"factors/{factor_id}/verify", {"challenge_id": challenge_id, "code": code}, token)
    except AuthTransientError:
        raise  # A transport fault is not a wrong code; let the UI offer a retry.
    except AuthError:
        raise AuthError("Code ist nicht gültig. Bitte den aktuellen Code aus der App eingeben.") from None
    new_token = session.get("access_token")
    if not isinstance(new_token, str) or not new_token:
        raise AuthError("Code ist nicht gültig.")
    _clear_attempts(db, key)
    return new_token


def mfa_reset(db: str | None = None) -> None:
    """Unenrol the member's factor (needs the second-factor session); the gate enrols again."""
    member = current(db)
    token = st.session_state.get("scope_access_token")
    if member.get("mfa_factor") and token:
        api(f"factors/{member['mfa_factor']}", token=token, method="DELETE")
        _verified_identities.pop(token, None)
    st.session_state.pop("scope_mfa", None)
    st.session_state.pop("scope_mfa_enrol", None)


def _finish_second_factor(old_token: str, new_token: str) -> None:
    _verified_identities.pop(old_token, None)
    st.session_state.pop("scope_mfa_enrol", None)
    st.session_state.pop("scope_mfa_setup", None)
    st.session_state["scope_access_token"] = new_token
    st.session_state["scope_mfa"] = True
    st.rerun()


def _qr_svg(raw: str) -> str:
    """The provider's QR code as inline SVG, whether sent bare or as a data URL."""
    text = raw.strip()
    if text.startswith("data:"):
        header, _, body = text.partition(",")
        text = base64.b64decode(body).decode("utf-8", "replace") if ";base64" in header else unquote(body)
    start = text.find("<svg")
    return text[start:] if start >= 0 else ""


def _second_factor_gate(member: dict, db: str, *, required: bool = True) -> None:
    token = st.session_state["scope_access_token"]
    if member.get("mfa_factor"):
        with login_page.card("Geben Sie den Code aus Ihrer Authenticator-App ein.", title="Zweiter Faktor"):
            with st.form("scope_mfa_form", border=False):
                with st.container(key="scope_login_code"):
                    code = st.text_input("Code aus der App", type="password", max_chars=6, placeholder="••••••")
                confirm = st.form_submit_button("Bestätigen", type="primary", width="stretch")
            try:
                if confirm:
                    _finish_second_factor(token, mfa_verify(token, member["mfa_factor"], code, member["email"], db))
            except AuthError as error:
                st.error(str(error))
            _logout_link()
        st.stop()
    caption = ("Zweiter Faktor ist für alle Mitglieder erforderlich."
               if required else "Optional: Schützen Sie Ihr Konto zusätzlich mit einer Authenticator-App.")
    try:
        enrol = st.session_state.get("scope_mfa_enrol") or mfa_enrol(token, member)
    except AuthError as error:
        with login_page.card(caption, title="Zweiter Faktor einrichten"):
            st.error(str(error))
            if st.button("Erneut versuchen", key="scope_mfa_retry", type="primary", width="stretch"):
                st.rerun()
            _postpone_link(required)
            _logout_link()
        st.stop()
    st.session_state["scope_mfa_enrol"] = enrol
    with login_page.card(caption + " Scannen Sie den QR-Code mit einer Authenticator-App und geben Sie den angezeigten Code ein.",
                         title="Zweiter Faktor einrichten"):
        if svg := _qr_svg(enrol["qr"]):
            st.image(svg, width=168)
        grouped = " ".join(enrol["secret"][i:i + 4] for i in range(0, len(enrol["secret"]), 4))
        st.html('<p class="scope-login-hint">Manuell eingeben, falls Scannen nicht möglich ist:</p>'
                f'<p class="scope-login-key">{html.escape(grouped)}</p>')
        with st.form("scope_mfa_enrol_form", border=False):
            with st.container(key="scope_login_code"):
                code = st.text_input("Code aus der App", type="password", max_chars=6, placeholder="••••••")
            activate = st.form_submit_button("Aktivieren", type="primary", width="stretch")
        try:
            if activate:
                _finish_second_factor(token, mfa_verify(token, enrol["id"], code, member["email"], db))
        except AuthError as error:
            st.error(str(error))
        _postpone_link(required)
        _logout_link()
    st.stop()


def _postpone_link(required: bool) -> None:
    """A voluntary set-up can be left for later; an enforced one cannot."""
    if not required and st.button("Später", key="scope_mfa_later", width="stretch"):
        st.session_state.pop("scope_mfa_setup", None)
        st.session_state.pop("scope_mfa_enrol", None)
        st.rerun()


def _logout_link() -> None:
    if st.button("Abmelden", key="scope_mfa_logout", width="stretch"):
        logout()
        st.rerun()


def current(db: str | None = None) -> dict:
    token = st.session_state.get("scope_access_token")
    if not token:
        raise AuthError("Bitte erneut anmelden.")
    member = _verified_identities.get(token)
    if member is None:
        member = verified_member(api("user", token=token), db or paths.DB)
        _verified_identities[token] = member
    return member


def _require_second_factor(member: dict, required: bool) -> None:
    if (required or member.get("mfa_factor")) and not st.session_state.get("scope_mfa"):
        raise AuthError("Bitte zuerst den zweiten Faktor bestätigen.")


def require_owner(db: str | None = None) -> dict | None:
    if enabled():
        member = current(db)
        _require_second_factor(member, mfa_required(db or paths.DB))
        if member["role"] != "Inhaber":
            raise AuthError("Nur Inhaber können das Team und die Einstellungen ändern.")
        return member
    return None


def require_write(db: str | None = None) -> dict | None:
    if enabled():
        try:
            member = current(db)
            _require_second_factor(member, mfa_required(db or paths.DB))
            if member["role"] not in ("Inhaber", "Bearbeiter"):
                raise AuthError("Lesezugriff: Änderungen sind nicht erlaubt.")
            return member
        except AuthError as error:
            st.error(str(error))
            st.stop()
            raise error  # st.stop is a no-op outside a Streamlit script context.
    return None


def check_transaction(con: sqlite3.Connection, actor: dict | None, *, owner: bool = False) -> None:
    """Recheck local revocation/role under the same lock as the domain write."""
    if actor is None:
        return
    con.execute("BEGIN IMMEDIATE")
    policy = con.execute("SELECT enforce_2fa FROM organisation_profile WHERE id=1").fetchone()
    _require_second_factor(actor, bool(policy and policy[0]))
    row = con.execute("SELECT m.role FROM organisation_members m JOIN scope_access a "
                      "ON a.member_id=m.id WHERE m.id=?", (actor["id"],)).fetchone()
    allowed = ("Inhaber",) if owner else ("Inhaber", "Bearbeiter")
    if not row or row[0] not in allowed:
        raise AuthError("Berechtigung wurde geändert. Bitte die Seite neu laden.")


def _clear_local_session() -> None:
    """Drop all per-user drafts/data locally, without calling the provider."""
    token = st.session_state.get("scope_access_token")
    for key in list(st.session_state):
        del st.session_state[key]
    if token:
        _verified_identities.pop(token, None)


def logout() -> None:
    token = st.session_state.get("scope_access_token")
    # Clear all per-user drafts/data even if the provider is temporarily offline.
    _clear_local_session()
    if token:
        try:
            api("logout", {}, token)
        except AuthError:
            pass


def gate(db: str) -> None:
    _verified_identities.clear()
    try:
        configuration()
        bootstrap_owner(db)
    except AuthError as error:
        st.error(str(error))
        st.stop()
    notice = None
    if st.session_state.get("scope_access_token"):
        try:
            member = current(db)
        except AuthTransientError as error:
            # Temporary provider/network fault: keep token and session, block
            # protected rendering, and offer a retry instead of logging out.
            with login_page.card("Persönlicher Zugang für eingeladene Mitglieder."):
                st.error(str(error))
                if st.button("Erneut versuchen", key="scope_retry", type="primary", width="stretch"):
                    st.rerun()
            st.stop()
        except AuthSessionExpired as error:
            # The provider already invalidated the token, so no provider
            # logout is needed; only the local session is cleared.
            _clear_local_session()
            notice = str(error)
        except AuthAccessRevoked:
            logout()
            notice = "Ihr Zugang wurde entzogen. Bitte wenden Sie sich an die Inhaberschaft."
        except AuthError:
            logout()
            notice = "Sitzung ist nicht mehr gültig. Bitte erneut anmelden."
        else:
            # Required by the organisation, chosen by the member (a verified
            # factor is always challenged), or being set up from the menu.
            required = mfa_required(db)
            wanted = required or bool(member.get("mfa_factor")) or bool(st.session_state.get("scope_mfa_setup"))
            if wanted and not st.session_state.get("scope_mfa"):
                _second_factor_gate(member, db, required=required)
            if member["role"] == "Leseweise":
                st.info("Lesezugriff: Sie können Daten ansehen, aber keine Änderungen speichern.")
            return
    with login_page.card("Persönlicher Zugang für eingeladene Mitglieder."):
        if notice:
            st.warning(notice)
        with st.form("scope_login", border=False):
            email = st.text_input("E-Mail-Adresse", max_chars=200, placeholder="name@firma.ch")
            request = st.form_submit_button("Code anfordern", width="stretch")
            login_page.divider()
            with st.container(key="scope_login_code"):
                code = st.text_input("Anmeldecode", type="password", max_chars=6, placeholder="••••••")
            login_page.hint("Sechsstellig, gilt 10 Minuten.")
            verify = st.form_submit_button("Anmelden", type="primary", width="stretch")
        try:
            if request:
                request_code(email, db)
                st.info("Wenn ein gültiger Scope-Zugang besteht, erhalten Sie einen Anmeldecode per E-Mail.")
            if verify:
                token = verify_code(email, code, db)
                for key in list(st.session_state):
                    del st.session_state[key]
                st.session_state["scope_access_token"] = token
                st.rerun()
        except AuthError as error:
            st.error(str(error))
    st.stop()
