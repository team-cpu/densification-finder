# Login page — design

Date: 2026-09-11. Approved option: **A — single card, two steps in one form**;
the shared-password gate gets the same card.

## Why

Both gates were bare Streamlit: default toolbar, an `st.title`, unlabeled
stacked widgets. They are the first thing a member sees on the public Railway
URL and the only page that does not use the Scope shell.

## What

A `login_page` module owns the card and its stylesheet; `scope_auth.gate()`
and `app.gate()` render their existing forms inside it. Authentication logic,
messages, widget labels and rate limits are unchanged.

- Streamlit header/toolbar hidden (same rule the shell uses), page on the
  theme background `#fbfbfc`.
- Card: 400 px, centred, top offset ~10 vh, white, `1px #ebebef` border,
  10 px radius. Brand row (`static/scope-mark.svg` + "Scope"), heading
  "Anmelden", one-line caption.
- Personal mode: uppercase field labels; "E-Mail-Adresse" → secondary
  full-width "Code anfordern"; "Anmeldecode" (six digits, monospace, wide
  letter-spacing) → primary full-width "Anmelden". Caption under the code
  field: "Sechsstellig, gilt 10 Minuten." Status and error messages stay
  inside the card.
- Shared mode: caption "Gemeinsamer Zugang mit Passwort.", one "Passwort"
  field and a primary "Anmelden" button in a form (Enter still submits).
- Responsive: below 480 px the card fills the width with 16 px margins.

## Not in scope

Two-step wizard, remembering the e-mail, 2FA, any change to code request /
verification behaviour.

## Verification

`test_scope_auth` widget tests keep passing unchanged (labels and buttons are
the same). New assertions: card container present on both gates, toolbar
hidden, shared gate accepts the password through the form button and rejects a
wrong one with the existing message. Browser check of both gates locally.
