"""Background worker for batch transcoding."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from PySide6 import QtCore

from usdb_syncer import db
from usdb_syncer.logger import song_logger
from usdb_syncer.usdb_song import UsdbSong
from usdb_syncer.utils import AppPaths

from .transcoder import TranscodeResult, process_audio, process_video
from .utils import is_aborted

if TYPE_CHECKING:
    from usdb_syncer import SongId

    from .batch_orchestrator import BatchTranscodeCandidate
    from .wizard.state import BatchWizardState
    from .config import TranscoderConfig

_logger = logging.getLogger(__name__)


class BatchAbortRegistry:
    """Thread-safe registry for batch transcode abort flags.
    
    This singleton class maintains abort flags for videos currently being
    transcoded in batch operations, allowing the UI abort signal to reach
    the FFmpeg execution layer.
    """

    _instance: Optional[BatchAbortRegistry] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._abort_flags: dict[SongId, bool] = {}
        self._flags_lock = threading.Lock()

    @classmethod
    def instance(cls) -> BatchAbortRegistry:
        """Get singleton instance with double-checked locking."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_abort(self, song_id: SongId) -> None:
        """Mark the given song for abort."""
        with self._flags_lock:
            self._abort_flags[song_id] = True
            _logger.debug(f"Set abort flag for song {song_id}")

    def is_aborted(self, song_id: SongId) -> bool:
        """Check if abort has been requested for the given song."""
        with self._flags_lock:
            return self._abort_flags.get(song_id, False)

    def clear(self, song_id: SongId) -> None:
        """Clear abort flag for the given song."""
        with self._flags_lock:
            self._abort_flags.pop(song_id, None)
            _logger.debug(f"Cleared abort flag for song {song_id}")

    def clear_all(self) -> None:
        """Clear all abort flags (for cleanup)."""
        with self._flags_lock:
            self._abort_flags.clear()
            _logger.debug("Cleared all batch abort flags")


