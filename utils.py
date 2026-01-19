"""Shared utilities for the Melody Mania Transcoder addon."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from usdb_syncer.utils import LinuxEnvCleaner

if TYPE_CHECKING:
    from usdb_syncer import SongId


def execute_ffmpeg(cmd: list[str], timeout: int) -> tuple[bool, str]:
    """Execute FFMPEG command with error handling.

    Returns: success flag, error message if failed
    """
    try:
        with LinuxEnvCleaner() as env:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env
            )
        if result.returncode != 0:
            return False, result.stderr[-500:] if result.stderr else "Unknown error"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


def check_encoder_available(encoder: str) -> bool:
    """Check if an encoder is available in FFMPEG."""
    cmd = [
        "ffmpeg", "-hide_banner",
        "-f", "lavfi", "-i", "nullsrc=s=2x2:d=0.1",
        "-c:v", encoder,
        "-f", "null", "-"
    ]
    try:
        with LinuxEnvCleaner() as env:
            result = subprocess.run(cmd, capture_output=True, timeout=5, env=env)
        return result.returncode == 0
    except Exception:
        return False


def is_audio_file(path: str | Path) -> bool:
    """Return True if the file extension looks like a standalone audio format."""

    p = path if isinstance(path, Path) else Path(path)
    return p.suffix.lower() in (
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".opus",
        ".wav",
        ".flac",
        ".alac",
    )


def is_video_file(path: str | Path) -> bool:
    """Return True if the file extension looks like a video container."""

    p = path if isinstance(path, Path) else Path(path)
    return p.suffix.lower() in (
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".wmv",
        ".m4v",
    )


def time_to_seconds(time_str: str) -> float:
    """Convert FFMPEG time string (HH:MM:SS.mm) to seconds."""
    try:
        parts = time_str.split(":")
        if len(parts) != 3:
            return 0.0
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, TypeError):
        return 0.0


def format_seconds(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def parse_ffmpeg_progress(line: str) -> dict[str, str]:
    """Parse FFMPEG progress line into a dictionary of key-value pairs."""
    # frame= 1234 fps= 45 q=-1.0 Lsize=   1234kB time=00:01:23.45 bitrate=1234.5kbits/s speed=1.5x
    pattern = r"(\w+)=\s*(\S+)"
    return dict(re.findall(pattern, line))


def is_aborted(song_id: "SongId") -> bool:
    """Check if the download job for the given song has been aborted.
    
    Checks both single-transcode abort (via DownloadManager) and
    batch-transcode abort (via BatchAbortRegistry).
    """
    # Check single-transcode abort (via download job)
    try:
        from usdb_syncer.song_loader import DownloadManager

        if job := DownloadManager._jobs.get(song_id):
            if job.abort:
                return True
    except (ImportError, AttributeError):
        pass
    
    # Check batch-transcode abort
    try:
        from .batch_worker import BatchAbortRegistry
        
        if BatchAbortRegistry.instance().is_aborted(song_id):
            return True
    except (ImportError, AttributeError):
        pass
    
    return False
