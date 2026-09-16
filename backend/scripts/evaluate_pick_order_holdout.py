"""Compare blind confirmed orders to full-route review suggestions; never promote them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.data.pick_order_consistency import (  # noqa: E402
    check_blind_label_audit,
    check_vod_verification,
    score_holdout,
)
from backend.services.data.pick_order_gallery_release import (  # noqa: E402
    validate_gallery_release,
)
from backend.services.data.pick_order_media_rights import require_media_rights  # noqa: E402
from backend.services.data.vod_pick_order_suggestions import (  # noqa: E402
    HERO_REFERENCE_GALLERY_DIR,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--suggestions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--vod-verification", type=Path)
    parser.add_argument("--media-rights-file", type=Path)
    parser.add_argument("--gallery-manifest", type=Path)
    parser.add_argument("--gallery-dir", type=Path, default=HERO_REFERENCE_GALLERY_DIR)
    args = parser.parse_args()
    for path in (args.holdout, args.gold, args.suggestions):
        if not path.is_file():
            parser.error(f"Missing input: {path}")
    try:
        holdout, gold, suggestions = (
            load_json(args.holdout), load_json(args.gold),
            load_json(args.suggestions)
        )
        report = score_holdout(holdout, gold, suggestions)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    sources = load_json(ROOT / "backend/data/vod_sources.json")["sources"]
    channels = {source["source_id"]: source["channel_id"] for source in sources}
    audit_errors = check_blind_label_audit(
        holdout, gold, suggestions,
        load_json(args.audit) if args.audit and args.audit.is_file() else {},
    )
    vod_errors = check_vod_verification(
        holdout,
        load_json(args.vod_verification) if args.vod_verification and
        args.vod_verification.is_file() else {},
        channels,
    )
    rights_error = None
    gallery_error = None
    suggestion_gallery_consistent = False
    try:
        rights = require_media_rights(
            args.media_rights_file, {channels[game["source_id"]]
                                     for game in holdout["games"]}
        )
        if not rights.get("freeze_private_gallery"):
            raise ValueError("Rights record does not include gallery retention")
    except (KeyError, TypeError, ValueError) as exc:
        rights_error = str(exc)
    try:
        if args.gallery_manifest is None:
            raise ValueError("Frozen gallery manifest is missing")
        gallery_release = validate_gallery_release(
            args.gallery_dir, args.gallery_manifest
        )
        report["gallery_release_id"] = gallery_release["release_id"]
        complete_rows = [
            row for row in suggestions.get("games", []) if row.get("order_complete")
        ]
        suggestion_gallery_consistent = bool(complete_rows) and all(
            (row.get("provenance") or {}).get("gallery_release_id") ==
            gallery_release["release_id"]
            for row in complete_rows
        )
        if not suggestion_gallery_consistent:
            raise ValueError("Complete suggestions do not use the frozen gallery release")
    except ValueError as exc:
        gallery_error = str(exc)
    report.update({
        "label_audit_valid": not audit_errors,
        "label_audit_errors": audit_errors,
        "official_vods_verified": not vod_errors,
        "vod_verification_errors": vod_errors,
        "media_rights_valid": rights_error is None,
        "media_rights_error": rights_error,
        "gallery_release_valid": gallery_error is None,
        "gallery_release_error": gallery_error,
        "suggestion_gallery_consistent": suggestion_gallery_consistent,
    })
    report["weekly_ready"] = (
        report["gate_passed"] and not audit_errors and not vod_errors and
        rights_error is None and gallery_error is None
    )
    save_json(args.output, report)
    print(json.dumps({
        "gate_passed": report["gate_passed"],
        "weekly_ready": report["weekly_ready"],
        "layouts": report["layouts"],
    }, sort_keys=True))
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
