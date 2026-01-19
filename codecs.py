"""Codec handler registries and implementations.

This module currently contains:

Video codecs
- H.264, VP8, HEVC, VP9, AV1

Audio codecs (Stage 2 expansion)
- MP3, Vorbis, AAC, Opus
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, Tuple, Type

if TYPE_CHECKING:
    from .config import TranscoderConfig
    from .hwaccel import HardwareAccelerator
    from .video_analyzer import VideoInfo


@dataclass
class CodecCapabilities:
    """Describes a codec handler's capabilities."""
    name: str                          # e.g., "h264", "vp8", "hevc"
    display_name: str                  # e.g., "H.264/AVC"
    container: str                     # Default container extension
    supports_quicksync_encode: bool    # Can use QSV encoder
    supports_quicksync_decode: bool    # Can use QSV decoder for this format
    unity_compatible: bool             # Supported by Unity 6 VideoPlayer


class CodecHandler(ABC):
    """Abstract base class for codec handlers."""

    @classmethod
    @abstractmethod
    def capabilities(cls) -> CodecCapabilities:
        """Return codec capabilities."""
        ...

    @classmethod
    @abstractmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        video_info: VideoInfo,
        cfg: TranscoderConfig,
        accel: type["HardwareAccelerator"] | None,
        hw_encode_enabled: bool = False,
        hw_decode_enabled: bool = False,
    ) -> list[str]:
        """Build FFMPEG command for encoding to this codec."""
        ...

    @classmethod
    @abstractmethod
    def is_compatible(cls, video_info: VideoInfo) -> bool:
        """Check if input video is already in this codec's target format."""
        ...

    @classmethod
    def get_qsv_decoder(cls, video_info: VideoInfo) -> str | None:
        """Return QSV decoder name for input codec, or None."""
        codec_to_decoder = {
            "h264": "h264_qsv",
            "hevc": "hevc_qsv",
            "h265": "hevc_qsv",
            "vp9": "vp9_qsv",
            "mpeg2video": "mpeg2_qsv",
            "vc1": "vc1_qsv",
            "av1": "av1_qsv",
            "mjpeg": "mjpeg_qsv",
        }
        return codec_to_decoder.get(video_info.codec_name.lower())

    @classmethod
    def get_hw_decoder(
        cls,
        video_info: VideoInfo,
        accel: type["HardwareAccelerator"] | None,
    ) -> str | None:
        """Return the selected hardware decoder name, or None.

        IMPORTANT: Decoder selection must come from the selected accelerator.
        This is critical for future accelerators (NVDEC/VideoToolbox/VAAPI).
        """
        if accel is None:
            return None
        return accel.get_decoder(video_info)


# Global codec registry
CODEC_REGISTRY: Dict[str, Type[CodecHandler]] = {}


def register_codec(handler: Type[CodecHandler]) -> Type[CodecHandler]:
    """Decorator to register a codec handler."""
    caps = handler.capabilities()
    CODEC_REGISTRY[caps.name] = handler
    return handler


def get_codec_handler(codec_name: str) -> Type[CodecHandler] | None:
    """Get handler for a codec by name."""
    return CODEC_REGISTRY.get(codec_name)


