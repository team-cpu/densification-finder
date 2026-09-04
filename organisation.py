"""Persistent organisation, team and settings controls.

The design export keeps these values only in browser memory. Scope persists
them in the application's existing SQLite volume instead. This is deliberately
not an authentication system: the app still has one optional shared-password
gate; members and roles are operational metadata until per-user login and
authorisation are introduced.
"""
from __future__ import annotations

import re
import sqlite3
from html import escape

import streamlit as st

import paths


DIALOG_OPEN = "organisation_dialog_open"
DIALOG_VIEW = "organisation_dialog_view"
ROLE_OPTIONS = ("Inhaber", "Bearbeiter", "Leseweise")
PROFILE_TEXT_FIELDS = (
    "name", "legal_name", "street", "postcode", "city", "uid", "billing_email",
)
PROFILE_BOOLEAN_FIELDS = (
    "weekly_digest", "due_reminders", "enforce_2fa", "shared_calculations",
)

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_UID = re.compile(
    r"^(?:CHE[- .]?)?\d{3}[.]?\d{3}[.]?\d{3}(?:\s?(?:MWST|TVA|IVA))?$", re.I
)
_TEXT_LIMITS = {
    "name": 160, "legal_name": 200, "street": 200, "postcode": 20,
    "city": 120, "uid": 40, "billing_email": 200,
}


def _db(db: str | None) -> str:
    return db or paths.DB


def _clean_text(field: str, value: object) -> str:
    if field not in PROFILE_TEXT_FIELDS:
        raise ValueError(f"Unbekanntes Firmenfeld: {field}")
    text = str(value or "").strip()
    limit = _TEXT_LIMITS[field]
    if len(text) > limit:
        raise ValueError(f"{field} darf höchstens {limit} Zeichen enthalten.")
    if field == "billing_email" and text and not _EMAIL.fullmatch(text):
        raise ValueError("Rechnungs-E-Mail ist keine gültige E-Mail-Adresse.")
    if field == "uid" and text and not _UID.fullmatch(text):
        raise ValueError("UID muss dem Format CHE-123.456.789 entsprechen.")
    return text


def _clean_email(value: object) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 200 or not _EMAIL.fullmatch(email):
        raise ValueError("Bitte eine gültige E-Mail-Adresse eingeben.")
    return email


def _member_name(email: str) -> str:
    words = re.split(r"[._+-]+", email.split("@", 1)[0])
    name = " ".join(word[:1].upper() + word[1:] for word in words if word)
    return (name or email)[:200]


def load_profile(db: str | None = None) -> dict[str, object]:
    """Load the singleton organisation profile."""
    with sqlite3.connect(_db(db)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT name, legal_name, street, postcode, city, uid, "
            "billing_email, weekly_digest, due_reminders, enforce_2fa, "
            "shared_calculations, updated_at FROM organisation_profile WHERE id = 1"
        ).fetchone()
    if row is None:
        return {
            **{field: "" for field in PROFILE_TEXT_FIELDS},
            "weekly_digest": True, "due_reminders": True,
            "enforce_2fa": False, "shared_calculations": True, "updated_at": "",
        }
    result = dict(row)
    for field in PROFILE_BOOLEAN_FIELDS:
        result[field] = bool(result[field])
    return result


