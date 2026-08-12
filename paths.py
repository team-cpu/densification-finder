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
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

DATA = os.environ.get("DENSIFICATION_DATA") or os.path.join(HERE, "data")
DB = os.environ.get("DENSIFICATION_DB") or os.path.join(HERE, "results.sqlite")

#: The copy committed to git — ~20,600 assessed candidates. Used to seed an
#: empty volume so a first deploy shows a working list immediately, rather than
#: "no results yet" from an app that cannot recompute until the geodata has been
#: uploaded separately.
SEED_DB = os.path.join(HERE, "results.sqlite")


def on_persistent_disk():
    """Whether DB sits on a mounted volume rather than the container filesystem.

    Worth asking because the failure is silent: without a volume the app runs
    correctly and simply forgets every ÖREB answer on each redeploy, which looks
    like the cadastre being slow rather than like a misconfiguration. The path
    alone cannot tell — DENSIFICATION_DB points at /data either way — so this
    reads the mount table.

    None when the question does not apply (no /proc, i.e. not on Linux).
    """
    if not os.path.exists("/proc/mounts"):
        return None
    target = os.path.dirname(os.path.abspath(DB))
    try:
        with open("/proc/mounts") as fh:
            mounts = {line.split()[1] for line in fh if len(line.split()) > 1}
    except OSError:
        return None
    return target in mounts


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
        _copy_seed_atomically()
        return True
    if os.path.exists(DB) and os.path.getsize(DB) > 0:
        return False
    _copy_seed_atomically()
    return True


def _copy_seed_atomically():
    """Replace the live database only after the seed copy is complete."""
    directory = os.path.dirname(DB) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".densification-seed-", suffix=".sqlite", dir=directory
    )
    os.close(fd)
    try:
        shutil.copy2(SEED_DB, temporary)
        # Same-directory replacement is atomic: readers see either the old
        # complete database or the new complete database, never a partial copy.
        os.replace(temporary, DB)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