@register_codec
class H264Handler(CodecHandler):
    """Handler for H.264/AVC encoding."""

    @classmethod
    def capabilities(cls) -> CodecCapabilities:
        return CodecCapabilities(
            name="h264",
            display_name="H.264/AVC",
            container="mp4",
            supports_quicksync_encode=True,
            supports_quicksync_decode=True,
            unity_compatible=True,
        )

    @classmethod
    def is_compatible(cls, video_info: VideoInfo) -> bool:
        """Check if already H.264 with Unity-compatible settings."""
        if video_info.codec_name.lower() not in ("h264", "avc"):
            return False
        if video_info.pixel_format != "yuv420p":
            return False
        if video_info.profile and video_info.profile.lower() not in ("baseline", "main", "high"):
            return False
        return True

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        video_info: VideoInfo,
        cfg: TranscoderConfig,
        accel: type["HardwareAccelerator"] | None,
        hw_encode_enabled: bool = False,
        hw_decode_enabled: bool = False,
    ) -> list[str]:
        h264_cfg = cfg.h264
        cmd = ["ffmpeg", "-y", "-hide_banner"]

        # Hardware decoder if available and enabled
        if hw_decode_enabled:
            if decoder := cls.get_hw_decoder(video_info, accel):
                cmd.extend(["-c:v", decoder])

        cmd.extend(["-i", str(input_path)])

        # Encoder selection
        if hw_encode_enabled and accel is not None:
            cmd.extend([
                "-c:v", "h264_qsv",
                "-preset", h264_cfg.preset,
                "-profile:v", h264_cfg.profile,
                "-global_quality", str(h264_cfg.crf),
                "-look_ahead", "1",
                "-pix_fmt", "nv12",
            ])
        else:
            cmd.extend([
                "-c:v", "libx264",
                "-preset", h264_cfg.preset,
                "-profile:v", h264_cfg.profile,
                "-crf", str(h264_cfg.crf),
                "-pix_fmt", h264_cfg.pixel_format,
            ])

        # Common settings
        cmd.extend([
            "-vsync", "cfr",
        ])
        # Optional caps
        if cfg.general.max_bitrate_kbps:
            max_k = int(cfg.general.max_bitrate_kbps)
            cmd.extend(["-maxrate", f"{max_k}k", "-bufsize", f"{max_k * 2}k"])

        vf: list[str] = []
        if cfg.general.max_resolution:
            max_w, max_h = cfg.general.max_resolution
            if cfg.usdb_integration.use_usdb_resolution:
                vf.append(
                    "scale='min(iw,{})':'min(ih,{})':force_original_aspect_ratio=decrease".format(
                        int(max_w), int(max_h)
                    )
                )
            else:
                vf.append(
                    "scale={}:{}:force_original_aspect_ratio=decrease,pad={}:{}:(ow-iw)/2:(oh-ih)/2".format(
                        int(max_w), int(max_h), int(max_w), int(max_h)
                    )
                )
        if cfg.general.max_fps:
            vf.append(f"fps=fps={int(cfg.general.max_fps)}")
        if vf:
            cmd.extend(["-vf", ",".join(vf)])

        # Audio handling - Fix MP4 compatibility
        if video_info.has_audio:
            if video_info.audio_codec in ("aac", "mp3", "alac"):
                cmd.extend(["-c:a", "copy"])
            else:
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-an"])

        if output_path.suffix.lower() in (".mp4", ".mov"):
            cmd.extend(["-movflags", "+faststart"])

        cmd.append(str(output_path))
        return cmd


