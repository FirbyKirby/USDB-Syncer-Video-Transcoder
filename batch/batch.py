"""Batch transcoding for existing video library.

This module follows the architecture plan's batch processing goal:
- Find existing videos managed by SyncMeta
- Reuse the same core transcoding engine

Wizard entry point for batch workflow redesign.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from usdb_syncer import SongId, settings
from usdb_syncer.logger import song_logger
from usdb_syncer.sync_meta import SyncMeta
from usdb_syncer.usdb_song import UsdbSong

from ..core.transcoder import process_video
from ..core.utils import is_aborted
from ..core.video_analyzer import VideoInfo, analyze_video, needs_transcoding

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow
    from ..core.config import TranscoderConfig
    from .wizard.state import BatchWizardState

_logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    """Result of a batch transcoding operation."""

    total: int
    successful: int
    skipped: int
    failed: int
    errors: list[tuple[int, str]]  # song_id, error_message


def find_videos_needing_transcode(
    cfg: "TranscoderConfig",
    song_dir: Path | None = None,
) -> Iterator[tuple[SongId, Path, VideoInfo]]:
    """Yield (song_id, video_path, video_info) for videos that need transcoding."""

    if song_dir is None:
        song_dir = settings.get_song_dir()

    for sync_meta in SyncMeta.get_in_folder(song_dir):
        video_path = sync_meta.path.parent / sync_meta.video.file.fname if sync_meta.video and sync_meta.video.file and sync_meta.video.file.fname else None
        if not video_path or not video_path.exists():
            continue

        info = analyze_video(video_path)
        if not info:
            _logger.warning(f"Could not analyze video: {video_path}")
            continue

        if needs_transcoding(info, cfg):
            yield sync_meta.song_id, video_path, info


def run_batch_with_wizard(parent: Optional["QMainWindow"] = None) -> Optional["BatchResult"]:
    """Launch the batch wizard workflow and execute transcode.
    
    This is the new entry point for batch processing that uses the wizard
    interface to guide users through batch operations.
    
    Args:
        parent: Parent window for modal dialogs
        
    Returns:
        BatchResult if batch completed, None if cancelled
    """
    from .wizard.orchestrator import BatchWizardOrchestrator
    from .orchestrator import BatchWorker, BatchProgressDialog, BatchResultsDialog, BatchTranscodeCandidate
    from ..core.config import load_config
    
    _logger.info("Launching batch wizard")
    orchestrator = BatchWizardOrchestrator(parent)
    wizard_state = orchestrator.run_wizard()
    
    if wizard_state is None:
        _logger.info("Batch wizard was cancelled by user")
        return None
    
    _logger.info("Batch wizard completed successfully, converting selections to batch jobs")
    
    # Phase 5: Convert wizard selections to batch worker format
    cfg = load_config()
    candidates = _convert_wizard_selections_to_candidates(wizard_state, cfg)
    
    if not candidates:
        _logger.info("No candidates selected for processing")
        return BatchResult(total=0, successful=0, skipped=0, failed=0, errors=[])
    
    # Execute batch transcode using existing batch worker
    _logger.info(f"Starting batch transcode of {len([c for c in candidates if c.selected])} selected items")
    
    # Create and show progress dialog
    selected_count = len([c for c in candidates if c.selected])
    progress_dialog = BatchProgressDialog(parent, selected_count)
    
    # Create batch worker with wizard context
    worker = BatchWorker(
        candidates=candidates,
        cfg=cfg,
        wizard_state=wizard_state,  # Pass wizard state for cache reuse
    )
    
    # Connect signals
    worker.video_started.connect(progress_dialog.update_current_video)
    worker.video_progress.connect(progress_dialog.update_video_progress)
    worker.video_completed.connect(
        lambda idx, res: progress_dialog.update_overall_progress(
            len([c for c in candidates if c.selected and c.status in ("success", "failed", "aborted")])
        )
    )
    worker.batch_completed.connect(progress_dialog.accept)
    worker.batch_aborted.connect(progress_dialog.reject)
    progress_dialog.abort_requested.connect(worker.abort)
    
    # Start worker
    worker.start()
    
    # Show progress dialog (modal)
    progress_dialog.exec()
    
    # Wait for worker to finish
    worker.wait()
    
    # Show results dialog
    if wizard_state.summary:
        results_dialog = BatchResultsDialog(parent, candidates, wizard_state.summary, worker._abort_requested)
        results_dialog.exec()
    
    # Build result summary
    selected = [c for c in candidates if c.selected]
    successful = len([c for c in selected if c.status == "success"])
    failed = len([c for c in selected if c.status == "failed"])
    aborted = len([c for c in selected if c.status == "aborted"])
    skipped = len([c for c in candidates if not c.selected])
    errors = [(int(c.song_id), c.error_message or "") for c in selected if c.status == "failed"]
    
    result = BatchResult(
        total=len(selected),
        successful=successful,
        skipped=skipped,
        failed=failed + aborted,
        errors=errors,
    )
    
    _logger.info(
        f"Batch wizard complete: {successful} successful, {failed} failed, "
        f"{aborted} aborted, {skipped} skipped"
    )
    
    return result


def _convert_wizard_selections_to_candidates(
    wizard_state: "BatchWizardState",
    cfg: "TranscoderConfig",
) -> list["BatchTranscodeCandidate"]:
    """Convert wizard selections to BatchTranscodeCandidate format.
    
    Args:
        wizard_state: Wizard state with selected songs
        cfg: Transcoder configuration
        
    Returns:
        List of BatchTranscodeCandidate objects
    """
    from .orchestrator import BatchTranscodeCandidate
    from .estimator import BatchEstimator
    from ..core.hwaccel import get_best_accelerator
    
    candidates: list[BatchTranscodeCandidate] = []
    hw_accel_available = get_best_accelerator(cfg.target_codec) is not None if cfg.general.hardware_encoding else False
    
    _logger.debug(f"Converting {len(wizard_state.selected_songs)} selected songs to candidates")
    
    for song_selection in wizard_state.selected_songs:
        # Process audio if selected
        if song_selection.process_audio and song_selection.audio_scan_result:
            scan_result = song_selection.audio_scan_result
            audio_info = scan_result.audio_info
            
            if audio_info:
                # Create estimates
                current_bitrate_kbps = getattr(audio_info, "bitrate_kbps", None)
                est_size = (scan_result.media_path.stat().st_size / (1024 * 1024))
                est_time = max(1.0, scan_result.duration_seconds * 0.1)
                
                candidate = BatchTranscodeCandidate(
                    song_id=song_selection.song_id,
                    song_title=song_selection.song_title,
                    artist=song_selection.artist,
                    video_path=scan_result.media_path,
                    media_type="audio",
                    current_codec=getattr(audio_info, "codec_name", "unknown"),
                    current_resolution="—",
                    current_fps=0.0,
                    current_container=scan_result.media_path.suffix.lstrip(".").lower(),
                    current_size_mb=scan_result.media_path.stat().st_size / (1024 * 1024),
                    duration_seconds=scan_result.duration_seconds,
                    current_bitrate_kbps=current_bitrate_kbps,
                    estimated_output_size_mb=est_size,
                    estimated_time_seconds=est_time,
                    selected=True,
                )
                candidates.append(candidate)
        
        # Process video if selected
        if song_selection.process_video and song_selection.video_scan_result:
            scan_result = song_selection.video_scan_result
            video_info = scan_result.video_info
            
            if video_info:
                # Create estimates
                est_size = BatchEstimator.estimate_output_size(video_info, cfg)  # type: ignore
                est_time = BatchEstimator.estimate_transcode_time(video_info, cfg, hw_accel_available)  # type: ignore
                current_resolution = f"{video_info.width}x{video_info.height}"  # type: ignore
                current_fps = float(video_info.frame_rate)  # type: ignore
                current_profile = getattr(video_info, "profile", None)
                current_pixel_format = getattr(video_info, "pixel_format", None)
                current_bitrate_kbps = getattr(video_info, "bitrate_kbps", None)
                
                candidate = BatchTranscodeCandidate(
                    song_id=song_selection.song_id,
                    song_title=song_selection.song_title,
                    artist=song_selection.artist,
                    video_path=scan_result.media_path,
                    media_type="video",
                    current_codec=getattr(video_info, "codec_name", "unknown"),
                    current_resolution=current_resolution,
                    current_fps=current_fps,
                    current_container=getattr(video_info, "container", scan_result.media_path.suffix.lstrip(".").lower()),
                    current_size_mb=scan_result.media_path.stat().st_size / (1024 * 1024),
                    duration_seconds=scan_result.duration_seconds,
                    current_profile=current_profile,
                    current_pixel_format=current_pixel_format,
                    current_bitrate_kbps=current_bitrate_kbps,
                    estimated_output_size_mb=float(est_size),
                    estimated_time_seconds=float(est_time),
                    selected=True,
                )
                candidates.append(candidate)
    
    _logger.debug(f"Converted {len(candidates)} candidates ({len([c for c in candidates if c.media_type == 'audio'])} audio, {len([c for c in candidates if c.media_type == 'video'])} video)")
    
    return candidates


