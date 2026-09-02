"""The organisation screen — a design preview, not a feature.

The design prototype behind the header's account chip mocks up a
multi-tenant product: a member roster with roles and pending invitations,
and an organisation-wide settings panel with a seat-licence count and a data
subscription. This application has none of that. `app.py`'s `gate()` is one
shared password for a single-user internal tool, and its own docstring
records that as a decision, not an omission — "accounts would be more
machinery than it is worth."

So this module draws the prototype's *layout* — two tabs, a roster header, a
settings panel with the same four toggles — behind a banner that says
plainly what is and is not real, and it does two things nowhere else in the
app has to:

- Every input, toggle, selectbox and the invite button carries
  `disabled=True`. A toggle the user can flip that changes nothing teaches
  them a lie about what this tool does; one they cannot flip states a fact.
  All of them share the `org_field_` key prefix precisely so a test (and
  any future reviewer) can enumerate exactly the preview's own controls
  without also catching `_dialog`'s own `Schliessen` button, which has to
  stay real or the dialog could never close.
- The roster carries no example rows. The prototype's own — "Marc Brunner",
  "Anja Sutter", "Reto Iten", "C. Meili" at `@hochbau-ag.ch` — are fictional,
  and `shell.py`'s `_ACCOUNT_LABEL` already made the same call for the
  header chip: a name on screen that belongs to nobody implies an account
  system that does not exist. The empty state says so instead.
- The "Datenlizenz" row tells the truth. The prototype shows a Zurich
  cadastral subscription expiring 31.12.2026 and a seat count ("3 von 6
  Lizenzen belegt"); this application has no subscription and no seats to
  count, so that row is replaced with the real provenance — Canton Aargau
  data from AGIS and geodienste.ch, per `README.md`'s own data-sources
  table — rather than ported or blanked.
"""
from __future__ import annotations

import streamlit as st

#: Whether the preview dialog is open. Set by the header's account chip
#: (`shell.py`, on click) and popped by this module's own `Schliessen`
#: button — the same shape as `acquisition.CONTACT_OPEN`, and for the same
#: reason: a dialog cannot be driven by `if st.button(...): dialog()`,
#: because the button that opened it reads `False` again on the rerun a
#: control *inside* the dialog triggers, so nothing in the dialog body
#: (including its own close button) would ever run a second time. Reading
#: this key from `shell.py` on every run, after the row that might have just
#: set it, is what makes the dialog reopen on that later run instead.
DIALOG_OPEN = "organisation_dialog_open"

_PREVIEW_BANNER = (
    "Vorschau. Benutzerkonten, Rollen und Einladungen sind nicht aktiv; der "
    "Zugang läuft über ein gemeinsames Passwort."
)

_ROSTER_COLUMNS = ("Name", "E-Mail", "Rolle", "Zuletzt aktiv")
_ROSTER_WIDTHS = (3, 3, 2, 2)

_ROLE_OPTIONS = ("Inhaber", "Bearbeiter", "Leseweise")


def _members_tab() -> None:
    """The roster's structure, with an empty state instead of the
    prototype's four fictional people — see the module docstring."""
    cols = st.columns(_ROSTER_WIDTHS)
    for col, label in zip(cols, _ROSTER_COLUMNS):
        col.caption(label)
    st.info(
        "Keine Mitgliederverwaltung aktiv. Dieses Werkzeug hat keine "
        "Benutzerkonten — es gibt keine Mitglieder, Rollen oder Einladungen "
        "zu verwalten. Diese Liste zeigt nur, wie das aussehen würde."
    )

    st.divider()
    st.caption("Einladen")
    st.text_input(
        "E-Mail-Adresse",
        key="org_field_invite_email",
        placeholder="name@firma.ch",
        disabled=True,
    )
    st.selectbox(
        "Rolle",
        _ROLE_OPTIONS,
        key="org_field_invite_role",
        disabled=True,
    )
    st.button("Einladen", key="org_field_invite_button", disabled=True)


def _toggle(label: str, *, value: bool, key: str, caption: str) -> None:
    """One settings toggle plus its caption, both disabled — a small
    wrapper so the four toggles below read as one shape instead of four
    near-identical `st.toggle`/`st.caption` pairs."""
    st.toggle(label, value=value, key=key, disabled=True)
    st.caption(caption)


def _settings_tab(data_as_of: str) -> None:
    """The four toggles at their prototype defaults, and the Datenlizenz
    row — see the module docstring for why that row's text is not the
    prototype's."""
    _toggle(
        "Wöchentliche Zusammenfassung",
        value=True,
        key="org_field_toggle_weekly_summary",
        caption=(
            "Jeden Montag eine Übersicht über neue Leads und fällige "
            "Kontakte per E-Mail."
        ),
    )
    _toggle(
        "Erinnerung bei fälligen Kontakten",
        value=True,
        key="org_field_toggle_due_reminders",
        caption="Benachrichtigung, sobald eine Wiedervorlage fällig wird.",
    )
    _toggle(
        "Zwei-Faktor-Authentifizierung erzwingen",
        value=False,
        key="org_field_toggle_2fa",
        caption="Verlangt einen zweiten Faktor bei jeder Anmeldung.",
    )
    _toggle(
        "Kalkulationen teamweit sichtbar",
        value=True,
        key="org_field_toggle_shared_calculations",
        caption=(
            "Residualwertrechnungen sind für alle Organisationsmitglieder "
            "einsehbar, nicht nur für die Ersteller:in."
        ),
    )

    st.divider()
    st.markdown("**Datenlizenz**")
    st.write(
        "Kanton Aargau: Zonierung, Planungszonen und Bauinventar von AGIS, "
        "Parzellen und Landbedeckung von geodienste.ch. Öffentliche "
        f"amtliche Daten, kein Abonnement. Datenstand {data_as_of}."
    )

    st.caption("Änderungen gelten für die ganze Organisation.")


def render(data_as_of: str) -> None:
    """The screen's body: banner, then the two tabs.

    `st.tabs` rather than `navigation.py`'s lazy `st.segmented_control`
    pattern — that laziness exists because Analyse is expensive to recompute
    on every rerun, and this is one small dialog with a handful of disabled
    widgets in each tab, so both tabs rendering unconditionally costs
    nothing and needs no such machinery.
    """
    st.warning(_PREVIEW_BANNER)

    members_tab, settings_tab = st.tabs(["Mitglieder", "Einstellungen"])
    with members_tab:
        _members_tab()
    with settings_tab:
        _settings_tab(data_as_of)


@st.dialog("Organisation", width="large")
def _dialog(data_as_of: str) -> None:
    render(data_as_of)
    if st.button("Schliessen", key="org_dialog_close"):
        st.session_state.pop(DIALOG_OPEN, None)
        st.rerun()


def open_if_requested(data_as_of: str) -> None:
    """Call once per run, after the row that draws the account chip.

    Mirrors `acquisition._render_open_contact_dialog`: the chip's own click
    handler only sets `DIALOG_OPEN` and reruns, it cannot call `_dialog`
    directly, so something has to check the flag on the *next* run — this
    is that something, and `shell.header` is its one caller.
    """
    if st.session_state.get(DIALOG_OPEN):
        _dialog(data_as_of)