@register_codec
class VP8Handler(CodecHandler):
    """Handler for VP8 encoding."""

    @classmethod
    def capabilities(cls) -> CodecCapabilities:
        return CodecCapabilities(
            name="vp8",
            display_name="VP8",
            container="webm",
            supports_quicksync_encode=False,
            supports_quicksync_decode=False,
            unity_compatible=True,
        )

    @classmethod
    def is_compatible(cls, video_info: VideoInfo) -> bool:
        return video_info.codec_name.lower() == "vp8"

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        video_info: VideoInfo,
        cfg: TranscoderConfig,
        accel: type["HardwareAccelerator"] | None,
        hw_encode_enabled: bool = False,
        hw_decode_enabled: bool = False,
    ) -> list[str]:
        vp8_cfg = cfg.vp8
        cmd = ["ffmpeg", "-y", "-hide_banner"]

        # Hardware decoder if available
        if hw_decode_enabled:
            if decoder := cls.get_hw_decoder(video_info, accel):
                cmd.extend(["-c:v", decoder])

        cmd.extend(["-i", str(input_path)])

        cmd.extend([
            "-c:v", "libvpx",
            "-crf", str(vp8_cfg.crf),
            "-b:v", "0",
            "-cpu-used", str(vp8_cfg.cpu_used),
            "-deadline", "good",
            "-auto-alt-ref", "1",
            "-lag-in-frames", "16",
            "-pix_fmt", "yuv420p",
            "-vsync", "cfr",
        ])

        # Optional caps
        if cfg.general.max_bitrate_kbps:
            max_k = int(cfg.general.max_bitrate_kbps)
            cmd.extend(["-maxrate", f"{max_k}k", "-bufsize", f"{max_k * 2}k"])

        vf: list[str] = []
        if cfg.general.max_resolution:
            max_w, max_h = cfg.general.max_resolution
            if cfg.usdb_integration.use_usdb_resolution:
                vf.append(
                    "scale='min(iw,{})':'min(ih,{})':force_original_aspect_ratio=decrease".format(
                        int(max_w), int(max_h)
                    )
                )
            else:
                vf.append(
                    "scale={}:{}:force_original_aspect_ratio=decrease,pad={}:{}:(ow-iw)/2:(oh-ih)/2".format(
                        int(max_w), int(max_h), int(max_w), int(max_h)
                    )
                )
        if cfg.general.max_fps:
            vf.append(f"fps=fps={int(cfg.general.max_fps)}")
        if vf:
            cmd.extend(["-vf", ",".join(vf)])

        if video_info.has_audio:
            if video_info.audio_codec in ("opus", "vorbis"):
                cmd.extend(["-c:a", "copy"])
            else:
                cmd.extend(["-c:a", "libopus", "-b:a", "160k"])
        else:
            cmd.extend(["-an"])

        cmd.append(str(output_path))
        return cmd


@register_codec
class HEVCHandler(CodecHandler):
    """Handler for HEVC/H.265 encoding."""

    @classmethod
    def capabilities(cls) -> CodecCapabilities:
        return CodecCapabilities(
            name="hevc",
            display_name="HEVC/H.265",
            container="mp4",
            supports_quicksync_encode=True,
            supports_quicksync_decode=True,
            unity_compatible=True,
        )

    @classmethod
    def is_compatible(cls, video_info: VideoInfo) -> bool:
        if video_info.codec_name.lower() not in ("hevc", "h265"):
            return False
        if video_info.pixel_format != "yuv420p":
            return False
        return True

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        video_info: VideoInfo,
        cfg: TranscoderConfig,
        accel: type["HardwareAccelerator"] | None,
        hw_encode_enabled: bool = False,
        hw_decode_enabled: bool = False,
    ) -> list[str]:
        hevc_cfg = cfg.hevc
        cmd = ["ffmpeg", "-y", "-hide_banner"]

        if hw_decode_enabled:
            if decoder := cls.get_hw_decoder(video_info, accel):
                cmd.extend(["-c:v", decoder])

        cmd.extend(["-i", str(input_path)])

        if hw_encode_enabled and accel is not None:
            cmd.extend([
                "-c:v", "hevc_qsv",
                "-preset", hevc_cfg.preset,
                "-profile:v", hevc_cfg.profile,
                "-global_quality", str(hevc_cfg.crf),
                "-rc_mode", "icq",
                "-pix_fmt", "nv12",
            ])
        else:
            cmd.extend([
                "-c:v", "libx265",
                "-preset", hevc_cfg.preset,
                "-profile:v", hevc_cfg.profile,
                "-crf", str(hevc_cfg.crf),
                "-tag:v", "hvc1",
                "-pix_fmt", hevc_cfg.pixel_format,
            ])

        cmd.extend(["-vsync", "cfr"])

        # Optional caps
        if cfg.general.max_bitrate_kbps:
            max_k = int(cfg.general.max_bitrate_kbps)
            cmd.extend(["-maxrate", f"{max_k}k", "-bufsize", f"{max_k * 2}k"])

        vf: list[str] = []
        if cfg.general.max_resolution:
            max_w, max_h = cfg.general.max_resolution
            if cfg.usdb_integration.use_usdb_resolution:
                vf.append(
                    "scale='min(iw,{})':'min(ih,{})':force_original_aspect_ratio=decrease".format(
                        int(max_w), int(max_h)
                    )
                )
            else:
                vf.append(
                    "scale={}:{}:force_original_aspect_ratio=decrease,pad={}:{}:(ow-iw)/2:(oh-ih)/2".format(
                        int(max_w), int(max_h), int(max_w), int(max_h)
                    )
                )
        if cfg.general.max_fps:
            vf.append(f"fps=fps={int(cfg.general.max_fps)}")
        if vf:
            cmd.extend(["-vf", ",".join(vf)])

        # Audio handling - Fix MP4 compatibility
        if video_info.has_audio:
            if video_info.audio_codec in ("aac", "mp3", "alac"):
                cmd.extend(["-c:a", "copy"])
            else:
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-an"])

        if output_path.suffix.lower() in (".mp4", ".mov"):
            cmd.extend(["-movflags", "+faststart"])

        cmd.append(str(output_path))
        return cmd


