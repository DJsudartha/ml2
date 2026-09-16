from __future__ import annotations

from typing import Any

__all__ = [
    "build_ban_dataset",
    "build_ordered_pick_dataset",
    "build_hero_feature_table",
]


def __getattr__(name: str) -> Any:
    if name in {"build_ban_dataset", "build_ordered_pick_dataset"}:
        from backend.services.modeling.dataset_builder import (
            build_ban_dataset,
            build_ordered_pick_dataset,
        )

        return {
            "build_ban_dataset": build_ban_dataset,
            "build_ordered_pick_dataset": build_ordered_pick_dataset,
        }[name]
    if name == "build_hero_feature_table":
        from backend.services.modeling.features import build_hero_feature_table

        return build_hero_feature_table
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
