"""Utilities for estimating batch transcode metrics."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import TranscoderConfig
    from .video_analyzer import VideoInfo
    from .batch_orchestrator import BatchTranscodeCandidate


class BatchEstimator:
    """Utilities for estimating batch transcode metrics."""

    @staticmethod
    def estimate_output_size(
        video_info: VideoInfo,
        cfg: TranscoderConfig
    ) -> float:
        """
        Estimate output file size in MB.

        Uses heuristics based on:
        - Duration
        - Target resolution
        - Target codec
        - CRF/quality settings
        - Bitrate settings

        Returns: estimated size in MB
        """
        duration = video_info.duration_seconds
        if duration <= 0:
            return 0.0

        # Base bitrate for 1080p H.264 at CRF 18 (roughly 5 Mbps)
        base_bitrate_kbps = 5000.0

        # 1. Codec efficiency factor
        # HEVC is ~50% more efficient than H.264
        # AV1 is ~60% more efficient than H.264
        # VP9 is ~40% more efficient than H.264
        codec_factors = {
            "h264": 1.0,
            "hevc": 0.5,
            "av1": 0.4,
            "vp9": 0.6,
            "vp8": 1.2,
        }
        codec_factor = codec_factors.get(cfg.target_codec, 1.0)

        # 2. CRF adjustment
        # CRF 18 is our baseline. Every 6 points roughly doubles/halves bitrate.
        codec_cfg = getattr(cfg, cfg.target_codec)
        crf = getattr(codec_cfg, "crf", 18)
        crf_factor = 2.0 ** ((18 - crf) / 6.0)

        # 3. Resolution scaling factor
        # Bitrate doesn't scale linearly with pixels, but roughly with square root of pixel count change
        target_w, target_h = video_info.width, video_info.height
        if cfg.general.max_resolution:
            max_w, max_h = cfg.general.max_resolution
            if cfg.usdb_integration.use_usdb_resolution:
                # Limit behavior
                target_w = min(video_info.width, max_w)
                target_h = min(video_info.height, max_h)
            else:
                # Exact behavior
                target_w, target_h = max_w, max_h

        pixel_ratio = (target_w * target_h) / (1920 * 1080)
        res_factor = pixel_ratio ** 0.75  # Heuristic scaling

        # 4. Bitrate limit
        estimated_bitrate_kbps = base_bitrate_kbps * codec_factor * crf_factor * res_factor
        if cfg.general.max_bitrate_kbps:
            estimated_bitrate_kbps = min(estimated_bitrate_kbps, cfg.general.max_bitrate_kbps)

        # Calculate size: (kbps * seconds) / 8 / 1024 = MB
        estimated_size_mb = (estimated_bitrate_kbps * duration) / 8192.0

        # Add 5% overhead for container and audio
        return estimated_size_mb * 1.05

    @staticmethod
    def estimate_transcode_time(
        video_info: VideoInfo,
        cfg: TranscoderConfig,
        hw_accel_available: bool
    ) -> float:
        """
        Estimate transcode time in seconds.

        Uses heuristics based on:
        - Duration
        - Resolution
        - Codec complexity
        - Hardware acceleration status

        Returns: estimated time in seconds
        """
        duration = video_info.duration_seconds
        if duration <= 0:
            return 0.0

        # Base speed: 1.0 means realtime (1 second of video takes 1 second to transcode)
        # Software encoding on a modern CPU for 1080p H.264 is roughly 2x realtime
        base_speed = 2.0

        if hw_accel_available:
            # Hardware encoding is much faster, e.g., 8x realtime
            base_speed = 8.0

        # 1. Codec complexity factor
        # HEVC is ~3x slower than H.264 in software
        # AV1 is ~10x slower than H.264 in software
        # VP9 is ~2x slower than H.264 in software
        complexity_factors = {
            "h264": 1.0,
            "hevc": 0.33,
            "av1": 0.1,
            "vp9": 0.5,
            "vp8": 0.8,
        }
        
        # Hardware acceleration mitigates some complexity
        if hw_accel_available:
            complexity_factors = {
                "h264": 1.0,
                "hevc": 0.8,  # HW HEVC is almost as fast as H.264
                "av1": 0.5,   # HW AV1 is still slower but much better than SW
                "vp9": 0.7,
                "vp8": 0.9,
            }
            
        complexity_factor = complexity_factors.get(cfg.target_codec, 1.0)

        # 2. Resolution factor
        # Time scales roughly linearly with pixel count
        target_w, target_h = video_info.width, video_info.height
        if cfg.general.max_resolution:
            max_w, max_h = cfg.general.max_resolution
            if cfg.usdb_integration.use_usdb_resolution:
                target_w = min(video_info.width, max_w)
                target_h = min(video_info.height, max_h)
            else:
                target_w, target_h = max_w, max_h

        res_factor = (target_w * target_h) / (1920 * 1080)
        if res_factor <= 0:
            res_factor = 1.0

        # 3. Preset adjustment
        # Presets like 'veryslow' can be 10x slower than 'ultrafast'
        preset_factors = {
            "ultrafast": 3.0,
            "superfast": 2.5,
            "veryfast": 2.0,
            "faster": 1.5,
            "fast": 1.2,
            "medium": 1.0,
            "slow": 0.7,
            "slower": 0.4,
            "veryslow": 0.2,
        }
        codec_cfg = getattr(cfg, cfg.target_codec)
        preset = getattr(codec_cfg, "preset", "medium")
        preset_factor = preset_factors.get(preset, 1.0)

        # Final estimation
        # estimated_time = duration / (base_speed * complexity * preset / resolution)
        # We divide by res_factor because higher resolution = slower = more time
        estimated_time = duration / (base_speed * complexity_factor * preset_factor / res_factor)

        return max(1.0, estimated_time)

    @staticmethod
    def get_free_disk_space(path: Path) -> float:
        """
        Get free disk space in MB.

        Returns: free space in MB
        """
        try:
            # Get the directory of the path if it's a file
            check_path = path.parent if path.is_file() else path
            if not check_path.exists():
                # Fallback to current directory if path doesn't exist yet
                check_path = Path(".")
            
            usage = shutil.disk_usage(check_path)
            return usage.free / (1024 * 1024)
        except Exception:
            return 0.0

    @staticmethod
    def calculate_disk_space_required(
        candidates: list[BatchTranscodeCandidate],
        rollback_enabled: bool,
        backup_original: bool
    ) -> float:
        """
        Calculate total disk space required for batch.

        Accounts for:
        - Estimated output sizes
        - Temporary transcoding files (usually same as output size)
        - Rollback backup space (if enabled and not already backed up)

        Returns: required space in MB
        """
        total_required = 0.0
        
        for candidate in candidates:
            if not candidate.selected:
                continue
                
            # 1. Space for the new output file
            total_required += candidate.estimated_output_size_mb
            
            # 2. Space for temporary file during transcoding (FFmpeg usually writes to a temp file)
            # We assume it needs at least the same amount of space as the output
            total_required += candidate.estimated_output_size_mb
            
            # 3. Space for rollback backup
            # If rollback is enabled, we might need to keep the original file
            # If backup_original is already True, it's already accounted for in the user's workflow,
            # but for the *batch* operation, we still need to ensure we have space for it.
            if rollback_enabled or backup_original:
                total_required += candidate.current_size_mb
                
        return total_required
