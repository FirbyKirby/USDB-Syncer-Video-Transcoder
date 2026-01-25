"""Core transcoding engine for the Transcoder addon."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from usdb_syncer.utils import LinuxEnvCleaner

from .utils import format_seconds, is_aborted, parse_ffmpeg_progress, time_to_seconds

if TYPE_CHECKING:
    from usdb_syncer import SongId
    from usdb_syncer.logger import SongLogger
    from usdb_syncer.usdb_song import UsdbSong

    from .config import TranscoderConfig
    from .video_analyzer import VideoInfo

_logger = logging.getLogger(__name__)


@dataclass
class TranscodeResult:
    """Result of a transcode operation."""
    success: bool
    output_path: Optional[Path]
    original_backed_up: bool
    backup_path: Optional[Path]
    duration_seconds: float
    error_message: Optional[str]
    aborted: bool = False


def process_audio(
    song: "UsdbSong",
    media_path: Path,
    cfg: "TranscoderConfig",
    slog: "SongLogger",
    progress_callback: Optional[Callable[[float, float, str, float, float], None]] = None,
) -> TranscodeResult:
    """Transcode (or extract+transcode) audio to the configured target audio codec.

    This function is intentionally designed to be safe and consistent with
    [`process_video()`](transcoder.py:41):
    - conservative temp-file output (".transcoding")
    - optional persistent backup behavior
    - optional verification
    - SyncMeta updates to avoid re-download loops

    Inputs
    - Audio-only files (e.g. mp3/m4a/flac/wav)
    - Video containers with audio streams (e.g. mp4/mkv). In this case the audio
      stream is extracted and written as audio-only output.

    Normalization
    - Optional audio normalization via FFmpeg filters (loudnorm two-pass or ReplayGain tagging)
    """

    from .audio_analyzer import analyze_audio
    from .codecs import get_audio_codec_handler
    from .audio_normalizer import maybe_apply_audio_normalization, LoudnormTargets
    from .sync_meta_updater import update_sync_meta_audio
    from .loudness_verifier import analyze_and_verify, verify_loudnorm_normalization
    from .loudness_cache import LoudnessCache, TargetSettings, get_cache_path

    start_time = time.time()

    # Analyze media for audio stream
    slog.info(f"Analyzing audio: {media_path.name}")
    audio_info = analyze_audio(media_path)
    if not audio_info:
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=0,
            error_message="Failed to analyze audio stream (no audio stream or ffprobe error)",
        )

    from .audio_analyzer import format_audio_info, needs_audio_transcoding
    slog.debug(f"Audio analysis: {format_audio_info(audio_info)}")

    # Check if transcoding needed (codec/container/force)
    needs_transcode, reasons = needs_audio_transcoding(audio_info, cfg)

    # Check normalization requirements
    # NOTE: Keep imports here limited to what is always needed. ReplayGain helpers are imported
    # inside the replaygain branch to avoid hard failures when replaygain isn't enabled.
    from .codecs import get_audio_codec_handler

    target_codec = cfg.audio.audio_codec
    handler = get_audio_codec_handler(target_codec)
    codec_matches = audio_info.codec_name.lower() == target_codec.lower()
    container_matches = handler.is_container_compatible(media_path) if handler else False
    format_matches = codec_matches and container_matches

    normalization_needed = False
    if cfg.audio.audio_normalization_enabled:
        if cfg.audio.audio_normalization_method == "loudnorm":
            if format_matches and not getattr(cfg.audio, "force_transcode_audio", False):
                # Check if verification is enabled
                if cfg.verification.enabled:
                    # Perform verification
                    slog.info(f"Verifying audio normalization for {media_path.name}")
                    cache_path = get_cache_path()
                    cache = LoudnessCache(cache_path)

                    # Get targets and tolerances (align with normalization logic)
                    if cfg.audio.audio_normalization_use_usdb_defaults:
                        integrated_lufs = cfg.audio.get_usdb_target_loudness()
                        true_peak_dbtp = None
                        lra_lu = None
                    else:
                        integrated_lufs = float(cfg.audio.audio_normalization_target)
                        true_peak_dbtp = float(cfg.audio.audio_normalization_true_peak)
                        lra_lu = float(cfg.audio.audio_normalization_lra)
                    targets = LoudnormTargets(
                        integrated_lufs=integrated_lufs,
                        true_peak_dbtp=true_peak_dbtp,
                        lra_lu=lra_lu,
                    )
                    slog.info(f"Verification loudnorm targets: I={integrated_lufs} LUFS, TP={true_peak_dbtp or 'default'} dBTP, LRA={lra_lu or 'default'} LU")
                    tolerances = cfg.verification.get_active_tolerances()

                    # Create target settings for cache
                    target_settings = TargetSettings(
                        normalization_method="loudnorm",
                        target_i=targets.integrated_lufs,
                        target_tp=targets.true_peak_dbtp,
                        target_lra=targets.lra_lu,
                        tolerance_preset=cfg.verification.tolerance_preset,
                    )

                    # Check cache first
                    cached_entry = cache.get(media_path, target_settings)
                    if cached_entry:
                        slog.info(f"Using cached verification result from {datetime.fromtimestamp(cached_entry.analyzed_at)}")
                        verification_result = verify_loudnorm_normalization(
                            cached_entry.measurements, targets, tolerances
                        )
                    else:
                        # Run analysis and verify
                        verification_result = analyze_and_verify(
                            input_path=media_path,
                            targets=targets,
                            tolerances=tolerances,
                            timeout_seconds=min(int(cfg.general.timeout_seconds), 300),
                            slog=slog,
                            cache=cache,
                            duration_seconds=audio_info.duration_seconds,
                        )
                        # Cache the result if analysis succeeded (even if out of tolerance, measurements are still valid)
                        if "Analysis failed" not in str(verification_result.reasons):
                            cache.put(media_path, target_settings, verification_result.measurements, song_id=song.song_id)

                    # Log verification outcome
                    if verification_result.within_tolerance:
                        slog.info("Audio within tolerance - skipping transcode")
                        return TranscodeResult(
                            success=True,
                            output_path=media_path,
                            original_backed_up=False,
                            backup_path=None,
                            duration_seconds=time.time() - start_time,
                            error_message=None,
                        )
                    else:
                        slog.info(f"Audio out of tolerance: {', '.join(verification_result.reasons)}")
                        normalization_needed = True
                        reasons.append("normalization requested (loudnorm - out of tolerance)")
                else:
                    # Verification disabled, assume normalized like before
                    slog.info(f"Audio format matches target and R128 normalization enabled - assuming file is already normalized, skipping transcode. To force re-normalization, enable 'Force Audio Transcode' in settings.")
                    return TranscodeResult(
                        success=True,
                        output_path=media_path,
                        original_backed_up=False,
                        backup_path=None,
                        duration_seconds=time.time() - start_time,
                        error_message=None,
                    )
            else:
                normalization_needed = True
                reasons.append("normalization requested (loudnorm)")
        elif cfg.audio.audio_normalization_method == "replaygain":
            # Import ReplayGain helper only when ReplayGain normalization is selected.
            # This avoids ImportError in other normalization modes if ReplayGain APIs change.
            from .audio_analyzer import get_replaygain_tags

            if format_matches and not getattr(cfg.audio, "force_transcode_audio", False):
                slog.debug(f"Checking ReplayGain tags for {media_path.name}")
                if get_replaygain_tags(media_path):
                    slog.info(f"Audio format matches target and ReplayGain tags detected - skipping transcode. To force re-normalization, enable 'Force Audio Transcode' in settings.")
                    return TranscodeResult(
                        success=True,
                        output_path=media_path,
                        original_backed_up=False,
                        backup_path=None,
                        duration_seconds=time.time() - start_time,
                        error_message=None,
                    )
                else:
                    normalization_needed = True
                    reasons.append("ReplayGain tags missing")
            else:
                normalization_needed = True
                reasons.append("normalization requested (replaygain)")

    if not needs_transcode and not normalization_needed:
        slog.info(f"Audio already in {cfg.audio.audio_codec} format - skipping transcode")
        return TranscodeResult(
            success=True,
            output_path=media_path,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=time.time() - start_time,
            error_message=None,
        )

    slog.info(f"Transcode needed: {', '.join(reasons)}")

    # Check disk space (same guard as video)
    if not _check_disk_space(media_path, cfg.general.min_free_space_mb):
        slog.error(f"Insufficient disk space for audio transcoding. Required: {cfg.general.min_free_space_mb} MB")
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=0,
            error_message="Insufficient disk space for transcoding",
        )

    # Select audio codec handler
    audio_codec = cfg.audio.audio_codec
    handler = get_audio_codec_handler(audio_codec)
    if not handler:
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=0,
            error_message=f"No handler for audio codec: {audio_codec}",
        )

    # Determine output extension / container
    new_ext = f".{handler.capabilities().container}"
    temp_output_path = media_path.with_suffix(f".transcoding{new_ext}")

    # Decide stream copy vs re-encode
    container_matches = handler.is_container_compatible(media_path)
    codec_matches = (audio_info.codec_name.lower() == audio_codec.lower())
    force_audio = bool(getattr(cfg.audio, "force_transcode_audio", False))
    stream_copy = (
        not force_audio
        and not normalization_needed
        and container_matches
        and codec_matches
    )

    # Build command
    try:
        cmd = handler.build_encode_command(
            media_path,
            temp_output_path,
            cfg,
            stream_copy=stream_copy,
        )
    except ValueError as e:
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=0,
            error_message=str(e),
        )

    # Apply normalization filters
    # IMPORTANT: must occur before encoding; stream-copy path skips normalization.
    cache = None
    if normalization_needed:
        # Check if we have precomputed measurements from verification
        precomputed_meas = None
        if cfg.verification.enabled and cfg.audio.audio_normalization_method == "loudnorm":
            # Try to get from cache or from recent verification
            # For now, we'll check cache again, but ideally we'd pass it from above
            # Since verification already ran, we can reuse the measurements
            # But to keep it simple, let's check cache again
            cache_path = get_cache_path()
            cache = LoudnessCache(cache_path)
            # Align with normalization logic
            if cfg.audio.audio_normalization_use_usdb_defaults:
                integrated_lufs = cfg.audio.get_usdb_target_loudness()
                true_peak_dbtp = None
                lra_lu = None
            else:
                integrated_lufs = float(cfg.audio.audio_normalization_target)
                true_peak_dbtp = float(cfg.audio.audio_normalization_true_peak)
                lra_lu = float(cfg.audio.audio_normalization_lra)
            targets = LoudnormTargets(
                integrated_lufs=integrated_lufs,
                true_peak_dbtp=true_peak_dbtp,
                lra_lu=lra_lu,
            )
            target_settings = TargetSettings(
                normalization_method="loudnorm",
                target_i=targets.integrated_lufs,
                target_tp=true_peak_dbtp,
                target_lra=lra_lu,
                tolerance_preset=cfg.verification.tolerance_preset,
            )
            cached_entry = cache.get(media_path, target_settings)
            if cached_entry:
                precomputed_meas = cached_entry.measurements

        cmd = maybe_apply_audio_normalization(
            base_cmd=cmd,
            input_path=media_path,
            cfg=cfg,
            slog=slog,
            stream_copy=stream_copy,
            precomputed_meas=precomputed_meas,
            cache=cache,
            duration_seconds=audio_info.duration_seconds,
        )
    if cache:
        cache.close()

    slog.debug(f"FFMPEG command (audio): {' '.join(cmd)}")

    # Execute transcode
    slog.info(
        f"Transcoding audio ({audio_info.codec_name}, "
        f"{audio_info.bitrate_kbps or '?'}kbps, "
        f"{audio_info.duration_seconds:.1f}s) to {audio_codec}..."
    )
    try:
        success, aborted = _execute_ffmpeg(
            cmd,
            cfg.general.timeout_seconds,
            slog,
            song.song_id,
            audio_info.duration_seconds,
            1.0,
            progress_callback,
        )
    except Exception as e:
        _safe_unlink(temp_output_path)
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=time.time() - start_time,
            error_message=str(e),
            aborted=False,
        )

    if aborted:
        _safe_unlink(temp_output_path)
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=time.time() - start_time,
            error_message="Transcode aborted by user",
            aborted=True,
        )

    if not success:
        _safe_unlink(temp_output_path)
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=time.time() - start_time,
            error_message="FFMPEG encoding failed",
            aborted=False,
        )

    # Verify output if configured
    if cfg.general.verify_output:
        out_info = analyze_audio(temp_output_path)
        if not out_info or out_info.duration_seconds <= 0:
            _safe_unlink(temp_output_path)
            return TranscodeResult(
                success=False,
                output_path=None,
                original_backed_up=False,
                backup_path=None,
                duration_seconds=time.time() - start_time,
                error_message="Transcoded audio output verification failed",
            )

    # Determine final output location
    final_path = media_path.with_suffix(new_ext)

    # Backup original if configured
    backup_path = None
    if cfg.general.backup_original:
        backup_path = media_path.with_name(f"{media_path.stem}{cfg.general.backup_suffix}{media_path.suffix}")
        try:
            shutil.move(str(media_path), str(backup_path))
        except OSError as e:
            slog.warning(f"Could not backup original audio: {e}")
            backup_path = None

    # Put final output in place
    Path(str(temp_output_path)).replace(str(final_path))

    # If we didn't back up and output differs, remove old source file
    if backup_path is None and final_path != media_path and media_path.exists():
        try:
            media_path.unlink()
        except OSError as e:
            slog.warning(f"Could not remove original after audio transcode: {e}")

    # Update SyncMeta (audio)
    sync_ok = update_sync_meta_audio(
        song=song,
        original_audio_path=backup_path or media_path,
        transcoded_audio_path=final_path,
        codec=audio_codec,
        slog=slog,
        backup_source=False,  # Already handled backup above
        backup_suffix=cfg.general.backup_suffix,
    )
    if not sync_ok:
        slog.warning("SyncMeta audio update failed; this may cause re-download loops.")

    duration = time.time() - start_time
    speed = audio_info.duration_seconds / duration if duration > 0 else 0
    slog.info(f"Audio transcode completed in {duration:.1f}s ({speed:.1f}x realtime): {final_path.name}")

    return TranscodeResult(
        success=True,
        output_path=final_path,
        original_backed_up=backup_path is not None,
        backup_path=backup_path,
        duration_seconds=duration,
        error_message=None,
        aborted=False,
    )


def process_video(
    song: UsdbSong,
    video_path: Path,
    cfg: TranscoderConfig,
    slog: SongLogger,
    progress_callback: Optional[Callable[[float, float, str, float, float], None]] = None
) -> TranscodeResult:
    """Main entry point for video processing."""
    from .codecs import get_codec_handler
    from .hwaccel import get_best_accelerator, get_best_decoder_accelerator
    from .sync_meta_updater import update_sync_meta_video
    from .video_analyzer import analyze_video, format_video_info, needs_transcoding

    start_time = time.time()

    # Analyze video
    slog.info(f"Analyzing video: {video_path.name}")
    video_info = analyze_video(video_path)

    if not video_info:
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=0,
            error_message="Failed to analyze video file"
        )

    slog.debug(f"Video analysis: {format_video_info(video_info)}")

    # Compute effective limits (optionally from USDB Syncer settings)
    cfg = _apply_limits(cfg, video_info)

    # Check if transcoding needed (unless force_transcode_video is enabled)
    slog.debug(f"Checking if transcoding is needed for target codec: {cfg.target_codec}")
    if not cfg.general.force_transcode_video and not needs_transcoding(video_info, cfg):
        slog.info(f"Video already in {cfg.target_codec} format - skipping transcode")
        return TranscodeResult(
            success=True,
            output_path=video_path,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=time.time() - start_time,
            error_message=None
        )

    if cfg.general.force_transcode_video and not needs_transcoding(video_info, cfg):
        slog.info(
            f"Video already in {cfg.target_codec} format, but force_transcode_video is enabled - proceeding"
        )

    # Check disk space
    if not _check_disk_space(video_path, cfg.general.min_free_space_mb):
        slog.error(f"Insufficient disk space for transcoding. Required: {cfg.general.min_free_space_mb} MB")
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=0,
            error_message="Insufficient disk space for transcoding"
        )

    # Get codec handler
    handler = get_codec_handler(cfg.target_codec)
    if not handler:
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=0,
            error_message=f"No handler for codec: {cfg.target_codec}"
        )

    # Determine whether we will use hardware *encoding*
    accel = None
    hw_encode_enabled = False
    hw_decode_enabled = False

    if cfg.general.hardware_encoding:
        accel = get_best_accelerator(cfg.target_codec)
        if accel:
            hw_encode_enabled = True
            slog.info(f"Hardware encoding enabled using: {accel.capabilities().display_name}")
            slog.debug(f"Selected accelerator: {accel.capabilities().name} for codec: {cfg.target_codec}")
        else:
            slog.warning(
                f"Hardware encoding requested for {cfg.target_codec} but no suitable accelerator found. Falling back to software."
            )
    else:
        slog.info(f"Hardware encoding disabled by user; using software encoder for {cfg.target_codec}")

    # Determine whether we will use hardware *decoding*
    if cfg.general.hardware_decode:
        # If we don't have an accelerator yet (because encoding is software), try to find one for decoding
        if not accel:
            accel = get_best_decoder_accelerator(video_info)

        # Avoid forcing a hardware decoder when we are going to use filters that are not
        # guaranteed to be hardware-surface compatible.
        if hw_encode_enabled and (cfg.general.max_resolution or cfg.general.max_fps):
            slog.debug(
                "Disabling hardware decode for this run (filters requested); using hardware encode only."
            )
            hw_decode_enabled = False
        elif accel and accel.get_decoder(video_info):
            hw_decode_enabled = True
            slog.info(f"Hardware decoding enabled for {video_info.codec_name} using {accel.capabilities().display_name}")
        else:
            hw_decode_enabled = False
            slog.info(f"No hardware decoder available for {video_info.codec_name}")
    else:
        hw_decode_enabled = False
        slog.info("Hardware decoding disabled by user")

    # Determine output path and container
    # NOTE: Use config container to honor user settings; fall back to handler default.
    codec_cfg = getattr(cfg, cfg.target_codec)
    caps = handler.capabilities()
    container = getattr(codec_cfg, "container", None) or caps.container
    new_ext = f".{container}"
    temp_output_path = video_path.with_suffix(f".transcoding{new_ext}")

    # Build command
    cmd = handler.build_encode_command(
        video_path,
        temp_output_path,
        video_info,
        cfg,
        accel,
        hw_encode_enabled=hw_encode_enabled,
        hw_decode_enabled=hw_decode_enabled,
    )
    slog.debug(f"FFMPEG command: {' '.join(cmd)}")

    # Execute transcode
    slog.info(
        f"Transcoding video ({video_info.codec_name}, "
        f"{video_info.width}x{video_info.height}, "
        f"{format_seconds(video_info.duration_seconds)}) to {cfg.target_codec}..."
    )
    try:
        success, aborted = _execute_ffmpeg(
            cmd, cfg.general.timeout_seconds, slog, song.song_id, video_info.duration_seconds, video_info.frame_rate, progress_callback
        )
    except Exception as e:
        # Cleanup partial output
        _safe_unlink(temp_output_path)
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=time.time() - start_time,
            error_message=str(e),
            aborted=False
        )

    if aborted:
        _safe_unlink(temp_output_path)
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=time.time() - start_time,
            error_message="Transcode aborted by user",
            aborted=True
        )

    if not success:
        _safe_unlink(temp_output_path)
        return TranscodeResult(
            success=False,
            output_path=None,
            original_backed_up=False,
            backup_path=None,
            duration_seconds=time.time() - start_time,
            error_message="FFMPEG encoding failed",
            aborted=False
        )

    # Verify output if configured
    if cfg.general.verify_output:
        output_info = analyze_video(temp_output_path)
        if not output_info:
            _safe_unlink(temp_output_path)
            return TranscodeResult(
                success=False,
                output_path=None,
                original_backed_up=False,
                backup_path=None,
                duration_seconds=time.time() - start_time,
                error_message="Transcoded output verification failed"
            )

    # Determine final output location
    final_path = video_path.with_suffix(new_ext)

    # Backup original if configured
    backup_path = None
    if cfg.general.backup_original:
        backup_path = video_path.with_name(
            f"{video_path.stem}{cfg.general.backup_suffix}{video_path.suffix}"
        )
        try:
            shutil.move(str(video_path), str(backup_path))
        except OSError as e:
            slog.warning(f"Could not backup original: {e}")
            backup_path = None

    # Atomically replace/put final output in place.
    # os.replace works cross-platform (incl. Windows) and replaces if destination exists.
    Path(str(temp_output_path)).replace(str(final_path))

    # If we didn't back up and the output path differs, remove the old source file *after*
    # the new output is successfully in place.
    if backup_path is None and final_path != video_path and video_path.exists():
        try:
            video_path.unlink()
        except OSError as e:
            slog.warning(f"Could not remove original after transcode: {e}")

    # Update SyncMeta
    sync_ok = update_sync_meta_video(
        song=song,
        original_video_path=backup_path or video_path,
        transcoded_video_path=final_path,
        codec=cfg.target_codec,
        profile=getattr(getattr(cfg, cfg.target_codec), "profile", "default"),
        slog=slog,
        backup_source=False,  # Already handled backup above
        backup_suffix=cfg.general.backup_suffix,
    )
    if not sync_ok:
        slog.warning("SyncMeta update failed; this may cause re-download loops.")

    duration = time.time() - start_time
    speed = video_info.duration_seconds / duration if duration > 0 else 0
    slog.info(
        f"FFMPEG transcode completed in {duration:.1f}s ({speed:.1f}x realtime): {final_path.name}"
    )

    return TranscodeResult(
        success=True,
        output_path=final_path,
        original_backed_up=backup_path is not None,
        backup_path=backup_path,
        duration_seconds=duration,
        error_message=None,
        aborted=False
    )


def _execute_ffmpeg(
    cmd: list[str],
    timeout: int,
    slog: "SongLogger",
    song_id: "SongId",
    duration: float,
    frame_rate: float,
    progress_callback: Optional[Callable[[float, float, str, float, float], None]] = None
) -> tuple[bool, bool]:
    """Execute FFMPEG command and handle output.

    Returns: (success, aborted)
    """
    start_time = time.time()
    last_logged_percent = -10.0

    try:
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW

        with LinuxEnvCleaner() as env, subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=1,
            universal_newlines=True,
            creationflags=flags,
        ) as process:
            if not process.stderr:
                return False, False

            while True:
                # Check for abort
                if is_aborted(song_id):
                    slog.warning("Transcode aborted by user")
                    if os.name == "nt":
                        # Forcefully kill the process tree on Windows to release file locks
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    else:
                        process.terminate()
    
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    
                    if process.stderr:
                        process.stderr.close()
                    return False, True

                # Read stderr line by line (non-blocking-ish with readline and poll)
                line = process.stderr.readline()
                if not line and process.poll() is not None:
                    break

                if not line:
                    continue

                # Parse progress
                if "time=" in line:
                    progress = parse_ffmpeg_progress(line)
                    current_time_str = progress.get("time")
                    if current_time_str:
                        current_seconds = time_to_seconds(current_time_str)
                        percent = (current_seconds / duration * 100) if duration > 0 else 0

                        if int(percent // 10) > int(last_logged_percent // 10):
                            fps = progress.get("fps", "?")
                            speed = progress.get("speed", "?")
                            slog.info(
                                f"Transcoding: {percent:.0f}% complete "
                                f"({current_time_str} / {format_seconds(duration)}) "
                                f"[fps={fps}, speed={speed}]"
                            )
                            last_logged_percent = percent

                        if progress_callback:
                            fps_val = 0.0
                            try:
                                fps_val = float(progress.get("fps", "0"))
                            except ValueError:
                                pass
                            progress_callback(
                                percent,
                                fps_val,
                                progress.get("speed", "?"),
                                time.time() - start_time,
                                (duration - current_seconds) / (fps_val / frame_rate) if fps_val > 0 and frame_rate > 0 else 0
                            )

                # Check timeout
                if time.time() - start_time > timeout:
                    slog.error(f"FFMPEG timeout after {timeout}s")
                    if os.name == "nt":
                        # Forcefully kill the process tree on Windows to release file locks
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    else:
                        process.terminate()

                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

                    if process.stderr:
                        process.stderr.close()
                    return False, False

        process.wait()
        duration_total = time.time() - start_time
        if process.returncode != 0:
            error_desc = _get_exit_code_description(process.returncode)
            slog.error(
                f"FFMPEG failed after {duration_total:.1f}s with code {process.returncode} "
                f"({error_desc})"
            )
            return False, False

        return True, False

    except Exception as e:
        slog.error(f"FFMPEG execution error: {e}")
        return False, False


def _get_exit_code_description(code: int) -> str:
    """Return a human-readable description for common exit codes."""
    # Windows-specific crash codes
    windows_codes = {
        0xC0000005: "Access Violation / Segfault",
        0xC0000374: "Heap Corruption",
        0xC0000135: "DLL Not Found",
        0xC0000142: "DLL Initialization Failed",
        0xC00000FD: "Stack Overflow",
        0xC0000409: "Stack Buffer Overrun",
    }
    
    # Convert to unsigned 32-bit for comparison if it's negative (signed)
    unsigned_code = code & 0xFFFFFFFF
    
    if unsigned_code in windows_codes:
        return windows_codes[unsigned_code]
    
    if code < 0:
        return f"Signal {-code}"
        
    return "Unknown error"


def _safe_unlink(path: Path, retries: int = 15, delay: float = 1.5) -> None:
    """Attempt to delete a file with retries to handle Windows locking issues."""
    if not path.exists():
        return

    # Try to rename it first, which can sometimes break locks or at least move it out of the way
    work_path = path
    try:
        temp_path = path.with_name(f"{path.name}.{int(time.time())}.deleted")
        path.rename(temp_path)
        work_path = temp_path
    except OSError:
        # Rename failed, stick with original path for deletion attempts
        pass

    for i in range(retries):
        try:
            work_path.unlink()
            _logger.debug(f"Deleted temporary file: {work_path}")
            return
        except OSError as e:
            if i < retries - 1:
                _logger.debug(f"Retrying deletion of {work_path} in {delay}s (attempt {i+1}/{retries}): {e}")
                time.sleep(delay)
            else:
                _logger.warning(f"Could not delete temporary file after {retries} attempts: {work_path}")


def _check_disk_space(video_path: Path, min_mb: int) -> bool:
    """Check if sufficient disk space is available."""
    try:
        usage = shutil.disk_usage(video_path.parent)
        free_mb = usage.free // (1024 * 1024)
        _logger.debug(f"Disk space check: {free_mb} MB free, {min_mb} MB required")
        return free_mb >= min_mb
    except OSError as e:
        _logger.warning(f"Could not check disk space: {e}")
        return True  # Assume OK if we can't check


def _apply_limits(cfg: "TranscoderConfig", video_info: "VideoInfo") -> "TranscoderConfig":
    """Return a config with max_resolution/max_fps resolved as limits.

    Uses the lesser of (source value, max limit) to avoid upscaling.
    """
    try:
        from usdb_syncer import settings
    except Exception:
        # If settings can't be imported (should be rare), use addon config as-is.
        settings = None

    max_res = cfg.general.max_resolution
    max_fps = cfg.general.max_fps

    # 1. Resolve USDB limits if enabled
    if settings:
        if cfg.usdb_integration.use_usdb_resolution:
            res = settings.get_video_resolution()
            max_res = (res.width(), res.height())

        if cfg.usdb_integration.use_usdb_fps:
            fps = settings.get_video_fps()
            max_fps = int(fps.value)

    # 2. Apply "lesser of" logic to avoid upscaling (ONLY for USDB integration)
    # Resolution limit
    if max_res and cfg.usdb_integration.use_usdb_resolution:
        limit_w, limit_h = max_res
        # Only apply limit if source exceeds it in either dimension
        if video_info.width > limit_w or video_info.height > limit_h:
            # Keep the limit as is (ffmpeg scale filter handles aspect ratio)
            pass
        else:
            # Source is within limits, don't force a resolution change
            max_res = None

    # FPS limit
    if max_fps and cfg.usdb_integration.use_usdb_fps:
        if video_info.frame_rate > max_fps + 0.1:
            # Source exceeds limit, keep max_fps
            pass
        else:
            # Source is within limits, don't force an FPS change
            max_fps = None

    if max_res != cfg.general.max_resolution or max_fps != cfg.general.max_fps:
        return replace(cfg, general=replace(cfg.general, max_resolution=max_res, max_fps=max_fps))
    return cfg
