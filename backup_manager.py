from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

from usdb_syncer import SongId, settings
from usdb_syncer.logger import song_logger
from usdb_syncer.sync_meta import SyncMeta
from .config import TranscoderConfig

_logger = logging.getLogger(__name__)


@dataclass
class BackupInfo:
    """Information about a discovered backup file."""
    song_id: SongId
    song_title: str
    artist: str
    media_type: Literal["video", "audio"]
    backup_path: Path
    active_media_path: Path  # The current transcoded/active media file
    size_mb: float
    backup_date: Optional[float]  # mtime timestamp
    
    # Runtime state
    selected: bool = False
    deletion_status: Literal["pending", "deleting", "deleted", "failed", "skipped"] = "pending"
    restore_status: Literal["pending", "restoring", "restored", "failed", "skipped"] = "pending"
    error_message: Optional[str] = None

    @property
    def active_video_path(self) -> Path:
        """Backward-compatible alias (legacy callers treat all backups as video)."""
        return self.active_media_path


@dataclass
class BackupRestoreResult:
    """Result of a backup restoration operation."""
    success: bool
    backups_restored: int
    backups_failed: int
    errors: list[tuple[SongId, str]]  # (song_id, error_message)


@dataclass
class BackupDeletionResult:
    """Result of a backup deletion operation."""
    success: bool
    backups_deleted: int
    backups_failed: int
    total_space_freed_mb: float
    errors: list[tuple[SongId, str]]  # (song_id, error_message)


def discover_backups(
    cfg: TranscoderConfig,
    cancel_check: Optional[Callable[[], bool]] = None
) -> list[BackupInfo]:
    """Discover all persistent backup files (video and audio) in the song library."""
    backups: list[BackupInfo] = []
    song_dir = settings.get_song_dir()
    suffix = cfg.general.backup_suffix
    
    for sync_meta in SyncMeta.get_in_folder(song_dir):
        if cancel_check and cancel_check():
            _logger.info("Backup discovery cancelled by user")
            break
            
        # Video
        video_path = (
            sync_meta.path.parent / sync_meta.video.file.fname
            if sync_meta.video and sync_meta.video.file and sync_meta.video.file.fname
            else None
        )
        if video_path and video_path.exists():
            _discover_for_media(
                backups=backups,
                sync_meta=sync_meta,
                media_type="video",
                active_media_path=video_path,
                backup_suffix=suffix,
            )

        # Audio
        audio_path = (
            sync_meta.path.parent / sync_meta.audio.file.fname
            if getattr(sync_meta, "audio", None)
            and sync_meta.audio
            and sync_meta.audio.file
            and sync_meta.audio.file.fname
            else None
        )
        if audio_path and audio_path.exists():
            _discover_for_media(
                backups=backups,
                sync_meta=sync_meta,
                media_type="audio",
                active_media_path=audio_path,
                backup_suffix=suffix,
            )
    
    return backups


def _discover_for_media(
    *,
    backups: list[BackupInfo],
    sync_meta: SyncMeta,
    media_type: Literal["video", "audio"],
    active_media_path: Path,
    backup_suffix: str,
) -> None:
    """Populate `backups` with discovered backups for a single media file."""
    # Check custom_data first (most reliable)
    if media_type == "video":
        source_key = "transcoder_source_fname"
    else:
        source_key = "transcoder_audio_source_fname"

    source_fname = sync_meta.custom_data.get(source_key)
    if source_fname:
        backup_path = sync_meta.path.parent / source_fname
        if backup_path.exists() and validate_backup(backup_path, active_media_path, media_type=media_type):
            backups.append(_create_backup_info(sync_meta, media_type, backup_path, active_media_path))
            return

    # Fallback: glob pattern search (stem + suffix + any extension)
    pattern = f"{active_media_path.stem}{backup_suffix}*"
    for candidate in sync_meta.path.parent.glob(pattern):
        if candidate != active_media_path and validate_backup(candidate, active_media_path, media_type=media_type):
            # Avoid duplicates
            if not any(b.backup_path == candidate for b in backups):
                backups.append(_create_backup_info(sync_meta, media_type, candidate, active_media_path))


