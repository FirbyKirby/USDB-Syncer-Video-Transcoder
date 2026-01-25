"""Configuration management for the Transcoder addon."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Optional


_logger = logging.getLogger(__name__)

TargetCodec = Literal["h264", "vp8", "hevc", "vp9", "av1"]
AudioCodec = Literal["mp3", "vorbis", "aac", "opus"]
H264Profile = Literal["baseline", "main", "high"]
HEVCProfile = Literal["main", "main10"]
AudioNormalizationMethod = Literal["loudnorm", "replaygain"]
VerificationTolerancePreset = Literal["strict", "balanced", "relaxed"]


@dataclass
class H264Config:
    """Configuration for H.264 encoding."""
    profile: H264Profile = "high"
    pixel_format: str = "yuv420p"
    crf: int = 18
    preset: str = "fast"
    container: str = "mp4"


@dataclass
class VP8Config:
    """Configuration for VP8 encoding."""
    crf: int = 10
    cpu_used: int = 4  # 0-5, lower = better quality, slower
    container: str = "webm"


@dataclass
class HEVCConfig:
    """Configuration for HEVC encoding."""
    profile: HEVCProfile = "main"
    pixel_format: str = "yuv420p"
    crf: int = 18
    preset: str = "faster"
    container: str = "mp4"


@dataclass
class VP9Config:
    """Configuration for VP9 encoding."""
    crf: int = 20
    cpu_used: int = 4  # 0-8, lower = better quality, slower
    deadline: str = "good"  # good, best, realtime
    container: str = "webm"


@dataclass
class AV1Config:
    """Configuration for AV1 encoding."""
    crf: int = 20
    cpu_used: int = 8  # For svt-av1: preset (0-13, lower = slower/better)
    container: str = "mkv"


@dataclass
class AudioConfig:
    """Configuration for standalone audio transcoding.

    Audio quality controls are codec-specific (see docs/ARCHITECTURE.md).

    - MP3: LAME VBR quality (0-9, lower = better)
    - Vorbis: quality scale (-1.0 to 10.0)
    - AAC: VBR mode (1-5)
    - Opus: bitrate in kbps (6-510)

    Normalization fields for audio normalization; config plumbing is prepared.
    """

    # Enable/disable automatic audio processing after download.
    # Kept in the audio section so video and audio can be toggled independently.
    audio_transcode_enabled: bool = False

    # Force re-transcode audio even when input codec/container already matches.
    # This is intentionally separate from the video force flag for independent control.
    force_transcode_audio: bool = False

    audio_codec: AudioCodec = "aac"

    # Codec-specific quality settings
    mp3_quality: int = 0
    vorbis_quality: float = 10.0
    aac_vbr_mode: int = 5
    opus_bitrate_kbps: int = 160

    # Normalization fields for audio normalization
    audio_normalization_enabled: bool = False
    audio_normalization_target: float = -18.0
    # EBU R128 loudnorm targets (used when audio_normalization_method == "loudnorm")
    # Defaults are conservative and align with typical karaoke/music playback normalization.
    audio_normalization_true_peak: float = -2.0
    audio_normalization_lra: float = 11.0
    audio_normalization_method: AudioNormalizationMethod = "loudnorm"
    audio_normalization_use_usdb_defaults: bool = True

    def get_usdb_target_loudness(self) -> float:
        """Return USDB Syncer default target loudness for the current codec."""
        return -23.0 if self.audio_codec == "opus" else -18.0


@dataclass
class VerificationTolerance:
    """Tolerances for loudness verification."""

    i_tolerance: float  # LUFS tolerance for integrated loudness
    tp_tolerance: float  # dB tolerance for true peak (allowable overshoot)
    lra_tolerance: float  # LU tolerance for loudness range


@dataclass
class VerificationConfig:
    """Configuration for loudness verification."""

    enabled: bool = False  # Changed to False - opt-in for automatic downloads (High Issue #6)
    tolerance_preset: VerificationTolerancePreset = "balanced"
    custom_i_tolerance: Optional[float] = None
    custom_tp_tolerance: Optional[float] = None
    custom_lra_tolerance: Optional[float] = None

    def get_active_tolerances(self) -> VerificationTolerance:
        """Return appropriate tolerances based on preset or custom values."""
        if (
            self.custom_i_tolerance is not None
            and self.custom_tp_tolerance is not None
            and self.custom_lra_tolerance is not None
        ):
            return VerificationTolerance(
                i_tolerance=self.custom_i_tolerance,
                tp_tolerance=self.custom_tp_tolerance,
                lra_tolerance=self.custom_lra_tolerance,
            )
        # Use preset
        presets = {
            "strict": VerificationTolerance(i_tolerance=1.0, tp_tolerance=0.3, lra_tolerance=2.0),
            "balanced": VerificationTolerance(i_tolerance=1.5, tp_tolerance=0.5, lra_tolerance=3.0),
            "relaxed": VerificationTolerance(i_tolerance=2.0, tp_tolerance=0.8, lra_tolerance=4.0),
        }
        return presets[self.tolerance_preset]


@dataclass
class GeneralConfig:
    """General transcoding settings."""
    hardware_encoding: bool = True
    # When True, allow hardware decoders (e.g. *_qsv) to be selected.
    # Kept separate from hardware_encoding so we can avoid "HW decode + SW encode"
    # pipelines that often fail without hwdownload/hwupload filters.
    hardware_decode: bool = True
    backup_original: bool = True
    backup_suffix: str = "-source"
    max_resolution: Optional[tuple[int, int]] = None
    max_fps: Optional[int] = None
    max_bitrate_kbps: Optional[int] = None
    timeout_seconds: int = 600
    verify_output: bool = True
    min_free_space_mb: int = 500
    force_transcode_video: bool = False


@dataclass
class UsdbIntegrationConfig:
    """Optional integration with USDB Syncer settings."""

    use_usdb_resolution: bool = True
    use_usdb_fps: bool = True


@dataclass
class TranscoderConfig:
    """Root configuration object."""
    # NOTE: This addon is still pre-release; keep the schema version stable.
    # New fields are added with safe defaults and unknown fields are ignored.
    version: int = 2
    auto_transcode_enabled: bool = False
    target_codec: TargetCodec = "h264"
    h264: H264Config = field(default_factory=H264Config)
    vp8: VP8Config = field(default_factory=VP8Config)
    hevc: HEVCConfig = field(default_factory=HEVCConfig)
    vp9: VP9Config = field(default_factory=VP9Config)
    av1: AV1Config = field(default_factory=AV1Config)
    audio: AudioConfig = field(default_factory=AudioConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)
    usdb_integration: UsdbIntegrationConfig = field(default_factory=UsdbIntegrationConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)


def get_config_path() -> Path:
    """Return path to config file in USDB Syncer data directory."""
    from usdb_syncer.utils import AppPaths
    return AppPaths.db.parent / "transcoder_config.json"


def load_config() -> TranscoderConfig:
    """Load configuration from JSON file, creating defaults if needed."""
    config_path = get_config_path()

    if not config_path.exists():
        cfg = TranscoderConfig()
        save_config(cfg)
        _logger.info(f"Created default config at {config_path}")
        return cfg

    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return _parse_config(data)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        _logger.warning(f"Config parse error, using defaults: {e}")
        return TranscoderConfig()


def save_config(cfg: TranscoderConfig) -> None:
    """Save configuration to JSON file."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, indent=2)