@register_codec
class VP9Handler(CodecHandler):
    """Handler for VP9 encoding."""

    @classmethod
    def capabilities(cls) -> CodecCapabilities:
        return CodecCapabilities(
            name="vp9",
            display_name="VP9",
            container="webm",
            supports_quicksync_encode=True,
            supports_quicksync_decode=True,
            unity_compatible=False,
        )

    @classmethod
    def is_compatible(cls, video_info: VideoInfo) -> bool:
        return video_info.codec_name.lower() == "vp9"

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        video_info: VideoInfo,
        cfg: TranscoderConfig,
        accel: type["HardwareAccelerator"] | None,
        hw_encode_enabled: bool = False,
        hw_decode_enabled: bool = False,
    ) -> list[str]:
        vp9_cfg = cfg.vp9
        cmd = ["ffmpeg", "-y", "-hide_banner"]

        # Hardware decoder if available
        if hw_decode_enabled:
            if decoder := cls.get_hw_decoder(video_info, accel):
                cmd.extend(["-c:v", decoder])

        cmd.extend(["-i", str(input_path)])

        # Encoder selection
        if hw_encode_enabled and accel is not None:
            # QSV VP9
            cmd.extend([
                "-c:v", "vp9_qsv",
                "-global_quality", str(vp9_cfg.crf),
                "-pix_fmt", "nv12",
            ])
        else:
            # Software VP9
            cmd.extend([
                "-c:v", "libvpx-vp9",
                "-crf", str(vp9_cfg.crf),
                "-b:v", "0",
                "-deadline", vp9_cfg.deadline,
                "-cpu-used", str(vp9_cfg.cpu_used),
                "-row-mt", "1",
                "-tile-columns", "2",
                "-g", "240",
                "-pix_fmt", "yuv420p",
            ])

        cmd.extend(["-vsync", "cfr"])

        # Optional caps
        if cfg.general.max_bitrate_kbps:
            max_k = int(cfg.general.max_bitrate_kbps)
            cmd.extend(["-maxrate", f"{max_k}k", "-bufsize", f"{max_k * 2}k"])

        # Video filters
        vf: list[str] = []
        if cfg.general.max_resolution:
            max_w, max_h = cfg.general.max_resolution
            vf.append(f"scale='min(iw,{int(max_w)})':'min(ih,{int(max_h)})':force_original_aspect_ratio=decrease")
        if cfg.general.max_fps:
            vf.append(f"fps=fps={int(cfg.general.max_fps)}")
        if vf:
            cmd.extend(["-vf", ",".join(vf)])

        # Audio handling - prefer Opus for WebM
        if video_info.has_audio:
            if video_info.audio_codec in ("opus", "vorbis"):
                cmd.extend(["-c:a", "copy"])
            else:
                cmd.extend(["-c:a", "libopus", "-b:a", "160k"])
        else:
            cmd.extend(["-an"])

        cmd.append(str(output_path))
        return cmd


