"""Orchestration for batch transcoding."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QMainWindow

from usdb_syncer import SongId, db, settings
from usdb_syncer.gui import icons
from usdb_syncer.sync_meta import SyncMeta
from usdb_syncer.utils import AppPaths

from .estimator import BatchEstimator
from ..gui.batch.preview_dialog import BatchPreviewDialog
from ..gui.batch.progress_dialog import BatchProgressDialog
from ..gui.batch.results_dialog import BatchResultsDialog
from .worker import BatchWorker
from .rollback import RollbackManager
from ..gui.backup.restore_progress_dialog import RollbackBackupProgressDialog
from ..workers.rollback_backup_worker import RollbackBackupWorker
from ..core.video_analyzer import analyze_video, needs_transcoding
from ..core.utils import is_audio_file

if TYPE_CHECKING:
    from ..core.config import TranscoderConfig
    from ..core.transcoder import TranscodeResult

_logger = logging.getLogger(__name__)


@dataclass
class BatchTranscodeCandidate:
    """Single media candidate for batch transcoding."""
    song_id: SongId
    song_title: str
    artist: str

    video_path: Path
    media_type: Literal["video", "audio"]
    
    # Current properties
    current_codec: str
    # Audio candidates will use placeholder values.
    current_resolution: str  # e.g., "1920x1080" or "—"
    current_fps: float
    current_container: str
    current_size_mb: float
    duration_seconds: float
    
    # Estimates
    estimated_output_size_mb: float
    estimated_time_seconds: float

    # Current properties (optional/codec-specific)
    current_profile: Optional[str] = None
    current_pixel_format: Optional[str] = None
    current_bitrate_kbps: Optional[int] = None
    
    # Runtime state
    selected: bool = True
    status: Literal["pending", "transcoding", "success", "failed", "aborted", "skipped", "rolled_back"] = "pending"
    error_message: Optional[str] = None
    actual_time_seconds: Optional[float] = None
    
    # Result (populated after transcode)
    result: Optional[TranscodeResult] = None


@dataclass
class BatchTranscodeSummary:
    """Summary configuration for entire batch."""
    target_codec: str
    target_container: str

    # Audio target (for audio candidates)
    target_audio_codec: str
    target_audio_container: str
    
    # Resolution setting (depends on USDB integration vs manual)
    resolution_display: str  # Human-readable, e.g., "Max: 1080p" or "Exact: 1080p" or "Original"
    resolution_value: Optional[tuple[int, int]]  # Actual value if set
    resolution_is_limit: bool  # True if max limit, False if exact target
    
    # FPS setting (depends on USDB integration vs manual)
    fps_display: str  # Human-readable, e.g., "Max: 60" or "Exact: 60" or "Original"
    fps_value: Optional[int]  # Actual value if set
    fps_is_limit: bool  # True if max limit, False if exact target
    
    hardware_acceleration: str  # e.g., "Intel QuickSync", "Disabled", "NVIDIA"
    
    total_videos: int
    selected_videos: int
    total_estimated_time_seconds: float
    total_disk_space_required_mb: float
    current_free_space_mb: float

    # Codec-specific (for h264/hevc)
    target_profile: Optional[str] = None
    target_pixel_format: Optional[str] = None
    target_bitrate_kbps: Optional[int] = None
    
    rollback_enabled: bool = False


class ScanWorker(QtCore.QThread):
    """Worker thread for scanning the library."""

    # Signals
    progress = QtCore.Signal(int, int, str)  # current, total, filename
    finished = QtCore.Signal(list)  # list of (song_id, media_path, info, media_type)
    aborted = QtCore.Signal()

    def __init__(self, cfg: TranscoderConfig):
        super().__init__()
        self.cfg = cfg
        self._abort_requested = False

    def abort(self) -> None:
        """Request abort of the scan operation."""
        self._abort_requested = True

    def run(self) -> None:
        """Execute library scan."""
        from ..core.audio_analyzer import analyze_audio
        from ..core.codecs import get_audio_codec_handler

        # Connect to database in this thread
        db.connect(AppPaths.db)
        
        results: list[tuple[SongId, Path, object, Literal["video", "audio"]]] = []
        song_dir = settings.get_song_dir()
        
        # We need to count total songs first for progress
        metas = list(SyncMeta.get_in_folder(song_dir))
        total = len(metas)
        
        for i, sync_meta in enumerate(metas):
            if self._abort_requested:
                self.aborted.emit()
                return

            # Discover BOTH video and audio media paths for this song.
            video_path = (
                sync_meta.path.parent / sync_meta.video.file.fname
                if sync_meta.video and sync_meta.video.file and sync_meta.video.file.fname
                else None
            )
            audio_path = (
                sync_meta.path.parent / sync_meta.audio.file.fname
                if getattr(sync_meta, "audio", None)
                and sync_meta.audio
                and sync_meta.audio.file
                and sync_meta.audio.file.fname
                else None
            )

            # Prefer scanning video first so progress feels familiar.
            for media_type, media_path in (("video", video_path), ("audio", audio_path)):
                if self._abort_requested:
                    self.aborted.emit()
                    return

                if not media_path or not media_path.exists():
                    continue

                # Skip non-audio for audio scans (defensive; helps if SyncMeta points to a container file).
                if media_type == "audio" and not is_audio_file(media_path):
                    continue

                self.progress.emit(i, total, media_path.name)

                if media_type == "video":
                    info = analyze_video(media_path)
                    if not info:
                        continue

                    if self.cfg.general.force_transcode_video:
                        _logger.debug(f"Including {media_path.name} (force transcode enabled)")
                        results.append((sync_meta.song_id, media_path, info, "video"))
                    elif needs_transcoding(info, self.cfg):
                        results.append((sync_meta.song_id, media_path, info, "video"))
                else:
                    # Audio scanning respects audio enable flag.
                    if not bool(self.cfg.audio.audio_transcode_enabled):
                        continue

                    info = analyze_audio(media_path)
                    if not info:
                        continue

                    handler = get_audio_codec_handler(self.cfg.audio.audio_codec)
                    if not handler:
                        continue

                    container_matches = handler.is_container_compatible(media_path)
                    codec_matches = (info.codec_name.lower() == self.cfg.audio.audio_codec.lower())
                    format_matches = container_matches and codec_matches
                    normalization_enabled = bool(self.cfg.audio.audio_normalization_enabled)
                    force_audio = bool(getattr(self.cfg.audio, "force_transcode_audio", False))

                    # Include if force transcode, or format mismatch, or normalization needed
                    needs_audio = force_audio or not format_matches
                    if normalization_enabled and not force_audio:
                        method = self.cfg.audio.audio_normalization_method
                        if method == "replaygain" and format_matches:
                            # For replaygain, include if format matches (to check tags during transcode)
                            needs_audio = True

                    if needs_audio:
                        if force_audio and container_matches and codec_matches and not normalization_enabled:
                            _logger.debug(f"Including {media_path.name} (force audio transcode enabled)")
                        results.append((sync_meta.song_id, media_path, info, "audio"))

        self.finished.emit(results)


class BatchTranscodeOrchestrator:
    """Orchestrates the entire batch transcode workflow."""
    
    def __init__(self, parent: QMainWindow, cfg: TranscoderConfig):
        self.parent = parent
        self.cfg = cfg
        self.candidates: list[BatchTranscodeCandidate] = []
        self.summary: Optional[BatchTranscodeSummary] = None
        self.rollback_manager: Optional[RollbackManager] = None
        self._existing_user_backups: dict[SongId, Path] = {}
        self._rollback_dir: Optional[Path] = None
        self._abort_flag = False
        self._worker: Optional[BatchWorker] = None
        self._progress_dialog: Optional[BatchProgressDialog] = None
        self._backup_worker: Optional[RollbackBackupWorker] = None
        self._backup_dialog: Optional[RollbackBackupProgressDialog] = None
        self._backup_success: bool = False
        self._backup_abort_requested: bool = False
        
    def start_batch_workflow(self) -> None:
        """Entry point: start the entire batch transcode workflow."""
        # Phase 1: Generate preview
        if not self._generate_preview():
            return
            
        # Phase 2: Show selection dialog
        if not self._show_selection_dialog():
            return
            
        # Phase 3: Execute batch with progress monitoring
        self._execute_batch()
        
        # Phase 4: Show results
        self._show_results()
    
    def _generate_preview(self) -> bool:
        """Generate preview data for all candidate media files."""
        # Show a simple progress dialog for scanning
        progress = QtWidgets.QProgressDialog(
            "Scanning library for media files needing transcoding...",
            "Cancel",
            0,
            100,
            self.parent,
        )
        progress.setWindowIcon(icons.Icon.FFMPEG.icon())
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        
        scan_results = []
        scan_aborted = False

        worker = ScanWorker(self.cfg)
        
        def on_progress(current, total, filename):
            progress.setMaximum(total)
            progress.setValue(current)
            progress.setLabelText(f"Scanning {current+1} of {total}: {filename}")

        def on_finished(results):
            nonlocal scan_results
            scan_results = results
            progress.accept()

        def on_aborted():
            nonlocal scan_aborted
            scan_aborted = True
            progress.reject()

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.aborted.connect(on_aborted)
        progress.canceled.connect(worker.abort)

        if self.cfg.general.force_transcode_video:
            _logger.info("Starting library scan for all videos (force transcode enabled)...")
        else:
            _logger.info("Starting library scan for media files needing transcode...")
        start_scan = time.time()
        worker.start()
        progress.exec()
        worker.wait()

        if scan_aborted or progress.wasCanceled():
            return False

        _logger.info(f"Library scan completed in {time.time() - start_scan:.2f}s. Found {len(scan_results)} candidates.")
        
        if not scan_results:
            QtWidgets.QMessageBox.information(
                self.parent,
                "Batch Media Transcode",
                "No media files found that need transcoding with current settings.",
            )
            return False
            
        # 2. Create candidates from scan results
        self.candidates = []
        hw_accel_available = self._is_hw_accel_available()
        
        for song_id, media_path, info, media_type in scan_results:
            # Get song info for display
            from usdb_syncer.usdb_song import UsdbSong
            song = UsdbSong.get(song_id)
            title = song.title if song else media_path.stem
            artist = song.artist if song else "Unknown"

            # Estimates
            if media_type == "video":
                est_size = BatchEstimator.estimate_output_size(info, self.cfg)  # type: ignore[arg-type]
                est_time = BatchEstimator.estimate_transcode_time(info, self.cfg, hw_accel_available)  # type: ignore[arg-type]
                current_resolution = f"{info.width}x{info.height}"  # type: ignore[attr-defined]
                current_fps = float(info.frame_rate)  # type: ignore[attr-defined]
                current_profile = getattr(info, "profile", None)
                current_pixel_format = getattr(info, "pixel_format", None)
                current_bitrate_kbps = getattr(info, "bitrate_kbps", None)
            else:
                # Audio: use conservative estimates (audio is usually much faster than realtime).
                current_resolution = "—"
                current_fps = 0.0
                current_profile = None
                current_pixel_format = None
                current_bitrate_kbps = getattr(info, "bitrate_kbps", None)
                est_size = (media_path.stat().st_size / (1024 * 1024))
                est_time = max(1.0, float(getattr(info, "duration_seconds", 0.0)) * 0.1)

            candidate = BatchTranscodeCandidate(
                song_id=song_id,
                video_path=media_path,
                media_type=media_type,
                song_title=title,
                artist=artist,
                current_codec=getattr(info, "codec_name", "unknown"),
                current_resolution=current_resolution,
                current_fps=current_fps,
                current_container=getattr(info, "container", media_path.suffix.lstrip(".").lower()),
                current_size_mb=media_path.stat().st_size / (1024 * 1024),
                duration_seconds=float(getattr(info, "duration_seconds", 0.0)),
                current_profile=current_profile,
                current_pixel_format=current_pixel_format,
                current_bitrate_kbps=current_bitrate_kbps,
                estimated_output_size_mb=float(est_size),
                estimated_time_seconds=float(est_time),
            )
            self.candidates.append(candidate)
            
        if not self.candidates:
            QtWidgets.QMessageBox.information(
                self.parent,
                "Batch Media Transcode",
                "No valid media files found for transcoding.",
            )
            return False
            
        # 3. Create summary
        codec_cfg = getattr(self.cfg, self.cfg.target_codec)

        from ..core.codecs import get_audio_codec_handler
        audio_handler = get_audio_codec_handler(self.cfg.audio.audio_codec)
        audio_container = audio_handler.capabilities().container if audio_handler else "m4a"
        
        self.summary = BatchTranscodeSummary(
            target_codec=self.cfg.target_codec,
            target_container=codec_cfg.container,
            target_audio_codec=self.cfg.audio.audio_codec,
            target_audio_container=audio_container,
            resolution_display=self._format_resolution_display(),
            resolution_value=self.cfg.general.max_resolution,
            resolution_is_limit=self.cfg.usdb_integration.use_usdb_resolution,
            fps_display=self._format_fps_display(),
            fps_value=self.cfg.general.max_fps,
            fps_is_limit=self.cfg.usdb_integration.use_usdb_fps,
            hardware_acceleration=self._get_hw_accel_string(),
            target_profile=getattr(codec_cfg, "profile", None),
            target_pixel_format=getattr(codec_cfg, "pixel_format", None),
            target_bitrate_kbps=self.cfg.general.max_bitrate_kbps,
            total_videos=len([c for c in self.candidates if c.media_type == "video"]),
            selected_videos=len([c for c in self.candidates if c.media_type == "video" and c.selected]),
            total_estimated_time_seconds=sum(c.estimated_time_seconds for c in self.candidates),
            total_disk_space_required_mb=BatchEstimator.calculate_disk_space_required(self.candidates, False, self.cfg.general.backup_original),
            current_free_space_mb=BatchEstimator.get_free_disk_space(self.candidates[0].video_path),
            rollback_enabled=False
        )
        
        return True
            
    def _show_selection_dialog(self) -> bool:
        """Show selection dialog, return True if user starts."""
        if not self.summary:
            return False
        dialog = BatchPreviewDialog(self.parent, self.candidates, self.summary)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.summary.rollback_enabled = dialog.is_rollback_enabled()
            return True
        return False
            
    def _execute_batch(self) -> None:
        """Execute batch transcode with progress monitoring."""
        if not self.summary:
            return
        selected_candidates = [c for c in self.candidates if c.selected]
        if not selected_candidates:
            return
            
        # Initialize rollback manager if enabled
        if self.summary.rollback_enabled:
            if not self._create_rollback_backups():
                # User aborted or critical error during backup creation
                # Clean up the rollback temp directory
                if self.rollback_manager:
                    self.rollback_manager.cleanup_rollback_data()
                return
        
        # Create and show progress dialog
        self._progress_dialog = BatchProgressDialog(self.parent, len(selected_candidates))
        self._progress_dialog.abort_requested.connect(self.abort_batch)
        
        # Use original config (no modification needed for rollback)
        self._worker = BatchWorker(
            self.candidates,
            self.cfg,
            on_video_success=self._on_video_success,
            wizard_state=None,  # Legacy batch flow doesn't use wizard
        )
        
        # Connect signals
        self._worker.video_started.connect(self._progress_dialog.update_current_video)
        self._worker.video_progress.connect(self._progress_dialog.update_video_progress)
        self._worker.video_completed.connect(lambda idx, res: self._progress_dialog.update_overall_progress(self._get_completed_count()) if self._progress_dialog else None)
        self._worker.batch_completed.connect(self._progress_dialog.accept)
        self._worker.batch_aborted.connect(self._progress_dialog.reject)
        
        # Start worker
        self._worker.start()
        
        # Show dialog (modal)
        if self._progress_dialog:
            self._progress_dialog.exec()
        
        # Wait for worker to finish
        self._worker.wait()
        
        # Handle abort/rollback
        if self._abort_flag:
            self._handle_abort()
        elif self.rollback_manager:
            # Success! Apply backup preservation rule FIRST (needs rollback backups)
            self._apply_backup_preservation_rule()
            
            # THEN clean up rollback data
            self.rollback_manager.cleanup_rollback_data()
    
    def _create_rollback_backups(self) -> bool:
        """Create pre-transcode backups in rollback temp directory (non-blocking)."""
        if not self.summary:
            return False
            
        selected_candidates = [c for c in self.candidates if c.selected]
        self.rollback_manager = RollbackManager(self.cfg)
        self._rollback_dir = self.rollback_manager.enable_rollback()
        
        # Record which media files already have user backups
        self._existing_user_backups.clear()
        for candidate in selected_candidates:
            user_backup_path = candidate.video_path.with_name(
                f"{candidate.video_path.stem}{self.cfg.general.backup_suffix}{candidate.video_path.suffix}"
            )
            if user_backup_path.exists():
                # NOTE: This is keyed by song_id for legacy behavior.
                # If a song ever has both video+audio in the same batch, this will be overwritten.
                # That's acceptable for now; the rollback entries still preserve correct paths.
                self._existing_user_backups[candidate.song_id] = user_backup_path
        
        _logger.info("Creating pre-transcode rollback backups...")
        
        # Create and show progress dialog
        self._backup_dialog = RollbackBackupProgressDialog(self.parent, len(selected_candidates))
        self._backup_worker = RollbackBackupWorker(selected_candidates, self.rollback_manager)
        
        # Connect signals
        self._backup_worker.progress.connect(self._backup_dialog.update_progress)
        self._backup_worker.finished.connect(self._on_backup_finished)
        self._backup_worker.error.connect(self._on_backup_error)
        self._backup_worker.aborted.connect(self._on_backup_aborted)
        
        def on_abort_requested():
            self._backup_abort_requested = True
            if self._backup_worker:
                self._backup_worker.abort()
        
        self._backup_dialog.abort_requested.connect(on_abort_requested)
        
        # Reset flags
        self._backup_success = False
        self._backup_abort_requested = False
        
        # Start worker
        self._backup_worker.start()
        
        # Show dialog (modal)
        self._backup_dialog.exec()
        
        # Wait for worker to finish
        self._backup_worker.wait()
        
        return self._backup_success

    def _on_backup_finished(self) -> None:
        """Called when backup worker finishes successfully."""
        if self._backup_abort_requested:
            self._on_backup_aborted()
            return
            
        self._backup_success = True
        if self._backup_dialog:
            self._backup_dialog.accept()

    def _on_backup_error(self, filename: str, error_msg: str) -> None:
        """Called when backup worker encounters an error."""
        _logger.error(f"Rollback backup error for {filename}: {error_msg}")
        
        # Ask user if they want to continue without rollback protection
        reply = QtWidgets.QMessageBox.critical(
            self.parent,
            "Rollback Backup Failed",
            f"Failed to create rollback backup for {filename}:\n{error_msg}\n\nContinue batch without rollback protection?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No
        )
        
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            # Continue without rollback protection for the rest?
            # Actually, the worker already stopped. We'll just accept and continue the batch.
            self._backup_success = True
            if self._backup_dialog:
                self._backup_dialog.accept()
        else:
            self._backup_success = False
            if self._backup_dialog:
                self._backup_dialog.reject()

    def _on_backup_aborted(self) -> None:
        """Called when backup worker is aborted."""
        self._backup_success = False
        if self._backup_dialog:
            self._backup_dialog.reject()

    def _apply_backup_preservation_rule(self) -> None:
        """Apply backup preservation rule after successful batch.
        
        For any video where a user backup exists (e.g., MyVideo-source.mp4),
        update it to contain the pre-transcode version. This keeps backups
        one revision behind regardless of settings.
        """
        if not self.rollback_manager:
            return
        
        _logger.info("Applying backup preservation rule...")
        
        for entry in self.rollback_manager.entries:
            candidate = next((c for c in self.candidates if c.song_id == entry.song_id), None)
            if not candidate or not candidate.result or not candidate.result.success:
                continue
            
            # Determine user backup path
            user_backup_path = entry.original_path.with_name(
                f"{entry.original_path.stem}{self.cfg.general.backup_suffix}{entry.original_path.suffix}"
            )
            
            # If user backup exists, update it to pre-transcode version
            if user_backup_path.exists():
                try:
                    # The rollback backup contains the pre-transcode version
                    if entry.rollback_backup_path.exists():
                        # Replace user backup with pre-transcode version
                        shutil.copy2(str(entry.rollback_backup_path), str(user_backup_path))
                        _logger.info(f"Updated user backup for {candidate.song_title}")
                    else:
                        _logger.warning(f"Rollback backup missing, cannot update user backup for {candidate.song_title}")
                except Exception as e:
                    _logger.error(f"Failed to update user backup for {candidate.song_title}: {e}")

    def _on_video_success(self, candidate: BatchTranscodeCandidate) -> None:
        """Called by worker when a media file is successfully transcoded."""
        if self.rollback_manager and candidate.result and candidate.result.output_path:
            rollback_backup_path = self.rollback_manager.get_rollback_backup_path(
                candidate.song_id,
                candidate.video_path,
                media_type=candidate.media_type,
            )
            
            # Check if rollback backup exists (should if we created it pre-transcode)
            if not rollback_backup_path.exists():
                _logger.warning(f"Rollback backup missing for {candidate.song_title}")
                return
            
            # Check if user backup existed before batch
            user_backup_existed = candidate.song_id in self._existing_user_backups
            
            self.rollback_manager.record_transcode(
                candidate.song_id,
                candidate.media_type,
                candidate.video_path,
                rollback_backup_path,
                candidate.result.output_path,
                user_backup_existed
            )

    def _get_completed_count(self) -> int:
        """Count how many selected media files have finished."""
        return sum(1 for c in self.candidates if c.selected and c.status in ("success", "failed", "aborted"))

    def _handle_abort(self) -> None:
        """Handle batch abort and offer rollback."""
        if not self.rollback_manager or not self.rollback_manager.entries:
            return
            
        reply = QtWidgets.QMessageBox.question(
            self.parent,
            "Batch Aborted",
            f"Batch operation was aborted. {len(self.rollback_manager.entries)} videos were already transcoded.\n\nDo you want to roll back these changes and restore the original videos?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes
        )
        
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            progress = QtWidgets.QProgressDialog("Rolling back changes...", None, 0, len(self.rollback_manager.entries), self.parent)
            progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
            progress.show()
            
            success, failed, rolled_back_ids = self.rollback_manager.rollback_all()
            
            # Update candidate statuses for successfully rolled back videos
            for song_id in rolled_back_ids:
                candidate = next((c for c in self.candidates if c.song_id == song_id), None)
                if candidate and candidate.status == "success":
                    candidate.status = "rolled_back"

            progress.close()
            QtWidgets.QMessageBox.information(self.parent, "Rollback Complete", f"Successfully restored {success} videos. {failed} failures.")

    def _show_results(self) -> None:
        """Show results dialog."""
        if not self.summary:
            return
        dialog = BatchResultsDialog(self.parent, self.candidates, self.summary, self._abort_flag)
        dialog.exec()
        
    def abort_batch(self) -> None:
        """Request abort of current batch operation."""
        self._abort_flag = True
        if self._worker:
            self._worker.abort()

    def _format_resolution_display(self) -> str:
        """Format resolution for display."""
        if self.cfg.usdb_integration.use_usdb_resolution:
            res = settings.get_video_resolution()
            return f"Max: {res.height()}p ({res.width()}x{res.height()})"
        elif self.cfg.general.max_resolution:
            w, h = self.cfg.general.max_resolution
            return f"Exact: {h}p ({w}x{h})"
        return "Original"

    def _format_fps_display(self) -> str:
        """Format FPS for display."""
        if self.cfg.usdb_integration.use_usdb_fps:
            fps = settings.get_video_fps()
            return f"Max: {fps.value} fps"
        elif self.cfg.general.max_fps:
            return f"Exact: {self.cfg.general.max_fps} fps"
        return "Original"

    def _is_hw_accel_available(self) -> bool:
        """Check if hardware acceleration is available for target codec."""
        if not self.cfg.general.hardware_encoding:
            return False
        from ..core.hwaccel import get_best_accelerator
        return get_best_accelerator(self.cfg.target_codec) is not None

    def _get_hw_accel_string(self) -> str:
        """Get human-readable HW accel status."""
        if not self.cfg.general.hardware_encoding:
            return "Disabled"
        from ..core.hwaccel import get_best_accelerator
        accel = get_best_accelerator(self.cfg.target_codec)
        if accel:
            return f"Enabled ({accel.capabilities().display_name})"
        return "Disabled (no accelerator found)"
