# Deploying to Railway

Verified end to end in the container this repo builds: health check passes, the
password gate holds, the volume seeds itself, and the page renders identically to
local.

## Railway service settings

The `Dockerfile` is picked up automatically. Build, health check and restart
settings, the volume mount and the variable names live in `.railway/railway.ts`
(Railway Infrastructure as Code; it replaces `railway.json`, which Railway stops
reading on 2026-12-01). Preview and apply it with the Railway CLI (Node >= 22.6):

    cd .railway && npm ci --ignore-scripts && cd ..
    railway config plan     # read-only
    railway config apply    # changes the linked environment

Variables are listed by name only. A variable added in the dashboard must be added
to `.railway/railway.ts` too, or the next `railway config apply` deletes it.

Two things have to be set by hand.

**A volume, mounted at `/data`.** `DENSIFICATION_DB` and `DENSIFICATION_DATA`
already point inside it from the Dockerfile, so nothing else needs configuring.
Without a volume the app still runs, but every ÖREB answer is lost on redeploy.

**One environment variable:**

    APP_PASSWORD = <a shared password>

Set it. A Railway URL is public, and this list is the output of Philipp's own
research — which parcels to approach before anyone else does. With the variable
unset the app serves that to anyone who finds the URL.

## What works on first deploy

`results.sqlite` ships inside the image and is copied onto the volume the first
time the page is opened, so the deployment is useful immediately: 20,659
built-parcel candidates plus 15,615 vacant-parcel candidates across
165 municipalities, filters, land-price references, direct map links, and the
ÖREB check.

## What does not, and why

**"Neu berechnen" cannot recompute the cascade** on the deployment, because that
needs `data/` — roughly 600 MB of cantonal GeoPackages, the federal building
register and per-municipality parcel XML. It is gitignored and not in the image.
The button still runs the ÖREB half.

That is the right split rather than a limitation to fix. The zoning plans update
about yearly and the building register quarterly, so recomputing is not something
Philipp needs on demand — what he needs weekly is filtering and the cadastre
check, and both work.

## Updating the hosted data

1. Run `ingest.py` locally, where `data/` lives.
2. Commit the new `results.sqlite` and push; Railway redeploys.
3. Set `DENSIFICATION_RESEED=1` on the service for that one deploy, then remove
   it.

Step 3 is required: `paths.ensure_db()` will not overwrite a populated volume, so
without it the deployment keeps the old data. The reseed replaces only the
parcel data (`parcel_results`, `runs`) with the image's copy. Everything people
entered stays as it was — the team and its invitations, saved leads and contact
progress, settings, saved searches, shared calculations, the mail outbox — and
so do the ÖREB answers already paid for; the seed only adds answers the volume
lacks. (Before October 2026 the reseed copied the whole file over the volume's,
which wiped all of that.) Leaving the variable set only redoes the same swap on
every restart.

**Before the reseed, check which saved leads it would drop from the board.**
The board joins saved leads back onto the parcel results, so a lead whose
municipality was not part of the recompute — or whose parcel fell out of the
new data — loses its card even though its row survives. To list those leads
first, copy the volume's database and the freshly computed `results.sqlite`
somewhere local and run, against the copies (never the live volume):

    python3 scripts/report_unmatched_leads.py \
        --live-db /path/to/volume-copy.sqlite \
        --seed-db /path/to/new-results.sqlite \
        --output /secure/path/unmatched-leads.csv

It opens both files strictly read-only and prints only the count and the CSV
path. The CSV contains names, phone numbers and e-mail addresses — personal
data under the GDPR — so treat it like one: it is written with 0600
permissions, never overwritten, and belongs in whatever secured channel the
team already uses for lead data, not in the repository, a ticket, or a chat.

Before Streamlit starts, `bootstrap.py` seeds the volume when requested and
applies additive schema migrations. Railway therefore cannot mark a deployment
healthy while its persistent database still has an older application schema.

## If you do want recompute on the deployment

Upload `data/` onto the volume and the button does the full pipeline — the code
path is identical, it only checks whether the files are there. The parcel XML
downloads itself on first run; the GWR extract comes from
`public.madd.bfs.admin.ch/ag.zip`; the six AGIS GeoPackages are manual downloads
listed in the README.

## Optional: personal member accounts

Opt-in personal mode replaces the shared password with per-member login against
Normiq's shared Supabase Auth project. Set `SCOPE_AUTH_MODE=personal`,
`SCOPE_NORMIQ_AUTH_URL` (the canonical HTTPS origin of the Normiq app whose
custom passcode API issues the six-digit login codes; `http://127.0.0.1:<port>`
or `http://localhost:<port>` is accepted for local development),
`SCOPE_SUPABASE_URL` and `SCOPE_SUPABASE_ANON_KEY` (copied from Normiq's
`NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY`), plus
`SCOPE_OWNER_EMAIL` (an existing Normiq user), `SCOPE_PUBLIC_URL` and the
Resend variables. The app needs no service-role key. Login codes come from
Normiq's own passcode API/Resend template (`isSignup: false`), never from the
Supabase Magic Link template. Without `SCOPE_NORMIQ_PROVISIONING_SECRET`,
invitations work only for people who already have a Normiq account; with it
(the same value as Normiq's `SCOPE_PROVISIONING_SECRET`), an invited member's
first code request asks Normiq to create an identity without any Normiq
product access, and an existing Normiq account is left untouched.
`SCOPE_SUPABASE_URL`/`SCOPE_SUPABASE_ANON_KEY` remain in use for access-token
validation and the TOTP second factor. See `docs/scope-accounts.md` for
behavior, rollout and rollback; rollback is `SCOPE_AUTH_MODE=shared` with the
personal variables removed.

## Local development is unaffected

Both path variables default to the repository, and the gate is inert while
`APP_PASSWORD` is unset:

    .venv/bin/streamlit run app.py
