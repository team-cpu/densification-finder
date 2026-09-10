"""Rank screening results by absolute potential, matching the reference UI."""

import pandas as pd


def rank_candidates(candidates: pd.DataFrame, parcel_type: str) -> pd.DataFrame:
    """Filter the parcel type, then show the largest additional floor area first.

    Stable ties retain source order. The ratio remains available to existing
    consumers, but does not determine which parcels enter the result window.
    """
    if parcel_type not in ("Bebaut", "Unbebaut", "Alle"):
        raise ValueError(f"Unknown parcel type: {parcel_type}")
    ranked = candidates.assign(
        ratio=candidates["delta"] / candidates["existing"].clip(lower=1)
    )
    if parcel_type == "Bebaut":
        ranked = ranked[ranked["buildings"] > 0]
    elif parcel_type == "Unbebaut":
        ranked = ranked[ranked["buildings"] == 0]
    return ranked.sort_values("delta", ascending=False, kind="stable")
