# Deploying to Railway

Verified end to end in the container this repo builds: health check passes, the
password gate holds, the volume seeds itself, and the page renders identically to
local.

## Railway service settings

The `Dockerfile` and `railway.json` are picked up automatically. Two things have
to be set by hand.

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
time the page is opened, so the deployment is useful immediately: 20,351
original built-parcel candidates plus 13,986 vacant-parcel candidates across
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
without it the deployment keeps the old data. Leaving the variable set would
discard every cached ÖREB answer on each restart.

Before Streamlit starts, `bootstrap.py` seeds the volume when requested and
applies additive schema migrations. Railway therefore cannot mark a deployment
healthy while its persistent database still has an older application schema.

## If you do want recompute on the deployment

Upload `data/` onto the volume and the button does the full pipeline — the code
path is identical, it only checks whether the files are there. The parcel XML
downloads itself on first run; the GWR extract comes from
`public.madd.bfs.admin.ch/ag.zip`; the six AGIS GeoPackages are manual downloads
listed in the README.

## Local development is unaffected

Both path variables default to the repository, and the gate is inert while
`APP_PASSWORD` is unset:

    .venv/bin/streamlit run app.py
