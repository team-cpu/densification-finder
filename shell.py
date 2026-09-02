"""The application shell — sticky header, wordmark and the styled navigation.

`app.py` used to open with a bare `st.title(...)` and an unstyled
`st.segmented_control`. The design prototype has a proper header bar instead:
logo, wordmark, the four-page pill group, a data-as-of stamp and an account
chip, all in one sticky row. This module draws that bar and restyles the pill
group in place — `navigation.render()` still owns the page state and is still
what decides which page is selected; this only dresses it.

DOM selectors used below, confirmed against the installed frontend rather than
guessed:

- `st.container(key="app_shell")` stamps the class `st-key-app_shell` onto the
  exact DOM node that also carries the block's own `data-testid`
  (`index.*.js`: `className:(0,dd.default)(Ju(t),$u(s))` together with
  `"data-testid":Ju(t)` on the same element, where `$u` builds `st-key-*` from
  a container's `key=`). So `.st-key-app_shell` needs no descendant
  combinator to reach the bar itself — confirmed once against the running
  app's `st-key-app_shell` node before it was assumed for anything else here.
- `st.segmented_control` renders through the `ButtonGroup` component
  (`ButtonGroup.*.js`): each pill is
  `button[data-variant="segmented_control"]`, and the selected one additionally
  carries the boolean attribute `data-selected`
  (`styled-components.*.js`: the compiled rule keys off exactly
  `` button[data-variant='segmented_control'][data-selected]:not([data-disabled]) ``).
  Both were located with `grep`/pattern search over
  `.venv/lib/python3.11/site-packages/streamlit/static/static/js/*.js` — the
  same technique an earlier task used to find the column selectors in
  `acquisition.py`'s `_BOARD_CSS` — not by inspecting a rendered page, which
  this task has no browser to do.

What was not, and could not be, confirmed without a browser: whether
`position: sticky` actually holds the bar in place inside Streamlit's own
scroll container, whether the pill group reads as intended at a glance, and
whether the header stays legible at narrow widths. See the worker report for
what was checked instead (the container key and style block are present in
`AppTest`'s rendered output; the page starts; the suite passes).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import navigation

#: The prototype's own mark, reproduced verbatim from its `<svg>` source —
#: redrawing it by eye would drift from the original on every ellipse angle.
_LOGO_SVG = """\
<svg width="20" height="20" viewBox="0 0 40 40" style="flex:none" aria-hidden="true">
  <rect width="40" height="40" rx="9" fill="#1c4e4a"></rect>
  <g fill="#ffffff">
    <ellipse cx="20" cy="8.6" rx="5.4" ry="2.9"></ellipse>
    <ellipse cx="28.1" cy="11.9" rx="5.4" ry="2.9" transform="rotate(45 28.1 11.9)"></ellipse>
    <ellipse cx="31.4" cy="20" rx="5.4" ry="2.9" transform="rotate(90 31.4 20)"></ellipse>
    <ellipse cx="28.1" cy="28.1" rx="5.4" ry="2.9" transform="rotate(135 28.1 28.1)"></ellipse>
    <ellipse cx="20" cy="31.4" rx="5.4" ry="2.9"></ellipse>
    <ellipse cx="11.9" cy="28.1" rx="5.4" ry="2.9" transform="rotate(45 11.9 28.1)"></ellipse>
    <ellipse cx="8.6" cy="20" rx="5.4" ry="2.9" transform="rotate(90 8.6 20)"></ellipse>
    <ellipse cx="11.9" cy="11.9" rx="5.4" ry="2.9" transform="rotate(135 11.9 11.9)"></ellipse>
  </g>
</svg>
"""

#: Wordmark + canton. `#8a8a94` (muted) and the `#eaeaee` divider have no
#: existing theme token to fall back on, so they are written here rather than
#: sourced from `.streamlit/config.toml`; ink and page background are not
#: repeated because every other span already inherits them from the theme.
_BRAND_HTML = f"""\
<div style="display:flex;align-items:center;gap:9px">
  {_LOGO_SVG}
  <span style="font-size:13.5px;font-weight:600;letter-spacing:-0.01em">Areal</span>
  <span style="font-size:12px;color:#8a8a94;padding-left:9px;border-left:1px solid #eaeaee">
    Kanton Aargau
  </span>
</div>
"""

#: Grows to fill the gap between the nav and the account chip. Carries a
#: marker class rather than relying on its position among the header's
#: children, so a later reordering of `header()` cannot silently hand
#: `flex:1` to the wrong element (see `_SHELL_CSS`).
_SPACER_HTML = '<div class="normiq-shell-spacer"></div>'

#: What the chip says, and why it does not say a name. The prototype shows a
#: signed-in person — "M. Brunner · Hochbau AG" — because it mocks up a
#: multi-user product. This application has no such thing: `gate()` is one
#: shared password, and its docstring records that as a decision rather than an
#: omission ("deliberately not a login: the brief describes a single-user
#: internal tool"). Carrying the prototype's person across would put a name on
#: screen that belongs to nobody and implies an account system that does not
#: exist. The slot is kept so the shell matches the design; what fills it is
#: true.
_ACCOUNT_LABEL = "Gemeinsamer Zugang"


def _account_html(data_as_of: str) -> str:
    """The right-hand block: the data-as-of stamp and the account chip.

    The chip is plain `div`/`span` markup with no `onclick`, `<a>`, or
    `<button>` — and a `title` that says so — because the menu behind it does
    not exist yet; a chip that merely *looked* clickable would be worse than
    one that visibly is not.
    """
    return f"""\
