"""
Where the data and the results live.

Locally both sit next to the code, which is what every module assumed. Deployed,
they have to sit on a mounted volume instead: the source geodata is ~600 MB of
GeoPackage, XML and CSV, far too much for an image layer, and a SQLite file
inside the container would be discarded on every redeploy.

    DENSIFICATION_DATA   directory holding the cantonal downloads
    DENSIFICATION_DB     path to results.sqlite

Both default to the repository, so nothing changes for local work.
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))

DATA = os.environ.get("DENSIFICATION_DATA") or os.path.join(HERE, "data")
DB = os.environ.get("DENSIFICATION_DB") or os.path.join(HERE, "results.sqlite")

#: The copy committed to git — ~20,600 assessed candidates. Used to seed an
#: empty volume so a first deploy shows a working list immediately, rather than
#: "no results yet" from an app that cannot recompute until the geodata has been
#: uploaded separately.
SEED_DB = os.path.join(HERE, "results.sqlite")


def ensure_db():
    """Copy the committed results into place if the configured path has none.

    Only ever creates; a populated file is left alone, so a recompute on the
    volume is never overwritten by the older copy baked into the image.

    "Has none" deliberately means empty-or-absent rather than absent. Any
    `sqlite3.connect()` on a missing path creates a zero-byte file as a side
    effect — a stray diagnostic is enough — and treating that as "already
    seeded" would leave the volume permanently broken with no way back except
    deleting it by hand.
    """
    if DB == SEED_DB or not os.path.exists(SEED_DB):
        return False
    # DENSIFICATION_RESEED=1 forces the image's copy over the volume's, which is
    # how a recompute done locally and committed reaches the deployment. Without
    # it, updating the hosted data would mean deleting a file on a live volume by
    # hand. Set it for one deploy, then unset — leaving it on would discard every
    # ÖREB answer the hosted app has paid for on each restart.
    if os.environ.get("DENSIFICATION_RESEED") == "1":
        os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
        shutil.copy2(SEED_DB, DB)
        return True
    if os.path.exists(DB) and os.path.getsize(DB) > 0:
        return False
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    shutil.copy2(SEED_DB, DB)
    return True
