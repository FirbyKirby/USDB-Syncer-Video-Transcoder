"""Audio normalization helpers.

Stage 3 implements normalization using FFmpeg built-in filters (no Python deps):
- EBU R128 loudness normalization via `loudnorm` (two-pass)
- ReplayGain tag writing via `replaygain` (optional / secondary)
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from usdb_syncer.utils import LinuxEnvCleaner

if TYPE_CHECKING:
    from usdb_syncer.logger import SongLogger

    from .config import TranscoderConfig


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoudnormTargets:
    """User-facing targets for loudnorm."""

    integrated_lufs: float
    true_peak_dbtp: float
    lra_lu: float


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
) -> LoudnormMeasurements:
    """Run loudnorm pass 1 analysis and return measurements for pass 2."""

    filter_str = (
        "loudnorm="
        f"I={_format_num(targets.integrated_lufs)}:"
        f"TP={_format_num(targets.true_peak_dbtp)}:"
        f"LRA={_format_num(targets.lra_lu)}:"
        "print_format=json"
    )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
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

    with LinuxEnvCleaner() as env:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=env,
        )

    if result.returncode != 0:
        tail = (result.stderr or "").strip()[-1000:]
        raise RuntimeError(f"ffmpeg loudnorm pass 1 failed (code {result.returncode}): {tail}")

    obj = _parse_loudnorm_json(result.stderr or "")
    meas = _extract_measurements(obj)

    slog.info(
        "Loudnorm measurements: "
        f"I={meas.measured_I} LUFS, TP={meas.measured_TP} dBTP, LRA={meas.measured_LRA} LU, "
        f"thresh={meas.measured_thresh} LUFS, offset={meas.offset}"
    )
    return meas


def build_loudnorm_pass2_filter(targets: LoudnormTargets, meas: LoudnormMeasurements) -> str:
    """Build the loudnorm filter string for pass 2."""

    return (
        "loudnorm="
        f"I={_format_num(targets.integrated_lufs)}:"
        f"TP={_format_num(targets.true_peak_dbtp)}:"
        f"LRA={_format_num(targets.lra_lu)}:"
        f"measured_I={_format_num(meas.measured_I)}:"
        f"measured_TP={_format_num(meas.measured_TP)}:"
        f"measured_LRA={_format_num(meas.measured_LRA)}:"
        f"measured_thresh={_format_num(meas.measured_thresh)}:"
        f"offset={_format_num(meas.offset)}"
    )


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
            targets = LoudnormTargets(
                integrated_lufs=float(cfg.audio.audio_normalization_target),
                true_peak_dbtp=float(cfg.audio.audio_normalization_true_peak),
                lra_lu=float(cfg.audio.audio_normalization_lra),
            )

            # Avoid spending the full transcode timeout on analysis; keep bounded.
            analysis_timeout = min(int(cfg.general.timeout_seconds), 300)
            meas = analyze_loudnorm_two_pass(
                input_path=input_path,
                targets=targets,
                timeout_seconds=analysis_timeout,
                slog=slog,
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
