"""Test learned performance system for E6 implementation.

This module tests the cache infrastructure, performance recording, and speed estimation
functionality added in the E6 phase.
"""

import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .audio_normalizer import LoudnormMeasurements, LoudnormTargets, analyze_loudnorm_two_pass
from .loudness_cache import LoudnessCache, TargetSettings


class TestLearnedPerformance:
    """Test the learned performance tracking system."""

    def test_record_analysis_performance_normal_data(self):
        """Test record_analysis_performance with normal data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Record some performance data
            cache.record_analysis_performance(10.0, 5.0, 'test_kind', 'test_sig')

            # Verify it was stored
            conn = cache._connection
            cursor = conn.execute("""
                SELECT kind, signature, count, ema_speed_x, p10_speed_x, samples_json
                FROM performance_stats WHERE kind = ? AND signature = ?
            """, ('test_kind', 'test_sig'))
            row = cursor.fetchone()
            assert row is not None
            kind, signature, count, ema_speed_x, p10_speed_x, samples_json = row
            assert kind == 'test_kind'
            assert signature == 'test_sig'
            assert count == 1
            assert ema_speed_x == 2.0  # 10.0 / 5.0
            assert p10_speed_x == 2.0
            samples = json.loads(samples_json)
            assert samples == [2.0]

    def test_record_analysis_performance_outlier_filtering(self):
        """Test that outliers are filtered out."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Too short duration
            cache.record_analysis_performance(3.0, 1.0)
            # Negative wall time
            cache.record_analysis_performance(10.0, 0.0)
            # Extreme speed
            cache.record_analysis_performance(10.0, 0.5)  # speed = 20.0 > 10.0
            cache.record_analysis_performance(10.0, 100.0)  # speed = 0.1 < 0.1

            # Verify no data was stored
            cursor = cache._connection.execute("SELECT COUNT(*) FROM performance_stats")
            count = cursor.fetchone()[0]
            assert count == 0

    def test_ema_calculation_multiple_samples(self):
        """Test EMA calculation after multiple samples."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Record multiple samples
            speeds = [2.0, 3.0, 1.5, 2.5]
            for speed in speeds:
                cache.record_analysis_performance(10.0, 10.0 / speed)

            # Check EMA calculation
            cursor = cache._connection.execute("""
                SELECT ema_speed_x FROM performance_stats
                WHERE kind = 'audio_loudnorm_analysis' AND signature = ''
            """)
            row = cursor.fetchone()
            assert row is not None
            ema = row[0]

            # Manual EMA calculation with alpha=0.2
            alpha = 0.2
            expected_ema = speeds[0]
            for speed in speeds[1:]:
                expected_ema = alpha * speed + (1 - alpha) * expected_ema

            assert abs(ema - expected_ema) < 1e-6

    def test_p10_percentile_calculation(self):
        """Test p10 percentile calculation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Record samples that will sort to: 1.0, 1.5, 2.0, 2.5, 3.0
            speeds = [2.0, 3.0, 1.0, 2.5, 1.5]
            for speed in speeds:
                cache.record_analysis_performance(10.0, 10.0 / speed)

            cursor = cache._connection.execute("""
                SELECT p10_speed_x FROM performance_stats
                WHERE kind = 'audio_loudnorm_analysis' AND signature = ''
            """)
            row = cursor.fetchone()
            assert row is not None
            p10 = row[0]

            # p10 should be the 10th percentile of sorted samples
            sorted_speeds = sorted(speeds)
            expected_p10 = sorted_speeds[int(0.1 * (len(sorted_speeds) - 1))]  # index 0 for 5 samples
            assert p10 == expected_p10

    def test_get_analysis_speed_estimate_sufficient_samples(self):
        """Test get_analysis_speed_estimate with sufficient samples."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Add 5 samples
            for i in range(5):
                cache.record_analysis_performance(10.0, 5.0)  # speed = 2.0

            estimate, count, status = cache.get_analysis_speed_estimate()
            assert estimate == 2.0  # p10 = 2.0, min(1.0, 2.0) = 2.0 but wait, conservative is min(1.0, p10)
            # Wait, p10 is 2.0, but conservative = min(1.0, p10) = 1.0
            assert estimate == 1.0
            assert count == 5
            assert status == 'learned'

    def test_get_analysis_speed_estimate_insufficient_samples(self):
        """Test get_analysis_speed_estimate with insufficient samples."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Add only 3 samples
            for i in range(3):
                cache.record_analysis_performance(10.0, 5.0)

            estimate, count, status = cache.get_analysis_speed_estimate()
            assert estimate is None
            assert count == 3
            assert status == 'insufficient_data'

    def test_get_analysis_speed_estimate_conservative_capping(self):
        """Test that conservative estimate is capped at 1.0x realtime."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Add samples with high speeds
            for i in range(5):
                cache.record_analysis_performance(10.0, 2.0)  # speed = 5.0

            estimate, count, status = cache.get_analysis_speed_estimate()
            assert estimate == 1.0  # capped at 1.0
            assert count == 5
            assert status == 'learned'

    def test_performance_recording_integration_with_cache(self):
        """Test that analyze_loudnorm_two_pass records performance when cache is provided."""
        # This would require mocking ffmpeg, which is complex
        # For now, test the logic by mocking the function
        pass  # TODO: Implement with proper mocking

    def test_performance_not_recorded_when_cache_none(self):
        """Test that performance is not recorded when cache is None."""
        # Mock analyze_loudnorm_two_pass to check if record_analysis_performance is called
        pass  # TODO: Implement

    def test_performance_only_recorded_on_success(self):
        """Test that performance is only recorded on successful analysis."""
        # Test that exceptions prevent recording
        pass  # TODO: Implement

    def test_schema_performance_stats_table_created(self):
        """Test that the performance_stats table is created correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Check table exists
            cursor = cache._connection.execute("""
                SELECT name FROM sqlite_master WHERE type='table' AND name='performance_stats'
            """)
            assert cursor.fetchone() is not None

            # Check columns
            cursor = cache._connection.execute("PRAGMA table_info(performance_stats)")
            columns = [row[1] for row in cursor.fetchall()]
            expected_columns = ['id', 'kind', 'signature', 'count', 'ema_speed_x', 'p10_speed_x', 'samples_json', 'updated_at']
            assert set(columns) == set(expected_columns)

    def test_schema_migration_to_version_2(self):
        """Test that existing cache files upgrade to schema version 2 seamlessly."""
        # Create a cache with old schema (only loudnorm_analysis table)
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            conn = sqlite3.connect(str(cache_path))
            conn.execute("""
                CREATE TABLE loudnorm_analysis (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    settings_hash TEXT NOT NULL,
                    song_id INTEGER,
                    measured_I REAL NOT NULL,
                    measured_TP REAL NOT NULL,
                    measured_LRA REAL NOT NULL,
                    measured_thresh REAL NOT NULL,
                    offset REAL NOT NULL,
                    raw_json TEXT,
                    created_at INTEGER NOT NULL,
                    addon_version TEXT NOT NULL,
                    UNIQUE(path, size_bytes, mtime_ns, settings_hash)
                )
            """)
            conn.commit()
            conn.close()

            # Now open with LoudnessCache - should add performance_stats table
            cache = LoudnessCache(cache_path)

            # Check performance_stats table was added
            cursor = cache._connection.execute("""
                SELECT name FROM sqlite_master WHERE type='table' AND name='performance_stats'
            """)
            assert cursor.fetchone() is not None

    def test_empty_cache_first_analysis(self):
        """Test with empty cache (first analysis)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            estimate, count, status = cache.get_analysis_speed_estimate()
            assert estimate is None
            assert count == 0
            assert status == 'insufficient_data'

    def test_exactly_min_samples(self):
        """Test with exactly min_samples (5) data points."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            for i in range(5):
                cache.record_analysis_performance(10.0, 5.0)

            estimate, count, status = cache.get_analysis_speed_estimate()
            assert estimate == 1.0  # capped
            assert count == 5
            assert status == 'learned'

    def test_p10_calculation_various_distributions(self):
        """Test p10 calculation with various distributions of speeds."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Add 10 samples with different speeds
            speeds = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
            for speed in speeds:
                cache.record_analysis_performance(10.0, 10.0 / speed)

            cursor = cache._connection.execute("""
                SELECT p10_speed_x FROM performance_stats
                WHERE kind = 'audio_loudnorm_analysis' AND signature = ''
            """)
            row = cursor.fetchone()
            assert row is not None
            p10 = row[0]

            # For 10 samples, p10 index = int(0.1 * 9) = 0, so smallest value
            expected_p10 = min(speeds)
            assert p10 == expected_p10

    def test_duration_filter_less_than_5_seconds(self):
        """Test that duration < 5 seconds is filtered out."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            cache.record_analysis_performance(3.0, 1.0)  # duration = 3.0 < 5

            cursor = cache._connection.execute("SELECT COUNT(*) FROM performance_stats")
            count = cursor.fetchone()[0]
            assert count == 0

    def test_wall_seconds_filter_non_positive(self):
        """Test that wall_seconds <= 0 is filtered out."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            cache.record_analysis_performance(10.0, 0.0)  # wall_seconds = 0

            cursor = cache._connection.execute("SELECT COUNT(*) FROM performance_stats")
            count = cursor.fetchone()[0]
            assert count == 0

    def test_end_to_end_multiple_analyses(self):
        """Simulate recording performance from multiple analyses and verify estimates improve."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Simulate 10 analyses with varying speeds
            speeds = [1.5, 2.0, 1.8, 2.2, 1.9, 2.1, 1.7, 2.3, 1.6, 2.4]
            for speed in speeds:
                cache.record_analysis_performance(10.0, 10.0 / speed)

            # Check that we have data
            estimate, count, status = cache.get_analysis_speed_estimate()
            assert estimate is not None
            assert count == 10
            assert status == 'learned'

            # Estimate should be conservative (min(1.0, p10))
            sorted_speeds = sorted(speeds)
            p10 = sorted_speeds[int(0.1 * (len(sorted_speeds) - 1))]
            expected_estimate = min(1.0, p10)
            assert estimate == expected_estimate

    def test_estimates_improve_over_time(self):
        """Test that estimates improve over time as more samples are added."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            estimates = []
            for i in range(1, 8):  # 1 to 7 samples
                cache.record_analysis_performance(10.0, 5.0)  # speed = 2.0
                estimate, count, status = cache.get_analysis_speed_estimate()
                estimates.append((estimate, count, status))

            # First 4 should be None (insufficient)
            for i in range(4):
                assert estimates[i][0] is None
                assert estimates[i][1] == i + 1
                assert estimates[i][2] == 'insufficient_data'

            # From 5 onwards, should have estimates
            for i in range(4, 7):
                assert estimates[i][0] == 1.0  # capped at 1.0
                assert estimates[i][1] == i + 1
                assert estimates[i][2] == 'learned'

    def test_conservative_estimate_always_le_1(self):
        """Test that the conservative estimate is always <= 1.0."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "test_cache.db"
            cache = LoudnessCache(cache_path)

            # Test with various high speeds
            high_speeds = [2.0, 5.0, 10.0, 100.0]
            for speed in high_speeds:
                # Clear previous data
                cache._connection.execute("DELETE FROM performance_stats")
                cache._connection.commit()

                for _ in range(5):
                    cache.record_analysis_performance(10.0, 10.0 / speed)

                estimate, _, _ = cache.get_analysis_speed_estimate()
                assert estimate is not None and estimate <= 1.0