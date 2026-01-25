"""Orchestrator for the batch transcode wizard workflow.

This module manages the sequence of wizard dialogs and maintains state between steps.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import QDialog

from .state import BatchWizardState

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow

_logger = logging.getLogger(__name__)


class BatchWizardOrchestrator:
    """Orchestrates the batch transcode wizard workflow.
    
    The wizard guides users through the following sequence:
    1. Goals dialog - Choose what to process (audio/video)
    2. Rules dialog - Configure transcode rules and verification
    3. Preflight dialog - Review estimates and opt into analysis
    4. Scan phase - Fast metadata scan of library
    5. Analysis phase (optional) - Loudness analysis if enabled
    6. Selection dialog - Choose which songs to transcode
    7. Hand off to existing transcode flow
    
    The orchestrator maintains wizard state between steps and handles
    Back/Next/Cancel navigation.
    """
    
    def __init__(self, parent: Optional[QMainWindow] = None):
        """Initialize the wizard orchestrator.
        
        Args:
            parent: Parent window for modal dialogs
        """
        self.parent = parent
        self.state = BatchWizardState()
        
    def run_wizard(self) -> Optional[BatchWizardState]:
        """Run the complete wizard workflow.

        Returns:
            BatchWizardState if wizard completed successfully, None if cancelled
        """
        _logger.info("Starting batch wizard workflow")

        # Prune orphaned cache entries at wizard start (F6a alternative)
        try:
            from ...core.loudness_cache import get_cache_path, LoudnessCache
            cache_path = get_cache_path()
            cache = LoudnessCache(cache_path)
            cache.prune_orphans()
            cache.close()
            _logger.info("Cache pruning completed at wizard start")
        except Exception as e:
            _logger.warning(f"Cache pruning failed at wizard start: {e}")
        
        # Step 1: Goals
        if not self._run_goals_step():
            _logger.info("Wizard cancelled at Goals step")
            return None
            
        # Step 2: Rules
        if not self._run_rules_step():
            _logger.info("Wizard cancelled at Rules step")
            return None
            
        # Step 3: Preflight
        if not self._run_preflight_step():
            _logger.info("Wizard cancelled at Preflight step")
            return None
        
        # Step 4: Scan
        if not self._run_scan_step():
            _logger.info("Wizard cancelled at Scan step")
            return None
        
        # Step 5: Analysis (optional)
        if self.state.verify_normalization and self.state.process_audio:
            if not self._run_analysis_step():
                _logger.info("Wizard cancelled at Analysis step")
                return None
        
        # Step 6: Selection
        if not self._run_selection_step():
            _logger.info("Wizard cancelled at Selection step")
            return None
        
        _logger.info("Wizard completed successfully")
        return self.state
    
    def _run_goals_step(self) -> bool:
        """Run the Goals dialog step.
        
        Returns:
            True if Next was clicked, False if Cancel was clicked
        """
        from ...gui.wizard.goals_dialog import BatchWizardGoalsDialog
        
        dialog = BatchWizardGoalsDialog(self.state, self.parent)
        result = dialog.exec()
        
        if result == QDialog.DialogCode.Accepted:
            self.state = dialog.get_state()
            _logger.debug(f"Goals step completed: audio={self.state.process_audio}, video={self.state.process_video}")
            return True
        return False
    
    def _run_rules_step(self) -> bool:
        """Run the Rules dialog step.
        
        Returns:
            True if Next was clicked, False if Back or Cancel was clicked
        """
        from ...gui.wizard.rules_dialog import BatchWizardRulesDialog
        
        while True:
            dialog = BatchWizardRulesDialog(self.state, self.parent)
            result = dialog.exec()
            
            if result == QDialog.DialogCode.Accepted:
                self.state = dialog.get_state()
                _logger.debug(f"Rules step completed: verify={self.state.verify_normalization}, preset={self.state.verification_tolerance_preset}")
                return True
            elif hasattr(dialog, 'went_back') and dialog.went_back:
                # Go back to Goals
                if not self._run_goals_step():
                    return False
                # Continue to show Rules again
                continue
            else:
                # Cancelled
                return False
    
    def _run_preflight_step(self) -> bool:
        """Run the Preflight dialog step.
        
        Returns:
            True if Next was clicked, False if Back or Cancel was clicked
        """
        from ...gui.wizard.preflight_dialog import BatchWizardPreflightDialog
        
        while True:
            dialog = BatchWizardPreflightDialog(self.state, self.parent)
            result = dialog.exec()
            
            if result == QDialog.DialogCode.Accepted:
                self.state = dialog.get_state()
                _logger.debug("Preflight step completed")
                return True
            elif hasattr(dialog, 'went_back') and dialog.went_back:
                # Go back to Rules
                if not self._run_rules_step():
                    return False
                # Continue to show Preflight again
                continue
            else:
                # Cancelled
                return False
    
    def _run_scan_step(self) -> bool:
        """Run the Scan phase.
        
        Returns:
            True if scan completed, False if cancelled
        """
        from ...gui.wizard.scan_dialog import ScanProgressDialog
        from ...core.config import load_config
        
        cfg = load_config()
        
        dialog = ScanProgressDialog(cfg, self.state, self.parent)
        result = dialog.exec()
        
        if result == QDialog.DialogCode.Accepted and not dialog.was_aborted():
            self.state.scan_results = dialog.get_results()
            _logger.debug(f"Scan step completed: {len(self.state.scan_results)} files found")
            
            if not self.state.validate_scan_results():
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self.parent,
                    "No Files Found",
                    "No media files were found that need processing with the current settings."
                )
                return False
            return True
        return False
    
    def _run_analysis_step(self) -> bool:
        """Run the Analysis phase.
        
        Returns:
            True if analysis completed or skipped, False if cancelled
        """
        from ...gui.wizard.analysis_dialog import AnalysisProgressDialog
        from ...core.config import load_config
        
        cfg = load_config()
        
        # Filter to audio files only
        audio_files = [r for r in self.state.scan_results if r.media_type == "audio"]
        
        if not audio_files:
            _logger.info("No audio files to analyze, skipping analysis step")
            return True
        
        _logger.info(f"Starting analysis for {len(audio_files)} audio files")
        
        dialog = AnalysisProgressDialog(cfg, self.state, audio_files, self.parent)
        result = dialog.exec()
        
        if result == QDialog.DialogCode.Accepted and not dialog.was_aborted():
            self.state.analysis_results = dialog.get_results()
            _logger.debug(f"Analysis step completed: {len(self.state.analysis_results)} files analyzed")
            
            # Update scan results with verification results
            # High Issue #5: Update needs_processing based on analysis outcomes
            for scan_result in self.state.scan_results:
                if scan_result.media_type == "audio":
                    path_key = str(scan_result.media_path)
                    if path_key in self.state.analysis_results:
                        verification = self.state.analysis_results[path_key]
                        scan_result.verification_result = verification
                        
                        # If within tolerance, mark as not needing processing
                        # (unless force transcode is enabled)
                        if verification.within_tolerance and not self.state.force_audio_transcode:
                            scan_result.needs_processing = False
                            _logger.debug(f"Marking {scan_result.media_path.name} as not needing processing (within tolerance)")
            
            return True
        elif dialog.was_aborted():
            # User cancelled - partial results are still in cache
            _logger.info("Analysis was cancelled, proceeding with partial results")
            self.state.analysis_results = dialog.get_results()
            
            # Update scan results with whatever we got
            # High Issue #5: Update needs_processing based on partial analysis
            for scan_result in self.state.scan_results:
                if scan_result.media_type == "audio":
                    path_key = str(scan_result.media_path)
                    if path_key in self.state.analysis_results:
                        verification = self.state.analysis_results[path_key]
                        scan_result.verification_result = verification
                        
                        # If within tolerance, mark as not needing processing
                        if verification.within_tolerance and not self.state.force_audio_transcode:
                            scan_result.needs_processing = False
                            _logger.debug(f"Marking {scan_result.media_path.name} as not needing processing (within tolerance)")
            
            return True  # Continue to selection even if analysis was cancelled
        
        return False
    
    def _run_selection_step(self) -> bool:
        """Run the Selection dialog step.
        
        Returns:
            True if Next clicked with valid selection, False if cancelled
        """
        from ...gui.wizard.selection_dialog import BatchWizardSelectionDialog
        
        while True:
            dialog = BatchWizardSelectionDialog(self.state, self.parent)
            result = dialog.exec()
            
            if result == QDialog.DialogCode.Accepted:
                self.state = dialog.get_state()
                _logger.debug(f"Selection step completed: {len(self.state.selected_songs)} songs selected")
                return True
            elif hasattr(dialog, 'went_back') and dialog.went_back:
                # Go back to preflight (or analysis if it was run)
                if not self._run_preflight_step():
                    return False
                
                # High Issue #4: Reuse cached scan/analysis results if available
                # Only re-run scan if no cached results exist
                if not self.state.scan_results:
                    _logger.info("No cached scan results, running scan")
                    if not self._run_scan_step():
                        return False
                else:
                    _logger.info("Reusing cached scan results")
                
                # Only re-run analysis if verification is enabled and no cached results exist
                if self.state.verify_normalization and self.state.process_audio:
                    if not self.state.analysis_results:
                        _logger.info("No cached analysis results, running analysis")
                        if not self._run_analysis_step():
                            return False
                    else:
                        _logger.info("Reusing cached analysis results")
                
                # Continue to show Selection again
                continue
            else:
                # Cancelled
                return False