def _create_backup_info(
    sync_meta: SyncMeta,
    media_type: Literal["video", "audio"],
    backup_path: Path,
    active_media_path: Path,
) -> BackupInfo:
    """Create a BackupInfo object from a SyncMeta and backup path."""
    from usdb_syncer.usdb_song import UsdbSong
    song = UsdbSong.get(sync_meta.song_id)
    stat = backup_path.stat()
    return BackupInfo(
        song_id=sync_meta.song_id,
        song_title=song.title if song else (sync_meta.custom_data.get("title") or "Unknown Title"),
        artist=song.artist if song else (sync_meta.custom_data.get("artist") or "Unknown Artist"),
        media_type=media_type,
        backup_path=backup_path,
        active_media_path=active_media_path,
        size_mb=stat.st_size / (1024 * 1024),
        backup_date=stat.st_mtime,
    )


def validate_backup(
    backup_path: Path,
    active_media_path: Path,
    *,
    media_type: Literal["video", "audio"] = "video",
) -> bool:
    """Validate that a file is actually a backup (not the active media file)."""
    if backup_path == active_media_path:
        return False
    if not backup_path.exists() or not backup_path.is_file():
        return False
    if not active_media_path.exists():
        _logger.warning(f"Active {media_type} missing for backup: {backup_path}")
        return False
    return True


def delete_backup(
    backup_info: BackupInfo,
    update_sync_meta: bool = True
) -> bool:
    """Delete a backup file and optionally update SyncMeta."""
    slog = song_logger(backup_info.song_id)
    
    try:
        # Double-check validation
        if not validate_backup(
            backup_info.backup_path,
            backup_info.active_media_path,
            media_type=backup_info.media_type,
        ):
            backup_info.error_message = "Validation failed"
            slog.error(f"Backup validation failed: {backup_info.backup_path}")
            return False
        
        # Check write permission on parent directory (required for deletion on POSIX)
        parent_dir = backup_info.backup_path.parent
        if not os.access(parent_dir, os.W_OK):
            backup_info.error_message = f"No write permission to delete backup in {parent_dir}"
            slog.error(f"No write permission for directory: {parent_dir}")
            return False
        
        # Delete the file
        backup_info.backup_path.unlink()
        slog.info(f"Deleted {backup_info.media_type} backup: {backup_info.backup_path.name}")
        
        # Update SyncMeta if requested
        if update_sync_meta:
            sync_meta = None
            for meta in SyncMeta.get_in_folder(settings.get_song_dir()):
                if meta.song_id == backup_info.song_id:
                    sync_meta = meta
                    break
            if sync_meta:
                if backup_info.media_type == "video":
                    source_key = "transcoder_source_fname"
                else:
                    source_key = "transcoder_audio_source_fname"

                source_fname = sync_meta.custom_data.get(source_key)
                if source_fname == backup_info.backup_path.name:
                    sync_meta.custom_data.set(source_key, None)
                    sync_meta.synchronize_to_file()
                    sync_meta.upsert()
                    slog.debug(f"Cleared {source_key} from sync_meta")
        
        return True
        
    except (PermissionError, OSError) as e:
        backup_info.error_message = str(e)
        slog.error(f"Error deleting backup: {e}")
        return False
    except Exception as e:
        backup_info.error_message = str(e)
        slog.error(f"Unexpected error: {e}")
        _logger.debug(None, exc_info=True)
        return False


