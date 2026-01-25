"""Worker thread for loudness analysis phase in batch wizard.

This worker analyzes audio files for loudness verification, using
the cache to avoid redundant analysis.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict

from PySide6 import QtCore

from ...core.audio_normalizer import LoudnormTargets
from ...core.loudness_cache import LoudnessCache, TargetSettings, get_cache_path
from ...core.loudness_verifier import VerificationTolerance, analyze_and_verify

if TYPE_CHECKING:
    from .state import BatchWizardState, ScanResult
    from ...core.config import TranscoderConfig
    from ...core.loudness_verifier import VerificationResult

_logger = logging.getLogger(__name__)

# NOTE: Tolerance presets removed - wizard now uses config tolerances for consistency
# This ensures wizard analysis and runtime verification use the same tolerance values,
# preventing cache misses and result mismatches. The config tolerance preset is used
# throughout the entire workflow.


class AnalysisWorker(QtCore.QThread):
    """Worker thread for loudness analysis."""

    # Signals
    progress = QtCore.Signal(int, int, str, float, float)  # current, total, filename, elapsed, eta
    file_completed = QtCore.Signal(str, object)  # filename, VerificationResult
    finished = QtCore.Signal(dict)  # Dict[str, VerificationResult]
    error = QtCore.Signal(str, str)  # filename, error message
    aborted = QtCore.Signal()

    def __init__(self, cfg: TranscoderConfig, state: BatchWizardState, audio_files: list[ScanResult]):
        """Initialize analysis worker.
        
        Args:
            cfg: Transcoder configuration
            state: Wizard state with verification settings
            audio_files: List of audio scan results to analyze
        """
        super().__init__()
        self.cfg = cfg
        self.state = state
        self.audio_files = audio_files
        self._abort_requested = False
        self._results: Dict[str, VerificationResult] = {}
        self._cache: LoudnessCache | None = None

    def abort(self) -> None:
        """Request abort of the analysis operation."""
        self._abort_requested = True

    def run(self) -> None:
        """Execute loudness analysis for all files."""
        try:
            # Initialize cache
            cache_path = get_cache_path()
            self._cache = LoudnessCache(cache_path)
            
            # Get targets from config
            targets = LoudnormTargets(
                integrated_lufs=float(self.cfg.audio.audio_normalization_target),
                true_peak_dbtp=float(self.cfg.audio.audio_normalization_true_peak),
                lra_lu=float(self.cfg.audio.audio_normalization_lra),
            )
            
            # Get tolerance from config (fixed: wizard now uses config tolerances)
            # This ensures consistency between wizard analysis and runtime verification
            tolerances = self.cfg.verification.get_active_tolerances()
            preset = self.cfg.verification.tolerance_preset
            
            # Create target settings for cache (uses config preset for consistency)
            target_settings = TargetSettings(
                normalization_method="loudnorm",
                target_i=targets.integrated_lufs,
                target_tp=targets.true_peak_dbtp,
                target_lra=targets.lra_lu,
                tolerance_preset=preset,
            )
            
            total = len(self.audio_files)
            start_time = time.time()
            
            _logger.info(f"Starting loudness analysis for {total} files...")
            
            for i, scan_result in enumerate(self.audio_files):
                if self._abort_requested:
                    _logger.info("Analysis aborted by user")
                    self.aborted.emit()
                    # Save partial results to cache before exiting
                    if self._cache:
                        self._cache.close()
                    return
                
                file_path = scan_result.media_path
                filename = file_path.name
                
                # Calculate elapsed and ETA
                elapsed = time.time() - start_time
                avg_time_per_file = elapsed / (i + 1) if i > 0 else 0
                remaining_files = total - i
                eta = avg_time_per_file * remaining_files if avg_time_per_file > 0 else 0
                
                self.progress.emit(i, total, filename, elapsed, eta)
                
                try:
                    # Check cache first
                    cached = self._cache.get(file_path, target_settings) if self._cache else None
                    
                    result: VerificationResult
                    
                    if cached:
                        _logger.debug(f"Using cached analysis for {filename}")
                        # Reconstruct VerificationResult from cached measurements
                        from ...core.loudness_verifier import verify_loudnorm_normalization
                        result = verify_loudnorm_normalization(
                            cached.measurements,
                            targets,
                            tolerances
                        )
                    else:
                        # Run analysis
                        _logger.debug(f"Analyzing {filename}...")
                        
                        # Create a minimal song logger
                        from usdb_syncer.logger import song_logger
                        with song_logger(scan_result.song_id, "Analysis") as slog:
                            result = analyze_and_verify(
                                file_path,
                                targets,
                                tolerances,
                                timeout_seconds=300,  # 5 minute timeout
                                slog=slog,
                                cache=self._cache,
                                duration_seconds=scan_result.duration_seconds,
                            )
                        
                        # Store in cache if successful
                        if result.within_tolerance is not None and self._cache:
                            self._cache.put(file_path, target_settings, result.measurements, song_id=scan_result.song_id)
                    
                    # Store result
                    self._results[str(file_path)] = result
                    self.file_completed.emit(filename, result)
                    
                except Exception as e:
                    _logger.error(f"Analysis failed for {filename}: {e}", exc_info=True)
                    self.error.emit(filename, str(e))
                    # Continue with next file
            
            _logger.info(f"Analysis complete: {len(self._results)} files analyzed")
            self.finished.emit(self._results)
            
        except Exception as e:
            _logger.error(f"Analysis worker error: {e}", exc_info=True)
            self.error.emit("Worker", str(e))
        finally:
            if self._cache:
                self._cache.close()
