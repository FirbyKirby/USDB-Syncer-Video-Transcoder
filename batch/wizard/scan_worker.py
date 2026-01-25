"""Worker thread for scanning library in the batch wizard.

This worker performs fast metadata scanning using ffprobe to discover
songs that need transcoding, similar to batch_orchestrator.ScanWorker
but stores results as ScanResult objects for the wizard workflow.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6 import QtCore
from usdb_syncer import db, settings
from usdb_syncer.sync_meta import SyncMeta
from usdb_syncer.utils import AppPaths

from ...core.audio_analyzer import analyze_audio
from .state import ScanResult
from ...core.codecs import get_audio_codec_handler
from ...core.utils import is_audio_file
from ...core.video_analyzer import analyze_video, needs_transcoding

if TYPE_CHECKING:
    from .state import BatchWizardState
    from ...core.config import TranscoderConfig

_logger = logging.getLogger(__name__)


class ScanWorker(QtCore.QThread):
    """Worker thread for scanning the library in batch wizard."""

    # Signals
    progress = QtCore.Signal(int, int, str)  # current, total, filename
    finished = QtCore.Signal(list)  # list of ScanResult objects
    error = QtCore.Signal(str)  # error message
    aborted = QtCore.Signal()

    def __init__(self, cfg: TranscoderConfig, state: BatchWizardState):
        """Initialize scan worker.
        
        Args:
            cfg: Transcoder configuration
            state: Wizard state containing processing flags
        """
        super().__init__()
        self.cfg = cfg
        self.state = state
        self._abort_requested = False

    def abort(self) -> None:
        """Request abort of the scan operation."""
        self._abort_requested = True

    def run(self) -> None:
        """Execute library scan."""
        try:
            # Connect to database in this thread
            db.connect(AppPaths.db)
            
            results: list[ScanResult] = []
            song_dir = settings.get_song_dir()
            
            # Get all songs
            metas = list(SyncMeta.get_in_folder(song_dir))
            total = len(metas)
            
            _logger.info(f"Scanning {total} songs for media files...")
            
            for i, sync_meta in enumerate(metas):
                if self._abort_requested:
                    _logger.info("Scan aborted by user")
                    self.aborted.emit()
                    return

                # Get song info
                from usdb_syncer.usdb_song import UsdbSong
                song = UsdbSong.get(sync_meta.song_id)
                title = song.title if song else sync_meta.path.stem
                artist = song.artist if song else "Unknown"

                # Discover video and audio paths
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

                # Scan video if enabled and exists
                if self.state.process_video and video_path and video_path.exists():
                    self.progress.emit(i, total, video_path.name)
                    
                    info = analyze_video(video_path)
                    if info:
                        needs_proc = (
                            self.state.force_video_transcode or 
                            needs_transcoding(info, self.cfg)
                        )
                        reasons = []
                        if self.state.force_video_transcode:
                            reasons.append("force transcode enabled")
                        elif needs_proc:
                            reasons.append("codec/container/settings mismatch")
                        
                        results.append(ScanResult(
                            song_id=sync_meta.song_id,
                            song_title=title,
                            artist=artist,
                            media_path=video_path,
                            media_type="video",
                            video_info=info,
                            needs_processing=needs_proc,
                            processing_reasons=reasons,
                            duration_seconds=float(getattr(info, "duration_seconds", 0.0)),
                        ))

                # Scan audio if enabled and exists
                if self.state.process_audio and audio_path and audio_path.exists():
                    if not is_audio_file(audio_path):
                        continue
                    
                    self.progress.emit(i, total, audio_path.name)
                    
                    info = analyze_audio(audio_path)
                    if info:
                        handler = get_audio_codec_handler(self.cfg.audio.audio_codec)
                        if not handler:
                            continue
                        
                        container_matches = handler.is_container_compatible(audio_path)
                        codec_matches = (info.codec_name.lower() == self.cfg.audio.audio_codec.lower())
                        format_matches = container_matches and codec_matches
                        normalization_enabled = bool(self.cfg.audio.audio_normalization_enabled)
                        force_audio = bool(self.state.force_audio_transcode)
                        
                        reasons = []
                        needs_proc = force_audio or not format_matches
                        
                        if force_audio:
                            reasons.append("force transcode enabled")
                        elif not codec_matches:
                            reasons.append(f"codec {info.codec_name} != {self.cfg.audio.audio_codec}")
                        elif not container_matches:
                            reasons.append(f"container {info.container} incompatible")
                        
                        # For normalization, include matching files if replaygain
                        # or if verify_normalization is enabled (will check during analysis)
                        if normalization_enabled and format_matches and not force_audio:
                            method = self.cfg.audio.audio_normalization_method
                            if method == "replaygain":
                                needs_proc = True
                                reasons.append("replaygain verification needed")
                            elif self.state.verify_normalization:
                                # Include for verification even if format matches
                                needs_proc = True
                                reasons.append("loudness verification needed")
                        
                        if needs_proc:
                            results.append(ScanResult(
                                song_id=sync_meta.song_id,
                                song_title=title,
                                artist=artist,
                                media_path=audio_path,
                                media_type="audio",
                                audio_info=info,
                                needs_processing=needs_proc,
                                processing_reasons=reasons,
                                duration_seconds=float(getattr(info, "duration_seconds", 0.0)),
                            ))
            
            _logger.info(f"Scan complete: found {len(results)} media files needing processing")
            self.finished.emit(results)
            
        except Exception as e:
            _logger.error(f"Scan worker error: {e}", exc_info=True)
            self.error.emit(str(e))