def update_profile(
    values: dict[str, object], db: str | None = None
) -> dict[str, object]:
    """Validate and persist a partial organisation profile atomically."""
    unknown = set(values) - set(PROFILE_TEXT_FIELDS) - set(PROFILE_BOOLEAN_FIELDS)
    if unknown:
        raise ValueError(f"Unbekannte Firmenfelder: {', '.join(sorted(unknown))}")
    clean: dict[str, object] = {}
    for field, value in values.items():
        if field in PROFILE_BOOLEAN_FIELDS and not isinstance(value, bool):
            raise ValueError(f"{field} muss ein Wahrheitswert sein.")
        clean[field] = (
            _clean_text(field, value)
            if field in PROFILE_TEXT_FIELDS
            else int(bool(value))
        )
    if not clean:
        return load_profile(db)
    with sqlite3.connect(_db(db)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "INSERT OR IGNORE INTO organisation_profile (id, name) VALUES (1, '')"
        )
        current = dict(
            connection.execute(
                "SELECT name, legal_name, street, postcode, city, uid, "
                "billing_email, weekly_digest, due_reminders, enforce_2fa, "
                "shared_calculations FROM organisation_profile WHERE id = 1"
            ).fetchone()
        )
        current.update(clean)
        connection.execute(
            "UPDATE organisation_profile SET name = ?, legal_name = ?, street = ?, "
            "postcode = ?, city = ?, uid = ?, billing_email = ?, "
            "weekly_digest = ?, due_reminders = ?, enforce_2fa = ?, "
            "shared_calculations = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            tuple(
                current[field]
                for field in (*PROFILE_TEXT_FIELDS, *PROFILE_BOOLEAN_FIELDS)
            ),
        )
    return load_profile(db)


def load_members(db: str | None = None) -> list[dict[str, object]]:
    """Return members in stable creation order."""
    with sqlite3.connect(_db(db)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, name, email, role, status, activity, is_self, "
            "created_at, updated_at FROM organisation_members ORDER BY id"
        ).fetchall()
    return [
        {
            **dict(row),
            "is_self": bool(row["is_self"]),
            "pending": row["status"] == "pending",
        }
        for row in rows
    ]


def invite_member(
    email: object, role: str = "Bearbeiter", *, db: str | None = None
) -> int:
    """Create a pending invitation, or refresh an existing pending one."""
    email_clean = _clean_email(email)
    if role not in ROLE_OPTIONS:
        raise ValueError("Unbekannte Rolle.")
    with sqlite3.connect(_db(db)) as connection:
        # Serialize lookup + insert so two sessions cannot race the unique email.
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id, status FROM organisation_members WHERE email = ?",
            (email_clean,),
        ).fetchone()
        if row and row[1] == "active":
            raise ValueError("Diese E-Mail-Adresse ist bereits Mitglied.")
        if row:
            connection.execute(
                "UPDATE organisation_members SET role = ?, activity = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (role, "erneut vorgemerkt", row[0]),
            )
            return int(row[0])
        cursor = connection.execute(
            "INSERT INTO organisation_members "
            "(name, email, role, status, activity) VALUES (?, ?, ?, 'pending', '—')",
            (_member_name(email_clean), email_clean, role),
        )
        return int(cursor.lastrowid)


def set_member_role(member_id: int, role: str, db: str | None = None) -> bool:
    if role not in ROLE_OPTIONS:
        raise ValueError("Unbekannte Rolle.")
    with sqlite3.connect(_db(db)) as connection:
        cursor = connection.execute(
            "UPDATE organisation_members SET role = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND is_self = 0",
            (role, int(member_id)),
        )
    return cursor.rowcount == 1


def remove_member(member_id: int, db: str | None = None) -> bool:
    """Remove an active non-self member; pending invitations are retained."""
    with sqlite3.connect(_db(db)) as connection:
        cursor = connection.execute(
            "DELETE FROM organisation_members "
            "WHERE id = ? AND is_self = 0 AND status = 'active'",
            (int(member_id),),
        )
    return cursor.rowcount == 1


def resend_invite(member_id: int, db: str | None = None) -> bool:
    with sqlite3.connect(_db(db)) as connection:
        cursor = connection.execute(
            "UPDATE organisation_members SET activity = 'erneut vorgemerkt', "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (int(member_id),),
        )
    return cursor.rowcount == 1