def _migrate_config(data: dict) -> dict:
    """Migrate old config data to current version."""
    version = data.get("version", 1)
    if version >= 2:
        return data

    _logger.info(f"Migrating config from version {version} to 2")

    # Remove per-codec hardware fields and move to global if any were disabled.
    hw_enabled = data.get("general", {}).get("hardware_encoding", True)

    codecs_to_check = [
        ("h264", "use_quicksync"),
        ("hevc", "use_quicksync"),
        ("vp9", "use_hardware"),
        ("av1", "use_hardware"),
    ]

    for section, field_name in codecs_to_check:
        if section in data and field_name in data[section]:
            if data[section][field_name] is False:
                _logger.info(f"Disabling global hardware encoding because {section}.{field_name} was False")
                hw_enabled = False
            del data[section][field_name]

    if "av1" in data and "encoder" in data["av1"]:
        del data["av1"]["encoder"]

    if "general" not in data:
        data["general"] = {}
    data["general"]["hardware_encoding"] = hw_enabled
    data["version"] = 2

    return data


def _parse_config(data: dict) -> TranscoderConfig:
    """Parse raw JSON dict into TranscoderConfig."""
    data = _migrate_config(data)

    general_data = data.get("general", {})
    if "max_resolution" in general_data and general_data["max_resolution"]:
        general_data["max_resolution"] = tuple(general_data["max_resolution"])

    # Filter out unknown fields for each dataclass to avoid TypeError
    def get_clean_dict(cls, d):
        return {k: v for k, v in d.items() if k in cls.__dataclass_fields__}

    return TranscoderConfig(
        version=data.get("version", 2),
        auto_transcode_enabled=data.get("auto_transcode_enabled", False),
        target_codec=data.get("target_codec", "h264"),
        h264=H264Config(**get_clean_dict(H264Config, data.get("h264", {}))),
        vp8=VP8Config(**get_clean_dict(VP8Config, data.get("vp8", {}))),
        hevc=HEVCConfig(**get_clean_dict(HEVCConfig, data.get("hevc", {}))),
        vp9=VP9Config(**get_clean_dict(VP9Config, data.get("vp9", {}))),
        av1=AV1Config(**get_clean_dict(AV1Config, data.get("av1", {}))),
        audio=AudioConfig(**get_clean_dict(AudioConfig, data.get("audio", {}))),
        general=GeneralConfig(**get_clean_dict(GeneralConfig, general_data)),
        usdb_integration=UsdbIntegrationConfig(**get_clean_dict(UsdbIntegrationConfig, data.get("usdb_integration", {}))),
        verification=VerificationConfig(**get_clean_dict(VerificationConfig, data.get("verification", {}))),
    )
