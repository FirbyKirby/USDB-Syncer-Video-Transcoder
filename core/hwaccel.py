"""Hardware acceleration registry and QuickSync implementation."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Type

from usdb_syncer.utils import LinuxEnvCleaner

if TYPE_CHECKING:
    from .video_analyzer import VideoInfo


@dataclass
class HWAccelCapabilities:
    """Describes a hardware accelerator's capabilities."""
    name: str                          # e.g., "quicksync", "nvenc", "videotoolbox"
    display_name: str                  # e.g., "Intel QuickSync"
    platforms: tuple[str, ...]         # e.g., "win32", "linux"
    h264_encoder: str | None           # e.g., "h264_qsv"
    hevc_encoder: str | None           # e.g., "hevc_qsv"
    vp8_encoder: str | None            # VP8 hardware support is rare
    vp9_encoder: str | None            # e.g., "vp9_qsv"
    av1_encoder: str | None            # e.g., "av1_qsv" for newer hardware


class HardwareAccelerator:
    """Abstract base class for hardware acceleration backends."""

    @classmethod
    def capabilities(cls) -> HWAccelCapabilities:
        """Return accelerator capabilities."""
        raise NotImplementedError

    @classmethod
    def is_available(cls) -> bool:
        """Check if this accelerator is available on current system."""
        raise NotImplementedError

    @classmethod
    def get_decoder(cls, video_info: VideoInfo) -> str | None:
        """Get hardware decoder for input codec, or None if unsupported."""
        raise NotImplementedError

    @classmethod
    def is_encoder_available(cls, encoder: str) -> bool:
        """Check if a specific encoder is available for this accelerator."""
        return cls.is_available()

    @classmethod
    def supports_platform(cls) -> bool:
        """Check if current platform is supported."""
        caps = cls.capabilities()
        return sys.platform in caps.platforms


# Global registry
HWACCEL_REGISTRY: Dict[str, Type[HardwareAccelerator]] = {}


def register_hwaccel(accel: Type[HardwareAccelerator]) -> Type[HardwareAccelerator]:
    """Decorator to register a hardware accelerator."""
    caps = accel.capabilities()
    HWACCEL_REGISTRY[caps.name] = accel
    return accel


def detect_available_accelerators() -> list[HWAccelCapabilities]:
    """Detect all available hardware accelerators on this system."""
    available = []
    for accel in HWACCEL_REGISTRY.values():
        if accel.supports_platform() and accel.is_available():
            available.append(accel.capabilities())
    return available


def get_best_accelerator(codec: str) -> Type[HardwareAccelerator] | None:
    """Get the best available accelerator for a codec.

    Priority order: QuickSync > NVENC > AMF > VideoToolbox > VAAPI > None
    """
    priority = ["quicksync", "nvenc", "amf", "videotoolbox", "vaapi"]

    for name in priority:
        if accel := HWACCEL_REGISTRY.get(name):
            if accel.supports_platform():
                caps = accel.capabilities()
                encoder = getattr(caps, f"{codec}_encoder", None)
                if not encoder:
                    continue

                # Check if the accelerator is available AND supports this specific encoder
                if hasattr(accel, "is_encoder_available"):
                    if accel.is_encoder_available(encoder):
                        return accel
                elif accel.is_available():
                    # Fallback for accelerators that don't implement granular checks
                    return accel
    return None


def get_best_decoder_accelerator(video_info: VideoInfo) -> Type[HardwareAccelerator] | None:
    """Get the best available accelerator that supports decoding the input video."""
    priority = ["quicksync", "nvenc", "amf", "videotoolbox", "vaapi"]

    for name in priority:
        if accel := HWACCEL_REGISTRY.get(name):
            if accel.supports_platform() and accel.is_available():
                if accel.get_decoder(video_info):
                    return accel
    return None


# Cache availability check
_qsv_available: bool | None = None


@register_hwaccel
class QuickSyncAccelerator(HardwareAccelerator):
    """Intel QuickSync Video hardware acceleration."""

    @classmethod
    def capabilities(cls) -> HWAccelCapabilities:
        return HWAccelCapabilities(
            name="quicksync",
            display_name="Intel QuickSync",
            platforms=("win32", "linux"),
            h264_encoder="h264_qsv",
            hevc_encoder="hevc_qsv",
            vp8_encoder=None,
            vp9_encoder="vp9_qsv",
            av1_encoder="av1_qsv",
        )

    @classmethod
    def is_available(cls) -> bool:
        global _qsv_available
        if _qsv_available is not None:
            return _qsv_available

        # Test h264_qsv encoder
        cmd = [
            "ffmpeg", "-hide_banner",
            "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
            "-c:v", "h264_qsv",
            "-f", "null", "-"
        ]
        try:
            with LinuxEnvCleaner() as env:
                result = subprocess.run(cmd, capture_output=True, timeout=10, env=env)
            _qsv_available = result.returncode == 0
        except Exception:
            _qsv_available = False

        return _qsv_available

    @classmethod
    def is_encoder_available(cls, encoder: str) -> bool:
        """Test if a specific QSV encoder is available."""
        # We use a small nullsrc test to see if the encoder can actually be opened.
        cmd = [
            "ffmpeg", "-hide_banner",
            "-f", "lavfi", "-i", "nullsrc=s=128x128:d=1",
            "-c:v", encoder,
        ]
        if encoder in ("h264_qsv", "hevc_qsv", "vp9_qsv", "av1_qsv"):
            cmd.extend(["-global_quality", "20"])
        cmd.extend(["-frames:v", "1", "-f", "null", "-"])
        try:
            with LinuxEnvCleaner() as env:
                result = subprocess.run(cmd, capture_output=True, timeout=5, env=env)
            return result.returncode == 0
        except Exception:
            return False

    @classmethod
    def get_decoder(cls, video_info: VideoInfo) -> str | None:
        codec_map = {
            "h264": "h264_qsv",
            "hevc": "hevc_qsv",
            "h265": "hevc_qsv",
            "vp9": "vp9_qsv",
            "av1": "av1_qsv",
            "mpeg2video": "mpeg2_qsv",
            "vc1": "vc1_qsv",
            "mjpeg": "mjpeg_qsv",
        }
        return codec_map.get(video_info.codec_name.lower())
