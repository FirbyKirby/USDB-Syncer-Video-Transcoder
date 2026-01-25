"""Wizard state management for batch workflow redesign.

This module defines the state container and data structures used by the batch
wizard to manage multi-step user workflows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Literal, Optional

from usdb_syncer import SongId

if TYPE_CHECKING:
    from ...core.audio_analyzer import AudioInfo
    from ..orchestrator import BatchTranscodeCandidate, BatchTranscodeSummary
    from ...core.loudness_verifier import VerificationResult
    from ...core.video_analyzer import VideoInfo

_logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result from scanning a single media file.
    
    Represents one discovered media file (audio or video) with its metadata.
    Used to populate the selection UI after scanning.
    """
    
    # Song identification
    song_id: SongId
    song_title: str
    artist: str
    
    # Media file info
    media_path: Path
    media_type: Literal["audio", "video"]
    
    # Analysis info (from fast scan)
    audio_info: Optional[AudioInfo] = None
    video_info: Optional[VideoInfo] = None
    
    # Processing decision (determined during scan)
    needs_processing: bool = False
    processing_reasons: List[str] = field(default_factory=list)
    
    # Duration for estimates
    duration_seconds: float = 0.0
    
    # Verification result (populated during analysis phase if run)
    verification_result: Optional[VerificationResult] = None


@dataclass
class SongSelection:
    """Represents user selection for a song in the wizard.
    
    Each song can have audio and/or video processing selected independently.
    """
    
    song_id: SongId
    song_title: str
    artist: str
    
    # Selection flags
    process_audio: bool = False
    process_video: bool = False
    
    # References to the actual scan results
    audio_scan_result: Optional[ScanResult] = None
    video_scan_result: Optional[ScanResult] = None


@dataclass
class BatchWizardState:
    """State container for the batch wizard workflow.
    
    This state is passed between wizard steps and accumulates information
    as the user progresses through the wizard.
    
    Attributes:
        # Processing options (chosen by user)
        process_audio: Enable audio processing in this batch
        process_video: Enable video processing in this batch
        force_audio_transcode: Force re-transcode even for matching audio
        force_video_transcode: Force re-transcode even for matching video
        
        # Verification options
        verify_normalization: Whether to run optional loudness analysis phase
        verification_tolerance_preset: Tolerance preset for verification
        
        # Scan and analysis results (populated during wizard)
        selected_songs: List of selected candidates for processing
        scan_results: Results from the fast metadata scan phase
        analysis_results: Optional results from loudness analysis phase (if run)
        
        # Rollback control
        rollback_enabled: Whether rollback protection is enabled for this run
    """
    
    # Processing flags
    process_audio: bool = False
    process_video: bool = False
    force_audio_transcode: bool = False
    force_video_transcode: bool = False
    
    # Verification flags
    verify_normalization: bool = False
    verification_tolerance_preset: str = "balanced"
    
    # Wizard step results - new Phase 4 types
    scan_results: List[ScanResult] = field(default_factory=list)
    selected_songs: List[SongSelection] = field(default_factory=list)
    analysis_results: Dict[str, VerificationResult] = field(default_factory=dict)
    summary: Optional[BatchTranscodeSummary] = None
    
    # Rollback control
    rollback_enabled: bool = False
    
    def validate_goals(self) -> bool:
        """Validate that at least one processing type is selected.
        
        Returns:
            True if at least one of process_audio or process_video is enabled
        """
        return self.process_audio or self.process_video
    
    def validate_scan_results(self) -> bool:
        """Validate that scan has produced results.
        
        Returns:
            True if scan_results is populated with at least one candidate
        """
        return len(self.scan_results) > 0
    
    def validate_selection(self) -> bool:
        """Validate that at least one song is selected.
        
        Returns:
            True if at least one song is selected for processing
        """
        return len(self.selected_songs) > 0
    
    def has_analysis_results(self) -> bool:
        """Check if analysis phase has been run and produced results.
        
        Returns:
            True if analysis_results is populated
        """
        return len(self.analysis_results) > 0
    
    def get_analysis_result(self, file_path: str) -> Optional[VerificationResult]:
        """Get analysis result for a specific file path.
        
        Args:
            file_path: Path to the audio file
            
        Returns:
            VerificationResult if available, None otherwise
        """
        return self.analysis_results.get(file_path)
    
    def get_scan_results_by_song(self) -> Dict[SongId, tuple[Optional[ScanResult], Optional[ScanResult]]]:
        """Group scan results by song ID, separating audio and video.
        
        Returns:
            Dictionary mapping song_id to (audio_result, video_result) tuple
        """
        results_by_song: Dict[SongId, tuple[Optional[ScanResult], Optional[ScanResult]]] = {}
        
        for scan_result in self.scan_results:
            if scan_result.song_id not in results_by_song:
                results_by_song[scan_result.song_id] = (None, None)
            
            audio_res, video_res = results_by_song[scan_result.song_id]
            
            if scan_result.media_type == "audio":
                results_by_song[scan_result.song_id] = (scan_result, video_res)
            else:  # video
                results_by_song[scan_result.song_id] = (audio_res, scan_result)
        
        return results_by_song
