"""Rollback management for batch transcoding."""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from usdb_syncer import SongId
from usdb_syncer.sync_meta import ResourceFile, SyncMeta

if TYPE_CHECKING:
    from .config import TranscoderConfig

_logger = logging.getLogger(__name__)


@dataclass
class RollbackEntry:
    """Single media rollback entry."""
    song_id: SongId
    media_type: str  # "video" or "audio"
    original_path: Path  # Original media path in user directory
    rollback_backup_path: Path  # Backup in temp directory
    new_output_path: Path  # Path to transcoded file
    transcoded_at: float
    user_backup_existed: bool  # Whether user backup existed before batch

    def to_dict(self) -> dict:
        return {
            "song_id": int(self.song_id),
            "media_type": self.media_type,
            "original_path": str(self.original_path),
            "rollback_backup_path": str(self.rollback_backup_path),
            "new_output_path": str(self.new_output_path),
            "transcoded_at": self.transcoded_at,
            "user_backup_existed": self.user_backup_existed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RollbackEntry:
        return cls(
            song_id=SongId(data["song_id"]),
            media_type=data.get("media_type", "video"),
            original_path=Path(data["original_path"]),
            rollback_backup_path=Path(data["rollback_backup_path"]),
            new_output_path=Path(data["new_output_path"]),
            transcoded_at=data["transcoded_at"],
            user_backup_existed=data.get("user_backup_existed", False),
        )


class RollbackManager:
    """Manages rollback operations for batch transcoding."""

    def __init__(self, cfg: TranscoderConfig):
        """
        Args:
            cfg: Transcoder configuration
        """
        self.cfg = cfg
        self.entries: list[RollbackEntry] = []
        self._manifest_path: Optional[Path] = None
        self._rollback_dir: Optional[Path] = None

    def enable_rollback(self) -> Path:
        """Enable rollback and create temp directory.
        
        Returns:
            Path to rollback temp directory for pre-transcode backups
        """
        import tempfile
        timestamp = int(time.time())
        
        # Create unique rollback directory in system temp
        self._rollback_dir = Path(tempfile.gettempdir()) / "usdb_syncer_transcoder" / f"rollback_{timestamp}"
        self._rollback_dir.mkdir(parents=True, exist_ok=True)
        
        self._manifest_path = self._rollback_dir / f"rollback_manifest_{timestamp}.json"
        _logger.info(f"Rollback enabled. Directory: {self._rollback_dir}")
        
        return self._rollback_dir

    def get_rollback_backup_path(self, song_id: SongId, original_path: Path, *, media_type: str = "video") -> Path:
        """Generate rollback backup path for a media file.
        
        Args:
            song_id: Song ID
            original_path: Original video path
            
        Returns:
            Path where rollback backup should be stored
        """
        if not self._rollback_dir:
            raise RuntimeError("Rollback not enabled")
        
        prefix = "audio" if media_type == "audio" else "video"
        filename = f"{prefix}_{song_id}_{original_path.stem}{original_path.suffix}"
        return self._rollback_dir / filename

    def record_transcode(
        self,
        song_id: SongId,
        media_type: str,
        original_path: Path,
        rollback_backup_path: Path,
        new_output_path: Path,
        user_backup_existed: bool
    ) -> None:
        """Record a successful transcode.
        
        Args:
            song_id: Song ID
            original_path: Original video path in user directory
            rollback_backup_path: Path to rollback backup in temp dir
            new_output_path: Path to new transcoded file
            user_backup_existed: Whether user backup existed before batch
        """
        entry = RollbackEntry(
            song_id=song_id,
            media_type=media_type,
            original_path=original_path,
            rollback_backup_path=rollback_backup_path,
            new_output_path=new_output_path,
            transcoded_at=time.time(),
            user_backup_existed=user_backup_existed
        )
        self.entries.append(entry)
        self._save_manifest()

    # Backward-compatible wrapper for older callers.
    def record_video_transcode(
        self,
        song_id: SongId,
        original_path: Path,
        rollback_backup_path: Path,
        new_output_path: Path,
        user_backup_existed: bool,
    ) -> None:
        self.record_transcode(
            song_id=song_id,
            media_type="video",
            original_path=original_path,
            rollback_backup_path=rollback_backup_path,
            new_output_path=new_output_path,
            user_backup_existed=user_backup_existed,
        )

    def rollback_all(self) -> tuple[int, int, list[SongId]]:
        """Rollback all transcodes using temp backups.
        
        Returns:
            (success_count, failure_count, list_of_successful_song_ids)
        """
        success = 0
        failed = 0
        successful_ids = []

        # Rollback in reverse order
        for entry in reversed(self.entries):
            try:
                _logger.info(f"Rolling back {entry.media_type} transcode for song {entry.song_id}")
                
                # 1. Restore original from rollback temp backup
                if not entry.rollback_backup_path.exists():
                    _logger.error(f"Rollback backup missing: {entry.rollback_backup_path}")
                    failed += 1
                    continue
                
                # If transcoded file exists at original location, remove it
                if entry.original_path.exists():
                    entry.original_path.unlink()
                
                # Copy rollback backup back to original location
                shutil.copy2(str(entry.rollback_backup_path), str(entry.original_path))
                
                # 2. Delete new output if different from original location
                if entry.new_output_path.exists() and entry.new_output_path != entry.original_path:
                    entry.new_output_path.unlink()
                
                # 3. Update SyncMeta to point back to original
                self._update_sync_meta_for_rollback(entry)
                
                success += 1
                successful_ids.append(entry.song_id)
            except Exception as e:
                _logger.error(f"Rollback failed for {entry.song_id}: {e}")
                failed += 1

        self._cleanup_rollback_directory()
        return success, failed, successful_ids

    def cleanup_rollback_data(self) -> None:
        """Clean up rollback temp directory and manifest after successful batch."""
        _logger.info("Cleaning up rollback data...")
        self._cleanup_rollback_directory()

    def _cleanup_rollback_directory(self) -> None:
        """Remove entire rollback directory."""
        if self._rollback_dir and self._rollback_dir.exists():
            try:
                shutil.rmtree(self._rollback_dir)
                _logger.info(f"Deleted rollback directory: {self._rollback_dir}")
            except Exception as e:
                _logger.warning(f"Failed to delete rollback directory {self._rollback_dir}: {e}")

    def _save_manifest(self) -> None:
        """Persist manifest to disk."""
        if not self._manifest_path:
            return

        try:
            data = {
                "version": 2,
                "created_at": time.time(),
                "entries": [e.to_dict() for e in self.entries]
            }
            with self._manifest_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            _logger.error(f"Failed to save rollback manifest: {e}")

    def _update_sync_meta_for_rollback(self, entry: RollbackEntry) -> None:
        """Update SyncMeta after rollback."""
        sync_meta = self._get_sync_meta(entry.song_id)
        if not sync_meta:
            _logger.warning(f"No active SyncMeta found for song {entry.song_id} during rollback")
            return

        if entry.media_type == "audio":
            if getattr(sync_meta, "audio", None) and sync_meta.audio and sync_meta.audio.file:
                sync_meta.audio.file = ResourceFile.new(entry.original_path, sync_meta.audio.file.resource)
                sync_meta.synchronize_to_file()
                sync_meta.upsert()
                _logger.info(f"Updated SyncMeta for song {entry.song_id} to original audio")
            return

        if sync_meta.video and sync_meta.video.file:
            # Update video file info to original
            sync_meta.video.file = ResourceFile.new(entry.original_path, sync_meta.video.file.resource)
            sync_meta.synchronize_to_file()
            sync_meta.upsert()
            _logger.info(f"Updated SyncMeta for song {entry.song_id} to original video")

    def _get_sync_meta(self, song_id: SongId) -> Optional[SyncMeta]:
        """Get SyncMeta for song ID."""
        from usdb_syncer import settings
        for meta in SyncMeta.get_in_folder(settings.get_song_dir()):
            if meta.song_id == song_id:
                return meta
        return None
