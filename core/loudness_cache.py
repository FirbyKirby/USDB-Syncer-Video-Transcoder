"""SQLite-based cache for loudness analysis results.

This module provides persistent caching of loudness measurements to avoid
repeated analysis of the same files with the same settings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .audio_normalizer import LoudnormMeasurements, LoudnormTargets

_logger = logging.getLogger(__name__)

# Addon version for cache invalidation
ADDON_VERSION = "1.0.0"

# Schema version for migrations
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class TargetSettings:
    """Settings that affect analysis results."""

    normalization_method: str  # "loudnorm" or "replaygain"
    target_i: float
    target_tp: Optional[float]
    target_lra: Optional[float]
    tolerance_preset: str  # "strict", "balanced", "relaxed"


@dataclass(frozen=True)
class CacheEntry:
    """Cached analysis result."""

    file_path: str
    file_mtime: int
    file_size: int
    normalization_method: str
    target_i: float
    target_tp: Optional[float]
    target_lra: Optional[float]
    measurements: LoudnormMeasurements
    analyzed_at: int  # unix timestamp
    addon_version: str


class LoudnessCache:
    """SQLite-based cache for loudness analysis results."""

    def __init__(self, cache_path: Path) -> None:
        """Initialize cache with database path."""
        self.cache_path = cache_path
        self._connection: Optional[sqlite3.Connection] = None
        self._ensure_connection()

    def _ensure_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._connection is None:
            db_existed = self.cache_path.exists()
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(str(self.cache_path))
            if not db_existed:
                _logger.info(f"Created cache database at {self.cache_path}")
            self._ensure_schema()
        return self._connection

    def _ensure_schema(self) -> None:
        """Create database tables if they don't exist."""
        conn = self._ensure_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS loudnorm_analysis (
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS performance_stats (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                signature TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                ema_speed_x REAL,
                p10_speed_x REAL,
                samples_json TEXT,
                updated_at INTEGER NOT NULL,
                UNIQUE(kind, signature)
            )
        """)
        conn.commit()

    def _get_settings_hash(self, target_settings: TargetSettings) -> str:
        """Generate hash for target settings."""
        key_parts = [
            target_settings.normalization_method,
            f"{target_settings.target_i:.3f}",
            f"{target_settings.target_tp:.3f}" if target_settings.target_tp is not None else "default_tp",
            f"{target_settings.target_lra:.3f}" if target_settings.target_lra is not None else "default_lra",
            target_settings.tolerance_preset,
        ]
        key_str = "|".join(key_parts)
        return hashlib.sha256(key_str.encode()).hexdigest()

    def get(self, file_path: Path, target_settings: TargetSettings) -> Optional[CacheEntry]:
        """Retrieve cached result if valid."""
        try:
            conn = self._ensure_connection()
            settings_hash = self._get_settings_hash(target_settings)

            # Get file stats
            stat = file_path.stat()
            file_size = stat.st_size
            mtime_ns = stat.st_mtime_ns

            cursor = conn.execute("""
                SELECT path, size_bytes, mtime_ns, measured_I, measured_TP, measured_LRA,
                       measured_thresh, offset, raw_json, created_at, addon_version
                FROM loudnorm_analysis
                WHERE path = ? AND size_bytes = ? AND mtime_ns = ? AND settings_hash = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (str(file_path), file_size, mtime_ns, settings_hash))

            row = cursor.fetchone()
            if row is None:
                return None

            path, size_bytes, mtime_ns, measured_I, measured_TP, measured_LRA, measured_thresh, offset, raw_json, created_at, addon_version = row

            # Reconstruct measurements from JSON (fixed security issue - no more eval())
            measurements = LoudnormMeasurements(
                measured_I=measured_I,
                measured_TP=measured_TP,
                measured_LRA=measured_LRA,
                measured_thresh=measured_thresh,
                offset=offset,
                raw=json.loads(raw_json) if raw_json else {},
            )

            return CacheEntry(
                file_path=path,
                file_mtime=mtime_ns,
                file_size=size_bytes,
                normalization_method=target_settings.normalization_method,
                target_i=target_settings.target_i,
                target_tp=target_settings.target_tp,
                target_lra=target_settings.target_lra,
                measurements=measurements,
                analyzed_at=created_at,
                addon_version=addon_version,
            )

        except (OSError, sqlite3.Error) as e:
            _logger.warning(f"Cache read error for {file_path}: {e}")
            return None

    def put(self, file_path: Path, target_settings: TargetSettings, measurements: LoudnormMeasurements, song_id: Optional[int] = None) -> None:
        """Store analysis result in cache."""
        try:
            conn = self._ensure_connection()
            settings_hash = self._get_settings_hash(target_settings)

            # Get file stats
            stat = file_path.stat()
            file_size = stat.st_size
            mtime_ns = stat.st_mtime_ns
            now = int(time.time())

            # Insert or replace
            conn.execute("""
                INSERT OR REPLACE INTO loudnorm_analysis
                (path, size_bytes, mtime_ns, settings_hash, song_id, measured_I, measured_TP, measured_LRA,
                 measured_thresh, offset, raw_json, created_at, addon_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(file_path),
                file_size,
                mtime_ns,
                settings_hash,
                song_id,
                measurements.measured_I,
                measurements.measured_TP,
                measurements.measured_LRA,
                measurements.measured_thresh,
                measurements.offset,
                json.dumps(measurements.raw),  # Store as JSON (fixed security issue)
                now,
                ADDON_VERSION,
            ))
            conn.commit()

        except (OSError, sqlite3.Error) as e:
            _logger.warning(f"Cache write error for {file_path}: {e}")

    def record_analysis_performance(self, duration_seconds: float, wall_seconds: float, kind: str = 'audio_loudnorm_analysis', signature: str = '') -> None:
        """Record performance data for analysis operations."""
        # Filter outliers
        if duration_seconds < 5 or wall_seconds <= 0:
            return
        speed_x = duration_seconds / wall_seconds
        if speed_x < 0.1 or speed_x > 10.0:
            return

        try:
            conn = self._ensure_connection()
            now = int(time.time())

            # Get existing stats
            cursor = conn.execute("""
                SELECT count, ema_speed_x, samples_json FROM performance_stats
                WHERE kind = ? AND signature = ?
            """, (kind, signature))
            row = cursor.fetchone()

            if row:
                count, ema_speed_x, samples_json = row
                samples = json.loads(samples_json) if samples_json else []
            else:
                count = 0
                ema_speed_x = None
                samples = []

            # Update count
            count += 1

            # Update EMA
            alpha = 0.2
            if ema_speed_x is None:
                ema_speed_x = speed_x
            else:
                ema_speed_x = alpha * speed_x + (1 - alpha) * ema_speed_x

            # Update samples (keep last 50)
            samples.append(speed_x)
            if len(samples) > 50:
                samples = samples[-50:]

            # Compute p10
            if samples:
                sorted_samples = sorted(samples)
                p10_index = int(0.1 * (len(sorted_samples) - 1))
                p10_speed_x = sorted_samples[p10_index]
            else:
                p10_speed_x = None

            # Insert or update
            conn.execute("""
                INSERT OR REPLACE INTO performance_stats
                (kind, signature, count, ema_speed_x, p10_speed_x, samples_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (kind, signature, count, ema_speed_x, p10_speed_x, json.dumps(samples), now))
            conn.commit()

        except sqlite3.Error as e:
            _logger.warning(f"Performance recording error: {e}")

    def get_analysis_speed_estimate(self, kind: str = 'audio_loudnorm_analysis', signature: str = '', min_samples: int = 5) -> tuple[float | None, int, str]:
        """Get conservative speed estimate for analysis."""
        try:
            conn = self._ensure_connection()
            cursor = conn.execute("""
                SELECT count, ema_speed_x, p10_speed_x FROM performance_stats
                WHERE kind = ? AND signature = ?
            """, (kind, signature))
            row = cursor.fetchone()

            if not row:
                return None, 0, 'insufficient_data'

            count, ema_speed_x, p10_speed_x = row

            if count < min_samples:
                return None, count, 'insufficient_data'

            # Conservative estimate: min(1.0, p10 or 0.8 * ema)
            if p10_speed_x is not None:
                conservative = min(1.0, p10_speed_x)
            else:
                conservative = min(1.0, ema_speed_x * 0.8) if ema_speed_x else 1.0

            return conservative, count, 'learned'

        except sqlite3.Error as e:
            _logger.warning(f"Performance estimate error: {e}")
            return None, 0, 'error'

    def invalidate(self, file_path: Path) -> None:
        """Remove all entries for a file."""
        try:
            conn = self._ensure_connection()
            conn.execute("DELETE FROM loudnorm_analysis WHERE path = ?", (str(file_path),))
            conn.commit()
        except sqlite3.Error as e:
            _logger.warning(f"Cache invalidation error for {file_path}: {e}")

    def cleanup_old_entries(self, days: int = 90) -> None:
        """Remove entries older than specified days."""
        try:
            conn = self._ensure_connection()
            cutoff = int(time.time()) - (days * 24 * 60 * 60)
            cursor = conn.execute("DELETE FROM loudnorm_analysis WHERE created_at < ?", (cutoff,))
            deleted_count = cursor.rowcount
            conn.commit()
            if deleted_count > 0:
                _logger.info(f"Cleaned up {deleted_count} old cache entries")
        except sqlite3.Error as e:
            _logger.warning(f"Cache cleanup error: {e}")

    def prune_orphans(self) -> bool:
        """Remove cache entries for songs that no longer exist in USDB Syncer or files that no longer exist."""
        try:
            from usdb_syncer import db
            from usdb_syncer.errors import AlreadyConnectedError
            from usdb_syncer.utils import AppPaths
            from usdb_syncer.usdb_song import UsdbSong

            # Connect to USDB Syncer database (skip if already connected)
            try:
                db.connect(AppPaths.db)
            except AlreadyConnectedError:
                # Already connected, proceed without connecting
                _logger.debug("Database already connected, proceeding without reconnecting")
                pass

            # Get all valid song IDs from USDB Syncer
            valid_song_ids = set()
            if hasattr(UsdbSong, "get_all"):
                for song in UsdbSong.get_all():
                    valid_song_ids.add(int(song.song_id))
            elif hasattr(db, "all_song_ids"):
                # USDB Syncer v0.18.0+: no UsdbSong.get_all(); enumerate ids via db.
                valid_song_ids = {int(song_id) for song_id in db.all_song_ids()}
            else:
                _logger.warning(
                    "prune_orphans: cannot enumerate songs (missing UsdbSong.get_all and db.all_song_ids); skipping pruning"
                )
                return False

            conn = self._ensure_connection()
            deleted_count = 0

            # Delete entries where song_id is not in valid set (but allow NULL song_id)
            if valid_song_ids:
                cursor = conn.execute(
                    "DELETE FROM loudnorm_analysis WHERE song_id IS NOT NULL AND song_id NOT IN ({})".format(
                        ",".join("?" * len(valid_song_ids))
                    ),
                    tuple(valid_song_ids)
                )
                deleted_count += cursor.rowcount

            # Delete entries where file path no longer exists
            cursor = conn.execute("SELECT DISTINCT path FROM loudnorm_analysis")
            paths_to_check = [row[0] for row in cursor.fetchall()]

            for path_str in paths_to_check:
                path = Path(path_str)
                if not path.exists():
                    cursor = conn.execute("DELETE FROM loudnorm_analysis WHERE path = ?", (path_str,))
                    deleted_count += cursor.rowcount

            conn.commit()

            if deleted_count > 0:
                _logger.info(f"Pruned {deleted_count} orphaned cache entries")

            return True

        except Exception as e:
            _logger.warning(f"Cache pruning error: {type(e).__name__}: {e}", exc_info=True)
            return False

    def close(self) -> None:
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> LoudnessCache:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def get_cache_path() -> Path:
    """Return path to cache database in USDB Syncer data directory."""
    from usdb_syncer.utils import AppPaths
    return AppPaths.db.parent / "transcoder_cache.sqlite"
