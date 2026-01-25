"""Test cache pruning functionality."""

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .audio_normalizer import LoudnormMeasurements
from .loudness_cache import LoudnessCache, TargetSettings


class TestCachePruning:
    """Test the cache pruning functionality."""

    def test_prune_orphans_removes_deleted_songs(self):
        """Test that prune_orphans removes entries for songs that no longer exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Create some test data
            target_settings = TargetSettings(
                normalization_method="loudnorm",
                target_i=-23.0,
                target_tp=-2.0,
                target_lra=7.0,
                tolerance_preset="balanced"
            )

            measurements = LoudnormMeasurements(
                measured_I=-20.0,
                measured_TP=-1.5,
                measured_LRA=6.0,
                measured_thresh=-30.0,
                offset=3.0,
                raw={}
            )

            # Create a temporary file to simulate a real file
            temp_file = Path(temp_dir) / "test_audio.mp3"
            temp_file.write_text("fake audio content")

            # Put entries with different song_ids
            cache.put(temp_file, target_settings, measurements, song_id=1)  # Valid song
            cache.put(temp_file, target_settings, measurements, song_id=2)  # Will be deleted
            cache.put(temp_file, target_settings, measurements, song_id=3)  # Will be deleted
            cache.put(temp_file, target_settings, measurements, song_id=None)  # No song_id, should be kept

            # Verify entries were added
            assert cache._connection is not None
            cursor = cache._connection.execute("SELECT COUNT(*) FROM loudnorm_analysis")
            count_before = cursor.fetchone()[0]
            assert count_before == 4

            # Mock the USDB Syncer database access
            with patch('usdb_syncer.db.connect'), \
                 patch('usdb_syncer.utils.AppPaths') as mock_app_paths, \
                 patch('usdb_syncer.usdb_song.UsdbSong') as mock_usdb_song:

                # Mock AppPaths.db to return a fake path
                mock_app_paths.db = MagicMock()

                # Mock UsdbSong.get_all() to return only song_id=1
                mock_song = MagicMock()
                mock_song.song_id = 1
                mock_usdb_song.get_all.return_value = [mock_song]

                # Run pruning
                cache.prune_orphans()

            # Verify that entries for song_ids 2 and 3 were removed, but 1 and None were kept
            cursor = cache._connection.execute("SELECT COUNT(*) FROM loudnorm_analysis")
            count_after = cursor.fetchone()[0]
            assert count_after == 2  # song_id=1 and song_id=NULL

            # Verify specific entries
            cursor = cache._connection.execute("SELECT song_id FROM loudnorm_analysis ORDER BY song_id")
            remaining_song_ids = [row[0] for row in cursor.fetchall()]
            assert remaining_song_ids == [None, 1]

    def test_prune_orphans_removes_missing_files(self):
        """Test that prune_orphans removes entries for files that no longer exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Create some test data
            target_settings = TargetSettings(
                normalization_method="loudnorm",
                target_i=-23.0,
                target_tp=-2.0,
                target_lra=7.0,
                tolerance_preset="balanced"
            )

            measurements = LoudnormMeasurements(
                measured_I=-20.0,
                measured_TP=-1.5,
                measured_LRA=6.0,
                measured_thresh=-30.0,
                offset=3.0,
                raw={}
            )

            # Create temporary files
            existing_file = Path(temp_dir) / "existing.mp3"
            existing_file.write_text("fake audio content")

            missing_file = Path(temp_dir) / "missing.mp3"
            # Don't create the missing file

            # Put entries
            cache.put(existing_file, target_settings, measurements, song_id=1)
            cache.put(missing_file, target_settings, measurements, song_id=2)

            # Verify entries were added
            cursor = cache._connection.execute("SELECT COUNT(*) FROM loudnorm_analysis")
            count_before = cursor.fetchone()[0]
            assert count_before == 2

            # Mock the USDB Syncer database access
            with patch('usdb_syncer.db.connect'), \
                 patch('usdb_syncer.utils.AppPaths') as mock_app_paths, \
                 patch('usdb_syncer.usdb_song.UsdbSong') as mock_usdb_song:

                # Mock AppPaths.db
                mock_app_paths.db = MagicMock()

                # Mock UsdbSong.get_all() to return both songs (so song pruning doesn't remove anything)
                mock_song1 = MagicMock()
                mock_song1.song_id = 1
                mock_song2 = MagicMock()
                mock_song2.song_id = 2
                mock_usdb_song.get_all.return_value = [mock_song1, mock_song2]

                # Run pruning
                cache.prune_orphans()

            # Verify that only the missing file entry was removed
            cursor = cache._connection.execute("SELECT COUNT(*) FROM loudnorm_analysis")
            count_after = cursor.fetchone()[0]
            assert count_after == 1

            # Verify the remaining entry is for the existing file
            cursor = cache._connection.execute("SELECT path FROM loudnorm_analysis")
            remaining_paths = [row[0] for row in cursor.fetchall()]
            assert len(remaining_paths) == 1
            assert str(existing_file) in remaining_paths[0]

    def test_prune_orphans_handles_errors_gracefully(self):
        """Test that prune_orphans handles database errors gracefully."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Mock the USDB Syncer imports to raise an exception
            with patch('usdb_syncer.db.connect', side_effect=Exception("Database error")), \
                 patch('usdb_syncer.utils.AppPaths') as mock_app_paths, \
                 patch('usdb_syncer.usdb_song.UsdbSong') as mock_usdb_song:

                # This should not raise an exception
                cache.prune_orphans()

            # Cache should still be functional
            assert cache._connection is not None