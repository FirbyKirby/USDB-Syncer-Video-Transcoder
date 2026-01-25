"""Video analysis using ffprobe."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from usdb_syncer.utils import LinuxEnvCleaner

from .utils import format_seconds

if TYPE_CHECKING:
    from .config import TranscoderConfig

_logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    """Information about a video file."""
    codec_name: str                    # e.g., "h264", "vp9", "hevc"
    codec_long_name: str               # e.g., "H.264 / AVC"
    container: str                     # e.g., "mp4", "webm", "mkv"
    width: int
    height: int
    pixel_format: str                  # e.g., "yuv420p"
    frame_rate: float                  # fps
    duration_seconds: float
    bitrate_kbps: Optional[int]        # may be N/A for some formats
    has_audio: bool
    audio_codec: Optional[str]         # e.g., "aac", "opus", "vorbis"
    profile: Optional[str]             # e.g., "Main", "Baseline"
    level: Optional[str]               # e.g., "3.1"

    @property
    def is_h264(self) -> bool:
        return self.codec_name.lower() in ("h264", "avc")

    @property
    def is_vp8(self) -> bool:
        return self.codec_name.lower() == "vp8"

    @property
    def is_vp9(self) -> bool:
        return self.codec_name.lower() == "vp9"

    @property
    def is_hevc(self) -> bool:
        return self.codec_name.lower() in ("hevc", "h265")

    @property
    def is_av1(self) -> bool:
        return self.codec_name.lower() == "av1"


def analyze_video(path: Path) -> Optional[VideoInfo]:
    """Analyze video file with ffprobe.

    Returns None if analysis fails or file is not a valid video.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path)
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
                env=env
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


def _parse_ffprobe_output(data: dict, path: Path) -> Optional[VideoInfo]:
    """Parse ffprobe JSON output into VideoInfo."""
    streams = data.get("streams", [])
    format_info = data.get("format", {})

    # Find video stream
    video_stream = None
    audio_stream = None

    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and not video_stream:
            video_stream = stream
        elif codec_type == "audio" and not audio_stream:
            audio_stream = stream

    if not video_stream:
        _logger.warning(f"No video stream found in {path}")
        return None

    # Parse frame rate (handle fractions like "30000/1001")
    fps_str = video_stream.get("r_frame_rate", "0/1")
    try:
        num, den = map(int, fps_str.split("/"))
        frame_rate = num / den if den != 0 else 0.0
    except (ValueError, ZeroDivisionError):
        frame_rate = 0.0

    # Parse bitrate
    bitrate = None
    if "bit_rate" in video_stream:
        try:
            bitrate = int(video_stream["bit_rate"]) // 1000
        except (ValueError, TypeError):
            pass
    elif "bit_rate" in format_info:
        try:
            bitrate = int(format_info["bit_rate"]) // 1000
        except (ValueError, TypeError):
            pass

    # Duration
    duration = 0.0
    if "duration" in format_info:
        try:
            duration = float(format_info["duration"])
        except (ValueError, TypeError):
            pass

    # Container from filename extension
    container = path.suffix.lstrip(".").lower()

    return VideoInfo(
        codec_name=video_stream.get("codec_name", "unknown"),
        codec_long_name=video_stream.get("codec_long_name", "Unknown"),
        container=container,
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        pixel_format=video_stream.get("pix_fmt", "unknown"),
        frame_rate=frame_rate,
        duration_seconds=duration,
        bitrate_kbps=bitrate,
        has_audio=audio_stream is not None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        profile=video_stream.get("profile"),
        level=str(video_stream.get("level")) if video_stream.get("level") else None,
    )


