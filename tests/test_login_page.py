import os
import shutil
import sqlite3
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import ingest
import login_page
import paths
import scope_auth as auth


def _html(app):
    return " ".join(node.proto.body for node in app.get("html"))


def test_card_stylesheet_hides_streamlit_chrome_and_centres_card():
    css = login_page.CSS
    assert '[data-testid="stToolbar"]' in css and '[data-testid="stHeader"]' in css
    assert ".st-key-scope_login_card" in css
    assert "max-width: 400px" in css


def test_personal_gate_renders_inside_card_with_brand(tmp_path, monkeypatch):
    db = str(tmp_path / "scope.sqlite")
    with sqlite3.connect(db) as con:
        ingest.schema(con)
    monkeypatch.setenv("SCOPE_AUTH_MODE", "personal")
    monkeypatch.setenv("SCOPE_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setattr(auth, "configuration", lambda: ("https://scope.supabase.co", "test-key"))
    monkeypatch.setattr(auth, "api", Mock())
    app = AppTest.from_string(f"import scope_auth\nscope_auth.gate({db!r})").run()
    assert not app.exception
    html = _html(app)
    assert "scope-mark.svg" in html and "Anmelden" in html
    assert "Persönlicher Zugang" in html
    assert "gilt 10 Minuten" in html
    assert [item.label for item in app.text_input] == ["E-Mail-Adresse", "Anmeldecode"]
    assert {button.label for button in app.button} >= {"Code anfordern", "Anmelden"}
    assert not app.title  # the card heading replaces the bare st.title


def test_shared_gate_uses_card_and_form_button(tmp_path, monkeypatch):
    # The app opens paths.DB on its way past the gate; keep the repository's
    # results.sqlite out of it.
    database = str(tmp_path / "results.sqlite")
    shutil.copy2(paths.SEED_DB, database)
    monkeypatch.setattr(paths, "DB", database)
    monkeypatch.setenv("APP_PASSWORD", "geheim")
    monkeypatch.delenv("SCOPE_AUTH_MODE", raising=False)
    app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=60).run()
    assert not app.exception
    html = _html(app)
    assert "scope-mark.svg" in html and "Gemeinsamer Zugang" in html
    assert [item.label for item in app.text_input] == ["Passwort"]
    assert not app.title

    app.text_input[0].set_value("falsch")
    next(button for button in app.button if button.label == "Anmelden").click().run()
    assert not app.exception
    assert [item.value for item in app.error] == ["Falsches Passwort."]
    assert "_ok" not in app.session_state

    app.text_input[0].set_value("geheim")
    next(button for button in app.button if button.label == "Anmelden").click().run()
    assert not app.exception
    assert app.session_state["_ok"] is True
    assert "Passwort" not in [item.label for item in app.text_input]  # gate gone, app rendered
