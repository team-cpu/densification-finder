"""The sign-in card that both gates render into.

The gates keep their own forms and logic (`scope_auth.gate` for personal
accounts, `app.gate` for the shared password); this module only owns the
frame around them: Streamlit's toolbar hidden as the shell hides it, the
brand row, and a centred card in the design system's tokens. Keeping the
chrome here means the two gates cannot drift apart visually, and neither
`scope_auth` nor `app` has to know how the card is styled.
"""
from __future__ import annotations

from contextlib import contextmanager
from html import escape
from typing import Iterator

import streamlit as st

#: Tokens are the theme's (`.streamlit/config.toml`): Instrument Sans, accent
#: #1c4e4a on the primary button via the theme, #ebebef borders, #17171b text.
CSS = """
<style>
/* Same rule as the shell: the product owns the top edge, not the toolbar. */
[data-testid="stHeader"],
[data-testid="stToolbar"] { display: none; }
[data-testid="stMainBlockContainer"] { padding-top: 0 !important; max-width: none; }

.st-key-scope_login_card {
  max-width: 400px;
  margin: 10vh auto 0;
  padding: 28px 28px 24px;
  gap: 14px !important;
  background: #ffffff;
  border: 1px solid #ebebef;
  border-radius: 10px;
  box-shadow: 0 1px 2px rgba(23, 23, 27, .04);
}
.scope-login-brand { display: flex; align-items: center; gap: 9px; margin-bottom: 18px; }
.scope-login-brand img { width: 20px; height: 20px; }
.scope-login-brand span { font-size: 13.5px; font-weight: 600; letter-spacing: -.01em; color: #17171b; }
.scope-login-title { margin: 0; font-size: 20px; font-weight: 600; letter-spacing: -.01em; line-height: 1.25; color: #17171b; }
.scope-login-caption { margin: 4px 0 0; font-size: 12.5px; line-height: 1.4; color: #77777f; }

/* Field labels follow the settings grid: small uppercase captions. */
.st-key-scope_login_card [data-testid="stWidgetLabel"] p {
  font-size: 10px !important; font-weight: 600 !important; letter-spacing: .07em !important;
  text-transform: uppercase !important; color: #8a8a94 !important;
}
/* White editable surfaces, as the shell paints them everywhere else. */
.st-key-scope_login_card [data-testid="stTextInputRootElement"]:has(input:enabled) {
  background-color: #fff !important;
}
.st-key-scope_login_card [data-testid="stTextInputRootElement"] input:enabled {
  background-color: transparent !important;
}
.st-key-scope_login_card [data-testid="stFormSubmitButton"] button,
.st-key-scope_login_card [data-testid="stButton"] button {
  width: 100%; height: 36px !important; min-height: 36px !important;
  border-radius: 6px !important; font-size: 12.5px !important;
}
.st-key-scope_login_card button[kind="secondaryFormSubmit"],
.st-key-scope_login_card button[kind="secondary"] {
  background-color: #fff !important; border: 1px solid #ebebef !important;
}
/* The six-digit code reads better spaced out in the mono face. */
.st-key-scope_login_code input { font-family: 'IBM Plex Mono', monospace; letter-spacing: .35em; }
.scope-login-hint { margin: -8px 0 0; font-size: 11px; line-height: 1.4; color: #9a9aa6; }
.scope-login-divider { height: 1px; margin: 2px 0; background: #f0f0f3; }

@media (max-width: 480px) {
  /* Streamlit gives the container width:100%; with side margins that would
     overflow the viewport, so let the margins define the width instead. */
  .st-key-scope_login_card { margin: 16px; max-width: none; width: auto !important; padding: 22px 20px 20px; }
}
</style>
"""

_BRAND = (
    '<div class="scope-login-brand">'
    '<img src="app/static/scope-mark.svg" alt="" /><span>Scope</span></div>'
)


@contextmanager
def card(caption: str, *, title: str = "Anmelden") -> Iterator[None]:
    """Render the brand, heading and caption; the caller adds its form inside."""
    st.html(CSS)
    with st.container(key="scope_login_card"):
        st.html(
            _BRAND
            + f'<h1 class="scope-login-title">{escape(title)}</h1>'
            + f'<p class="scope-login-caption">{escape(caption)}</p>'
        )
        yield


def hint(text: str) -> None:
    """A quiet caption under a field, inside the card."""
    st.html(f'<p class="scope-login-hint">{escape(text)}</p>')


def divider() -> None:
    st.html('<div class="scope-login-divider"></div>')