@register_codec
class AV1Handler(CodecHandler):
    """Handler for AV1 encoding."""

    @classmethod
    def capabilities(cls) -> CodecCapabilities:
        return CodecCapabilities(
            name="av1",
            display_name="AV1",
            container="mkv",
            supports_quicksync_encode=True,
            supports_quicksync_decode=True,
            unity_compatible=False,
        )

    @classmethod
    def is_compatible(cls, video_info: VideoInfo) -> bool:
        return video_info.codec_name.lower() == "av1"

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        video_info: VideoInfo,
        cfg: TranscoderConfig,
        accel: type["HardwareAccelerator"] | None,
        hw_encode_enabled: bool = False,
        hw_decode_enabled: bool = False,
    ) -> list[str]:
        av1_cfg = cfg.av1
        cmd = ["ffmpeg", "-y", "-hide_banner"]

        # Hardware decoder if available
        if hw_decode_enabled:
            if decoder := cls.get_hw_decoder(video_info, accel):
                cmd.extend(["-c:v", decoder])

        cmd.extend(["-i", str(input_path)])

        # Encoder selection
        from .utils import check_encoder_available
        if hw_encode_enabled and accel is not None:
            # QSV AV1
            cmd.extend([
                "-c:v", "av1_qsv",
                "-rc_mode", "icq",
                "-global_quality", str(av1_cfg.crf),
                "-pix_fmt", "nv12",
            ])
        elif check_encoder_available("libsvtav1"):
            # Software SVT-AV1
            cmd.extend([
                "-c:v", "libsvtav1",
                "-crf", str(av1_cfg.crf),
                "-preset", str(av1_cfg.cpu_used),
                "-g", "240",
                "-pix_fmt", "yuv420p10le",
            ])
        elif check_encoder_available("libaom-av1"):
            # Software libaom-av1
            cmd.extend([
                "-c:v", "libaom-av1",
                "-crf", str(av1_cfg.crf),
                "-cpu-used", str(av1_cfg.cpu_used),
                "-g", "240",
                "-pix_fmt", "yuv420p10le",
            ])
        else:
            # Fallback to generic av1 encoder
            cmd.extend(["-c:v", "av1"])

        cmd.extend(["-vsync", "cfr"])

        # Optional caps
        if cfg.general.max_bitrate_kbps:
            max_k = int(cfg.general.max_bitrate_kbps)
            cmd.extend(["-maxrate", f"{max_k}k", "-bufsize", f"{max_k * 2}k"])

        # Video filters
        vf: list[str] = []
        if cfg.general.max_resolution:
            max_w, max_h = cfg.general.max_resolution
            vf.append(f"scale='min(iw,{int(max_w)})':'min(ih,{int(max_h)})':force_original_aspect_ratio=decrease")
        if cfg.general.max_fps:
            vf.append(f"fps=fps={int(cfg.general.max_fps)}")
        if vf:
            cmd.extend(["-vf", ",".join(vf)])

        # Audio handling - Opus for MKV/WebM, AAC for MP4
        if video_info.has_audio:
            if output_path.suffix.lower() in (".mp4", ".mov"):
                if video_info.audio_codec in ("aac", "mp3", "alac"):
                    cmd.extend(["-c:a", "copy"])
                else:
                    cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            else:
                if video_info.audio_codec in ("opus", "vorbis"):
                    cmd.extend(["-c:a", "copy"])
                else:
                    cmd.extend(["-c:a", "libopus", "-b:a", "160k"])
        else:
            cmd.extend(["-an"])

        if output_path.suffix.lower() in (".mp4", ".mov"):
            cmd.extend(["-movflags", "+faststart"])

        cmd.append(str(output_path))
        return cmd


# ============================================================
# Audio codec support (Stage 2)
# ============================================================