<div style="display:flex;align-items:center;gap:14px">
  <span style="font-size:12px;color:#8a8a94;white-space:nowrap">
    Datenstand {data_as_of}
  </span>
  <div title="Dieses Werkzeug hat keine Benutzerkonten — der Zugang ist ein gemeinsames Passwort."
       style="display:flex;align-items:center;gap:8px;cursor:default">
    <span aria-hidden="true"
          style="width:26px;height:26px;border-radius:999px;background:#f1f1f4;
                 border:1px solid #e2e2e8;color:#8a8a94;display:inline-flex;
                 align-items:center;justify-content:center;font-size:12px;
                 font-weight:600;flex:none">·</span>
    <span style="font-size:12.5px;font-weight:500;color:#8a8a94;white-space:nowrap">
      {_ACCOUNT_LABEL}
    </span>
  </div>
</div>
"""


#: Emitted once per render via `st.html`, scoped under `.st-key-app_shell` so
#: none of it can leak onto `screening.py`/`merkliste.py`/`detail.py`/
#: `acquisition.py`, which draw their own scoped blocks the same way
#: (`acquisition.py`'s `_BOARD_CSS`, `detail.py`'s `PAGE_CSS`).
_SHELL_CSS = """
<style>
/* The bar itself. `.st-key-app_shell` stays a plain (non-horizontal)
   container — see the module docstring for why the row inside is a second,
   narrower container instead of putting `max-width` here too: doing it on
   this element would also clip the translucent background to 1560px instead
   of letting it fill whatever width the page gives the shell. */
.st-key-app_shell {
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(251, 251, 252, .92);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid #eaeaee;
}

/* The content row: capped and centered like the prototype's, with its own
   padding and height rather than the bar's. */
.st-key-app_shell_row {
  max-width: 1560px;
  margin: 0 auto;
  padding: 0 28px;
  height: 52px;
}

/* Streamlit gives every element its own stacking margin for vertical
   rhythm; inside a horizontal row that shows up as items sitting off-centre
   rather than as spacing, so it is zeroed here rather than fought with
   `gap`. Scoped to element leaves only — nested blocks such as
   `app_shell_row` itself are not `stElementContainer`s and are unaffected. */
.st-key-app_shell [data-testid="stElementContainer"] {
  margin: 0;
}

/* The spacer between the nav and the account block, selected by the marker
   class `_SPACER_HTML` carries rather than by position. */
.st-key-app_shell [data-testid="stElementContainer"]:has(.normiq-shell-spacer) {
  flex: 1 1 auto;
}

/* The pill track. Selectors confirmed against the installed frontend bundle
   — see the module docstring — not guessed from the prototype's markup,
   which describes the track but not what Streamlit actually emits for
   `st.segmented_control`. */
.st-key-app_shell [data-testid="stButtonGroup"] {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: 7px;
  background: #f1f1f4;
  width: fit-content;
}

.st-key-app_shell button[data-variant="segmented_control"] {
  font-size: 12px;
  font-weight: 500;
}

/* Raised on white with a subtle ring, rather than Streamlit's own default
   selected state (a wash of the theme's primary colour) — the prototype's
   pills read as flat/inactive vs. raised/active, not as unaccented/accented. */
.st-key-app_shell button[data-variant="segmented_control"][data-selected]:not([data-disabled]) {
  background-color: #ffffff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, .08), 0 0 0 1px rgba(0, 0, 0, .04);
}
</style>
"""


def data_as_of(runs: pd.DataFrame | None) -> str:
    """The latest `finished_at` across every municipality run, as
    `DD.MM.YYYY` — genuinely when the stored results were last computed.

    `—` for a missing, empty, or all-null table rather than inventing a date:
    the banner would otherwise claim results are fresher than they are, which
    is worse than admitting the run date is unknown.

    `finished_at` is written by `ingest.py` as SQLite's `datetime('now')`
    (`YYYY-MM-DD HH:MM:SS`), which — like other ISO-ish timestamps in this
    codebase (see `acquisition.py`'s `overdue`) — sorts correctly as plain
    text, so the latest run is just the lexicographic max.
    """
    if runs is None or runs.empty or "finished_at" not in runs.columns:
        return "—"
    finished = runs["finished_at"].dropna()
    if finished.empty:
        return "—"
    latest = finished.astype(str).max()
    try:
        return pd.Timestamp(latest).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        # A malformed timestamp must read as unknown, not crash the header —
        # the rest of the page is still usable without a data-as-of stamp.
        return "—"


def header(data_as_of: str) -> str:
    """Draw the sticky header and return the selected page.

    Replaces `app.py`'s old `st.title(...)` followed by a direct
    `navigation.render()` call with one call that does both: the bar around
    it, and the nav inside it. `navigation.render()` is still what actually
    draws the control and owns `st.session_state[navigation.PAGE]` — this
    only wraps it in the shell's markup and CSS.
    """
    with st.container(key="app_shell"):
        st.html(_SHELL_CSS)
        with st.container(
            key="app_shell_row", horizontal=True, gap=28, vertical_alignment="center"
        ):
            st.html(_BRAND_HTML)
            page = navigation.render()
            st.html(_SPACER_HTML)
            st.html(_account_html(data_as_of))
    return page
