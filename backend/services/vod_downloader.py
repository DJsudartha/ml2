from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any

DEFAULT_MAX_TEMP_BYTES = 500 * 1024 * 1024
RUNTIME_SEGMENT_DIR = Path("backend/data/runtime/vod_segments")


def _load_yt_dlp():
    try:
        import yt_dlp
    except ImportError as exc:
        raise ImportError(
            "yt-dlp is required for VOD section downloads. "
            "Install backend/requirements-vod.txt to use this helper."
        ) from exc
    return yt_dlp


def _load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            "opencv-python-headless is required for VOD frame extraction. "
            "Install backend/requirements-vod.txt to use this helper."
        ) from exc
    return cv2


def _format_selector(max_height: int) -> str:
    return (
        f"bestvideo[height<={max_height}]/"
        f"best[height<={max_height}]/worstvideo/worst"
    )


def get_stream_url(url: str, *, max_height: int) -> str:
    yt_dlp = _load_yt_dlp()
    with yt_dlp.YoutubeDL(
        {
            "format": _format_selector(max_height),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
    ) as ydl:
        info = ydl.extract_info(url, download=False)
    stream_url = info.get("url")
    if not stream_url:
        requested_formats = info.get("requested_formats") or []
        stream_url = next(
            (item.get("url") for item in requested_formats if item.get("url")),
            None,
        )
    if not stream_url:
        raise ValueError(f"Could not resolve a bounded media stream for {url}")
    return str(stream_url)


def extract_frames_from_remote_stream(
    *,
    url: str,
    output_dir: Path,
    start_sec: float,
    duration_sec: float,
    fps: float,
    max_height: int,
    max_temp_bytes: int = DEFAULT_MAX_TEMP_BYTES,
) -> list[Path]:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise FileNotFoundError("FFmpeg is required for direct bounded stream extraction")
    if duration_sec > 720:
        raise ValueError("Remote stream sections are capped at 12 minutes")
    output_dir.mkdir(parents=True, exist_ok=True)
    stream_url = get_stream_url(url, max_height=max_height)
    subprocess.run(
        [
            ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(float(start_sec)),
            "-i",
            stream_url,
            "-t",
            str(float(duration_sec)),
            "-vf",
            f"fps={float(fps)}",
            "-q:v",
            "3",
            str(output_dir / "frame_%05d.jpg"),
        ],
        check=True,
        timeout=max(120.0, duration_sec * 3.0),
    )
    paths = sorted(output_dir.glob("frame_*.jpg"))
    output_bytes = sum(path.stat().st_size for path in paths)
    if output_bytes > max_temp_bytes:
        clear_frame_directory(output_dir)
        raise RuntimeError(
            f"Extracted frames exceeded the {max_temp_bytes} byte temporary-storage limit"
        )
    if not paths:
        raise ValueError("Direct bounded stream extraction produced no frames")
    return paths


def download_vod_section(
    url: str,
    output_dir: Path,
    *,
    start_sec: float,
    duration_sec: float,
    max_height: int,
    max_temp_bytes: int = DEFAULT_MAX_TEMP_BYTES,
) -> Path:
    if start_sec < 0 or duration_sec <= 0:
        raise ValueError("VOD section start must be non-negative and duration must be positive")
    if duration_sec > 720:
        raise ValueError("Detailed VOD sections are capped at 12 minutes")

    yt_dlp = _load_yt_dlp()
    output_dir.mkdir(parents=True, exist_ok=True)

    def enforce_size(status: dict[str, Any]) -> None:
        downloaded = int(status.get("downloaded_bytes") or 0)
        estimate = int(status.get("total_bytes_estimate") or status.get("total_bytes") or 0)
        if max(downloaded, estimate) > max_temp_bytes:
            raise RuntimeError(
                f"VOD section exceeded the {max_temp_bytes} byte temporary-storage limit"
            )

    ydl_opts = {
        "outtmpl": str(output_dir / "segment.%(ext)s"),
        "format": _format_selector(max_height),
        "download_ranges": yt_dlp.utils.download_range_func(
            [],
            [[float(start_sec), float(start_sec + duration_sec)]],
        ),
        "force_keyframes_at_cuts": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [enforce_size],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        prepared_path = Path(ydl.prepare_filename(info))

    candidate_paths = [prepared_path, *sorted(output_dir.glob("segment.*"))]
    for path in candidate_paths:
        if path.exists() and path.is_file() and path.stat().st_size <= max_temp_bytes:
            return path
    raise FileNotFoundError(f"No bounded VOD section was produced for {url}")


def extract_frames_from_file(
    video_path: Path,
    output_dir: Path,
    *,
    fps: float,
) -> list[Path]:
    if fps <= 0:
        raise ValueError("Frame sampling FPS must be positive")
    cv2 = _load_cv2()
    output_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"Could not open VOD section {video_path}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    source_fps = source_fps if source_fps > 0.0 else 30.0
    next_sample_frame = 0.0
    frame_index = 0
    saved_paths: list[Path] = []
    try:
        while True:
            readable, frame = capture.read()
            if not readable:
                break
            if frame_index + 1e-9 >= next_sample_frame:
                frame_path = output_dir / f"frame_{len(saved_paths):05d}.jpg"
                if cv2.imwrite(str(frame_path), frame):
                    saved_paths.append(frame_path)
                next_sample_frame += source_fps / fps
            frame_index += 1
    finally:
        capture.release()
    return saved_paths


def extract_remote_section_frames(
    *,
    url: str,
    output_dir: Path,
    start_sec: float,
    duration_sec: float,
    fps: float,
    max_height: int,
    max_temp_bytes: int = DEFAULT_MAX_TEMP_BYTES,
    prefer_direct_stream: bool = False,
) -> list[Path]:
    if prefer_direct_stream and shutil.which("ffmpeg"):
        direct_output_dir = output_dir / "_direct"
        try:
            direct_paths = extract_frames_from_remote_stream(
                url=url,
                output_dir=direct_output_dir,
                start_sec=start_sec,
                duration_sec=duration_sec,
                fps=fps,
                max_height=max_height,
                max_temp_bytes=max_temp_bytes,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            resolved_paths: list[Path] = []
            for index, source_path in enumerate(direct_paths):
                destination = output_dir / f"frame_{index:05d}.jpg"
                source_path.replace(destination)
                resolved_paths.append(destination)
            clear_frame_directory(direct_output_dir)
            return resolved_paths
        except (FileNotFoundError, subprocess.SubprocessError, TimeoutError, ValueError):
            clear_frame_directory(direct_output_dir)

    RUNTIME_SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="vod-section-",
        dir=RUNTIME_SEGMENT_DIR,
    ) as temp_directory:
        segment_path = download_vod_section(
            url,
            Path(temp_directory),
            start_sec=start_sec,
            duration_sec=duration_sec,
            max_height=max_height,
            max_temp_bytes=max_temp_bytes,
        )
        return extract_frames_from_file(segment_path, output_dir, fps=fps)


def clear_frame_directory(path: Path) -> None:
    for attempt in range(5):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
    shutil.rmtree(path, ignore_errors=True)


def process_vod(
    url: str,
    match_id: str,
    start_sec: float = 180,
    duration: float = 240,
    fps: float = 1,
    *,
    max_height: int = 1080,
    max_temp_bytes: int = DEFAULT_MAX_TEMP_BYTES,
    replace_existing: bool = False,
) -> str:
    frame_output_dir = Path("backend/data/raw/frames") / match_id
    if frame_output_dir.exists() and any(frame_output_dir.glob("*.jpg")):
        if not replace_existing:
            return str(frame_output_dir)
        clear_frame_directory(frame_output_dir)

    extract_remote_section_frames(
        url=url,
        output_dir=frame_output_dir,
        start_sec=float(start_sec),
        duration_sec=min(float(duration), 720.0),
        fps=float(fps),
        max_height=max_height,
        max_temp_bytes=max_temp_bytes,
    )
    return str(frame_output_dir)


def list_formats(url: str) -> None:
    yt_dlp = _load_yt_dlp()
    with yt_dlp.YoutubeDL({"listformats": True}) as ydl:
        ydl.extract_info(url, download=False)