def delete_backups_batch(
    backups: list[BackupInfo],
    progress_callback: Optional[Callable[[int, int, BackupInfo], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> BackupDeletionResult:
    """Delete multiple backups with progress tracking."""
    deleted = 0
    failed = 0
    total_freed_mb = 0.0
    errors: list[tuple[SongId, str]] = []
    
    for i, backup in enumerate(backups):
        # Check for cancellation
        if cancel_check and cancel_check():
            _logger.info("Backup deletion cancelled by user")
            break
        
        # Update progress (start)
        backup.deletion_status = "deleting"
        if progress_callback:
            progress_callback(i, len(backups), backup)
        
        # Delete the backup
        if delete_backup(backup):
            backup.deletion_status = "deleted"
            deleted += 1
            total_freed_mb += backup.size_mb
        else:
            backup.deletion_status = "failed"
            failed += 1
            error_msg = backup.error_message or "Unknown error"
            errors.append((backup.song_id, error_msg))

        # Update progress (completion)
        if progress_callback:
            progress_callback(i, len(backups), backup)
    
    return BackupDeletionResult(
        success=(failed == 0),
        backups_deleted=deleted,
        backups_failed=failed,
        total_space_freed_mb=total_freed_mb,
        errors=errors
    )


def restore_backup(
    backup_info: BackupInfo,
) -> bool:
    """Restore a backup file by replacing the active media file (video or audio)."""
    import shutil
    import time
    import tempfile
    from usdb_syncer.utils import get_mtime
    slog = song_logger(backup_info.song_id)
    
    temp_path: Optional[Path] = None
    try:
        # 1. Validation and permission checks
        if not validate_backup(
            backup_info.backup_path,
            backup_info.active_media_path,
            media_type=backup_info.media_type,
        ):
            backup_info.error_message = "Validation failed"
            slog.error(f"Backup validation failed: {backup_info.backup_path}")
            return False

        if not os.access(backup_info.backup_path, os.R_OK):
            backup_info.error_message = "No read permission on backup"
            slog.error(f"No read permission for backup: {backup_info.backup_path}")
            return False

        dest_dir = backup_info.active_media_path.parent
        if not os.access(dest_dir, os.W_OK):
            backup_info.error_message = "No write permission on destination"
            slog.error(f"No write permission for directory: {dest_dir}")
            return False

        # 2. Identify original and transcoded paths
        # The backup file preserves the original extension.
        # We restore to the original filename by using the active video's base name
        # but with the backup's original extension.
        original_media_path = backup_info.active_media_path.with_suffix(backup_info.backup_path.suffix)
        active_media_path = backup_info.active_media_path
        
        # 3. Create safety backup of current active video if it exists
        safety_path: Optional[Path] = None
        if active_media_path.exists():
            timestamp = int(time.time())
            safety_path = active_media_path.with_suffix(
                f".safety-{timestamp}{active_media_path.suffix}"
            )
            shutil.copy2(active_media_path, safety_path)
            slog.info(f"Created safety backup ({backup_info.media_type}): {safety_path.name}")

        # 4. Atomic replacement pattern
        # Copy backup to a temporary file in the same directory
        with tempfile.NamedTemporaryFile(
            dir=dest_dir,
            suffix=original_media_path.suffix,
            delete=False
        ) as tmp:
            temp_path = Path(tmp.name)
        
        shutil.copy2(backup_info.backup_path, temp_path)
        
        # Atomic replacement to the ORIGINAL filename
        os.replace(temp_path, original_media_path)
        temp_path = None # Successfully replaced
        slog.info(f"Restored {backup_info.media_type} backup to: {original_media_path.name}")

        # 5. Cleanup transcoded video if it had a different name/extension
        if active_media_path != original_media_path and active_media_path.exists():
            active_media_path.unlink()
            slog.info(f"Deleted active {backup_info.media_type}: {active_media_path.name}")

        # 6. Update SyncMeta
        sync_meta = None
        for meta in SyncMeta.get_in_folder(settings.get_song_dir()):
            if meta.song_id == backup_info.song_id:
                sync_meta = meta
                break
        
        if sync_meta:
            if backup_info.media_type == "video":
                if sync_meta.video and sync_meta.video.file:
                    # Update mtime and filename to match the restored file
                    sync_meta.video.file.mtime = get_mtime(original_media_path)
                    sync_meta.video.file.fname = original_media_path.name

                    # Clear transcoding metadata (video)
                    sync_meta.custom_data.set("transcoder_codec", None)
                    sync_meta.custom_data.set("transcoder_profile", None)
                    sync_meta.custom_data.set("transcoder_timestamp", None)
                    sync_meta.custom_data.set("transcoder_output_fname", None)
                    sync_meta.custom_data.set("transcoder_source_fname", None)

                    sync_meta.synchronize_to_file()
                    sync_meta.upsert()
                    slog.debug("Updated sync_meta after video restoration")

                    # Update .txt file #VIDEO header if filename changed
                    txt_path = sync_meta.txt_path()
                    if txt_path and txt_path.exists():
                        from .sync_meta_updater import update_txt_video_header
                        if not update_txt_video_header(txt_path, original_media_path.name, slog):
                            slog.warning("Could not update .txt #VIDEO header after restore")
                else:
                    slog.warning(
                        "Could not update metadata after restore: video metadata missing. Song may show as out-of-sync."
                    )
            else:
                if getattr(sync_meta, "audio", None) and sync_meta.audio and sync_meta.audio.file:
                    sync_meta.audio.file.mtime = get_mtime(original_media_path)
                    sync_meta.audio.file.fname = original_media_path.name

                    # Clear transcoding metadata (audio)
                    sync_meta.custom_data.set("transcoder_audio_codec", None)
                    sync_meta.custom_data.set("transcoder_audio_timestamp", None)
                    sync_meta.custom_data.set("transcoder_audio_output_fname", None)
                    sync_meta.custom_data.set("transcoder_audio_source_fname", None)

                    sync_meta.synchronize_to_file()
                    sync_meta.upsert()
                    slog.debug("Updated sync_meta after audio restoration")

                    # Update .txt file headers (#AUDIO and #MP3)
                    txt_path = sync_meta.txt_path()
                    if txt_path and txt_path.exists():
                        from .sync_meta_updater import update_txt_audio_headers
                        if not update_txt_audio_headers(txt_path, original_media_path.name, slog):
                            slog.warning("Could not update .txt #AUDIO/#MP3 headers after restore")
                else:
                    slog.warning(
                        "Could not update metadata after restore: audio metadata missing. Song may show as out-of-sync."
                    )
        else:
            slog.warning("Could not update metadata after restore: SyncMeta not found. Song may show as out-of-sync.")

        # 7. Cleanup: Delete the backup file and safety backup after successful restore
        # We set the status here so the cleanup logic can verify it
        backup_info.restore_status = "restored"

        if backup_info.restore_status == "restored":
            # Delete the backup file
            try:
                if backup_info.backup_path.exists():
                    backup_info.backup_path.unlink()
                    slog.info(
                        f"Deleted {backup_info.media_type} backup file after successful restore: {backup_info.backup_path.name}"
                    )
            except Exception as e:
                slog.warning(f"Failed to delete backup after restore: {e}")

            # Delete the safety backup (user considers it a leftover if restore succeeded)
            try:
                if safety_path and safety_path.exists():
                    safety_path.unlink()
                    slog.info(f"Deleted safety backup after successful restore: {safety_path.name}")
            except Exception as e:
                slog.warning(f"Failed to delete safety backup after restore: {e}")

            # Also clear transcoder_source_fname since the backup no longer exists
            if sync_meta:
                try:
                    if backup_info.media_type == "video":
                        sync_meta.custom_data.set("transcoder_source_fname", None)
                    else:
                        sync_meta.custom_data.set("transcoder_audio_source_fname", None)
                    sync_meta.synchronize_to_file()
                    sync_meta.upsert()
                except Exception as e:
                    slog.warning(f"Failed to update SyncMeta during cleanup: {e}")

        return True

    except (PermissionError, OSError) as e:
        backup_info.error_message = str(e)
        slog.error(f"Error restoring backup: {e}")
        return False
    except Exception as e:
        backup_info.error_message = str(e)
        slog.error(f"Unexpected error during restore: {e}")
        _logger.debug(None, exc_info=True)
        return False
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def restore_backups_batch(
    backups: list[BackupInfo],
    progress_callback: Optional[Callable[[int, int, BackupInfo], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> BackupRestoreResult:
    """Restore multiple backups with progress tracking."""
    restored = 0
    failed = 0
    errors: list[tuple[SongId, str]] = []
    
    for i, backup in enumerate(backups):
        # Check for cancellation
        if cancel_check and cancel_check():
            _logger.info("Backup restoration cancelled by user")
            break
        
        # Update progress (start)
        backup.restore_status = "restoring"
        if progress_callback:
            progress_callback(i, len(backups), backup)
        
        # Restore the backup
        if restore_backup(backup):
            backup.restore_status = "restored"
            restored += 1
        else:
            backup.restore_status = "failed"
            failed += 1
            error_msg = backup.error_message or "Unknown error"
            errors.append((backup.song_id, error_msg))

        # Update progress (completion)
        if progress_callback:
            progress_callback(i, len(backups), backup)
    
    return BackupRestoreResult(
        success=(failed == 0),
        backups_restored=restored,
        backups_failed=failed,
        errors=errors
    )
