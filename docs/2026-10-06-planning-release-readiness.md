# Planning panel: release and data activation

Status: release preparation in the local `feat/scope-regulation-panel` worktree.
History up to 6 October was strictly local-only: no push, production deployment,
scheduled provider access or external message was made by any continuation.
On 7 October the user explicitly authorized push/deployment for the current
release preparation; the lead performs review, commit/push and the actual
deployment. As of this document no deployment has succeeded and none is claimed
— a successful deployment still has to be proven by the lead after push.
Local preview data is not a production data feed.

## Target verified on 6 October 2026

Read-only Railway checks resolved the Scope target explicitly:

- Project `blissful-presence`: `c3889dad-ecec-41f7-9001-cb259b0f8e4b`.
- Service `densification-finder`: `9b5afa30-7994-4629-9ba0-790347678bda`.
- Environment `production`: `69e082c5-26d9-4aa1-8bbc-23edb2400473`.
- Domain: `scope.normiq.ai`; existing `/data` volume.
- Active successful deployment `fb811d1d-b3df-42f9-8c3f-f2b60b258604`, created
  30 September 2026, commit `dcf3fa111421926a6f1f73194506ca4df9ec28af` on `master`.
- `SCOPE_PLANNING_PUBLICATIONS`, `SCOPE_PLANNING_PREVIEW` and
  `SCOPE_PLANNING_INFERENCE` were unset/empty. `APP_PASSWORD` is configured;
  its value was not printed or copied. Auth/reseed settings were not changed.
- A read-only Python probe confirmed that the running container has working
  `Europe/Zurich` timezone data (+02:00 on 6 October).

Do not use the implicit Railway project link: it can point to Normiq instead.
A successful localhost test is not evidence of a production deployment.

## Data reviewed locally

### Initial snapshot (historical, 40 records)

The existing Seon/Menziken search snapshots contain 40 unique publications:
24 included and 16 excluded; no held/unreviewed decisions remain in this
snapshot. This 40-record set is only the initial snapshot, not the final
reviewed store described below. Other municipalities do not have complete
municipal query coverage. A multi-municipality canton approval is scoped to
its named municipalities; only one reviewed consultation is currently included
canton-wide.

The three previously held entries were excluded from this panel after review:

