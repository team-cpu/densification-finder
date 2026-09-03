"""Truthful Team and Einstellungen previews for the shared-access app.

The supplied design models a multi-user product. This app currently has one
shared password and no account, invitation, organisation-profile, or settings
backend. The dialog therefore follows the design's layout closely while every
preview control remains disabled and no fictional company or person is shown.
"""
from __future__ import annotations

from html import escape

import streamlit as st


DIALOG_OPEN = "organisation_dialog_open"
DIALOG_VIEW = "organisation_dialog_view"

_ROLE_OPTIONS = ("Inhaber", "Bearbeiter", "Leseweise")

_DIALOG_CSS = """
<style>
div[data-testid="stDialog"]:has(.scope-org-modal) {
  align-items: stretch !important;
  padding: 0 !important;
}

div[data-testid="stDialog"]:has(.scope-org-modal) > div {
  position: fixed !important;
  inset: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  box-sizing: border-box !important;
  margin: 0 !important;
  background: rgba(23, 23, 27, .28) !important;
  padding: 40px 24px !important;
}

div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"] {
  width: 660px !important;
  min-width: 0 !important;
  max-width: calc(100vw - 48px) !important;
  max-height: calc(100vh - 80px) !important;
  padding: 0 !important;
  overflow: hidden !important;
  border: 1px solid #e4e4ea !important;
  border-radius: 11px !important;
  background: #fff !important;
  box-shadow: none !important;
  position: relative !important;
}

body:has(div[data-testid="stDialog"] .scope-org-modal)
  div[data-testid="stPopoverBody"]:has(.scope-account-menu) {
  display: none !important;
}

div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"]
  > h2 {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  padding: 0 !important;
  margin: -1px !important;
  overflow: hidden !important;
  clip: rect(0, 0, 0, 0) !important;
  white-space: nowrap !important;
}

div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"]
  > button[aria-label="Close"] {
  top: 18px !important;
  right: 20px !important;
  z-index: 3 !important;
  width: 26px !important;
  min-width: 26px !important;
  height: 26px !important;
  min-height: 26px !important;
  padding: 0 !important;
  border: 1px solid #e2e2e8 !important;
  border-radius: 5px !important;
  background: #fff !important;
  color: #77777f !important;
}

div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"]
  > div:last-child {
  padding: 0 !important;
}

div[data-testid="stDialog"]:has(.scope-org-modal)
  section[role="dialog"] [data-testid="stVerticalBlock"] {
  gap: 0 !important;
}

.scope-org-modal,
.scope-org-modal * {
  box-sizing: border-box;
}

.scope-org-header {
  position: relative;
  padding: 18px 52px 18px 20px;
  border-bottom: 1px solid #f0f0f3;
}

.scope-org-kicker,
.scope-org-section-title,
.scope-org-field-label {
  color: #8a8a94;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .09em;
  line-height: 1.2;
  text-transform: uppercase;
}

.scope-org-kicker {
  margin-bottom: 6px;
  letter-spacing: .1em;
}

.scope-org-title {
  color: #17171b;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -.01em;
  line-height: 1.25;
}

.scope-org-subtitle {
  margin-top: 4px;
  color: #9a9aa6;
  font-size: 11.5px;
  line-height: 1.35;
}

.scope-org-body {
  overflow-y: auto;
  padding: 16px 20px 18px;
}

.scope-org-body--settings {
  max-height: calc(100vh - 234px);
}

.scope-org-invite {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 102px auto;
  gap: 8px;
  margin-bottom: 16px;
}

.scope-org-modal input,
.scope-org-modal select {
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: 1px solid #e2e2e8;
  border-radius: 6px;
  background: #fff;
  color: #77777f;
  font-family: inherit;
  font-size: 12.5px;
  opacity: 1;
}

.scope-org-modal select {
  padding-right: 8px;
}

.scope-org-invite-button {
  height: 32px;
  padding: 0 14px;
  border: 1px solid #1c4e4a;
  border-radius: 6px;
  background: #1c4e4a;
  color: #fff;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  opacity: .48;
}

.scope-org-modal :disabled {
  cursor: not-allowed;
}

.scope-org-roster {
  min-height: 203px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 22px;
  border: 1px solid #f0f0f3;
  border-radius: 8px;
  background: #fff;
  text-align: center;
}

.scope-org-empty-avatar {
  width: 29px;
  height: 29px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 9px;
  border-radius: 50%;
  background: #e8f0ef;
  color: #143a37;
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: .02em;
}

.scope-org-empty-title {
  color: #17171b;
  font-size: 12.5px;
  font-weight: 500;
}

.scope-org-empty-copy,
.scope-org-roles {
  color: #9a9aa6;
  font-size: 11px;
  line-height: 1.45;
}

.scope-org-empty-copy {
  max-width: 390px;
  margin-top: 3px;
}

.scope-org-roles {
  margin: 12px 0 0;
  color: #b0b0b8;
}

.scope-org-section-title {
  margin-bottom: 11px;
}

.scope-org-profile-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 14px;
  margin-bottom: 22px;
}

.scope-org-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.scope-org-field--wide {
  grid-column: 1 / -1;
}

.scope-org-profile-grid input {
  height: 31px;
  padding: 0 9px;
  border-color: #e0e0e6;
}

.scope-org-profile-grid input::placeholder {
  color: #aaaab3;
  opacity: 1;
}

.scope-org-settings-title {
  margin-bottom: 2px;
}

.scope-org-setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  min-height: 65px;
  padding: 13px 2px;
  border-bottom: 1px solid #f4f4f6;
}

.scope-org-setting-copy {
  min-width: 0;
}

.scope-org-setting-name {
  color: #17171b;
  font-size: 12.5px;
  font-weight: 500;
  line-height: 1.3;
}

.scope-org-setting-caption {
  margin-top: 3px;
  color: #9a9aa6;
  font-size: 11px;
  line-height: 1.4;
  text-wrap: pretty;
}

.scope-org-toggle {
  width: 34px;
  min-width: 34px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  padding: 0;
  border: 1px solid #e2e2e8;
  border-radius: 20px;
  background: #f1f1f4;
  opacity: .72;
}

.scope-org-toggle--on {
  justify-content: flex-end;
  border-color: #1c4e4a;
  background: #1c4e4a;
}

.scope-org-toggle span {
  width: 16px;
  height: 16px;
  margin: 0 1.5px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(23, 23, 27, .2);
}

.scope-org-license-value {
  flex: 0 0 auto;
  color: #4a4a54;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.st-key-org_modal_footer {
  min-height: 59px;
  padding: 14px 20px !important;
  border-top: 1px solid #f0f0f3;
}

.st-key-org_modal_footer[data-testid="stHorizontalBlock"],
.st-key-org_modal_footer [data-testid="stHorizontalBlock"] {
  align-items: center !important;
  justify-content: space-between !important;
  gap: 16px !important;
}

.scope-org-footer-note {
  color: #b0b0b8;
  font-size: 11px;
  line-height: 1.35;
}

.st-key-org_dialog_close button[kind="primary"] {
  min-height: 30px !important;
  height: 30px !important;
  padding: 0 14px !important;
  border: 1px solid #1c4e4a !important;
  border-radius: 6px !important;
  background: #1c4e4a !important;
  color: #fff !important;
  font-size: 12px !important;
  font-weight: 500 !important;
}

@media (max-width: 700px) {
  div[data-testid="stDialog"]:has(.scope-org-modal) > div {
    padding: 20px 12px !important;
  }

  div[data-testid="stDialog"]:has(.scope-org-modal) section[role="dialog"] {
    max-width: calc(100vw - 24px) !important;
    max-height: calc(100vh - 40px) !important;
  }

  .scope-org-invite,
  .scope-org-profile-grid {
    grid-template-columns: 1fr;
  }

  .scope-org-field--wide {
    grid-column: auto;
  }

  .scope-org-body--settings {
    max-height: calc(100vh - 194px);
  }

  .scope-org-license-value {
    display: none;
  }
}
</style>
"""


