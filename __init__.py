"""Transcoder Addon - Converts media for various formats and compatibility."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from usdb_syncer import hooks
from usdb_syncer import utils as usdb_utils
from usdb_syncer.logger import song_logger

if TYPE_CHECKING:
    from usdb_syncer.usdb_song import UsdbSong

# Import addon modules
from .core import config, transcoder

# Phase 1: Ensure config exists at load time
config.load_config()

# Optional GUI integration (batch transcode menu entry)
try:
    from usdb_syncer import gui
except Exception:  # noqa: BLE001
    gui = None

_logger = logging.getLogger(__name__)

# Store action references for theme updates
_settings_action = None
_batch_action = None
_backup_mgmt_action = None


def on_download_finished(song: UsdbSong) -> None:
    """Process media after song download completes.

    Video processing remains the default behavior and audio processing is attempted
    when SyncMeta includes an audio file.
    """
    slog = song_logger(song.song_id)

    try:
        # Load configuration
        cfg = config.load_config()

        # Check FFMPEG availability
        if not usdb_utils.ffmpeg_is_available():
            slog.error("FFMPEG not available - skipping media transcode")
            return

        # 1. Process Video
        if cfg.auto_transcode_enabled:
            # Get video path
            if song.sync_meta:
                video_path = song.sync_meta.path.parent / song.sync_meta.video.file.fname if song.sync_meta.video and song.sync_meta.video.file and song.sync_meta.video.file.fname else None
                if video_path and video_path.exists():
                    # Analyze and potentially transcode video
                    transcoder.process_video(song, video_path, cfg, slog)
                else:
                    slog.debug("No video file found - skipping video transcode")
            else:
                slog.debug("No sync_meta - skipping video transcode")
        else:
            slog.debug("Automatic video transcoding disabled")

        # 2. Process Audio
        if cfg.audio.audio_transcode_enabled:
            # Get audio path
            audio_path = (
                song.sync_meta.path.parent / song.sync_meta.audio.file.fname
                if song.sync_meta
                and getattr(song.sync_meta, "audio", None)
                and song.sync_meta.audio
                and song.sync_meta.audio.file
                and song.sync_meta.audio.file.fname
                else None
            )
            if audio_path and audio_path.exists():
                # Analyze and potentially transcode standalone audio
                transcoder.process_audio(song, audio_path, cfg, slog)
            else:
                slog.debug("No audio file found - skipping audio transcode")
        else:
            slog.debug("Automatic audio transcoding disabled")

    except Exception as e:
        slog.error(f"Media transcode failed: {type(e).__name__}: {e}")
        _logger.debug(None, exc_info=True)


# Register hook on module import
hooks.SongLoaderDidFinish.subscribe(on_download_finished)


def _register_gui_hooks() -> None:
    if gui is None:
        return

    try:
        from PySide6.QtWidgets import QMessageBox

        from usdb_syncer.gui import events, icons
        from usdb_syncer.gui.progress import run_with_progress

        from .batch.orchestrator import BatchTranscodeOrchestrator
    except Exception:  # noqa: BLE001
        # If PySide isn't available (headless mode) or imports fail, skip GUI hook.
        return

    def on_theme_changed(_theme_key: str) -> None:
        """Update menu icons when theme changes."""
        if _settings_action:
            _settings_action.setIcon(icons.Icon.VIDEO.icon())
        if _batch_action:
            _batch_action.setIcon(icons.Icon.FFMPEG.icon())
        if _backup_mgmt_action:
            _backup_mgmt_action.setIcon(icons.Icon.CHANGES.icon())

    def on_window_loaded(main_window) -> None:
        """Add batch transcode menu item under Tools."""
        global _settings_action, _batch_action, _backup_mgmt_action

        def open_settings() -> None:
            from .gui.settings_gui import show_settings

            show_settings(main_window)

        def start_batch_transcode() -> None:
            cfg = config.load_config()
            orchestrator = BatchTranscodeOrchestrator(main_window, cfg)
            orchestrator.start_batch_workflow()

        def manage_backups() -> None:
            from .gui.backup.dialog_orchestrator import BackupDialogOrchestrator
            orchestrator = BackupDialogOrchestrator(main_window)
            orchestrator.start_workflow()

        # Ui_MainWindow provides menu_tools
        _settings_action = main_window.menu_tools.addAction("Media Transcoder Settings", open_settings)
        _settings_action.setIcon(icons.Icon.VIDEO.icon())
        _settings_action.setToolTip("Configure media transcoding settings for video and audio files.")
        
        _batch_action = main_window.menu_tools.addAction("Batch Media Transcode", start_batch_transcode)
        _batch_action.setIcon(icons.Icon.FFMPEG.icon())
        _batch_action.setToolTip("Transcode multiple synchronized media files (video/audio) in one pass.")

        _backup_mgmt_action = main_window.menu_tools.addAction("Manage Media Backups", manage_backups)
        _backup_mgmt_action.setIcon(icons.Icon.CHANGES.icon())
        _backup_mgmt_action.setToolTip("Find, remove, or restore persistent media backup files (video/audio).")

        # Subscribe to theme changes
        events.ThemeChanged.subscribe(on_theme_changed)

    gui.hooks.MainWindowDidLoad.subscribe(on_window_loaded)


_register_gui_hooks()

# Prune orphaned cache entries once per app launch (F6a recommendation)
try:
    from .core.loudness_cache import get_cache_path, LoudnessCache
    cache_path = get_cache_path()
    cache = LoudnessCache(cache_path)
    success = cache.prune_orphans()
    cache.close()
    if success:
        _logger.info("Cache pruning completed during addon initialization")
except Exception as e:
    _logger.warning(f"Cache pruning failed during addon initialization: {e}")

_logger.info("Transcoder addon loaded")