| Publication | Reason within this panel's scope | Additional primary source |
|---|---|---|
| `00.003.015` | Road-noise remediation and relief applications for existing streets/properties; the notice does not establish a change to general building or zoning rules. This does not assert that noise is irrelevant to an affected property. | [Seon notice](https://www.seon.ch/verwaltung/aktuelles/news.html/87/news/29/newsarchive/1) |
| `00.049.009` | Road classification/register consultation; no concrete BNO or land-use-plan amendment established by the inspected material. Access and contribution-cost questions remain outside this panel. | [Seon consultation](https://www.seon.ch/verwaltung/aktuelles/news.html/87/news/3187), [municipal regulations](https://www.seon.ch/verwaltung/dienstleistungen.html/21/egov_service/493) |
| `6e061a0a-800c-4028-baea-ff4d7b48064f_de` | General mobility strategy; the source leaves concrete measures to later planning instruments. No specific change in building rules identified. | [Canton consultation](https://www.ag.ch/de/themen/staat-politik/anhoerungen-vernehmlassungen/laufende-anhoerungen?dc=6e061a0a-800c-4028-baea-ff4d7b48064f_de) |

Thirteen explicit legal-source facts have been applied locally for eleven publication
IDs: seven public-display intervals and six municipality-specific canton
approval checks. These establish historical facts, not a complete current
procedure history. In particular, Menziken `00.102.357` states a display interval
of 4 September–5 October 2026; on 6 October it cannot receive an active
“Entwurf in Auflage” label. Seon Heidegrabe is Mitwirkung, not formal Auflage.
Neither municipal referendum expiry nor a canton approval alone proves that
a plan is currently in force. OEREBlex's existing in-force rows are separate.

### Final reviewed store (6 October)

On 6 October the full review queue of 1502 publications received bound
decisions (each tied to publication ID, metadata fingerprint, source URL and
raw-response hash): 1380 included with verified territorial coverage, 120
excluded, 2 held. Merged with the earlier snapshot, the final reviewed store
contains 1542 candidates: 1404 included, 136 excluded, 2 held, with no
unreviewed items and no changed fingerprints. The initial 40 reviewed records
and the 13 prior legal-source facts remain intact inside this store; the new
batch overwrote no earlier review or evidence.

The 2 held items are not shown as approved items and received no coverage or
stage evidence:

| Publication | Reason held |
|---|---|
| `00.014.042` (Windpark Lindenberg) | Source announces a Mitwirkung report but does not establish which planning/regulation instrument is being changed; no coverage or stage evidence given on suspicion. |
| `00.035.267` (Rudolfstetten-Friedlisberg) | Raw text inspected but source HTML failed structural validation; the raw response is preserved and the item is held without coverage or stage evidence. |

767 new stage-evidence records confirm source events/dates: 401 canton
approvals, 10 rejections and 356 Auflage periods. No new `in_kraft` proof was
created, and no new force proof was added anywhere in this pass. Eleven
already-corrected original versions were barred from grounding new stage
evidence. Old approvals or expired deadlines do not by themselves mean a rule
is in force today; items with insufficient stage evidence stay neutral.

Review verification on 6 October (lead-run): 1501 raw articles validated with
1 response quarantined (0 unrecorded failures/guards; one early timeout
recovered by a single manual retry); a complete decision audit over exactly
1502 IDs/fingerprints; 154 targeted automated tests passed (102
preview/detail/local-integration, 38 reader, 14 apply-helper); 163 dataset
municipality views rendered and passed through the real store loader and HTML
renderer — titles and source links present, both held items absent, no
"Relevant" badge and no unrelated 227-municipality list. These 163 are the
municipalities present in the local dataset, not a claim of coverage of all
municipalities in the canton. Four real-application Analyse views passed
through Streamlit AppTest without exceptions: Seon (16 preview items),
Oberrüti (13), Birmenstorf (25), Menziken (25), including the corrected
Oberrüti deadline of 14 October 2026 with an active Auflage badge and the
Birmenstorf rejection still recorded as a rejection. Afterwards browser visual
verification was completed for two municipal examples (Seon parcel 1268 with
its BNO in force and neutral unproved statuses; Oberrüti parcel 918 with the
amber active Auflage badge, explicit 14 September–14 October 2026 dates, and a
click on the source arrow opening the original corrected Amtsblatt notice).
That visual check covered two municipalities, not all 163 dataset
municipalities. The final reviewed store hash, review inputs and baseline were
re-verified after testing; the 40 reviewed records and 13 prior proofs stayed
intact together with all 327 source selections and candidate metadata.

## Scheduled refresh configuration

The existing Docker command already starts `scheduler.py`. The planning refresh
hook is independent of personal-mode email jobs and is off by default. It
collects bounded metadata and maintains a review queue; it does not approve new
publications or infer their legal status.

Before enabling a live scheduled job, retain a documented basis for both
provider access and the planned Scope reuse. The flag below records an
operator's configuration; it does not itself grant rights.

Configuration to prepare after that basis exists:

```text
SCOPE_PLANNING_PREVIEW=/data/planning-preview.json
SCOPE_PLANNING_REFRESH_ENABLED=1
SCOPE_PLANNING_REFRESH_MUNICIPALITIES=Seon,Menziken
SCOPE_PLANNING_REFRESH_HOUR=8
SCOPE_PLANNING_REFRESH_MAX_PAGES=5
SCOPE_PLANNING_REFRESH_TIMEOUT=20
SCOPE_PLANNING_REUSE_AUTHORIZED=1
SCOPE_PLANNING_REUSE_REFERENCE=<actual approval or reviewed legal-basis reference>
```

Leave `SCOPE_PLANNING_PUBLICATIONS` and `SCOPE_PLANNING_INFERENCE` unset for this
preview path. Do not copy an invented permission reference or enable a live
scheduled job merely to satisfy configuration validation.

`python -m planning_refresh --check` checks configuration without fetching
providers or creating state. `--once` observes the same enablement, daily due
time, lock and retry rules as the background job. Source failures must preserve
last good data and remain visible. Successful repeat runs must not bypass review.
The schedule uses Europe/Zurich, not the operator laptop's timezone.

## Release sequence

1. Review the complete release diff, including pre-existing auth-adjacent,
   SQLite-startup, ÖREB refresh and PDF changes in this worktree. Never upload an
   unfiltered working directory with credentials, scratch review files or local
   databases. Prepare an isolated allowlisted release snapshot with file hashes.
2. Run the full Python suite, the calculation-table Node suite and the security
   scan on that exact snapshot. Verify the real panel by browser visual
   inspection.
3. Preserve the existing production database and auth configuration. Use the
   persistent `/data` volume for both review and scheduler state; do not set
   `DENSIFICATION_RESEED=1`. No schema/data deletion is needed for this work.
4. Deploy an approved, fixed source snapshot with explicit Scope project,
   service and environment identifiers. Keep data activation disabled until the
   source-use basis exists. A CLI upload is a real production change and is not
   performed by this document.
5. Confirm Railway reports a successful deployment for the intended release,
   check `/_stcore/health`, and verify sign-in plus the parcel panel in the
   production browser. A healthy endpoint alone does not prove correct data.
6. After data activation is permitted, seed/import only validated metadata and
   reviewed decisions on the persistent volume. Run `--check`, then one due
   pass; inspect successful and failed source timestamps and the review queue.
   Check Seon, Menziken and a municipality without municipal coverage. Retain
   neutral status for unsupported or expired proofs. Preview data stays outside
   the PDF export.

## Rollback

Disable `SCOPE_PLANNING_REFRESH_ENABLED` to stop scheduled provider access. Unset
`SCOPE_PLANNING_PREVIEW` to restore the source-link fallback. Preserve the review
store and scheduler sidecars for investigation. Roll back code to the previous
known successful deployment if necessary; do not drop or reseed the database.

## External dependency

The [permission/data-access request](2026-10-05-amtsblatt-freigabe-anfrage.md)
is drafted and ready for the sender to fill in the company, actual audience and
signature. It has not been sent. A source response or documented legal basis is
still needed before activating the production data feed; the provider-reuse
basis remains unresolved. No email subscription (Suchabo) is required by the
implemented direct-metadata technical route.

## Verification completed

6 October (lead, historical): the full suite passed **952 tests, 56 subtests;
one existing address-fixture assertion skipped**, in 107.59 seconds; the
calculation-table Node suite passed 12 tests; the affected suite independently
passed 556 tests. That record is superseded for release purposes by the fresh
7 October gate below.

7 October (executor, current authorized release preparation): the full Python
suite passed **973 tests, 56 subtests; the same one existing address-fixture
assertion skipped**, in 111.82 seconds, exit 0. The calculation-table Node
suite passed 12 tests. `git diff --check` and syntax compilation of all changed
runtime modules passed. `python -m planning_refresh --check` and `--once` with
refresh explicitly disabled and no preview/publications configured both
returned `off` (exit 0, no provider access). The production-platform dependency
audit (Python 3.11, x86_64 Linux) resolved 40 packages and found no known
vulnerabilities; Bandit returned no findings; the secret scan found only dummy
test fixtures. Recurring refresh stays off and no force proofs were added.
See the [7 October strict security gate](2026-10-07-planning-release-security.md)
and the [scoped security report](2026-10-06-planning-security-verification.md)
for scope, commands, exclusions and the distinction between security
verification and data-use authorization.

A source-only release candidate and SHA256 manifest are saved with the local
review artifacts as `scope-release-candidate-20261006.tar.gz` and
`scope-release-manifest-20261006.json`. The archive is prepared for review; it
has not been uploaded or deployed. It contains repository source and its
committed seed database, not the scratch reviewed data store or environment
credentials. Keep the separately saved metadata/proof records out of the image.
