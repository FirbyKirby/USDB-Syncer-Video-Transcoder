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

from .config import TranscoderConfig


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

    # Debug aid: ffprobe sometimes reports embedded artwork as a "video" stream
    # (typically with disposition.attached_pic=1). When users report misleading
    # has_video=True for audio-only songs, this stream dump helps confirm whether
    # the detected "video" is actually album art.
    if _logger.isEnabledFor(logging.DEBUG):
        summaries: list[str] = []
        for idx, stream in enumerate(streams):
            disp = stream.get("disposition") or {}
            summaries.append(
                " ".join(
                    [
                        f"stream[{idx}]",
                        f"type={stream.get('codec_type')}",
                        f"codec={stream.get('codec_name')}",
                        f"attached_pic={disp.get('attached_pic')}",
                    ]
                )
            )
        _logger.debug(f"ffprobe streams for {path}: " + "; ".join(summaries))

    audio_stream = None
    has_video = False
    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            disposition = stream.get("disposition") or {}
            if disposition.get("attached_pic") != 1:
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


def format_audio_info(info: AudioInfo, minimal: bool = False, reasons: list[str] | None = None) -> str:
    """Format audio info into a detailed string for logging.

    If minimal is True, only properties mentioned in reasons are included.
    """
    if not minimal:
        log_parts = [
            f"codec={info.codec_name}",
            f"bitrate={info.bitrate_kbps}kbps" if info.bitrate_kbps else "bitrate=unknown",
            f"channels={info.channels}",
            f"sample_rate={info.sample_rate_hz}Hz",
            f"duration={info.duration_seconds:.1f}s",
            f"container={info.container}",
            f"has_video={info.has_video}",
        ]
        return ", ".join(log_parts)

    # Minimal format based on reasons
    if not reasons:
        return f"codec={info.codec_name}"

    log_parts = []
    reason_str = " ".join(reasons).lower()

    if "codec" in reason_str:
        log_parts.append(f"codec={info.codec_name}")
    if "bitrate" in reason_str:
        log_parts.append(f"bitrate={info.bitrate_kbps}kbps" if info.bitrate_kbps else "bitrate=unknown")
    if "channels" in reason_str:
        log_parts.append(f"channels={info.channels}")
    if "sample rate" in reason_str:
        log_parts.append(f"sample_rate={info.sample_rate_hz}Hz")
    if "container" in reason_str:
        log_parts.append(f"container={info.container}")
    if "video" in reason_str:
        log_parts.append(f"has_video={info.has_video}")

    # Fallback if nothing matched but we have reasons
    if not log_parts:
        return f"codec={info.codec_name}"

    return ", ".join(log_parts)


def needs_audio_transcoding(info: AudioInfo, cfg: TranscoderConfig) -> tuple[bool, list[str]]:
    """Determine if audio needs transcoding for target codec and settings.

    Returns (needs_transcode, reasons).

    Note: Normalization checks are handled separately in process_audio().
    """
    from .codecs import get_audio_codec_handler
    reasons: list[str] = []
    target_codec = cfg.audio.audio_codec
    handler = get_audio_codec_handler(target_codec)

    if not handler:
        return False, []

    # 1. Check codec match
    if info.codec_name.lower() != target_codec.lower():
        reasons.append(f"codec {info.codec_name} != {target_codec}")

    # 2. Check container compatibility
    if not handler.is_container_compatible(Path(f"dummy.{info.container}")):
        reasons.append(f"container {info.container} incompatible with {target_codec}")

    # 3. Check force transcode
    if getattr(cfg.audio, "force_transcode_audio", False):
        reasons.append("force_transcode_audio enabled")

    return bool(reasons), reasons


def is_audio_only(info: AudioInfo) -> bool:
    """Return True if the media appears to be audio-only."""
    return info.has_audio and not info.has_video


def is_video_with_audio(info: AudioInfo) -> bool:
    """Return True if the media contains both video and audio."""
    return info.has_audio and info.has_video


@dataclass
class ReplayGainTags:
    """ReplayGain tag values extracted from audio file."""

    track_gain_db: Optional[float] = None
    track_peak: Optional[float] = None
    album_gain_db: Optional[float] = None
    album_peak: Optional[float] = None
    reference_level_db: Optional[float] = None  # Usually -18 or -23 LUFS


def _parse_gain(value: Optional[str]) -> Optional[float]:
    """Parse ReplayGain gain value (e.g. '-7.2 dB')."""
    if not value:
        return None
    try:
        return float(value.strip(' dB'))
    except (ValueError, AttributeError):
        return None


def _parse_peak(value: Optional[str]) -> Optional[float]:
    """Parse ReplayGain peak value (e.g. '1.0')."""
    if not value:
        return None
    try:
        return float(value.strip(' dB'))
    except (ValueError, AttributeError):
        return None


def _parse_ref(value: Optional[str]) -> Optional[float]:
    """Parse ReplayGain reference level value."""
    if not value:
        return None
    try:
        return float(value)
    except (ValueError, AttributeError):
        return None


def get_replaygain_tags(path: Path) -> Optional[ReplayGainTags]:
    """Extract ReplayGain tag values from audio file using ffprobe.

    Returns ReplayGainTags if any tags are found, None otherwise.

    Checks both format-level and stream-level tags, with container-specific logic:
    - For Ogg containers: checks stream tags (Vorbis comments)
    - For other containers: checks format tags

    Looks for standard ReplayGain tags: track_gain, track_peak, album_gain, album_peak
    (case-insensitive, with or without 'replaygain_' prefix).
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    try:
        _logger.debug(f"Extracting ReplayGain tags: {' '.join(cmd)}")
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
            _logger.warning(f"ffprobe failed for ReplayGain extraction {path}: {result.stderr}")
            return None

        data = json.loads(result.stdout)

        # Check format tags
        format_tags = data.get("format", {}).get("tags", {})

        # For Ogg containers, also check stream tags (Vorbis comments)
        container = path.suffix.lstrip(".").lower()
        stream_tags = {}
        if container in ("ogg", "oga", "opus"):
            streams = data.get("streams", [])
            for stream in streams:
                if stream.get("codec_type") == "audio":
                    stream_tags = stream.get("tags", {})
                    break

        # Extract values, preferring stream tags for Ogg, format for others
        tags_to_check = stream_tags if stream_tags else format_tags

        track_gain_db = _parse_gain(tags_to_check.get('REPLAYGAIN_TRACK_GAIN'))
        track_peak = _parse_peak(tags_to_check.get('REPLAYGAIN_TRACK_PEAK'))
        album_gain_db = _parse_gain(tags_to_check.get('REPLAYGAIN_ALBUM_GAIN'))
        album_peak = _parse_peak(tags_to_check.get('REPLAYGAIN_ALBUM_PEAK'))
        reference_level_db = _parse_ref(tags_to_check.get('REPLAYGAIN_REFERENCE_LEVEL'))

        # If any tag is present, return the tags object
        if any([track_gain_db, track_peak, album_gain_db, album_peak, reference_level_db]):
            return ReplayGainTags(
                track_gain_db=track_gain_db,
                track_peak=track_peak,
                album_gain_db=album_gain_db,
                album_peak=album_peak,
                reference_level_db=reference_level_db,
            )

        return None

    except subprocess.TimeoutExpired:
        _logger.error(f"ffprobe timeout extracting ReplayGain for {path}")
        return None
    except json.JSONDecodeError as e:
        _logger.error(f"ffprobe output parse error extracting ReplayGain: {e}")
        return None
    except Exception as e:
        _logger.error(f"ffprobe error extracting ReplayGain for {path}: {type(e).__name__}: {e}")
        return None
