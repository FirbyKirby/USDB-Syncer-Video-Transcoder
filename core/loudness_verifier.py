"""Loudness verification logic for audio normalization.

This module provides functions to verify if audio files are already normalized
correctly against target loudness values with configurable tolerances.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from .audio_normalizer import LoudnormMeasurements, LoudnormTargets, analyze_loudnorm_two_pass
from .audio_analyzer import get_replaygain_tags

if TYPE_CHECKING:
    from usdb_syncer.logger import SongLogger

    from .loudness_cache import LoudnessCache

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationTolerance:
    """Tolerances for loudness verification."""

    i_tolerance: float  # LUFS tolerance for integrated loudness
    tp_tolerance: float  # dB tolerance for true peak (allowable overshoot)
    lra_tolerance: float  # LU tolerance for loudness range


@dataclass(frozen=True)
class VerificationResult:
    """Result of loudness verification."""

    within_tolerance: bool
    reasons: List[str]
    measurements: LoudnormMeasurements
    analyzed_at: datetime


def verify_loudnorm_normalization(
    measurements: LoudnormMeasurements,
    targets: LoudnormTargets,
    tolerances: VerificationTolerance,
) -> VerificationResult:
    """Verify if loudnorm measurements are within tolerance of targets.

    Args:
        measurements: Measured loudness values from analysis
        targets: Target loudness values
        tolerances: Acceptable tolerances for each measurement

    Returns:
        VerificationResult with status and reasons for any out-of-tolerance values
    """
    reasons = []
    within_tolerance = True

    # Check integrated loudness
    i_diff = abs(measurements.measured_I - targets.integrated_lufs)
    if i_diff > tolerances.i_tolerance:
        within_tolerance = False
        reasons.append(
            f"Integrated loudness out of range: measured {measurements.measured_I:.1f} LUFS "
            f"(target {targets.integrated_lufs:.1f} LUFS, difference {i_diff:.1f} LU)"
        )

    # Check true peak (measured TP should be <= target TP + tolerance)
    if targets.true_peak_dbtp is not None:
        tp_limit = targets.true_peak_dbtp + tolerances.tp_tolerance
        if measurements.measured_TP > tp_limit:
            within_tolerance = False
            overshoot = measurements.measured_TP - targets.true_peak_dbtp
            reasons.append(
                f"True peak exceeds ceiling: measured {measurements.measured_TP:.1f} dBTP "
                f"(ceiling {targets.true_peak_dbtp:.1f} dBTP, overshoot {overshoot:.1f} dB)"
            )

    # Check loudness range
    if targets.lra_lu is not None:
        lra_diff = abs(measurements.measured_LRA - targets.lra_lu)
        if lra_diff > tolerances.lra_tolerance:
            within_tolerance = False
            reasons.append(
                f"Loudness range out of range: measured {measurements.measured_LRA:.1f} LU "
                f"(target {targets.lra_lu:.1f} LU, difference {lra_diff:.1f} LU)"
            )

    return VerificationResult(
        within_tolerance=within_tolerance,
        reasons=reasons,
        measurements=measurements,
        analyzed_at=datetime.now(),
    )


def analyze_and_verify(
    input_path: Path,
    targets: LoudnormTargets,
    tolerances: VerificationTolerance,
    timeout_seconds: int,
    slog: "SongLogger",
    cache: Optional["LoudnessCache"] = None,
    duration_seconds: Optional[float] = None,
) -> VerificationResult:
    """Run loudnorm analysis and verify against targets.

    Args:
        input_path: Path to audio file
        targets: Target loudness values
        tolerances: Verification tolerances
        timeout_seconds: Analysis timeout
        slog: Song logger for progress

    Returns:
        VerificationResult from analysis and verification
    """
    try:
        measurements = analyze_loudnorm_two_pass(
            input_path=input_path,
            targets=targets,
            timeout_seconds=timeout_seconds,
            slog=slog,
            cache=cache,
            duration_seconds=duration_seconds,
        )
        return verify_loudnorm_normalization(measurements, targets, tolerances)
    except Exception as e:
        _logger.warning(f"Loudness verification failed for {input_path}: {e}")
        # Return a failed result - this will be handled by callers
        # For now, create a dummy measurements object
        dummy_measurements = LoudnormMeasurements(
            measured_I=float('nan'),
            measured_TP=float('nan'),
            measured_LRA=float('nan'),
            measured_thresh=float('nan'),
            offset=float('nan'),
            raw={},
        )
        return VerificationResult(
            within_tolerance=False,
            reasons=[f"Analysis failed: {type(e).__name__}: {e}"],
            measurements=dummy_measurements,
            analyzed_at=datetime.now(),
        )


def analyze_replaygain(input_path: Path, timeout_seconds: int, slog: "SongLogger") -> None:
    """Stub for ReplayGain analysis - to be implemented later."""
    raise NotImplementedError("ReplayGain verification not yet implemented")