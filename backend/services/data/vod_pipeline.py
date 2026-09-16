from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from typing import Any, Callable, TypeVar

from backend.services.common.file_utils import load_json
from backend.services.data.pick_order_annotations import (
    PICK_ORDER_ANNOTATIONS_PATH,
    load_pick_order_annotations,
)
from backend.services.data.raw_games import RAW_TOURNAMENTS_DIR, load_raw_games_by_id
from backend.services.data.vod_layouts import (
    LAYOUTS_PATH,
    DraftWindow,
    crop_slots,
    load_layout,
    load_layout_registry,
    locate_draft_windows_from_timed_frames,
)
from backend.services.data.vod_pick_order_suggestions import (
    EXTRACTOR_VERSION,
    extract_hero_identity_observations,
    stable_hero_events,
    suggest_pick_order_from_hero_events,
)
from backend.services.vod_downloader import (
    DEFAULT_MAX_TEMP_BYTES,
    clear_frame_directory,
    extract_remote_section_frames,
)

RUNTIME_DIR = Path("backend/data/runtime/pick_order")
DEFAULT_LEDGER_PATH = RUNTIME_DIR / "jobs.sqlite3"
DEFAULT_RESULTS_DIR = Path("backend/data/raw/pick_order_suggestions/jobs")
DEFAULT_REVIEW_PATH = Path("backend/data/raw/pick_order_suggestions/review.json")
COARSE_FPS = 0.2
DETAIL_FPS = 1.0
MAX_SECTION_SEC = 720.0

T = TypeVar("T")


class UnsupportedLayoutError(ValueError):
    pass


class DraftWindowNotFoundError(ValueError):
    pass