def account_summary(db: str | None = None) -> dict[str, object]:
    """Truthful header/menu identity without inventing a signed-in person."""
    profile = load_profile(db)
    members = load_members(db)
    active = [member for member in members if not member["pending"]]
    current = next((member for member in active if member["is_self"]), None)
    org_name = str(profile["name"]).strip()
    if current:
        short_name = str(current["name"]).strip() or str(current["email"])
        label = f"{short_name} · {org_name}" if org_name else short_name
        title, caption = short_name, str(current["email"])
    else:
        label = org_name or "Gemeinsamer Zugang"
        title = org_name or "Gemeinsamer Zugang"
        caption = f"{len(active)} aktive Mitglieder" if active else "Noch keine aktiven Mitglieder"
    initials = _initials(title)
    return {
        "label": label, "title": title, "caption": caption,
        "initials": initials, "member_count": len(active),
    }


_DIALOG_CSS = """
<style>
div[data-testid="stDialog"]:has(.scope-org-modal){align-items:stretch!important;padding:0!important}
div[data-testid="stDialog"]:has(.scope-org-modal)>div{position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;display:flex!important;align-items:center!important;justify-content:center!important;box-sizing:border-box!important;margin:0!important;background:rgba(23,23,27,.28)!important;padding:40px 24px!important}
div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"]{width:660px!important;min-width:0!important;max-width:calc(100vw - 48px)!important;max-height:calc(100vh - 80px)!important;padding:0!important;overflow:auto!important;border:1px solid #e4e4ea!important;border-radius:11px!important;background:#fff!important;box-shadow:none!important;position:relative!important}
body:has(div[data-testid="stDialog"] .scope-org-modal) div[data-testid="stPopoverBody"]:has(.scope-account-menu){display:none!important}
div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"]>h2{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important}
div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"]>button[aria-label="Close"]{top:18px!important;right:20px!important;z-index:3!important;width:26px!important;min-width:26px!important;height:26px!important;min-height:26px!important;padding:0!important;border:1px solid #e2e2e8!important;border-radius:5px!important;background:#fff!important;color:#77777f!important}
div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"]>div:last-child{padding:0!important}
div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"] [data-testid="stVerticalBlock"]{gap:0!important}
.scope-org-modal,.scope-org-modal *{box-sizing:border-box}.scope-org-header{padding:18px 52px 18px 20px;border-bottom:1px solid #f0f0f3}.scope-org-kicker,.scope-org-section-title{color:#8a8a94;font-size:10px;font-weight:600;letter-spacing:.09em;line-height:1.2;text-transform:uppercase}.scope-org-kicker{margin-bottom:6px;letter-spacing:.1em}.scope-org-title{color:#17171b;font-size:16px;font-weight:600;letter-spacing:-.01em;line-height:1.25}.scope-org-subtitle{margin-top:4px;color:#9a9aa6;font-size:11.5px;line-height:1.35}.scope-org-section-title{margin:0 0 11px}.scope-org-roles,.scope-org-empty{margin:12px 0 0;color:#b0b0b8;font-size:11px;line-height:1.45}.scope-org-empty{padding:22px;border:1px solid #f0f0f3;border-radius:8px;text-align:center}.scope-org-member-name{font-size:12.5px;font-weight:500;color:#17171b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.scope-org-member-email,.scope-org-member-activity{margin-top:2px;color:#9a9aa6;font-size:11px}.scope-org-member-avatar{width:26px;height:26px;display:flex;align-items:center;justify-content:center;border-radius:50%;background:#e8f0ef;color:#143a37;font-size:9.5px;font-weight:600}.scope-org-pending{display:inline-flex;margin-left:7px;padding:3px 7px;border-radius:20px;background:#f6f2ee;color:#7a5a3a;font-size:10px;font-weight:500}.scope-org-setting-copy{padding:13px 2px}.scope-org-setting-name{color:#17171b;font-size:12.5px;font-weight:500}.scope-org-setting-caption{margin-top:3px;color:#9a9aa6;font-size:11px;line-height:1.4}.scope-org-license{display:flex;align-items:center;justify-content:space-between;gap:20px;border-bottom:1px solid #f4f4f6}.scope-org-license-value{color:#4a4a54;font:12px 'IBM Plex Mono',monospace;white-space:nowrap}.scope-org-footer-note{color:#b0b0b8;font-size:11px;line-height:1.35}
.st-key-org_team_body,.st-key-org_settings_body{padding:16px 20px 18px!important}.st-key-org_invite{gap:8px!important;margin-bottom:16px}.st-key-org_invite [data-testid="stTextInput"] label,.st-key-org_invite [data-testid="stSelectbox"] label{display:none}.st-key-org_invite [data-testid="stButton"] button{height:32px!important;min-height:32px!important;padding:0 14px!important;border-radius:6px!important;font-size:12px!important}.st-key-org_roster{border:1px solid #f0f0f3;border-radius:8px;overflow:hidden}.st-key-org_roster>div+div{border-top:1px solid #f2f2f5}[class*="st-key-org_member_row_"]{padding:10px 12px!important;gap:11px!important;align-items:center!important}[class*="st-key-org_member_row_"] [data-testid="stButton"] button{height:27px!important;min-height:27px!important;padding:0 9px!important;border-radius:5px!important;font-size:11.5px!important}.st-key-org_profile_grid [data-testid="stTextInput"]{margin-bottom:12px}.st-key-org_profile_grid label p{font-size:10px!important;font-weight:600!important;letter-spacing:.07em!important;text-transform:uppercase!important;color:#8a8a94!important}[class*="st-key-org_setting_row_"]{min-height:65px;padding:0 2px!important;border-bottom:1px solid #f4f4f6;align-items:center!important}.st-key-org_modal_footer{min-height:59px;padding:14px 20px!important;border-top:1px solid #f0f0f3;align-items:center!important}.st-key-org_modal_footer button[kind="primary"]{height:30px!important;min-height:30px!important;padding:0 14px!important;border-radius:6px!important;font-size:12px!important}

/* Streamlit wraps each control in a flex child; size those wrappers too. */
.st-key-org_invite > .st-key-org_invite_email {
  flex: 1 1 0 !important; min-width: 0 !important;
}
.st-key-org_invite > .st-key-org_invite_role {
  flex: 0 0 110px !important; width: 110px !important; min-width: 0 !important;
}
/* Streamlit 1.61 uses React Aria controls, including a separate caret button. */
.st-key-org_invite [data-testid="stTextInputRootElement"],
.st-key-org_profile_grid [data-testid="stTextInputRootElement"],
.st-key-org_invite [data-testid="stSelectbox"] [role="group"],
[class*="st-key-org_member_row_"] [data-testid="stSelectbox"] [role="group"] {
  height: 32px !important;
  min-height: 32px !important;
  border: 1px solid #e2e2e8 !important;
  border-radius: 6px !important;
  background: #fff !important;
}
.st-key-org_profile_grid [data-testid="stTextInputRootElement"] {
  height: 31px !important;
  min-height: 31px !important;
}
.st-key-org_profile_grid [data-testid="stTextInput"] label {
  height: 12px !important;
  min-height: 12px !important;
  margin: 0 0 6px !important;
  line-height: 12px !important;
}
.st-key-org_profile_grid [data-testid="stTextInput"] label p {
  line-height: 12px !important;
}
.st-key-org_profile_grid [data-testid="stTextInputRootElement"]:focus-within,
.st-key-org_invite [data-testid="stTextInputRootElement"]:focus-within {
  border-color: #1c4e4a !important;
  box-shadow: 0 0 0 3px #e2eceb !important;
}
[class*="st-key-org_member_row_"] [data-testid="stSelectbox"] [role="group"] {
  height: 27px !important;
  min-height: 27px !important;
  border-radius: 5px !important;
}
.st-key-org_invite input,
.st-key-org_profile_grid input,
[class*="st-key-org_member_row_"] input {
  height: 100% !important;
  min-height: 0 !important;
  min-width: 0 !important;
  padding: 0 8px !important;
  background: transparent !important;
  font: 12.5px 'Instrument Sans', sans-serif !important;
}
[class*="st-key-org_member_row_"] input {
  padding: 0 6px !important;
  font-size: 11.5px !important;
}
.st-key-org_invite [data-testid="stSelectbox"] button,
[class*="st-key-org_member_row_"] [data-testid="stSelectbox"] button {
  flex: 0 0 24px !important;
  width: 24px !important;
  min-width: 24px !important;
  height: 100% !important;
  min-height: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
}
[class*="st-key-org_member_row_"] > [data-testid="stElementContainer"] {
  flex: 0 0 auto !important; width: auto !important; min-width: 0 !important;
}
[class*="st-key-org_member_row_"] > div:has(.scope-org-member-name) {
  flex: 1 1 0 !important;
}
[class*="st-key-org_member_row_"] > div:has([data-testid="stSelectbox"]) {
  flex: 0 0 102px !important; width: 102px !important;
}
.scope-org-member-avatar--pending { background: #f2f2f5; color: #9a9aa6; }
.scope-org-member-email { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Only the body scrolls; the reference keeps Close and Fertig available. */
.st-key-org_modal_content {
  max-height: calc(100vh - 82px);
  min-height: 0;
  overflow: hidden;
}
.st-key-org_modal_content > div {
  flex: 0 0 auto !important;
}
.st-key-org_modal_content > div:has(> .st-key-org_team_body),
.st-key-org_modal_content > div:has(> .st-key-org_settings_body) {
  flex: 1 1 auto !important;
  min-height: 0 !important;
  overflow-y: auto;
}
@media(max-width:700px) {
  .st-key-org_modal_content { max-height: calc(100vh - 42px); }
}

@media(max-width:700px){div[data-testid="stDialog"]:has(.scope-org-modal)>div{padding:20px 12px!important}div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"]{max-width:calc(100vw - 24px)!important;max-height:calc(100vh - 40px)!important}[class*="st-key-org_member_row_"]{flex-wrap:wrap!important}.scope-org-license-value{display:none}}
</style>
"""