def _header(view: str) -> str:
    kicker = "Einstellungen" if view == "settings" else "Team"
    return f"""
<div class="scope-org-header">
  <div class="scope-org-kicker">{kicker}</div>
  <div class="scope-org-title">Gemeinsamer Zugang</div>
  <div class="scope-org-subtitle">Vorschau · keine Benutzerkonten aktiv · gemeinsames Passwort</div>
</div>
"""


def _team_body() -> str:
    role_options = "".join(f"<option>{escape(role)}</option>" for role in _ROLE_OPTIONS)
    return f"""
<div class="scope-org-body scope-org-body--team">
  <div class="scope-org-invite">
    <input type="email" aria-label="E-Mail-Adresse" placeholder="name@firma.ch"
      data-org-field="org_field_invite_email" disabled>
    <select aria-label="Rolle" data-org-field="org_field_invite_role" disabled>
      {role_options}
    </select>
    <button type="button" class="scope-org-invite-button"
      data-org-field="org_field_invite_button" disabled>Einladen</button>
  </div>
  <div class="scope-org-roster">
    <div>
      <span class="scope-org-empty-avatar">MB</span>
      <div class="scope-org-empty-title">Keine Mitgliederverwaltung aktiv</div>
      <div class="scope-org-empty-copy">Dieses Werkzeug hat keine Benutzerkonten. Teammitglieder, Rollen und Einladungen werden deshalb nicht erfunden.</div>
    </div>
  </div>
  <p class="scope-org-roles">Rollen: Inhaber verwaltet Abo und Team · Bearbeiter kann Analysen und Akquisition ändern · Leseweise sieht Screening und Merkliste ohne Bearbeitungsrechte.</p>
</div>
"""


