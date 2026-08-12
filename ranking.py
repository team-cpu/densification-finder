"""Ranking rules for built, vacant, and mixed parcel result lists."""

import pandas as pd


def rank_candidates(candidates: pd.DataFrame, parcel_type: str) -> pd.DataFrame:
    """Rank candidates without comparing incompatible built/vacant scores.

    Built parcels rank by additional floor area relative to estimated existing
    floor area. Vacant parcels have no meaningful ratio, so they rank by
    absolute developable floor area. A mixed list interleaves both rankings so
    ``Alle`` actually represents both parcel types in every result window.
    """
    ratio = candidates["delta"] / candidates["existing"].clip(lower=1)
    ranked = candidates.assign(ratio=ratio)

    built = ranked[ranked["buildings"] > 0].sort_values(
        ["ratio", "delta"], ascending=[False, False], kind="stable"
    )
    vacant = ranked[ranked["buildings"] == 0].sort_values(
        "delta", ascending=False, kind="stable"
    )

    if parcel_type == "Bebaut":
        return built
    if parcel_type == "Unbebaut":
        return vacant
    if parcel_type != "Alle":
        raise ValueError(f"Unknown parcel type: {parcel_type}")

    built = built.assign(_kind_order=0, _kind_rank=range(len(built)))
    vacant = vacant.assign(_kind_order=1, _kind_rank=range(len(vacant)))
    return pd.concat((built, vacant)).sort_values(
        ["_kind_rank", "_kind_order"], kind="stable"
    )