class BatchWorker(QtCore.QThread):
    """Worker thread for batch transcoding."""

    # Signals (kept as "video_*" for backward compatibility; they now report media items)
    video_started = QtCore.Signal(str, str)  # title, artist
    video_progress = QtCore.Signal(float, float, str, float, float)  # percent, fps, speed, elapsed, eta
    video_completed = QtCore.Signal(int, object)  # index, TranscodeResult
    batch_completed = QtCore.Signal()
    batch_aborted = QtCore.Signal()

    def __init__(
        self,
        candidates: list[BatchTranscodeCandidate],
        cfg: TranscoderConfig,
        on_video_success: Optional[Callable[[BatchTranscodeCandidate], None]] = None,
        wizard_state: Optional[BatchWizardState] = None,
    ):
        super().__init__()
        self.candidates = candidates
        # Apply wizard force flags if wizard state is provided (fixed: wizard force flags now respected)
        self.cfg = self._apply_wizard_overrides(cfg, wizard_state)
        self.on_video_success = on_video_success
        self.wizard_state = wizard_state
        self._abort_requested = False
        self._current_song_id: Optional[SongId] = None

    def abort(self) -> None:
        """Request abort of the batch operation."""
        self._abort_requested = True
        # If a video is currently transcoding, mark it for immediate abort
        if self._current_song_id is not None:
            BatchAbortRegistry.instance().set_abort(self._current_song_id)
            _logger.info(f"Abort requested for currently transcoding song {self._current_song_id}")

    def run(self) -> None:
        """Execute batch transcode."""
        try:
            # Connect to database in this thread
            db.connect(AppPaths.db)

            selected_candidates = [c for c in self.candidates if c.selected]
            _logger.info(f"Starting batch transcode of {len(selected_candidates)} selected candidates (out of {len(self.candidates)} total)")
            
            for i, candidate in enumerate(self.candidates):
                if self._abort_requested or is_aborted(candidate.song_id):
                    if candidate.selected:
                        _logger.info(f"Batch transcode aborted by user at candidate {i} ({candidate.song_title})")
                    else:
                        _logger.info("Batch transcode aborted by user")
                    # Mark remaining candidates
                    for remaining in self.candidates[i:]:
                        if remaining.status == "pending":
                            remaining.status = "aborted" if remaining.selected else "skipped"
                    self.batch_aborted.emit()
                    return

                if not candidate.selected:
                    candidate.status = "skipped"
                    continue

                candidate.status = "transcoding"
                self.video_started.emit(candidate.song_title, candidate.artist)
                self._current_song_id = candidate.song_id
                
                start_time = time.time()
                
                try:
                    song = UsdbSong.get(candidate.song_id)
                    if not song:
                        raise ValueError(f"Song {candidate.song_id} not found in database")

                    slog = song_logger(candidate.song_id)

                    # Progress callback for process_video
                    def progress_callback(percent: float, fps: float, speed: str, elapsed: float, eta: float) -> None:
                        self.video_progress.emit(percent, fps, speed, elapsed, eta)

                    # Perform the transcode (video or audio)
                    if getattr(candidate, "media_type", "video") == "audio":
                        # When wizard ran analysis, results are cached and process_audio will reuse them
                        if self.wizard_state and self.wizard_state.verify_normalization:
                            _logger.info(f"Processing {candidate.video_path.name} (wizard analysis cache available)")
                        
                        result = process_audio(
                            song=song,
                            media_path=candidate.video_path,
                            cfg=self.cfg,
                            slog=slog,
                            progress_callback=progress_callback,
                        )
                    else:
                        result = process_video(
                            song=song,
                            video_path=candidate.video_path,
                            cfg=self.cfg,
                            slog=slog,
                            progress_callback=progress_callback
                        )
                    
                    candidate.actual_time_seconds = time.time() - start_time
                    candidate.result = result
                    
                    if result.success:
                        candidate.status = "success"
                        if self.on_video_success:
                            self.on_video_success(candidate)
                    elif result.aborted:
                        candidate.status = "aborted"
                        _logger.info(f"Transcode aborted for {candidate.song_title}")
                    else:
                        candidate.status = "failed"
                        candidate.error_message = result.error_message
                        _logger.error(f"Transcode failed for {candidate.song_title}: {result.error_message}")

                    self.video_completed.emit(i, result)

                except Exception as e:
                    candidate.status = "failed"
                    candidate.error_message = str(e)
                    candidate.actual_time_seconds = time.time() - start_time
                    _logger.exception(f"Unexpected error transcoding {candidate.song_title}")
                    # Emit a dummy failed result
                    self.video_completed.emit(i, TranscodeResult(
                        success=False,
                        output_path=None,
                        original_backed_up=False,
                        backup_path=None,
                        duration_seconds=0,
                        error_message=str(e)
                    ))
                finally:
                    BatchAbortRegistry.instance().clear(candidate.song_id)
                    self._current_song_id = None

            _logger.info("Batch transcode completed")
            self.batch_completed.emit()
        finally:
            BatchAbortRegistry.instance().clear_all()
    
    def _apply_wizard_overrides(self, cfg: TranscoderConfig, wizard_state: Optional[BatchWizardState]) -> TranscoderConfig:
        """Apply wizard force flags to config for this batch run.
        
        This ensures wizard-selected force flags override config values for this batch.
        Fixes critical issue where wizard force transcode selections were ignored.
        
        Args:
            cfg: Base configuration
            wizard_state: Wizard state with force flags (if used)
            
        Returns:
            Modified config with wizard overrides applied
        """
        if wizard_state is None:
            return cfg
        
        # Apply wizard force flags by creating modified config instances
        from dataclasses import replace
        
        modified_cfg = cfg
        
        # Apply force audio transcode from wizard
        if wizard_state.force_audio_transcode and not cfg.audio.force_transcode_audio:
            _logger.info("Wizard: Applying force_audio_transcode override")
            modified_audio = replace(cfg.audio, force_transcode_audio=True)
            modified_cfg = replace(modified_cfg, audio=modified_audio)
        
        # Apply force video transcode from wizard
        if wizard_state.force_video_transcode and not cfg.general.force_transcode_video:
            _logger.info("Wizard: Applying force_video_transcode override")
            modified_general = replace(cfg.general, force_transcode_video=True)
            modified_cfg = replace(modified_cfg, general=modified_general)
        
        return modified_cfg