def format_video_info(info: VideoInfo, minimal: bool = False, reasons: list[str] | None = None) -> str:
    """Format video info into a detailed string for logging.

    If minimal is True, only properties mentioned in reasons are included.
    """
    if not minimal:
        log_parts = [
            f"codec={info.codec_name}",
        ]

        # Only show profile for codecs that use it meaningfully
        if info.codec_name.lower() in ("h264", "avc", "hevc", "h265") and info.profile:
            log_parts.append(f"profile={info.profile}")

        log_parts.extend(
            [
                f"pixel_format={info.pixel_format}",
                f"resolution={info.width}x{info.height}",
                f"fps={info.frame_rate:.1f}",
                f"bitrate={info.bitrate_kbps}kbps",
                f"duration={format_seconds(info.duration_seconds)}",
                f"container={info.container}",
                f"has_audio={info.has_audio}",
            ]
        )
        return ", ".join(log_parts)

    # Minimal format based on reasons
    if not reasons:
        return f"codec={info.codec_name}"

    log_parts = []
    reason_str = " ".join(reasons).lower()

    if "codec" in reason_str:
        log_parts.append(f"codec={info.codec_name}")
    if "profile" in reason_str and info.profile:
        log_parts.append(f"profile={info.profile}")
    if "pixel format" in reason_str:
        log_parts.append(f"pixel_format={info.pixel_format}")
    if "resolution" in reason_str:
        log_parts.append(f"resolution={info.width}x{info.height}")
    if "fps" in reason_str:
        log_parts.append(f"fps={info.frame_rate:.1f}")
    if "bitrate" in reason_str:
        log_parts.append(f"bitrate={info.bitrate_kbps}kbps")
    if "container" in reason_str:
        log_parts.append(f"container={info.container}")
    if "audio" in reason_str:
        log_parts.append(f"has_audio={info.has_audio}")

    # Fallback if nothing matched but we have reasons
    if not log_parts:
        return f"codec={info.codec_name}"

    return ", ".join(log_parts)


def needs_transcoding(info: VideoInfo, cfg: TranscoderConfig) -> bool:
    """Determine if video needs transcoding for target codec and settings.

    Returns True if video should be transcoded.
    """
    reasons: list[str] = []
    target_codec = cfg.target_codec
    codec_cfg = getattr(cfg, target_codec)

    # 1. Codec-specific checks (highest priority)
    if target_codec == "h264":
        if not info.is_h264:
            reasons.append(f"codec {info.codec_name} != h264")
        else:
            if info.pixel_format != codec_cfg.pixel_format:
                reasons.append(f"pixel format {info.pixel_format} != {codec_cfg.pixel_format}")
            if info.profile and info.profile.lower() != codec_cfg.profile.lower():
                reasons.append(f"H.264 profile {info.profile} != {codec_cfg.profile}")

    elif target_codec == "hevc":
        if not info.is_hevc:
            reasons.append(f"codec {info.codec_name} != hevc")
        else:
            if info.pixel_format != codec_cfg.pixel_format:
                reasons.append(f"pixel format {info.pixel_format} != {codec_cfg.pixel_format}")
            if info.profile and info.profile.lower() != codec_cfg.profile.lower():
                reasons.append(f"profile {info.profile} != {codec_cfg.profile}")

    elif target_codec == "vp8":
        if not info.is_vp8:
            reasons.append(f"codec {info.codec_name} != vp8")

    elif target_codec == "vp9":
        if not info.is_vp9:
            reasons.append(f"codec {info.codec_name} != vp9")

    elif target_codec == "av1":
        if not info.is_av1:
            reasons.append(f"codec {info.codec_name} != av1")

    # 2. Check container format
    target_container = getattr(codec_cfg, "container", None)
    if target_container and info.container != target_container:
        reasons.append(f"container {info.container} != {target_container}")

    # 3. Check general limits (resolution, FPS, bitrate)
    if cfg.general.max_resolution:
        max_w, max_h = cfg.general.max_resolution
        if cfg.usdb_integration.use_usdb_resolution:
            # Limit behavior
            if info.width > max_w or info.height > max_h:
                reasons.append(f"resolution {info.width}x{info.height} exceeds max {max_w}x{max_h}")
        else:
            # Exact behavior
            if info.width != max_w or info.height != max_h:
                reasons.append(f"resolution {info.width}x{info.height} != exact {max_w}x{max_h}")

    if cfg.general.max_fps:
        if cfg.usdb_integration.use_usdb_fps:
            # Limit behavior
            if info.frame_rate > cfg.general.max_fps + 0.1:
                reasons.append(f"FPS {info.frame_rate:.2f} exceeds max {cfg.general.max_fps}")
        else:
            # Exact behavior
            if abs(info.frame_rate - cfg.general.max_fps) > 0.1:
                reasons.append(f"FPS {info.frame_rate:.2f} != exact {cfg.general.max_fps}")

    if (
        cfg.general.max_bitrate_kbps
        and info.bitrate_kbps
        and info.bitrate_kbps > cfg.general.max_bitrate_kbps
    ):
        reasons.append(f"bitrate {info.bitrate_kbps}k exceeds max {cfg.general.max_bitrate_kbps}k")

    # 4. Handler-specific compatibility
    if not reasons:
        from .codecs import get_codec_handler
        handler = get_codec_handler(target_codec)
        if handler and not handler.is_compatible(info):
            reasons.append(f"{target_codec} handler reports incompatible settings")

    if reasons:
        _logger.info(f"Transcode needed: {', '.join(reasons)}")
        return True

    return False
