"""Audio normalization helpers.

Audio normalization using FFmpeg built-in filters:
- EBU R128 loudness normalization via `loudnorm` (two-pass)
- ReplayGain tag writing via `replaygain` (optional / secondary)
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from usdb_syncer.utils import LinuxEnvCleaner

from .utils import format_seconds, parse_ffmpeg_progress, time_to_seconds

if TYPE_CHECKING:
    from usdb_syncer.logger import SongLogger

    from .config import TranscoderConfig
    from .loudness_cache import LoudnessCache


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoudnormTargets:
    """User-facing targets for loudnorm."""

    integrated_lufs: float
    true_peak_dbtp: Optional[float]
    lra_lu: Optional[float]


@dataclass(frozen=True)
class LoudnormMeasurements:
    """Measurements returned by loudnorm pass 1."""

    measured_I: float
    measured_TP: float
    measured_LRA: float
    measured_thresh: float
    offset: float
    raw: dict[str, Any]


def _is_finite_number(value: object) -> bool:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(v)


def _format_num(value: float) -> str:
    """Format floats for ffmpeg filter args.

    Keep a few decimals for stability while avoiding huge strings.
    """

    if not math.isfinite(value):
        # Should never be passed to ffmpeg; guard anyway.
        return "0"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _parse_loudnorm_json(stderr_text: str) -> dict[str, Any]:
    """Extract the loudnorm JSON object from ffmpeg stderr.

    `loudnorm=...:print_format=json` prints a JSON object (usually across multiple
    lines) to stderr.

    We parse the *last* JSON object that contains the expected keys.
    """

    # Grab all "{ ... }" blocks (multiline) and attempt to parse those that look
    # like loudnorm output.
    candidates = re.findall(r"\{[\s\S]*?\}", stderr_text)
    last_good: Optional[dict[str, Any]] = None

    for blob in candidates:
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue

        # loudnorm output includes at least these keys.
        if not isinstance(obj, dict):
            continue
        if "input_i" in obj and "input_tp" in obj and "input_lra" in obj and "input_thresh" in obj:
            last_good = obj

    if last_good is None:
        raise ValueError("Could not locate loudnorm JSON output in ffmpeg stderr")

    return last_good


def _extract_measurements(obj: dict[str, Any]) -> LoudnormMeasurements:
    """Map ffmpeg loudnorm JSON fields into pass-2 parameters."""

    measured_I = obj.get("input_i")
    measured_TP = obj.get("input_tp")
    measured_LRA = obj.get("input_lra")
    measured_thresh = obj.get("input_thresh")
    offset = obj.get("target_offset")

    # Validate values. ffmpeg reports these as strings sometimes.
    fields = {
        "measured_I": measured_I,
        "measured_TP": measured_TP,
        "measured_LRA": measured_LRA,
        "measured_thresh": measured_thresh,
        "offset": offset,
    }
    bad = [name for name, val in fields.items() if not _is_finite_number(val)]
    if bad:
        raise ValueError(f"Invalid loudnorm measurement values: {', '.join(bad)}")

    return LoudnormMeasurements(
        measured_I=float(measured_I),
        measured_TP=float(measured_TP),
        measured_LRA=float(measured_LRA),
        measured_thresh=float(measured_thresh),
        offset=float(offset),
        raw=obj,
    )


def analyze_loudnorm_two_pass(
    *,
    input_path: Path,
    targets: LoudnormTargets,
    timeout_seconds: int,
    slog: "SongLogger",
    cache: Optional["LoudnessCache"] = None,
    duration_seconds: Optional[float] = None,
) -> LoudnormMeasurements:
    """Run loudnorm pass 1 analysis and return measurements for pass 2."""

    filter_str = f"loudnorm=I={_format_num(targets.integrated_lufs)}:"
    if targets.true_peak_dbtp is not None:
        filter_str += f"TP={_format_num(targets.true_peak_dbtp)}:"
    if targets.lra_lu is not None:
        filter_str += f"LRA={_format_num(targets.lra_lu)}:"
    filter_str += "print_format=json"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        # Enable stats so we can parse `time=` + `speed=` progress, similar to how
        # long-running transcodes report progress.
        "-stats",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:a:0?",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        filter_str,
        "-f",
        "null",
        "-",
    ]

    slog.info(
        "Running loudnorm analysis (pass 1): "
        f"target I={targets.integrated_lufs} LUFS, TP={targets.true_peak_dbtp} dBTP, LRA={targets.lra_lu} LU"
    )
    slog.debug(f"FFMPEG command (loudnorm pass 1): {' '.join(cmd)}")

    start_time = time.time()
    stderr_lines = []

    try:
        with LinuxEnvCleaner() as env:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                bufsize=1,
                universal_newlines=True,
                # Keep behavior consistent with main transcoding on Windows.
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )

            if not process.stderr:
                raise RuntimeError("Failed to open stderr pipe for ffmpeg process")

            last_logged_percent = -10.0
            last_progress_log_time = 0.0

            while True:
                line = process.stderr.readline()
                if not line and process.poll() is not None:
                    break

                if not line:
                    continue

                stderr_lines.append(line)

                # Show progress (style/frequency aligned with `_execute_ffmpeg()` in
                # [`transcoder._execute_ffmpeg()`](transcoder.py:691)).
                #
                # For pass-1 loudnorm, ffmpeg can run *much faster* than realtime,
                # so using wall-clock elapsed time as a proxy will severely
                # under-report progress. Instead, parse ffmpeg's own `time=` and
                # `speed=` fields from stats output.
                if duration_seconds and duration_seconds > 30 and "time=" in line:
                    progress = parse_ffmpeg_progress(line)
                    current_time_str = progress.get("time")
                    if current_time_str:
                        current_seconds = time_to_seconds(current_time_str)
                        percent = (current_seconds / duration_seconds * 100) if duration_seconds > 0 else 0.0

                        now = time.time()
                        # Log on 10% boundaries like transcoding, but also ensure
                        # we emit something periodically so the log never appears
                        # hung on very fast/slow devices.
                        should_log_bucket = int(percent // 10) > int(last_logged_percent // 10)
                        # Transcode progress tends to emit in ~6s intervals in practice.
                        should_log_periodic = (now - last_progress_log_time) >= 6.0 and percent > max(last_logged_percent, 0.0)

                        if should_log_bucket or should_log_periodic:
                            speed = progress.get("speed", "?")
                            slog.info(
                                f"Loudnorm analysis: {percent:.0f}% complete "
                                f"({current_time_str} / {format_seconds(duration_seconds)}) "
                                f"[speed={speed}]"
                            )
                            last_logged_percent = max(last_logged_percent, percent)
                            last_progress_log_time = now

                # Check timeout
                if time.time() - start_time > timeout_seconds:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise subprocess.TimeoutExpired(cmd, timeout_seconds)

            process.wait()
            wall_seconds = time.time() - start_time

            if process.returncode != 0:
                stderr_text = "".join(stderr_lines)
                tail = stderr_text.strip()[-1000:]
                raise RuntimeError(f"ffmpeg loudnorm pass 1 failed (code {process.returncode}): {tail}")

            # Always log a completion timing line (even when the analysis is fast)
            # so users can tell the process completed quickly instead of hanging.
            if duration_seconds and duration_seconds > 0 and wall_seconds > 0:
                speed = duration_seconds / wall_seconds
                slog.info(
                    f"Loudnorm analysis completed in {wall_seconds:.1f}s "
                    f"({speed:.1f}x realtime): {input_path.name}"
                )

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg loudnorm pass 1 timeout after {timeout_seconds}s")

    stderr_text = "".join(stderr_lines)
    obj = _parse_loudnorm_json(stderr_text)
    meas = _extract_measurements(obj)

    slog.info(
        "Loudnorm measurements: "
        f"I={meas.measured_I} LUFS, TP={meas.measured_TP} dBTP, LRA={meas.measured_LRA} LU, "
        f"thresh={meas.measured_thresh} LUFS, offset={meas.offset}"
    )

    # Record analysis performance if cache is available
    if cache:
        duration = obj.get("duration")
        if duration and isinstance(duration, (int, float)) and duration > 5:
            cache.record_analysis_performance(duration, wall_seconds)

    return meas


def build_loudnorm_pass2_filter(targets: LoudnormTargets, meas: LoudnormMeasurements) -> str:
    """Build the loudnorm filter string for pass 2."""

    filter_str = f"loudnorm=I={_format_num(targets.integrated_lufs)}:"
    if targets.true_peak_dbtp is not None:
        filter_str += f"TP={_format_num(targets.true_peak_dbtp)}:"
    if targets.lra_lu is not None:
        filter_str += f"LRA={_format_num(targets.lra_lu)}:"
    filter_str += (
        f"measured_I={_format_num(meas.measured_I)}:"
        f"measured_TP={_format_num(meas.measured_TP)}:"
        f"measured_LRA={_format_num(meas.measured_LRA)}:"
        f"measured_thresh={_format_num(meas.measured_thresh)}:"
        f"offset={_format_num(meas.offset)}"
    )
    return filter_str


def build_replaygain_filter() -> str:
    """Build a ReplayGain tagging filter.

    Note: This writes tags on output for formats/containers that support them.
    """

    return "replaygain"


def inject_audio_filter(cmd: list[str], filter_str: str) -> list[str]:
    """Inject `-af <filter_str>` into a single-output ffmpeg command.

    Assumes the last argument is the output path.
    """

    if len(cmd) < 2:
        return cmd

    # Insert immediately before output path.
    out_idx = len(cmd) - 1
    return cmd[:out_idx] + ["-af", filter_str] + cmd[out_idx:]


def maybe_apply_audio_normalization(
    *,
    base_cmd: list[str],
    input_path: Path,
    cfg: "TranscoderConfig",
    slog: "SongLogger",
    stream_copy: bool,
    precomputed_meas: Optional[LoudnormMeasurements] = None,
    cache: Optional["LoudnessCache"] = None,
    duration_seconds: Optional[float] = None,
) -> list[str]:
    """Return an ffmpeg command with normalization filters injected when enabled.

    If normalization fails for any reason, logs and returns the original command.
    """

    if not cfg.audio.audio_normalization_enabled:
        return base_cmd

    if stream_copy:
        # Stream copy cannot be combined with filters.
        slog.debug("Audio normalization requested but stream_copy is enabled; skipping normalization")
        return base_cmd

    method = cfg.audio.audio_normalization_method

    try:
        if method == "loudnorm":
            # Use USDB Syncer defaults if enabled
            if cfg.audio.audio_normalization_use_usdb_defaults:
                integrated_lufs = cfg.audio.get_usdb_target_loudness()
                true_peak_dbtp = None
                lra_lu = None
                slog.info(f"Using USDB Syncer loudnorm defaults: I={integrated_lufs} LUFS (TP/LRA omitted to use ffmpeg defaults)")
            else:
                integrated_lufs = float(cfg.audio.audio_normalization_target)
                true_peak_dbtp = float(cfg.audio.audio_normalization_true_peak)
                lra_lu = float(cfg.audio.audio_normalization_lra)
                slog.info(f"Using custom loudnorm targets: I={integrated_lufs} LUFS, TP={true_peak_dbtp} dBTP, LRA={lra_lu} LU")

            targets = LoudnormTargets(
                integrated_lufs=integrated_lufs,
                true_peak_dbtp=true_peak_dbtp,
                lra_lu=lra_lu,
            )

            if precomputed_meas is not None:
                # Use precomputed measurements from verification
                meas = precomputed_meas
                slog.info("Using precomputed loudnorm measurements from verification")
            else:
                # Avoid spending the full transcode timeout on analysis; keep bounded.
                analysis_timeout = min(int(cfg.general.timeout_seconds), 300)
                meas = analyze_loudnorm_two_pass(
                    input_path=input_path,
                    targets=targets,
                    timeout_seconds=analysis_timeout,
                    slog=slog,
                    cache=cache,
                    duration_seconds=duration_seconds,
                )
            pass2_filter = build_loudnorm_pass2_filter(targets, meas)
            slog.info("Applying loudnorm normalization (pass 2)")
            return inject_audio_filter(base_cmd, pass2_filter)

        if method == "replaygain":
            # AAC/M4A tag writing support varies; allow attempt but warn.
            if input_path.suffix.lower() in (".m4a", ".mp4", ".aac"):
                slog.warning("ReplayGain tagging for AAC/M4A may not be supported by all players")
            slog.info("Applying ReplayGain tagging")
            return inject_audio_filter(base_cmd, build_replaygain_filter())

        slog.warning(f"Unknown audio normalization method '{method}'; skipping normalization")
        return base_cmd

    except Exception as e:  # noqa: BLE001
        slog.warning(f"Audio normalization failed; continuing without normalization: {type(e).__name__}: {e}")
        _logger.debug(None, exc_info=True)
        return base_cmd
