"""SQLite identifier quoting.

The remediated SQL sites build external identifiers through `ident`.
SQLite's rule is simple: a delimited identifier
is wrapped in double quotes, and an embedded double quote is written twice.
That accepts everything a real GeoPackage table or column can legitimately be
named — spaces, digits at the front, unicode — while every remaining
character is part of the name, never syntax. Names are deliberately NOT
restricted to an ASCII pattern, which would reject valid SQLite names.

Audit provenance: docs/2026-09-29-bandit-review.md (Bandit B608 remediation).
"""


def ident(name: str) -> str:
    """Quote one SQLite identifier, rejecting values that are not names."""
    if not isinstance(name, str):
        raise TypeError(
            f"SQL identifier must be a string, got {type(name).__name__}"
        )
    if not name:
        raise ValueError("SQL identifier must not be empty")
    if "\x00" in name:
        raise ValueError("SQL identifier must not contain NUL")
    return '"' + name.replace('"', '""') + '"'