@dataclass
class AudioCodecCapabilities:
    """Describes an audio codec handler's capabilities."""

    name: str  # e.g., "mp3", "aac"
    display_name: str  # e.g., "MP3 (LAME)"
    container: str  # Default container extension (no leading dot)
    container_extensions: Tuple[str, ...]  # Allowed extensions (no leading dot)


class AudioCodecHandler(ABC):
    """Abstract base class for audio codec handlers.

    Audio handlers build FFmpeg commands for:
    - standalone audio inputs (e.g. .wav/.flac/.mp3)
    - extracting/transcoding audio from video containers (e.g. .mp4 with audio stream)
    """

    @classmethod
    @abstractmethod
    def capabilities(cls) -> AudioCodecCapabilities:
        """Return audio codec capabilities."""
        ...

    @classmethod
    @abstractmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        cfg: "TranscoderConfig",
        *,
        stream_copy: bool = False,
    ) -> list[str]:
        """Build FFmpeg command for audio-only output.

        Notes:
        - Always uses `-vn` to ensure no video stream is written.
        - Uses `-map 0:a:0?` to gracefully handle inputs without audio streams.
        """
        ...

    @classmethod
    def validate_config(cls, cfg: "TranscoderConfig") -> None:
        """Validate relevant config for this codec.

        Implementations should raise ValueError with a user-actionable message.
        """
        return

    @classmethod
    def is_container_compatible(cls, path: Path) -> bool:
        """Return True if the file extension matches this codec's container."""
        ext = path.suffix.lower().lstrip(".")
        return ext in set(cls.capabilities().container_extensions)


# Global audio codec registry
AUDIO_CODEC_REGISTRY: Dict[str, Type[AudioCodecHandler]] = {}


def register_audio_codec(handler: Type[AudioCodecHandler]) -> Type[AudioCodecHandler]:
    """Decorator to register an audio codec handler."""
    caps = handler.capabilities()
    AUDIO_CODEC_REGISTRY[caps.name] = handler
    return handler


def get_audio_codec_handler(codec_name: str) -> Type[AudioCodecHandler] | None:
    """Get audio codec handler for a codec by name."""
    return AUDIO_CODEC_REGISTRY.get(codec_name)


def _ensure_int_in_range(name: str, value: int, low: int, high: int) -> None:
    if value < low or value > high:
        raise ValueError(f"{name} must be between {low} and {high} (got {value})")


def _ensure_float_in_range(name: str, value: float, low: float, high: float) -> None:
    if value < low or value > high:
        raise ValueError(f"{name} must be between {low} and {high} (got {value})")


def _audio_common_prefix(input_path: Path) -> list[str]:
    """Return common FFmpeg arguments for audio-only outputs."""
    # `-map 0:a:0?` selects the first audio stream if present, and avoids a hard
    # error for containers without audio (ffmpeg will fail later on encoding).
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i",
        str(input_path),
        "-map",
        "0:a:0?",
        "-vn",
        "-sn",
        "-dn",
    ]


def _audio_force_extension(path: Path, extensions: Iterable[str]) -> None:
    """Validate output extension against supported container extensions."""
    ext = path.suffix.lower().lstrip(".")
    if ext not in set(extensions):
        raise ValueError(f"Unsupported output extension '.{ext}' for audio codec")


@register_audio_codec
class MP3AudioHandler(AudioCodecHandler):
    """Handler for MP3 (LAME) encoding."""

    @classmethod
    def capabilities(cls) -> AudioCodecCapabilities:
        return AudioCodecCapabilities(
            name="mp3",
            display_name="MP3 (LAME)",
            container="mp3",
            container_extensions=("mp3",),
        )

    @classmethod
    def validate_config(cls, cfg: "TranscoderConfig") -> None:
        _ensure_int_in_range("mp3_quality", int(cfg.audio.mp3_quality), 0, 9)

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        cfg: "TranscoderConfig",
        *,
        stream_copy: bool = False,
    ) -> list[str]:
        cls.validate_config(cfg)
        _audio_force_extension(output_path, cls.capabilities().container_extensions)

        cmd = _audio_common_prefix(input_path)
        if stream_copy:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend([
                "-c:a",
                "libmp3lame",
                "-q:a",
                str(int(cfg.audio.mp3_quality)),
            ])
        cmd.append(str(output_path))
        return cmd