def _profile_field(label: str, key: str, *, wide: bool = False) -> str:
    wide_class = " scope-org-field--wide" if wide else ""
    return f"""
<label class="scope-org-field{wide_class}">
  <span class="scope-org-field-label">{escape(label)}</span>
  <input type="text" placeholder="Nicht konfiguriert"
    data-org-field="{escape(key)}" disabled>
</label>
"""


def _setting_row(
    name: str,
    caption: str,
    *,
    key: str | None = None,
    on: bool = False,
    value: str | None = None,
) -> str:
    if key is not None:
        state_class = " scope-org-toggle--on" if on else ""
        control = (
            f'<button type="button" class="scope-org-toggle{state_class}" '
            f'aria-label="{escape(name)}" aria-pressed="{str(on).lower()}" '
            f'data-org-field="{escape(key)}" disabled><span></span></button>'
        )
    else:
        control = f'<span class="scope-org-license-value">{escape(value or "")}</span>'

    return f"""
<div class="scope-org-setting-row">
  <div class="scope-org-setting-copy">
    <div class="scope-org-setting-name">{escape(name)}</div>
    <div class="scope-org-setting-caption">{escape(caption)}</div>
  </div>
  {control}
</div>
"""


def _settings_body(data_as_of: str) -> str:
    fields = "".join(
        (
            _profile_field("Firmenname", "org_field_company_name"),
            _profile_field("Rechtlicher Name", "org_field_legal_name"),
            _profile_field("Strasse und Nr.", "org_field_street", wide=True),
            _profile_field("PLZ", "org_field_postcode"),
            _profile_field("Ort", "org_field_city"),
            _profile_field("UID", "org_field_uid"),
            _profile_field("Rechnungs-E-Mail", "org_field_billing_email"),
        )
    )
    rows = "".join(
        (
            _setting_row(
                "Wöchentliche Zusammenfassung",
                "Montags 07:00 — neue Parzellen und Änderungen der Bau- und Zonenordnung.",
                key="org_field_toggle_weekly_summary",
                on=True,
            ),
            _setting_row(
                "Erinnerung bei fälligen Kontakten",
                "E-Mail am Fälligkeitstag für alle zugewiesenen Akquisitionen.",
                key="org_field_toggle_due_reminders",
                on=True,
            ),
            _setting_row(
                "Zwei-Faktor-Authentifizierung erzwingen",
                "Gilt für alle Mitglieder der Organisation.",
                key="org_field_toggle_2fa",
            ),
            _setting_row(
                "Kalkulationen teamweit sichtbar",
                "Residualwert-Annahmen sind für alle Bearbeiter einsehbar.",
                key="org_field_toggle_shared_calculations",
                on=True,
            ),
            _setting_row(
                "Datenlizenz",
                "Kanton Aargau: AGIS sowie Parzellen und Landbedeckung von geodienste.ch. Öffentliche amtliche Daten, kein Abonnement. "
                f"Datenstand {data_as_of}.",
                value="Kanton AG · öffentlich",
            ),
        )
    )
    return f"""
<div class="scope-org-body scope-org-body--settings">
  <div class="scope-org-section-title">Firmenangaben</div>
  <div class="scope-org-profile-grid">{fields}</div>
  <div class="scope-org-section-title scope-org-settings-title">Organisation</div>
  <div class="scope-org-settings">{rows}</div>
</div>
"""


def render(data_as_of: str, view: str = "team") -> None:
    """Render one design-matched, non-operable preview view."""
    view = "settings" if view == "settings" else "team"
    body = _settings_body(data_as_of) if view == "settings" else _team_body()
    st.html(
        _DIALOG_CSS
        + f'<div class="scope-org-modal scope-org-modal--{view}">'
        + _header(view)
        + body
        + "</div>"
    )


def _close_dialog() -> None:
    st.session_state.pop(DIALOG_OPEN, None)
    st.session_state.pop(DIALOG_VIEW, None)


@st.dialog(
    "Organisation",
    width="large",
    on_dismiss=_close_dialog,
)
def _dialog(data_as_of: str) -> None:
    view = st.session_state.get(DIALOG_VIEW, "team")
    render(data_as_of, view)
    note = (
        "Vorschau · Änderungen sind noch nicht aktiv."
        if view == "settings"
        else "Vorschau · Einladungen und Rollen sind noch nicht aktiv."
    )
    with st.container(
        key="org_modal_footer",
        horizontal=True,
        horizontal_alignment="distribute",
        vertical_alignment="center",
        gap="small",
    ):
        st.html(f'<span class="scope-org-footer-note">{escape(note)}</span>')
        if st.button("Fertig", key="org_dialog_close", type="primary"):
            _close_dialog()
            st.rerun()


def open_if_requested(data_as_of: str) -> None:
    """Open the view selected from the account menu, when requested."""
    if st.session_state.get(DIALOG_OPEN):
        _dialog(data_as_of)
