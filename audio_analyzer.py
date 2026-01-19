"""Audio analysis using ffprobe.

This module mirrors the existing video analyzer patterns in
[`video_analyzer.analyze_video()`](video_analyzer.py:60), but focuses on audio streams.

It supports:
- audio-only files (e.g. .mp3/.m4a/.flac/.wav)
- containers that may also include video (e.g. .mp4/.mkv) when used for audio extraction
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from usdb_syncer.utils import LinuxEnvCleaner


_logger = logging.getLogger(__name__)


@dataclass
class AudioInfo:
    """Information about a media file's primary audio stream."""

    codec_name: str  # e.g. "aac", "mp3", "vorbis", "opus"
    codec_long_name: str
    container: str  # derived from file extension
    duration_seconds: float
    bitrate_kbps: Optional[int]
    channels: Optional[int]
    sample_rate_hz: Optional[int]
    has_audio: bool
    has_video: bool


def analyze_audio(path: Path) -> Optional[AudioInfo]:
    """Analyze a media file with ffprobe and return AudioInfo.

    Returns None if analysis fails or if the file has no audio stream.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    try:
        _logger.debug(f"Running ffprobe: {' '.join(cmd)}")
        with LinuxEnvCleaner() as env:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=env,
            )

        if result.returncode != 0:
            _logger.warning(f"ffprobe failed for {path}: {result.stderr}")
            return None

        data = json.loads(result.stdout)
        return _parse_ffprobe_output(data, path)

    except subprocess.TimeoutExpired:
        _logger.error(f"ffprobe timeout for {path}")
        return None
    except json.JSONDecodeError as e:
        _logger.error(f"ffprobe output parse error: {e}")
        return None
    except Exception as e:
        _logger.error(f"ffprobe error for {path}: {type(e).__name__}: {e}")
        return None


def _parse_ffprobe_output(data: dict, path: Path) -> Optional[AudioInfo]:
    """Parse ffprobe JSON output into AudioInfo."""
    streams = data.get("streams", [])
    format_info = data.get("format", {})

    audio_stream = None
    has_video = False
    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            has_video = True
        if codec_type == "audio" and audio_stream is None:
            audio_stream = stream

    if audio_stream is None:
        _logger.warning(f"No audio stream found in {path}")
        return None

    duration = 0.0
    if "duration" in format_info:
        try:
            duration = float(format_info["duration"])
        except (ValueError, TypeError):
            pass

    bitrate = None
    if "bit_rate" in audio_stream:
        try:
            bitrate = int(audio_stream["bit_rate"]) // 1000
        except (ValueError, TypeError):
            pass
    elif "bit_rate" in format_info:
        try:
            bitrate = int(format_info["bit_rate"]) // 1000
        except (ValueError, TypeError):
            pass

    channels = None
    if "channels" in audio_stream:
        try:
            channels = int(audio_stream["channels"])
        except (ValueError, TypeError):
            pass

    sample_rate = None
    if "sample_rate" in audio_stream:
        try:
            sample_rate = int(audio_stream["sample_rate"])
        except (ValueError, TypeError):
            pass

    container = path.suffix.lstrip(".").lower()

    return AudioInfo(
        codec_name=audio_stream.get("codec_name", "unknown"),
        codec_long_name=audio_stream.get("codec_long_name", "Unknown"),
        container=container,
        duration_seconds=duration,
        bitrate_kbps=bitrate,
        channels=channels,
        sample_rate_hz=sample_rate,
        has_audio=True,
        has_video=has_video,
    )


def is_audio_only(info: AudioInfo) -> bool:
    """Return True if the media appears to be audio-only."""
    return info.has_audio and not info.has_video


def is_video_with_audio(info: AudioInfo) -> bool:
    """Return True if the media contains both video and audio."""
    return info.has_audio and info.has_video

