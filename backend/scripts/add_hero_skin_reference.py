from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.data.vod_pick_order_suggestions import (  # noqa: E402
    HERO_ICON_DIR,
    HERO_REFERENCE_GALLERY_DIR,
)
from backend.services.data.raw_games import (  # noqa: E402
    RAW_TOURNAMENTS_DIR,
    iter_raw_games,
)


def add_reference(
    hero_name: str,
    image_path: Path,
    gallery_dir: Path,
    *,
    known_heroes: set[str] | None = None,
) -> Path:
    canonical_exists = (HERO_ICON_DIR / f"{hero_name}.png").exists()
    if not canonical_exists and hero_name not in (known_heroes or set()):
        raise ValueError(f"Unknown hero name or missing canonical asset: {hero_name}")
    if not image_path.exists() or image_path.suffix.casefold() not in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }:
        raise ValueError(f"Expected a supported review crop: {image_path}")

    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()[:16]
    destination = gallery_dir / hero_name / f"{digest}{image_path.suffix.casefold()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(image_path, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add a human-confirmed broadcast/skin crop to a hero reference gallery."
    )
    parser.add_argument("--hero", required=True, help="Exact hero name used by project data.")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--gallery-dir", type=Path, default=HERO_REFERENCE_GALLERY_DIR)
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_TOURNAMENTS_DIR,
        help="Liquipedia raw-game directory used to validate newly introduced heroes.",
    )
    args = parser.parse_args()
    known_heroes = {
        hero
        for game in iter_raw_games(args.raw_dir)
        for team in ("blue", "red")
        for hero in game.get(f"{team}_picks", [])
    }
    destination = add_reference(
        args.hero,
        args.image,
        args.gallery_dir,
        known_heroes=known_heroes,
    )
    print(f"Added reference: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