@register_audio_codec
class VorbisAudioHandler(AudioCodecHandler):
    """Handler for Ogg Vorbis encoding."""

    @classmethod
    def capabilities(cls) -> AudioCodecCapabilities:
        return AudioCodecCapabilities(
            name="vorbis",
            display_name="Ogg Vorbis",
            container="ogg",
            container_extensions=("ogg",),
        )

    @classmethod
    def validate_config(cls, cfg: "TranscoderConfig") -> None:
        _ensure_float_in_range("vorbis_quality", float(cfg.audio.vorbis_quality), -1.0, 10.0)

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        cfg: "TranscoderConfig",
        *,
        stream_copy: bool = False,
    ) -> list[str]:
        cls.validate_config(cfg)
        _audio_force_extension(output_path, cls.capabilities().container_extensions)

        cmd = _audio_common_prefix(input_path)
        if stream_copy:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend([
                "-c:a",
                "libvorbis",
                "-q:a",
                str(float(cfg.audio.vorbis_quality)),
            ])
        cmd.append(str(output_path))
        return cmd


@register_audio_codec
class AACAudioHandler(AudioCodecHandler):
    """Handler for AAC (native `aac`) encoding in an M4A container."""

    @classmethod
    def capabilities(cls) -> AudioCodecCapabilities:
        return AudioCodecCapabilities(
            name="aac",
            display_name="AAC (M4A)",
            container="m4a",
            # Accept mp4 as compatible container for AAC stream-copy operations.
            container_extensions=("m4a", "mp4"),
        )

    @classmethod
    def validate_config(cls, cfg: "TranscoderConfig") -> None:
        _ensure_int_in_range("aac_vbr_mode", int(cfg.audio.aac_vbr_mode), 1, 5)

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        cfg: "TranscoderConfig",
        *,
        stream_copy: bool = False,
    ) -> list[str]:
        cls.validate_config(cfg)
        _audio_force_extension(output_path, ("m4a", "mp4"))

        cmd = _audio_common_prefix(input_path)
        if stream_copy:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend([
                "-c:a",
                "aac",
                "-vbr",
                str(int(cfg.audio.aac_vbr_mode)),
            ])

        # MP4-family containers: enable faststart.
        if output_path.suffix.lower() in (".m4a", ".mp4", ".mov"):
            cmd.extend(["-movflags", "+faststart"])

        cmd.append(str(output_path))
        return cmd


@register_audio_codec
class OpusAudioHandler(AudioCodecHandler):
    """Handler for Opus (`libopus`) encoding in an Ogg Opus container."""

    @classmethod
    def capabilities(cls) -> AudioCodecCapabilities:
        return AudioCodecCapabilities(
            name="opus",
            display_name="Opus",
            container="opus",
            container_extensions=("opus",),
        )

    @classmethod
    def validate_config(cls, cfg: "TranscoderConfig") -> None:
        _ensure_int_in_range("opus_bitrate_kbps", int(cfg.audio.opus_bitrate_kbps), 6, 510)

    @classmethod
    def build_encode_command(
        cls,
        input_path: Path,
        output_path: Path,
        cfg: "TranscoderConfig",
        *,
        stream_copy: bool = False,
    ) -> list[str]:
        cls.validate_config(cfg)
        _audio_force_extension(output_path, cls.capabilities().container_extensions)

        cmd = _audio_common_prefix(input_path)
        if stream_copy:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend([
                "-c:a",
                "libopus",
                "-b:a",
                f"{int(cfg.audio.opus_bitrate_kbps)}k",
            ])
        cmd.append(str(output_path))
        return cmd