class JobLedger:
    def __init__(self, path: Path = DEFAULT_LEDGER_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_key TEXT PRIMARY KEY,
                    game_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    output_path TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def completed_output(self, job_key: str) -> Path | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, output_path FROM jobs WHERE job_key = ?",
                (job_key,),
            ).fetchone()
        if not row or row["status"] != "completed" or not row["output_path"]:
            return None
        output_path = Path(str(row["output_path"]))
        return output_path if output_path.exists() else None

    def start(self, job_key: str, game_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (job_key, game_id, status, attempts, updated_at)
                VALUES (?, ?, 'running', 1, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    status = 'running',
                    attempts = attempts + 1,
                    error = NULL,
                    updated_at = excluded.updated_at
                """,
                (job_key, game_id, now),
            )

    def complete(self, job_key: str, output_path: Path) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = 'completed', output_path = ?, error = NULL, "
                "updated_at = ? WHERE job_key = ?",
                (str(output_path), datetime.now(timezone.utc).isoformat(), job_key),
            )

    def fail(self, job_key: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = 'failed', error = ?, updated_at = ? WHERE job_key = ?",
                (error[:2000], datetime.now(timezone.utc).isoformat(), job_key),
            )


def _safe_path_segment(value: str) -> str:
    safe = "".join(character if character.isalnum() or character in "_.-" else "_" for character in value)
    return safe.strip("_") or "job"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def job_key(entry: dict[str, Any], layout: dict[str, Any]) -> str:
    identity = {
        "game_id": entry.get("game_id"),
        "video_id": entry.get("video_id"),
        "vod_url": entry.get("vod_url"),
        "range": [
            entry.get("detected_start_sec", entry.get("scan_start_sec")),
            entry.get("detected_end_sec", entry.get("scan_duration_sec")),
        ],
        "layout_id": layout.get("layout_id"),
        "layout_version": layout.get("version"),
        "extractor_version": EXTRACTOR_VERSION,
    }
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _confirmed_game_ids(annotations_path: Path) -> set[str]:
    payload = load_pick_order_annotations(annotations_path)
    return {
        str(game.get("game_id"))
        for game in payload.get("games", [])
        if isinstance(game, dict) and game.get("status") == "confirmed"
    }


def _retry(operation: Callable[[], T], *, retries: int = 2) -> T:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return operation()
        except Exception as exc:  # the caller records the final actionable error
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError("Operation failed without an exception") from last_error


def _iter_scan_sections(start_sec: float, duration_sec: float):
    offset = 0.0
    while offset < duration_sec:
        section_duration = min(MAX_SECTION_SEC, duration_sec - offset)
        if section_duration <= 0.0:
            break
        yield start_sec + offset, section_duration
        offset += section_duration


def _coarse_scan(
    *,
    entry: dict[str, Any],
    output_dir: Path,
    registry: dict[str, Any],
    network_slots: threading.BoundedSemaphore,
    stop_after_first_window: bool,
) -> list[DraftWindow]:
    scan_start = float(entry.get("scan_start_sec") or 0.0)
    scan_duration = float(entry.get("scan_duration_sec") or 1500.0)
    preferred_layout = [str(entry["layout_id"])] if entry.get("layout_id") else []
    timed_frames: list[tuple[Path, float]] = []

    for section_index, (section_start, section_duration) in enumerate(
        _iter_scan_sections(scan_start, scan_duration)
    ):
        section_dir = output_dir / f"coarse_{section_index:03d}"

        def extract() -> list[Path]:
            with network_slots:
                return extract_remote_section_frames(
                    url=str(entry["vod_url"]),
                    output_dir=section_dir,
                    start_sec=section_start,
                    duration_sec=section_duration,
                    fps=COARSE_FPS,
                    max_height=360,
                    max_temp_bytes=DEFAULT_MAX_TEMP_BYTES,
                )

        paths = _retry(extract)
        timed_frames.extend(
            (path, section_start + index / COARSE_FPS)
            for index, path in enumerate(sorted(paths))
        )
        windows = locate_draft_windows_from_timed_frames(
            timed_frames,
            registry=registry,
            preferred_layout_ids=preferred_layout,
            sample_fps=COARSE_FPS,
        )
        if stop_after_first_window and windows:
            return windows
    return locate_draft_windows_from_timed_frames(
        timed_frames,
        registry=registry,
        preferred_layout_ids=preferred_layout,
        sample_fps=COARSE_FPS,
    )


def _resolve_window(
    *,
    entry: dict[str, Any],
    scan_dir: Path,
    registry: dict[str, Any],
    network_slots: threading.BoundedSemaphore,
) -> DraftWindow:
    if entry.get("detected_start_sec") is not None:
        start = float(entry["detected_start_sec"])
        end = float(entry.get("detected_end_sec") or start + entry.get("duration_sec", 240))
        return DraftWindow(
            layout_id=str(entry["layout_id"]),
            start_sec=start,
            end_sec=min(end, start + MAX_SECTION_SEC),
            confidence=float(entry.get("layout_confidence", 1.0)),
        )
    if entry.get("draft_start_sec") is not None:
        start = float(entry["draft_start_sec"])
        return DraftWindow(
            layout_id=str(entry["layout_id"]),
            start_sec=start,
            end_sec=start + min(float(entry.get("duration_sec", 240)), MAX_SECTION_SEC),
            confidence=1.0,
        )

    windows = _coarse_scan(
        entry=entry,
        output_dir=scan_dir,
        registry=registry,
        network_slots=network_slots,
        stop_after_first_window=entry.get("source_kind") == "per_game",
    )
    if not windows:
        raise DraftWindowNotFoundError("No supported draft-screen run was found in the scan range")
    if entry.get('source_kind') == 'timestamped_game' and len(windows) != 1:
        raise DraftWindowNotFoundError('Multiple drafts around Liquipedia timestamp; review the game window.')
    return windows[0]


def _persist_evidence(
    *,
    suggestion: dict[str, Any],
    layout: dict[str, Any],
    evidence_dir: Path,
) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise ImportError("opencv-python-headless is required to save evidence crops") from exc

    evidence_dir.mkdir(parents=True, exist_ok=True)
    copied_frames: dict[str, str] = {}
    for pick in suggestion.get("picks", []):
        persisted: list[str] = []
        crop_path: str | None = None
        for source_value in list(pick.get("evidence_frames", []))[:3]:
            source_path = Path(str(source_value))
            if not source_path.exists():
                continue
            destination = evidence_dir / f"frame_{len(copied_frames):03d}{source_path.suffix}"
            if str(source_path) not in copied_frames:
                shutil.copy2(source_path, destination)
                copied_frames[str(source_path)] = str(destination)
            persisted.append(copied_frames[str(source_path)])

            if crop_path is None and pick.get("slot"):
                frame = cv2.imread(str(source_path))
                if frame is not None:
                    slot_crop = crop_slots(frame, layout).get(str(pick["slot"]))
                    if slot_crop is not None and slot_crop.size:
                        crop_destination = evidence_dir / f"pick_{pick['global_pick_index']:02d}.jpg"
                        if cv2.imwrite(str(crop_destination), slot_crop):
                            crop_path = str(crop_destination)
        pick["evidence_frames"] = persisted
        pick["evidence_frame"] = persisted[0] if persisted else None
        pick["hero_crop"] = crop_path


def _persist_review_crops(
    *,
    frame_paths: list[Path],
    layout: dict[str, Any],
    evidence_dir: Path,
    maximum_per_slot: int = 8,
) -> dict[str, list[str]]:
    try:
        import cv2
    except ImportError as exc:
        raise ImportError("opencv-python-headless is required to save review crops") from exc

    review_dir = evidence_dir / "review_crops"
    review_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, list[str]] = {}
    seen_hashes: dict[str, set[int]] = {}
    for frame_index, frame_path in enumerate(sorted(frame_paths)):
        if frame_index % 5:
            continue
        frame = cv2.imread(str(frame_path))
        if frame is None:
            continue
        for slot, crop in crop_slots(frame, layout).items():
            if "_pick" not in slot or len(saved.get(slot, [])) >= maximum_per_slot:
                continue
            gray = cv2.cvtColor(cv2.resize(crop, (9, 8)), cv2.COLOR_BGR2GRAY)
            bits = (gray[:, 1:] > gray[:, :-1]).flatten()
            crop_hash = sum(int(bit) << index for index, bit in enumerate(bits))
            prior_hashes = seen_hashes.setdefault(slot, set())
            if any((crop_hash ^ prior).bit_count() < 8 for prior in prior_hashes):
                continue
            prior_hashes.add(crop_hash)
            destination = review_dir / f"{slot}_{len(saved.get(slot, [])):02d}.jpg"
            if cv2.imwrite(str(destination), crop):
                saved.setdefault(slot, []).append(str(destination))
    return saved


def _process_entry(
    *,
    entry: dict[str, Any],
    raw_game: dict[str, Any],
    layouts_path: Path,
    results_dir: Path,
    ledger: JobLedger,
    network_slots: threading.BoundedSemaphore,
) -> dict[str, Any]:
    game_id = str(entry["game_id"])
    try:
        layout = load_layout(str(entry["layout_id"]), layouts_path)
    except (KeyError, ValueError) as exc:
        raise UnsupportedLayoutError(str(exc)) from exc

    key = job_key(entry, layout)
    completed_path = ledger.completed_output(key)
    if completed_path:
        return {
            "game_id": game_id,
            "status": "skipped_completed",
            "job_key": key,
            "output_path": str(completed_path),
        }

    ledger.start(key, game_id)
    job_dir = RUNTIME_DIR / "jobs" / key
    output_path = results_dir / f"{_safe_path_segment(game_id)}.{key}.json"
    try:
        window = _resolve_window(
            entry=entry,
            scan_dir=job_dir / "scan",
            registry=load_layout_registry(layouts_path),
            network_slots=network_slots,
        )
        if window.layout_id != layout["layout_id"]:
            layout = load_layout(window.layout_id, layouts_path)

        detail_dir = job_dir / "detail"

        def extract_detail() -> list[Path]:
            with network_slots:
                return extract_remote_section_frames(
                    url=str(entry["vod_url"]),
                    output_dir=detail_dir,
                    start_sec=window.start_sec,
                    duration_sec=min(window.end_sec - window.start_sec, MAX_SECTION_SEC),
                    fps=DETAIL_FPS,
                    max_height=1080,
                    max_temp_bytes=DEFAULT_MAX_TEMP_BYTES,
                )

        frame_paths = _retry(extract_detail)
        observations = extract_hero_identity_observations(
            frame_paths=frame_paths,
            layout=layout,
            raw_game=raw_game,
            fps=DETAIL_FPS,
            start_sec=window.start_sec,
        )
        events = stable_hero_events(observations)
        provenance = {
            key_name: entry.get(key_name)
            for key_name in (
                "source_id",
                "channel_id",
                "video_id",
                "source_kind",
                "match_confidence",
            )
            if entry.get(key_name) is not None
        }
        provenance.update(
            {
                "layout_id": layout["layout_id"],
                "layout_version": layout.get("version"),
                "layout_confidence": window.confidence,
                "detected_start_sec": window.start_sec,
                "detected_end_sec": window.end_sec,
            }
        )
        suggestion = suggest_pick_order_from_hero_events(
            raw_game,
            events,
            provenance=provenance,
        )
        evidence_dir = results_dir.parent / "evidence" / key
        suggestion["review_crops"] = _persist_review_crops(
            frame_paths=frame_paths,
            layout=layout,
            evidence_dir=evidence_dir,
        )
        _persist_evidence(
            suggestion=suggestion,
            layout=layout,
            evidence_dir=evidence_dir,
        )
        _atomic_write_json(output_path, {"version": 1, "games": [suggestion]})
        ledger.complete(key, output_path)
        return {
            "game_id": game_id,
            "status": "completed",
            "job_key": key,
            "output_path": str(output_path),
        }
    except Exception as exc:
        ledger.fail(key, f"{type(exc).__name__}: {exc}")
        return {
            "game_id": game_id,
            "status": "processing_error",
            "job_key": key,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if job_dir.exists():
            clear_frame_directory(job_dir)


def _resolve_shared_fallback_windows(
    *,
    entries: list[dict[str, Any]],
    raw_games: dict[str, dict[str, Any]],
    layouts_path: Path,
    network_slots: threading.BoundedSemaphore,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    per_game_entries = [entry for entry in entries if entry.get("source_kind") in ("per_game", "timestamped_game")]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if entry.get("source_kind") not in ("per_game", "timestamped_game"):
            grouped.setdefault(str(entry.get("video_id") or entry.get("vod_url")), []).append(entry)

    resolved = list(per_game_entries)
    errors: list[dict[str, Any]] = []
    registry = load_layout_registry(layouts_path)
    for video_key, group in grouped.items():
        if len(group) == 1 or all(entry.get("detected_start_sec") is not None for entry in group):
            resolved.extend(group)
            continue

        try:
            load_layout(str(group[0]["layout_id"]), layouts_path)
        except (KeyError, ValueError):
            resolved.extend(group)
            continue

        scan_start = min(float(entry.get("scan_start_sec") or 0.0) for entry in group)
        scan_end = max(
            float(entry.get("scan_start_sec") or 0.0)
            + float(entry.get("scan_duration_sec") or 0.0)
            for entry in group
        )
        synthetic_entry = {
            **group[0],
            "scan_start_sec": scan_start,
            "scan_duration_sec": max(0.0, scan_end - scan_start),
        }
        scan_key = hashlib.sha256(video_key.encode("utf-8")).hexdigest()[:16]
        scan_dir = RUNTIME_DIR / "shared_scans" / scan_key
        try:
            windows = _coarse_scan(
                entry=synthetic_entry,
                output_dir=scan_dir,
                registry=registry,
                network_slots=network_slots,
                stop_after_first_window=False,
            )
        except Exception as exc:
            for entry in group:
                errors.append(
                    {
                        "game_id": str(entry["game_id"]),
                        "status": "processing_error",
                        "error": f"Shared VOD scan failed: {type(exc).__name__}: {exc}",
                    }
                )
            continue
        finally:
            if scan_dir.exists():
                clear_frame_directory(scan_dir)

        ordered_entries = sorted(
            group,
            key=lambda entry: (
                str(raw_games[str(entry["game_id"])].get("date") or ""),
                int(raw_games[str(entry["game_id"])].get("series_index") or 0),
                int(raw_games[str(entry["game_id"])].get("game_index") or 0),
            ),
        )
        if len(windows) < len(ordered_entries):
            for entry in ordered_entries:
                errors.append(
                    {
                        "game_id": str(entry["game_id"]),
                        "status": "processing_error",
                        "error": (
                            f"Shared VOD scan found {len(windows)} draft windows for "
                            f"{len(ordered_entries)} games."
                        ),
                    }
                )
            continue

        for entry, window in zip(ordered_entries, windows, strict=False):
            resolved.append(
                {
                    **entry,
                    "layout_id": window.layout_id,
                    "layout_confidence": window.confidence,
                    "detected_start_sec": window.start_sec,
                    "detected_end_sec": window.end_sec,
                }
            )
    return resolved, errors


def _merge_review_results(results: list[dict[str, Any]], review_path: Path) -> None:
    games: list[dict[str, Any]] = []
    for result in results:
        output_path = result.get("output_path")
        if not output_path:
            continue
        payload = load_json(Path(str(output_path)))
        if isinstance(payload, dict):
            games.extend(game for game in payload.get("games", []) if isinstance(game, dict))
    games.sort(key=lambda game: str(game.get("game_id", "")))
    _atomic_write_json(review_path, {"version": 1, "games": games})


def run_vod_pipeline(
    *,
    manifest_path: Path,
    raw_dir: Path = RAW_TOURNAMENTS_DIR,
    annotations_path: Path = PICK_ORDER_ANNOTATIONS_PATH,
    layouts_path: Path = LAYOUTS_PATH,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    review_path: Path = DEFAULT_REVIEW_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    max_vod_streams: int = 2,
    max_inference_workers: int = 4,
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("games"), list):
        raise ValueError(f"Expected a VOD manifest with games at {manifest_path}")
    raw_games = load_raw_games_by_id(raw_dir)
    confirmed = _confirmed_game_ids(annotations_path)
    ledger = JobLedger(ledger_path)
    network_slots = threading.BoundedSemaphore(max(1, max_vod_streams))

    immediate_results: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for entry in manifest["games"]:
        if not isinstance(entry, dict):
            continue
        game_id = str(entry.get("game_id", ""))
        if entry.get("status") != "matched":
            immediate_results.append(
                {
                    "game_id": game_id,
                    "status": str(entry.get("status", "missing_vod")),
                    "error": entry.get("reason"),
                }
            )
        elif game_id in confirmed:
            immediate_results.append({"game_id": game_id, "status": "skipped_confirmed"})
        elif game_id not in raw_games:
            immediate_results.append(
                {"game_id": game_id, "status": "processing_error", "error": "Unknown raw game"}
            )
        else:
            entries.append(entry)

    entries, fallback_errors = _resolve_shared_fallback_windows(
        entries=entries,
        raw_games=raw_games,
        layouts_path=layouts_path,
        network_slots=network_slots,
    )
    immediate_results.extend(fallback_errors)

    processed_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, max_inference_workers)) as executor:
        futures = {
            executor.submit(
                _process_entry,
                entry=entry,
                raw_game=raw_games[str(entry["game_id"])],
                layouts_path=layouts_path,
                results_dir=results_dir,
                ledger=ledger,
                network_slots=network_slots,
            ): entry
            for entry in entries
        }
        for future in as_completed(futures):
            try:
                processed_results.append(future.result())
            except UnsupportedLayoutError as exc:
                entry = futures[future]
                processed_results.append(
                    {
                        "game_id": str(entry["game_id"]),
                        "status": "unsupported_layout",
                        "error": str(exc),
                    }
                )

    results = sorted(
        [*immediate_results, *processed_results],
        key=lambda result: str(result.get("game_id", "")),
    )
    _merge_review_results(results, review_path)
    counts: dict[str, int] = {}
    for result in results:
        status = str(result["status"])
        counts[status] = counts.get(status, 0) + 1
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "results": results,
        "review_path": str(review_path),
    }
    _atomic_write_json(review_path.with_name("run_report.json"), report)
    return report