def _header(view: str, profile: dict[str, object], members: list[dict[str, object]]) -> str:
    active = sum(not member["pending"] for member in members)
    return f"""
<div class="scope-org-modal scope-org-modal--{escape(view)}">
  <div class="scope-org-header">
    <div class="scope-org-kicker">{'Einstellungen' if view == 'settings' else 'Team'}</div>
    <div class="scope-org-title">{escape(str(profile['name']).strip() or 'Organisation')}</div>
    <div class="scope-org-subtitle">{active} aktive Mitglieder · Gemeinsamer Zugang</div>
  </div>
</div>
"""


def _initials(name: object) -> str:
    text = str(name or "").strip()
    initials = "".join(char for char in text if char.isupper())[:2]
    fallback = "".join(word[:1] for word in text.split())[:2].upper()
    # This value is also used in a CSS string for the header avatar.
    return "".join(char for char in (initials or fallback) if char.isalpha()) or "GZ"


def _save_member_role(member_id: int, widget_key: str, db: str | None) -> None:
    """Write only on user change, never from a stale widget during a rerender."""
    try:
        changed = set_member_role(member_id, st.session_state[widget_key], db)
    except ValueError as error:
        st.session_state["org_error"] = str(error)
    else:
        if not changed:
            st.session_state["org_error"] = "Mitglied nicht mehr verfügbar oder nicht editierbar."


