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
import organisation

#: The prototype's own mark lives at `static/scope-mark.svg`. `st.html`
#: sanitises inline SVG, and the app CSP does not paint SVG data URLs used as a
#: CSS background. Serving the exact exported asset through Streamlit's static
#: route keeps the artwork local and actually visible.

#: Wordmark + canton. `#8a8a94` (muted) and the `#eaeaee` divider have no
#: existing theme token to fall back on, so they are written here rather than
#: sourced from `.streamlit/config.toml`; ink and page background are not
#: repeated because every other span already inherits them from the theme.
_BRAND_HTML = f"""\
<div class="normiq-shell-brand" style="display:flex;align-items:center;gap:9px">
  <img class="normiq-shell-logo" src="app/static/scope-mark.svg" alt="" />
  <span style="font-size:13.5px;font-weight:600;letter-spacing:-0.01em">Scope</span>
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
#:
#: The chip used to end here, as inert markup with a `title` explaining that
#: the menu behind it did not exist. `organisation.py` gives it somewhere
#: genuine to go — an explicitly-labelled preview, not a working feature — so
#: the chip is now a real `st.button` (`_account_chip`, below) rather than a
#: non-interactive `div`.
_ACCOUNT_LABEL = "Gemeinsamer Zugang"


def _data_as_of_html(data_as_of: str) -> str:
    """The data-as-of stamp, split out from the account chip now that the
    chip is a real `st.button` and can no longer share one `st.html` blob
    with it."""
    return f"""\
<span class="normiq-shell-data" style="font-size:12px;color:#8a8a94;white-space:nowrap;
             display:flex;align-items:center;height:100%">
  Datenstand {data_as_of}
</span>
"""


def _account_chip() -> None:
    """The account area, now genuinely clickable: opens `organisation.py`'s
    preview dialog.

    A plain `st.button` rather than more HTML — the previous inert chip was
    hand-styled markup because there was nothing behind it to wire a real
    widget to; now that there is, using the actual widget is what makes it
    keyboard-reachable and lets Streamlit manage its own click/rerun cycle
    instead of this module re-implementing one over `st.html`.
    """
    if st.button(
        _ACCOUNT_LABEL,
        key="app_shell_account_open",
        help="Organisation öffnen (Vorschau).",
    ):
        st.session_state[organisation.DIALOG_OPEN] = True
        st.rerun()


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
  /* Escape the main block's page gutter so the divider and translucent
     background span the viewport, while the row below restores the design's
     28px content inset. */
  margin: -16px -28px 0;
  width: calc(100% + 56px);
  max-width: none;
  background: rgba(251, 251, 252, .92);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid #eaeaee;
}

/* Streamlit's default block container reserves 84px above the first element
   and limits content to 80rem. The prototype starts after a compact 16px inset
   and permits a 1560px canvas; keeping the default made the new shell look like
   a floating card and compressed the seven-column filters unnecessarily. */
[data-testid="stMainBlockContainer"] {
  max-width: 1560px;
  padding: 16px 28px 90px;
}

/* The content row: capped and centered like the prototype's, with its own
   padding and height rather than the bar's. */
.st-key-app_shell_row {
  max-width: 1560px;
  margin: 0 auto;
  padding: 0 28px;
  height: 52px;
  min-height: 52px;
  flex-wrap: nowrap !important;
  align-items: center;
}

/* Streamlit marks `st.html` children as width="100%" even inside a horizontal
   container. Left alone, brand, spacer and date each claim a full row and the
   52px shell becomes a 124px two-line block. The shell has explicit flex roles,
   so its children must size to content unless a marker below says otherwise. */
.st-key-app_shell_row > [data-testid="stElementContainer"] {
  flex: 0 0 auto !important;
  width: auto !important;
  min-width: 0;
}

.normiq-shell-logo {
  display: block;
  width: 20px;
  height: 20px;
  flex: none;
  background-position: center;
  background-repeat: no-repeat;
  background-size: contain;
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
  flex: 1 1 auto !important;
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

/* The account chip: a plain `st.button` (`data-testid="stButton"`, distinct
   from the `stButtonGroup` the pill track above uses, so this cannot bleed
   onto the nav) restyled to read as the same rounded, muted chip the
   prototype and the old inert markup both showed — clickable now, not just
   chip-shaped. */
.st-key-app_shell [data-testid="stButton"] button {
  background: #f1f1f4;
  border: 1px solid #e2e2e8;
  border-radius: 999px;
  color: #4a4a52;
  font-size: 12.5px;
  font-weight: 500;
  padding: 4px 14px;
}

.st-key-app_shell [data-testid="stButton"] button:hover {
  background: #e9e9ee;
  border-color: #d5d5dc;
  color: #1c4e4a;
}

/* Mobile keeps the account action and all four destinations reachable without
   letting five intrinsic-width Streamlit children turn into four tall rows.
   Brand + account share the first row; navigation gets the second; the data
   stamp is available on wider screens where it does not compete for space. */
@media (max-width: 760px) {
  .st-key-app_shell {
    position: sticky;
    margin: -12px -14px 0;
    width: calc(100% + 28px);
  }

  .st-key-app_shell_row {
    height: auto;
    min-height: 78px;
    padding: 10px 14px;
    gap: 8px !important;
    flex-wrap: wrap !important;
  }

  .st-key-app_shell_row > [data-testid="stElementContainer"]:has(.normiq-shell-brand) {
    order: 1;
    flex: 0 0 auto !important;
  }

  .st-key-app_shell_row > .st-key-app_shell_account_open {
    order: 2;
  }

  .st-key-app_shell_row > .st-key-acq_page {
    order: 3;
    flex: 0 0 100% !important;
    width: 100% !important;
  }

  .st-key-app_shell_row > [data-testid="stElementContainer"]:has(.normiq-shell-spacer),
  .st-key-app_shell_row > [data-testid="stElementContainer"]:has(.normiq-shell-data) {
    display: none;
  }

  .st-key-app_shell [data-testid="stButtonGroup"] {
    width: 100%;
  }

  .st-key-app_shell [data-testid="stButtonGroup"] [role="radiogroup"] {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    width: 100%;
    max-width: none;
  }

  .st-key-app_shell button[data-variant="segmented_control"] {
    width: 100%;
    padding-inline: 6px;
  }

  [data-testid="stMainBlockContainer"] {
    padding: 12px 14px 60px;
  }
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

    `organisation.open_if_requested` is called after the row rather than
    from inside it, alongside the chip that can set `organisation.DIALOG_OPEN`
    — a `st.dialog` renders as a portal regardless of where in the tree it is
    called from, so nesting it inside the flex row would buy nothing and
    only muddy which element the row's CSS is meant to reach. Same shape as
    `acquisition._render_open_contact_dialog`, which sits after the board
    for the same reason — see `organisation.DIALOG_OPEN`'s docstring for why
    it has to run on a later script run than the click that requested it.
    """
    with st.container(key="app_shell"):
        st.html(_SHELL_CSS)
        with st.container(
            key="app_shell_row", horizontal=True, gap=28, vertical_alignment="center"
        ):
            st.html(_BRAND_HTML)
            page = navigation.render()
            st.html(_SPACER_HTML)
            st.html(_data_as_of_html(data_as_of))
            _account_chip()
        organisation.open_if_requested(data_as_of)
    return page
