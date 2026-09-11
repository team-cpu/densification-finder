"""Separate Scope identities, verified by the configured Scope Supabase project.

Legacy shared access is retained only when personal mode is explicitly disabled.
The local access table, not Supabase signup or user metadata, grants Scope access.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import streamlit as st
import login_page
import paths


class AuthError(ValueError):
    pass


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


def api(endpoint: str, payload: dict | None = None, token: str | None = None) -> dict:
    url, key = configuration()
    if endpoint not in ("otp", "verify", "user", "logout"):
        raise AuthError("Ungültige Authentifizierungsanfrage.")
    headers = {"apikey": key, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{url}/auth/v1/{endpoint}", headers=headers,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      method="GET" if endpoint == "user" else "POST")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(65536)
        result = json.loads(raw) if raw else {}
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (URLError, OSError, HTTPException, ValueError):
        raise AuthError("Anmeldung konnte nicht bestätigt werden. Bitte erneut versuchen.") from None


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
            raise AuthError("Kein gültiger Scope-Zugang.")
        if row["auth_id"] and row["status"] != "active":
            raise AuthError("Kein gültiger Scope-Zugang.")
        if not row["auth_id"]:
            if not bind or row["invited_until"] <= time.time():
                raise AuthError("Einladung ist nicht gültig.")
            con.execute("UPDATE scope_access SET auth_id=? WHERE member_id=?", (auth_id, row["id"]))
            con.execute("UPDATE organisation_members SET status='active',activity='Angemeldet' WHERE id=?", (row["id"],))
        return dict(row)


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
        attempt = con.execute("SELECT window_start,attempts FROM scope_auth_verifications WHERE email=?", (email,)).fetchone()
        if attempt and now - attempt[0] < 300 and attempt[1] >= 5:
            raise AuthError("Zu viele Versuche. Bitte in fünf Minuten erneut versuchen.")
        if not attempt or now - attempt[0] >= 300:
            con.execute("INSERT INTO scope_auth_verifications VALUES (?,?,1) ON CONFLICT(email) "
                        "DO UPDATE SET window_start=excluded.window_start,attempts=1", (email, now))
        else:
            con.execute("UPDATE scope_auth_verifications SET attempts=attempts+1 WHERE email=?", (email,))
    result = api("verify", {"email": email, "token": code, "type": "email"})
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise AuthError("Kein gültiger Scope-Zugang.")
    # Never decode an unverified JWT or trust profile metadata for permissions.
    verified_member(api("user", token=token), db, bind=True)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scope_auth_verifications WHERE email=?", (email,))
    return token


def current(db: str | None = None) -> dict:
    token = st.session_state.get("scope_access_token")
    if not token:
        raise AuthError("Bitte erneut anmelden.")
    member = _verified_identities.get(token)
    if member is None:
        member = verified_member(api("user", token=token), db or paths.DB)
        _verified_identities[token] = member
    return member


def require_owner(db: str | None = None) -> dict | None:
    if enabled():
        member = current(db)
        if member["role"] != "Inhaber":
            raise AuthError("Nur Inhaber können das Team und die Einstellungen ändern.")
        return member
    return None


def require_write(db: str | None = None) -> dict | None:
    if enabled():
        try:
            member = current(db)
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
    row = con.execute("SELECT m.role FROM organisation_members m JOIN scope_access a "
                      "ON a.member_id=m.id WHERE m.id=?", (actor["id"],)).fetchone()
    allowed = ("Inhaber",) if owner else ("Inhaber", "Bearbeiter")
    if not row or row[0] not in allowed:
        raise AuthError("Berechtigung wurde geändert. Bitte die Seite neu laden.")


def logout() -> None:
    token = st.session_state.get("scope_access_token")
    # Clear all per-user drafts/data even if the provider is temporarily offline.
    for key in list(st.session_state):
        del st.session_state[key]
    if token:
        _verified_identities.pop(token, None)
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
            if member["role"] == "Leseweise":
                st.info("Lesezugriff: Sie können Daten ansehen, aber keine Änderungen speichern.")
            return
        except AuthError:
            logout()
            notice = "Sitzung abgelaufen oder Zugang nicht mehr gültig. Bitte erneut anmelden."
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