def _render_team(db: str | None) -> None:
    members = load_members(db)
    with st.container(key="org_team_body"):
        with st.container(key="org_invite", horizontal=True, vertical_alignment="bottom"):
            email = st.text_input(
                "E-Mail-Adresse", placeholder="name@firma.ch", key="org_invite_email",
                label_visibility="collapsed", max_chars=200,
            )
            role = st.selectbox(
                "Rolle", ROLE_OPTIONS, index=1, key="org_invite_role",
                label_visibility="collapsed",
            )
            if st.button("Einladen", key="org_invite_submit", type="primary"):
                try:
                    invite_member(email, role, db=db)
                except ValueError as error:
                    st.session_state["org_error"] = str(error)
                else:
                    st.session_state.pop("org_error", None)
                    st.toast("Einladung als offen gespeichert.")
                    st.rerun()
        if error := st.session_state.pop("org_error", None):
            st.error(error)

        if not members:
            st.html('<div class="scope-org-empty">Noch keine Mitglieder oder offene Einladungen.</div>')
        else:
            with st.container(key="org_roster"):
                for member in members:
                    member_id = int(member["id"])
                    with st.container(
                        key=f"org_member_row_{member_id}", horizontal=True,
                        vertical_alignment="center",
                    ):
                        avatar_class = "scope-org-member-avatar"
                        if member["pending"]:
                            avatar_class += " scope-org-member-avatar--pending"
                        st.html(
                            f'<span class="{avatar_class}">'
                            f'{escape(_initials(member["name"]))}</span>'
                        )
                        pending = (
                            '<span class="scope-org-pending">Einladung offen</span>'
                            if member["pending"] else ""
                        )
                        st.html(
                            '<div style="min-width:0;flex:1">'
                            f'<div class="scope-org-member-name">{escape(str(member["name"]))}{pending}</div>'
                            f'<div class="scope-org-member-email">{escape(str(member["email"]))}</div></div>'
                        )
                        st.html(f'<span class="scope-org-member-activity">{escape(str(member["activity"]))}</span>')
                        role_key = f"org_member_role_{member_id}"
                        st.session_state[role_key] = str(member["role"])
                        st.selectbox(
                            "Rolle", ROLE_OPTIONS,
                            key=role_key,
                            label_visibility="collapsed", disabled=bool(member["is_self"]),
                            on_change=_save_member_role,
                            args=(member_id, role_key, db),
                        )
                        if member["pending"]:
                            if st.button("Erneut senden", key=f"org_resend_{member_id}"):
                                resend_invite(member_id, db)
                                st.toast("Einladung erneut vorgemerkt.")
                                st.rerun()
                        elif not member["is_self"]:
                            if st.button("Entfernen", key=f"org_remove_{member_id}"):
                                remove_member(member_id, db)
                                st.rerun()
                        else:
                            st.html('<span style="width:1px"></span>')
        st.html(
            '<p class="scope-org-roles">Vorgesehene Rollen: Inhaber · Bearbeiter · '
            'Leseweise. Rollen werden gespeichert, steuern beim gemeinsamen '
            'Zugang aber noch keine Berechtigungen.</p>'
        )


