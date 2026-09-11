#!/bin/zsh
# Run Scope locally with the process environment the app expects.
#
# The app reads process environment only (no dotenv). This exports
# `.env.local` (gitignored; see `.env.example` for the names) and starts
# Streamlit on 127.0.0.1. Use `SCOPE_PORT` to pick a port (default 8521).
#
# Resend for invitation e-mails: set RESEND_API_KEY / RESEND_FROM_EMAIL in
# `.env.local`. As a local convenience, if they are missing and the sibling
# `normiq-frontend/.env.local` exists, its Resend values are exported for this
# process only — nothing is copied to disk.
set -euo pipefail
APP="${0:A:h:h}"
cd "$APP"

if [[ -f .env.local ]]; then
  set -a; source .env.local; set +a
fi
SIBLING="$APP/../normiq-frontend/.env.local"
if [[ -z "${RESEND_API_KEY:-}" && -f "$SIBLING" ]]; then
  export RESEND_API_KEY="$(grep -E '^RESEND_API_KEY=' "$SIBLING" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
  export RESEND_FROM_EMAIL="${RESEND_FROM_EMAIL:-$(grep -E '^RESEND_FROM_EMAIL=' "$SIBLING" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")}"
  export RESEND_FROM_NAME="${RESEND_FROM_NAME:-Scope}"
fi
[[ -n "${RESEND_API_KEY:-}" ]] || echo "run-local: RESEND_API_KEY not set — invitation e-mails will report 'noch nicht konfiguriert'." >&2

PY="$APP/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3
exec "$PY" -m streamlit run app.py \
  --server.address 127.0.0.1 --server.port "${SCOPE_PORT:-8521}" \
  --server.headless true --browser.gatherUsageStats false
