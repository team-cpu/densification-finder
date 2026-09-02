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

#: The exported Scope header contains only the mark and wordmark. The canton
#: belongs to the filter panel below; repeating it here made the live header
#: visibly wider than the supplied design.
_BRAND_HTML = """\
<div class="normiq-shell-brand" style="display:flex;align-items:center;gap:9px">
  <img class="normiq-shell-logo" src="app/static/scope-mark.svg" alt="" />
  <span style="font-size:13.5px;font-weight:600;letter-spacing:-0.01em">Scope</span>
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
<span class="normiq-shell-data" style="font-size:11.5px;color:#77777f;white-space:nowrap;
             display:flex;align-items:center;height:100%">
  Datenstand <span style="font-family:'IBM Plex Mono',monospace">{data_as_of}</span>
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

/* The product has its own complete application header. Streamlit's floating
   development toolbar otherwise sits above the first 52px of the document and
   covers that header exactly: the wordmark and navigation are present in the
   accessibility tree but invisible on screen. Remove the redundant chrome so
   the product shell owns the top edge, as it does in the supplied design. */
[data-testid="stHeader"],
[data-testid="stToolbar"] {
  display: none;
}

/* Streamlit's default block container reserves 84px above the first element
   and limits content to 80rem. Scope is a data-dense work surface, so the
   application canvas follows the browser width instead of stopping at 1560px
   and leaving half of an ultrawide display empty. */
[data-testid="stMainBlockContainer"] {
  width: 100%;
  max-width: none;
  padding: 16px 28px 90px;
}

/* The shell uses the same full-width canvas as every page. Keeping a separate
   cap here would make the navigation stop while the tables continue. */
.st-key-app_shell_row {
  width: 100%;
  max-width: none;
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
  height: 26px;
  min-height: 26px;
  padding: 0 11px;
  border: 0 !important;
  border-radius: 5px;
  background: transparent;
  color: #8a8a94;
  font-size: 12px;
  font-weight: 500;
}

/* Raised on white with a subtle ring, rather than Streamlit's own default
   selected state (a wash of the theme's primary colour) — the prototype's
   pills read as flat/inactive vs. raised/active, not as unaccented/accented. */
.st-key-app_shell button[data-variant="segmented_control"][data-selected]:not([data-disabled]) {
  background-color: #ffffff;
  color: #17171b;
  box-shadow: 0 0 0 1px #e4e4ea;
}

/* The account action is intentionally flat in the export. It gains a quiet
   background only on hover; the large pill used previously was not present
   in the supplied design. */
.st-key-app_shell_row > .st-key-app_shell_account_open {
  margin-left: -10px;
  padding-left: 18px;
  border-left: 1px solid #e6e6ea;
}

.st-key-app_shell [data-testid="stButton"] button {
  height: 28px;
  min-height: 28px;
  gap: 8px;
  padding: 0 8px 0 5px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #77777f;
  font-size: 11.5px;
  font-weight: 400;
}

.st-key-app_shell [data-testid="stButton"] button:hover {
  border: 0;
  background: #f1f1f4;
  color: #17171b;
}

/* Match the exported account avatar: a compact green circle with the MB
   initials. The surrounding label remains truthful to this app's shared-access
   model instead of copying the prototype's invented organisation name. */
.st-key-app_shell_account_open button::before {
  content: "MB";
  display: inline-flex;
  width: 19px;
  height: 19px;
  flex: 0 0 19px;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #1c4e4a;
  color: #fff;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: .02em;
}

.st-key-app_shell_account_open button::after {
  content: "▾";
  color: #a8a8b2;
  font-size: 9px;
  line-height: 1;
  transform-origin: center;
}

/* Mobile keeps the account action and all four destinations reachable without
   letting five intrinsic-width Streamlit children turn into four tall rows.
   Brand + account share the first row; navigation gets the second; the data
   stamp is available on wider screens where it does not compete for space. */
@media (max-width: 960px) {
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
    margin-left: 0;
    padding-left: 0;
    border-left: 0;
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