def _save_profile_field(field: str, widget_key: str, db: str | None) -> None:
    errors = dict(st.session_state.get("org_profile_errors", {}))
    try:
        update_profile({field: st.session_state[widget_key]}, db)
    except ValueError as error:
        errors[field] = str(error)
    else:
        errors.pop(field, None)
    st.session_state["org_profile_errors"] = errors


def _profile_input(container, label: str, field: str, profile: dict[str, object],
                   db: str | None, *, placeholder: str) -> None:
    key = f"org_profile_{field}"
    container.text_input(
        label, value=str(profile[field]), placeholder=placeholder,
        max_chars=_TEXT_LIMITS[field], key=key, on_change=_save_profile_field,
        args=(field, key, db),
    )


def _setting_toggle(field: str, name: str, caption: str,
                    profile: dict[str, object], db: str | None) -> None:
    key = f"org_profile_{field}"
    with st.container(
        key=f"org_setting_row_{field}", horizontal=True,
        horizontal_alignment="distribute", vertical_alignment="center",
    ):
        st.html(
            '<div class="scope-org-setting-copy">'
            f'<div class="scope-org-setting-name">{escape(name)}</div>'
            f'<div class="scope-org-setting-caption">{escape(caption)}</div></div>'
        )
        st.toggle(
            name, value=bool(profile[field]), key=key, label_visibility="collapsed",
            on_change=_save_profile_field, args=(field, key, db),
        )


