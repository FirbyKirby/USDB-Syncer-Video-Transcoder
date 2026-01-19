"""SyncMeta update logic after transcoding.

CRITICAL: This module MUST properly update sync metadata to prevent re-download loops.
See Section 4 "Critical: Sync Tracking Integration" in the architecture document.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from usdb_syncer.db import JobStatus
from usdb_syncer.sync_meta import Resource, ResourceFile
from usdb_syncer.utils import get_mtime

if TYPE_CHECKING:
    from usdb_syncer.logger import SongLogger
    from usdb_syncer.usdb_song import UsdbSong

_logger = logging.getLogger(__name__)


def update_sync_meta_video(
    song: UsdbSong,
    original_video_path: Path,
    transcoded_video_path: Path,
    codec: str,
    profile: str,
    slog: SongLogger,
    backup_source: bool = True,
    backup_suffix: str = "-source",
) -> bool:
    """Update SyncMeta with transcoded video file information.

    CRITICAL: This function MUST be called after successful transcoding to prevent
    re-download loops. It updates fname, mtime, preserves resource ID, and persists.

    Args:
        song: The UsdbSong object with sync_meta
        original_video_path: Path to the original downloaded video
        transcoded_video_path: Path to the transcoded video file
        codec: Codec used for transcoding (e.g., "h264")
        profile: Encoding profile used (e.g., "baseline")
        slog: Song-specific logger
        backup_source: If True, rename original to <name><suffix>.<ext>
        backup_suffix: Suffix to use for backup (e.g., "-source")

    Returns:
        True if update succeeded, False otherwise
    """
    if not song.sync_meta:
        slog.error("Cannot update SyncMeta - no sync_meta present")
        return False

    # Verify transcoded file exists
    if not transcoded_video_path.exists():
        slog.error(f"Cannot update SyncMeta - transcoded file not found: {transcoded_video_path}")
        return False

    sync_meta = song.sync_meta

    # CRITICAL: Get the ORIGINAL resource identifier - must preserve this!
    original_resource_id = ""
    if sync_meta.video and sync_meta.video.file:
        original_resource_id = sync_meta.video.file.resource
        slog.debug(f"Preserving original resource ID: {original_resource_id[:50]}...")

    # Backup original if requested
    source_backup_name = None
    if backup_source and original_video_path.exists():
        # Rename to pattern using backup_suffix
        source_backup_path = original_video_path.with_name(
            f"{original_video_path.stem}{backup_suffix}{original_video_path.suffix}"
        )
        try:
            original_video_path.rename(source_backup_path)
            source_backup_name = source_backup_path.name
            slog.debug(f"Preserved source video: {source_backup_name}")
        except OSError as e:
            slog.warning(f"Could not backup source video: {e}")

    # Create new ResourceFile with CORRECT values
    new_resource_file = ResourceFile(
        fname=transcoded_video_path.name,             # NEW filename
        mtime=get_mtime(transcoded_video_path),        # NEW mtime in microseconds
        resource=original_resource_id,                 # PRESERVED resource ID
    )

    # Create new Resource with the file
    new_resource = Resource(
        status=JobStatus.SUCCESS,
        file=new_resource_file,
    )

    # Update the video resource in sync_meta
    sync_meta.video = new_resource

    # Store transcoding metadata in custom_data (values must be strings!)
    sync_meta.custom_data.set("transcoder_source_fname", source_backup_name or original_video_path.name)
    sync_meta.custom_data.set("transcoder_output_fname", transcoded_video_path.name)
    sync_meta.custom_data.set("transcoder_codec", codec)
    sync_meta.custom_data.set("transcoder_profile", profile)
    sync_meta.custom_data.set("transcoder_timestamp", str(time.time()))

    # Update .txt file #VIDEO header
    txt_path = sync_meta.txt_path()
    if txt_path and txt_path.exists():
        if not update_txt_video_header(txt_path, transcoded_video_path.name, slog):
            slog.warning("Could not update .txt #VIDEO header")

    try:
        # CRITICAL: Persist to file first (writes JSON, updates mtime)
        sync_meta.synchronize_to_file()

        # Then update database
        sync_meta.upsert()

        slog.info(f"SyncMeta updated: {transcoded_video_path.name} "
                  f"(mtime={new_resource_file.mtime})")
        return True

    except Exception as e:
        slog.error(f"Failed to update SyncMeta: {type(e).__name__}: {e}")
        _logger.debug(None, exc_info=True)
        return False


def update_sync_meta_audio(
    song: UsdbSong,
    original_audio_path: Path,
    transcoded_audio_path: Path,
    codec: str,
    slog: SongLogger,
    backup_source: bool = True,
    backup_suffix: str = "-source",
) -> bool:
    """Update SyncMeta with transcoded audio file information.

    This is the audio analogue of
    [`update_sync_meta_video()`](sync_meta_updater.py:25).

    CRITICAL: Resource IDs must be preserved, and `mtime` must be updated in
    microseconds to prevent re-download loops.

    Additionally updates the song text headers:
    - `#AUDIO:` (newer)
    - `#MP3:` (legacy compatibility)
    """

    if not song.sync_meta:
        slog.error("Cannot update SyncMeta - no sync_meta present")
        return False

    # Verify transcoded file exists
    if not transcoded_audio_path.exists():
        slog.error(f"Cannot update SyncMeta - transcoded file not found: {transcoded_audio_path}")
        return False

    sync_meta = song.sync_meta

    # Preserve original resource ID
    original_resource_id = ""
    if getattr(sync_meta, "audio", None) and sync_meta.audio.file:
        original_resource_id = sync_meta.audio.file.resource
        slog.debug(f"Preserving original audio resource ID: {original_resource_id[:50]}...")

    # Backup original if requested
    source_backup_name = None
    if backup_source and original_audio_path.exists():
        source_backup_path = original_audio_path.with_name(
            f"{original_audio_path.stem}{backup_suffix}{original_audio_path.suffix}"
        )
        try:
            original_audio_path.rename(source_backup_path)
            source_backup_name = source_backup_path.name
            slog.debug(f"Preserved source audio: {source_backup_name}")
        except OSError as e:
            slog.warning(f"Could not backup source audio: {e}")

    # Create new ResourceFile with correct values
    new_resource_file = ResourceFile(
        fname=transcoded_audio_path.name,
        mtime=get_mtime(transcoded_audio_path),
        resource=original_resource_id,
    )
    new_resource = Resource(
        status=JobStatus.SUCCESS,
        file=new_resource_file,
    )

    # Update the audio resource in sync_meta
    try:
        sync_meta.audio = new_resource
    except Exception as e:
        slog.error(f"Cannot update SyncMeta audio section: {type(e).__name__}: {e}")
        return False

    # Store audio transcoding metadata (separate keys from video)
    sync_meta.custom_data.set("transcoder_audio_source_fname", source_backup_name or original_audio_path.name)
    sync_meta.custom_data.set("transcoder_audio_output_fname", transcoded_audio_path.name)
    sync_meta.custom_data.set("transcoder_audio_codec", codec)
    sync_meta.custom_data.set("transcoder_audio_timestamp", str(time.time()))

    # Update song .txt headers
    txt_path = sync_meta.txt_path()
    if txt_path and txt_path.exists():
        if not update_txt_audio_headers(txt_path, transcoded_audio_path.name, slog):
            slog.warning("Could not update .txt #AUDIO/#MP3 headers")

    try:
        sync_meta.synchronize_to_file()
        sync_meta.upsert()
        slog.info(
            f"SyncMeta audio updated: {transcoded_audio_path.name} "
            f"(mtime={new_resource_file.mtime})"
        )
        return True
    except Exception as e:
        slog.error(f"Failed to update SyncMeta audio: {type(e).__name__}: {e}")
        _logger.debug(None, exc_info=True)
        return False


def update_txt_video_header(txt_path: Path, video_filename: str, slog: SongLogger) -> bool:
    """Update #VIDEO: tag in the song's .txt file.

    Args:
        txt_path: Path to the .txt file
        video_filename: New video filename to set
        slog: Song-specific logger

    Returns:
        True if updated successfully
    """
    try:
        content = txt_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        updated = False

        for i, line in enumerate(lines):
            if line.upper().startswith("#VIDEO:"):
                lines[i] = f"#VIDEO:{video_filename}"
                updated = True
                slog.debug(f"Updated #VIDEO header: {video_filename}")
                break

        if not updated:
            # Insert #VIDEO tag - find first non-header line
            insert_idx = 0
            for i, line in enumerate(lines):
                if line and not line.startswith("#"):
                    insert_idx = i
                    break
                insert_idx = i + 1

            lines.insert(insert_idx, f"#VIDEO:{video_filename}")
            slog.debug(f"Inserted #VIDEO header: {video_filename}")
            updated = True

        if updated:
            txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True

        return False

    except Exception as e:
        slog.warning(f"Could not update .txt file: {type(e).__name__}: {e}")
        return False


def update_txt_audio_headers(txt_path: Path, audio_filename: str, slog: SongLogger) -> bool:
    """Update `#AUDIO:` and `#MP3:` tags in the song's .txt file.

    USDB Syncer maintains both headers for compatibility across different
    karaoke engines and historical formats.

    Returns True if the file was updated successfully.
    """
    try:
        content = txt_path.read_text(encoding="utf-8")
        lines = content.splitlines()

        def set_or_insert(tag: str, value: str) -> bool:
            upper_tag = tag.upper()
            for idx, line in enumerate(lines):
                if line.upper().startswith(upper_tag):
                    lines[idx] = f"{tag}{value}"
                    return True

            # Insert after the last header line, before the first non-header line.
            insert_idx = 0
            for i, line in enumerate(lines):
                if line and not line.startswith("#"):
                    insert_idx = i
                    break
                insert_idx = i + 1

            lines.insert(insert_idx, f"{tag}{value}")
            return True

        updated_a = set_or_insert("#AUDIO:", audio_filename)
        updated_m = set_or_insert("#MP3:", audio_filename)

        if updated_a or updated_m:
            txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            slog.debug(f"Updated #AUDIO/#MP3 headers: {audio_filename}")
            return True
        return False

    except Exception as e:
        slog.warning(f"Could not update audio headers in .txt file: {type(e).__name__}: {e}")
        return False


def check_already_transcoded(song: UsdbSong, target_codec: str) -> bool:
    """Check if video was already transcoded to target codec.

    Args:
        song: The UsdbSong to check
        target_codec: Target codec to check for

    Returns:
        True if already transcoded and output file exists
    """
    if not song.sync_meta:
        return False

    sync_meta = song.sync_meta

    # Check custom_data for previous transcode
    previous_codec = sync_meta.custom_data.get("transcoder_codec")
    if previous_codec != target_codec:
        return False

    # Verify output file still exists
    output_fname = sync_meta.custom_data.get("transcoder_output_fname")
    if not output_fname:
        return False

    output_path = sync_meta.path.parent / output_fname
    return output_path.exists()
