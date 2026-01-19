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
from . import config, transcoder

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

    Stage 2 adds audio support. Video processing remains the default behavior
    and audio processing is attempted when SyncMeta includes an audio file.
    """
    slog = song_logger(song.song_id)

    try:
        # Load configuration
        cfg = config.load_config()

        if not cfg.auto_transcode_enabled:
            slog.debug("Automatic video transcoding disabled")
            return

        # Check FFMPEG availability
        if not usdb_utils.ffmpeg_is_available():
            slog.error("FFMPEG not available - skipping video transcode")
            return

        # Get video path
        if not song.sync_meta:
            slog.debug("No sync_meta - skipping video transcode")
            return

        video_path = song.sync_meta.path.parent / song.sync_meta.video.file.fname if song.sync_meta.video and song.sync_meta.video.file and song.sync_meta.video.file.fname else None
        if not video_path or not video_path.exists():
            slog.debug("No video file found - skipping transcode")
            return

        # Analyze and potentially transcode video
        transcoder.process_video(song, video_path, cfg, slog)

        # Analyze and potentially transcode standalone audio (if present)
        audio_path = (
            song.sync_meta.path.parent / song.sync_meta.audio.file.fname
            if getattr(song.sync_meta, "audio", None)
            and song.sync_meta.audio
            and song.sync_meta.audio.file
            and song.sync_meta.audio.file.fname
            else None
        )
        if cfg.audio.audio_transcode_enabled and audio_path and audio_path.exists():
            transcoder.process_audio(song, audio_path, cfg, slog)

    except Exception as e:
        slog.error(f"Video transcode failed: {type(e).__name__}: {e}")
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

        from .batch_orchestrator import BatchTranscodeOrchestrator
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
            from .settings_gui import show_settings

            show_settings(main_window)

        def start_batch_transcode() -> None:
            cfg = config.load_config()
            orchestrator = BatchTranscodeOrchestrator(main_window, cfg)
            orchestrator.start_batch_workflow()

        def manage_backups() -> None:
            from .backup_dialog_orchestrator import BackupDialogOrchestrator
            orchestrator = BackupDialogOrchestrator(main_window)
            orchestrator.start_workflow()

        # Ui_MainWindow provides menu_tools
        _settings_action = main_window.menu_tools.addAction("Transcoder Settings", open_settings)
        _settings_action.setIcon(icons.Icon.VIDEO.icon())
        _settings_action.setToolTip("Configure video transcoding settings and hardware acceleration.")
        
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
_logger.info("Transcoder addon loaded")