def _render_settings(profile: dict[str, object], data_as_of: str,
                     db: str | None) -> None:
    with st.container(key="org_settings_body"):
        st.html('<div class="scope-org-section-title">Firmenangaben</div>')
        with st.container(key="org_profile_grid"):
            first = st.columns(2, gap="small")
            _profile_input(first[0], "Firmenname", "name", profile, db, placeholder="Organisation")
            _profile_input(first[1], "Rechtlicher Name", "legal_name", profile, db, placeholder="Rechtlicher Firmenname")
            _profile_input(st, "Strasse und Nr.", "street", profile, db, placeholder="Strasse 1")
            second = st.columns(2, gap="small")
            _profile_input(second[0], "PLZ", "postcode", profile, db, placeholder="8000")
            _profile_input(second[1], "Ort", "city", profile, db, placeholder="Ort")
            third = st.columns(2, gap="small")
            _profile_input(third[0], "UID", "uid", profile, db, placeholder="CHE-000.000.000")
            _profile_input(third[1], "Rechnungs-E-Mail", "billing_email", profile, db, placeholder="buchhaltung@firma.ch")
        for error in st.session_state.get("org_profile_errors", {}).values():
            st.error(error)
        st.html('<div class="scope-org-section-title" style="margin-top:10px">Organisation</div>')
        _setting_toggle("weekly_digest", "Wöchentliche Zusammenfassung", "Präferenz gespeichert. Automatischer E-Mail-Versand ist noch nicht eingerichtet.", profile, db)
        _setting_toggle("due_reminders", "Erinnerung bei fälligen Kontakten", "Präferenz gespeichert. Fälligkeiten erscheinen im Board; E-Mail-Versand ist noch nicht eingerichtet.", profile, db)
        _setting_toggle("enforce_2fa", "Zwei-Faktor-Authentifizierung erzwingen", "Präferenz gespeichert. Keine aktive 2FA ohne persönliche Benutzerkonten.", profile, db)
        _setting_toggle("shared_calculations", "Kalkulationen teamweit sichtbar", "Präferenz gespeichert. Annahmen bleiben derzeit pro Sitzung; keine Freigabesteuerung.", profile, db)
        st.html(
            '<div class="scope-org-setting-copy scope-org-license"><div>'
            '<div class="scope-org-setting-name">Datenlizenz</div>'
            '<div class="scope-org-setting-caption">Kanton Aargau: AGIS sowie Parzellen '
            f'und Landbedeckung von geodienste.ch. Datenstand {escape(data_as_of)}.</div></div>'
            '<span class="scope-org-license-value">Kanton AG · öffentlich</span></div>'
        )


def render(data_as_of: str, view: str = "team", db: str | None = None) -> None:
    view = "settings" if view == "settings" else "team"
    profile = load_profile(db)
    members = load_members(db)
    st.html(_DIALOG_CSS + _header(view, profile, members))
    if view == "settings":
        _render_settings(profile, data_as_of, db)
    else:
        _render_team(db)


def _close_dialog() -> None:
    st.session_state.pop(DIALOG_OPEN, None)
    st.session_state.pop(DIALOG_VIEW, None)
    st.session_state.pop("org_error", None)
    st.session_state.pop("org_profile_errors", None)


@st.dialog("Organisation", width="large", on_dismiss=_close_dialog)
def _dialog(data_as_of: str) -> None:
    view = st.session_state.get(DIALOG_VIEW, "team")
    note = (
        "Änderungen gelten für die ganze Organisation."
        if view == "settings"
        else "Einladungen werden gespeichert; E-Mail-Versand benötigt persönliche Benutzerkonten."
    )
    with st.container(key="org_modal_content"):
        render(data_as_of, view)
        with st.container(
            key="org_modal_footer", horizontal=True,
            horizontal_alignment="distribute", vertical_alignment="center", gap="small",
        ):
            st.html(f'<span class="scope-org-footer-note">{escape(note)}</span>')
            close_clicked = st.button("Fertig", key="org_dialog_close", type="primary")
            if close_clicked and not st.session_state.get("org_profile_errors"):
                _close_dialog()
                st.rerun()


def open_if_requested(data_as_of: str) -> None:
    if st.session_state.get(DIALOG_OPEN):
        _dialog(data_as_of)
