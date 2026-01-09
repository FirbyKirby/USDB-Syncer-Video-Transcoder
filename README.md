# Video Transcoder Addon

Automatically converts downloaded videos to various formats. After each song download, the addon analyzes the video and, if needed, transcodes it to your chosen target codec, ensuring the output matches your configured settings exactly (e.g., H.264 profile, pixel format).

Tip: Transcoding runs automatically. No manual steps needed after installation.

## Overview

Why this exists
- Some video players do not support some common web codecs/containers out of the box (e.g., VP9). This addon ensures downloaded videos play reliably by converting them to compatible formats.

What it does
- Enforces your configured target codec and settings exactly (e.g., H.264 profile, pixel format)
- Supports five target codecs: H.264, HEVC, VP8, VP9, and AV1
- Uses Intel QuickSync hardware encoding when available for much faster encodes
- Preserves source audio by default via passthrough (no re-encode)
- Updates USDB_Syncer metadata to avoid re-download loops and updates the song’s #VIDEO tag
- Can batch-transcode synchronized videos from the GUI (Tools → Batch Video Transcode) with preview, filtering, live stats, rollback, progress, and results export

Codec compatibility snapshot
- H.264/AVC (MP4): Best compatibility
- HEVC (MP4): Best quality/size ratio
- VP8/VP9 (WebM): Open formats
- AV1 (MKV/MP4): Next-gen efficiency

See also the architecture notes and compatibility table in [plans/video-transcoder-addon-architecture.md](plans/video-transcoder-addon-architecture.md).

## Installation

Prerequisites
- FFMPEG and FFPROBE must be available. USDB_Syncer can use system PATH or a folder set in its settings. Verification guidance is in [addons/video_transcoder/TROUBLESHOOTING.md](addons/video_transcoder/TROUBLESHOOTING.md).

Steps
1) Close USDB_Syncer if running.
2) Copy the folder addons/video_transcoder into your USDB_Syncer addons directory. USDB_Syncer loads addons from [src/usdb_syncer/utils.py](src/usdb_syncer/utils.py:110) → [utils.AppPaths.addons](src/usdb_syncer/utils.py:115).
   - Windows: %LocalAppData%/usdb_syncer/addons
   - macOS: ~/Library/Application Support/usdb_syncer/addons
   - Linux: ~/.local/share/usdb_syncer/addons
3) Start USDB_Syncer. The log will include: Video Transcoder addon loaded. Hook registration occurs in [addons/video_transcoder/__init__.py](addons/video_transcoder/__init__.py) and uses [hooks.SongLoaderDidFinish](src/usdb_syncer/hooks.py:47).

## Configuration

The addon can be configured graphically via **Tools → Transcoder Settings...** in USDB Syncer.

Alternatively, you can edit the config file at [addons/video_transcoder/config.json](addons/video_transcoder/config.json). This file is created automatically when the addon is loaded. Full option reference and presets live in [addons/video_transcoder/CONFIGURATION.md](addons/video_transcoder/CONFIGURATION.md).

Default config excerpt
```json
{
  "version": 2,
  "auto_transcode_enabled": true,
  "target_codec": "h264",
  "h264": { "profile": "high", "pixel_format": "yuv420p", "crf": 18, "preset": "fast", "container": "mp4" },
  "vp8": { "crf": 10, "cpu_used": 4, "container": "webm" },
  "hevc": { "profile": "main", "pixel_format": "yuv420p", "crf": 18, "preset": "faster", "container": "mp4" },
  "vp9": { "crf": 20, "cpu_used": 4, "deadline": "good", "container": "webm" },
  "av1": { "crf": 20, "cpu_used": 8, "container": "mkv" },
  "general": { "hardware_encoding": true, "hardware_decode": true, "backup_original": true, "backup_suffix": "-source", "timeout_seconds": 600, "verify_output": true, "min_free_space_mb": 500, "max_resolution": null, "max_fps": null, "max_bitrate_kbps": null },
  "usdb_integration": { "use_usdb_resolution": true, "use_usdb_fps": true }
}
```

Important paths and behavior
- Config file location: [addons/video_transcoder/config.json](addons/video_transcoder/config.json) (created on first run by [config.load_config()](addons/video_transcoder/config.py:109))
- Backup originals: when enabled, originals are preserved as name-source.ext (see default [config.GeneralConfig](addons/video_transcoder/config.py:64))
- Automatic run: executes after each download via [hooks.SongLoaderDidFinish](src/usdb_syncer/hooks.py:47)
- Optional limits: max_resolution/max_fps/max_bitrate_kbps are applied by codec handlers (see [addons/video_transcoder/codecs.py](addons/video_transcoder/codecs.py))
 - Optional USDB settings integration: use_usdb_resolution/use_usdb_fps can read values from USDB Syncer settings (see [transcoder._apply_limits()](addons/video_transcoder/transcoder.py:474))

## Transcoding decisions (what triggers a transcode)

The addon now uses strict matching against your configuration rather than a loose “Unity-compatible” check. A file will be transcoded if any of the following is true:
- Target codec mismatch (e.g., file is VP9 but target_codec is h264)
- General caps exceeded: resolution, FPS, or bitrate higher than configured maximums
- Codec setting mismatch for the selected target codec
  - H.264: pixel_format or profile does not match your configuration
  - HEVC: pixel_format or profile does not match your configuration
  - VP8/VP9/AV1: only the codec must match; additional properties are not checked for decision

Notes
- Container choice (mp4/webm/mkv) affects output but is not used to decide whether an input needs transcoding.

## Codec selection guide

H.264 (MP4)
- Best overall compatibility across platforms and Unity versions
- Fastest when QuickSync is available; quality at CRF 18 preset slow is near-lossless
- Recommended default

VP8 (WebM)
- Good fallback if you explicitly need WebM; widely supported in browsers
- Software-only encode; slower than H.264; file sizes often larger than HEVC

H.265/HEVC (MP4)
- Smallest files at similar visual quality compared to H.264
- Decoding support varies. Windows 11 may need the Microsoft HEVC Video Extensions. Some devices lack HEVC playback
- Use when you control the playback environment and know HEVC is supported

Tip: If unsure, pick H.264. Switch to HEVC for smallest files once playback is verified on your target system.

## Hardware acceleration (global controls)

What you need
- Intel CPU with integrated graphics that supports QuickSync
- Windows or Linux with proper drivers
- FFMPEG build that includes QSV encoders/decoders (e.g., h264_qsv, hevc_qsv)

How it behaves
- Global-only controls: toggle [config.GeneralConfig.hardware_encoding](addons/video_transcoder/config.py:64) and [config.GeneralConfig.hardware_decode](addons/video_transcoder/config.py:64) to affect all codecs
- Auto-selection: when hardware encoding is enabled, the addon selects the best available accelerator via [hwaccel.get_best_accelerator()](addons/video_transcoder/hwaccel.py:79)
- Current support: Intel QuickSync only, implemented by [hwaccel.QuickSyncAccelerator](addons/video_transcoder/hwaccel.py:121). The architecture permits future accelerators
- AV1 behavior: if targeting AV1 and hardware encoding is enabled, QSV is used when available; otherwise encoding falls back to software AV1 encoders (prefers libsvtav1, then libaom-av1). Encoder selection code path: [codecs.AV1Handler.build_encode_command()](addons/video_transcoder/codecs.py:521)

Note: If you set max_resolution or max_fps, the addon may disable hardware decoding for that run to avoid hardware decode + filter pipeline issues, while still using hardware encoding when possible.

## Live progress, abort, and enhanced logging

What you will see during a transcode
- The addon parses FFMPEG stderr in real time to extract progress from lines like time=HH:MM:SS.xx. Parsing helpers: [utils.parse_ffmpeg_progress()](addons/video_transcoder/utils.py:77), [utils.time_to_seconds()](addons/video_transcoder/utils.py:54)
- Progress is logged roughly every 5 seconds in this format: Transcoding: 45% complete (1:23 / 3:00) [fps=..., speed=...]. Logging occurs inside [transcoder._execute_ffmpeg()](addons/video_transcoder/transcoder.py:296) when it encounters time=...
- Before the encode starts, the log includes video properties and the exact FFMPEG command. See [transcoder.process_video()](addons/video_transcoder/transcoder.py:41)
  - Example entries: Analyzing video..., Video analysis: codec=..., resolution=..., FFMPEG command: ..., Transcoding video (...)
- On completion, a summary is printed with total wall time and approximate realtime speed. See completion log in [transcoder.process_video()](addons/video_transcoder/transcoder.py:285)

How to abort an in-progress transcode
- Click Abort in USDB Syncer. The addon observes both single-transcode and batch-transcode abort sources via [utils.is_aborted()](addons/video_transcoder/utils.py:84)
- Abort takes effect immediately: the running FFMPEG process is terminated within ~500ms. It is stopped gracefully first and then forcefully if needed: [transcoder._execute_ffmpeg()](addons/video_transcoder/transcoder.py:331) sends SIGTERM, waits briefly, then sends SIGKILL if FFMPEG does not exit [transcoder._execute_ffmpeg()](addons/video_transcoder/transcoder.py:347)
- Partial outputs are cleaned automatically: temporary .transcoding* files are removed on abort or failure in [transcoder.process_video()](addons/video_transcoder/transcoder.py:189) and [transcoder.process_video()](addons/video_transcoder/transcoder.py:213)
- Batch transcode now aborts immediately as well. The active video encode is stopped within ~500ms and the batch ends: worker integration in [BatchWorker](addons/video_transcoder/batch_worker.py)

Notes
- Abort is immediate in most cases. The FFMPEG process is terminated within ~500ms after you click Abort. On some systems, shutdown may take a second or two depending on OS scheduling and encoder state
- The general timeout still applies as a hard cap: [config.GeneralConfig.timeout_seconds](addons/video_transcoder/config.py:76). If FFMPEG does not make progress and does not shut down, it is force-terminated per [transcoder._execute_ffmpeg()](addons/video_transcoder/transcoder.py:396)

Performance expectation
- On modest Intel iGPU hardware, a 3-minute video typically encodes in about 1 minute with H.264 QuickSync

Check your environment
- CLI: ffmpeg -encoders | findstr qsv (Windows) or ffmpeg -encoders | grep qsv (macOS/Linux) should list h264_qsv and hevc_qsv
- Logs: when active you will see hardware encoding messages; otherwise a software fallback warning

Warning: On macOS, QuickSync is not used by this addon. It will fall back to software encoding.

## Batch Transcoding Workflow

Use this feature to convert an existing library of synchronized videos in one pass. The workflow is fully guided and consists of four phases.

1) Launch
- Open USDB Syncer and choose Tools → Batch Video Transcode
- This invokes the batch orchestrator entry point [BatchTranscodeOrchestrator.start_batch_workflow()](addons/video_transcoder/batch_orchestrator.py:164)

2) Preview generation and analysis
- The addon scans your synchronized songs for videos that require transcoding based on your current configuration
- Each candidate video is analyzed, then size and time estimates are calculated
  - Video analysis: [video_analyzer.analyze_video()](addons/video_transcoder/video_analyzer.py:60)
  - Strict transcode decision: [video_analyzer.needs_transcoding()](addons/video_transcoder/video_analyzer.py:198)
  - Estimates: [BatchEstimator.estimate_output_size()](addons/video_transcoder/batch_estimator.py:19), [BatchEstimator.estimate_transcode_time()](addons/video_transcoder/batch_estimator.py:89)
- The preview phase is implemented in [BatchTranscodeOrchestrator._generate_preview()](addons/video_transcoder/batch_orchestrator.py:180)

3) Selection dialog with filtering and statistics
- An interactive dialog lists all candidates with current properties and estimated output sizes
- Features include search filtering, select/deselect all, dynamic totals, and disk space validation against current free space
  - Dialog implementation: [BatchPreviewDialog](addons/video_transcoder/batch_preview_dialog.py)
  - Live statistics and space checks: [BatchPreviewDialog._update_statistics()](addons/video_transcoder/batch_preview_dialog.py:170) using [BatchEstimator.calculate_disk_space_required()](addons/video_transcoder/batch_estimator.py:200) and [BatchEstimator.get_free_disk_space()](addons/video_transcoder/batch_estimator.py:181)
- Optional rollback protection can be enabled before starting. When enabled, the system can restore originals if the batch is aborted
  - Rollback manager: [RollbackManager](addons/video_transcoder/rollback.py) with [RollbackManager.enable_rollback()](addons/video_transcoder/rollback.py:66)
- This phase is driven by [BatchTranscodeOrchestrator._show_selection_dialog()](addons/video_transcoder/batch_orchestrator.py:291)

4) Progress monitoring (with abort)
- A modal progress dialog shows the current video, per-video percent/fps/speed, elapsed/ETA, and overall progress
- You can request an abort at any time; the current video stops within ~500ms and the batch ends. If rollback is enabled, you are prompted to restore processed videos
  - Progress UI: [BatchProgressDialog](addons/video_transcoder/batch_progress_dialog.py)
  - Abort signal flow: [BatchProgressDialog.abort_requested](addons/video_transcoder/batch_progress_dialog.py:27) → [BatchTranscodeOrchestrator.abort_batch()](addons/video_transcoder/batch_orchestrator.py:393) → worker abort coordination in [BatchWorker](addons/video_transcoder/batch_worker.py)
  - Background worker: [BatchWorker](addons/video_transcoder/batch_worker.py) emits [BatchWorker.video_progress](addons/video_transcoder/batch_worker.py:31) and stops the active FFMPEG promptly
  - Execution: [BatchTranscodeOrchestrator._execute_batch()](addons/video_transcoder/batch_orchestrator.py:301)

5) Results reporting and export
- A results dialog summarizes successes, failures, skipped, aborted, and rolled back items; shows total time and net space saved
- You can export the full report to CSV or copy a text summary to the clipboard
  - Results UI: [BatchResultsDialog](addons/video_transcoder/batch_results_dialog.py)
  - CSV export: [BatchResultsDialog._export_to_csv()](addons/video_transcoder/batch_results_dialog.py:180)
  - Display is invoked by [BatchTranscodeOrchestrator._show_results()](addons/video_transcoder/batch_orchestrator.py:386)

Notes and safeguards
- Disk space validation is enforced before starting; if insufficient, the Start button is disabled
- Rollback protection creates temporary or permanent backups depending on your settings; on abort, you will be offered to restore processed videos
  - Rollback on abort: [BatchTranscodeOrchestrator._handle_abort()](addons/video_transcoder/batch_orchestrator.py:363) using [RollbackManager.rollback_all()](addons/video_transcoder/rollback.py:90)
  - During transcoding, partial outputs are handled by the normal single-file pipeline; see cleanup in [transcoder.process_video()](addons/video_transcoder/transcoder.py:189) and [transcoder.process_video()](addons/video_transcoder/transcoder.py:213)

## Managing Video Backups (Tools → Manage Video Backups...)

Use a single unified workflow to delete or restore backups from one place: scan → select → choose action (Delete or Restore) → confirm → execute → results. The selection dialog presents both options with two action buttons: Delete Selected and Restore Selected.

How backups are discovered
- The manager first looks for an exact stored filename in each song’s sync data: transcoder_source_fname
  - If present and the file exists, it is treated as the backup
- If not present, it searches for files next to the active video that match: <active_video_stem><backup_suffix>*
  - The backup_suffix is configured in [addons/video_transcoder/CONFIGURATION.md](addons/video_transcoder/CONFIGURATION.md) and defaults to -source
  - Example with default suffix: MySong.mp4 → MySong-source.mkv or MySong-source.mp4
- Discovery and validation logic: [addons/video_transcoder/backup_manager.py](addons/video_transcoder/backup_manager.py)

Access
- Tools → Manage Video Backups... → [backup_dialog_orchestrator.BackupDialogOrchestrator.start_workflow()](addons/video_transcoder/backup_dialog_orchestrator.py:93)

Unified phases
1) Scan
   - A small progress dialog scans your library for backups. You can cancel the scan
2) Select
   - A table lists each discovered backup with columns: Title, Artist, Backup File, Size, Date
   - Includes per-row checkboxes, a text filter, Select All / Deselect All, and live Selected count
   - Dialog implementation: [addons/video_transcoder/backup_selection_dialog.py](addons/video_transcoder/backup_selection_dialog.py)
   - The dialog provides two actions so you can decide after selecting: Delete Selected or Restore Selected
3) Confirm (depends on chosen action)
   - Delete: shows how many files will be deleted and the total space to be freed
   - Restore: warns that active videos will be overwritten and that a safety backup of the current video will be created first
4) Execute
   - Delete: per-file deletion with progress and Cancel — UI: [addons/video_transcoder/backup_deletion_progress_dialog.py](addons/video_transcoder/backup_deletion_progress_dialog.py)
   - Restore: per-file restoration with progress and Cancel — UI: [addons/video_transcoder/backup_restore_progress_dialog.py](addons/video_transcoder/backup_restore_progress_dialog.py)
5) Results
   - Delete: summary of succeeded/failed deletions and space freed — UI: [addons/video_transcoder/backup_deletion_results_dialog.py](addons/video_transcoder/backup_deletion_results_dialog.py)
   - Restore: summary of successfully restored/failed items — UI: [addons/video_transcoder/backup_restore_results_dialog.py](addons/video_transcoder/backup_restore_results_dialog.py)

Restore behavior and safeguards
- What restore does: replaces the active transcoded video with the selected backup file
- Safety backup: before replacement, the current active video is saved next to it with a .safety-[timestamp] suffix; creation occurs in [backup_manager.restore_backup()](addons/video_transcoder/backup_manager.py:207), naming at [backup_manager.restore_backup()](addons/video_transcoder/backup_manager.py:231)
- Metadata update: after restoration, the song’s sync data is updated to reflect the active filename and stored source reference in [backup_manager.restore_backup()](addons/video_transcoder/backup_manager.py:246)
- Space stats: when you choose Delete Selected, the dialog shows Total Space to Reclaim; when you choose Restore Selected, this metric is hidden by design

Deletion safeguards (unchanged)
- Multi-level confirmation: selection screen → explicit irreversible warning prompt → progress dialog
- Validation before deletion ensures the target is not the active video and that the file exists and is writable
- Space reclaim estimate updates live before you commit to deletion
- Cancel is available during both scanning and deletion; completed deletions are not reverted
- Sync metadata is updated to clear the stored transcoder_source_fname when its backup is removed
- Active transcoded videos are never deleted by this workflow

Scope and limitations
- Affects only persistent user backups created alongside your song files using the configured backup_suffix
- Does not touch temporary rollback backups created by Batch Video Transcode; those live in a separate system temp folder managed by [addons/video_transcoder/rollback.py](addons/video_transcoder/rollback.py)
- Respects your current backup_suffix setting when searching by pattern; exact matches saved in sync data are honored even if you later change the suffix

Tip: To reduce future backup accumulation, you can disable keeping new originals by setting general.backup_original to false in [addons/video_transcoder/CONFIGURATION.md](addons/video_transcoder/CONFIGURATION.md). This does not affect existing backups; use the manager to remove or restore them.

## Troubleshooting

Common fixes and checks are documented in [addons/video_transcoder/TROUBLESHOOTING.md](addons/video_transcoder/TROUBLESHOOTING.md).

## Technical details (for advanced users)

High-level flow
1) Analyze with [video_analyzer.analyze_video()](addons/video_transcoder/video_analyzer.py:58)
2) Decide if work is needed via [video_analyzer.needs_transcoding()](addons/video_transcoder/video_analyzer.py:198). This step performs strict matching against your configured settings (profile, pixel format, and general caps). See decision rules summarized above and implementation in [addons/video_transcoder/video_analyzer.py](addons/video_transcoder/video_analyzer.py).
3) Build the FFMPEG command from the codec handler: [codecs.H264Handler](addons/video_transcoder/codecs.py:105), [codecs.VP8Handler](addons/video_transcoder/codecs.py:216), [codecs.HEVCHandler](addons/video_transcoder/codecs.py:305), [codecs.VP9Handler](addons/video_transcoder/codecs.py:409), [codecs.AV1Handler](addons/video_transcoder/codecs.py:501)
4) Optionally enable hardware decode/encode via [hwaccel.get_best_accelerator()](addons/video_transcoder/hwaccel.py:79) and [hwaccel.QuickSyncAccelerator](addons/video_transcoder/hwaccel.py:121)
5) Execute and verify; then update sync metadata and the song’s #VIDEO tag via [sync_meta_updater.update_sync_meta_video()](addons/video_transcoder/sync_meta_updater.py:25)

Entry points and config
- Hook: [hooks.SongLoaderDidFinish](src/usdb_syncer/hooks.py:47) triggers [transcoder.process_video()](addons/video_transcoder/transcoder.py:41)
- Configuration dataclass: [config.TranscoderConfig](addons/video_transcoder/config.py:91)
- Batch module: [addons/video_transcoder/batch.py](addons/video_transcoder/batch.py)

Sync tracking (critical)
- USDB_Syncer uses file mtime to decide if resources are in sync. The addon updates filenames and mtimes accordingly to prevent re-download loops. Details: [addons/video_transcoder/sync_meta_updater.py](addons/video_transcoder/sync_meta_updater.py)

## FAQ

- Does this run automatically? Yes. The addon subscribes to [hooks.SongLoaderDidFinish](src/usdb_syncer/hooks.py:47) and processes each newly downloaded song.
- Will my original file be kept? Yes, if general.backup_original is true. The original is renamed to name-source.ext.
- Does it re-encode audio? No, audio is copied if present. If the container requires a different audio codec (e.g., WebM without Opus/Vorbis), it will encode as needed.
- Which container will I get? H.264 → .mp4, VP8 → .webm, HEVC → .mp4. See handler capabilities in [addons/video_transcoder/codecs.py](addons/video_transcoder/codecs.py).
- Which container will I get? Default is H.264 → .mp4, VP8 → .webm, HEVC → .mp4, but you can override per-codec container via the `container` config key.
- Where is the config file? [addons/video_transcoder/config.json](addons/video_transcoder/config.json) (created on first run by [config.load_config()](addons/video_transcoder/config.py:110)).
- How do I turn off automatic transcoding? Set auto_transcode_enabled to false in the config JSON. The hook still loads but exits early. Batch transcoding remains available via the Tools menu.
- My videos still don’t play. Start with H.264, ensure yuv420p and CFR, and review [addons/video_transcoder/TROUBLESHOOTING.md](addons/video_transcoder/TROUBLESHOOTING.md).

When does the addon skip transcoding?
- Only when the input already matches your configured target codec and its checked settings, and does not exceed any configured limits (resolution, FPS, bitrate). For H.264, that includes profile/pixel_format; for HEVC, profile/pixel_format; for VP8/VP9/AV1, the codec must match.

New in this version — progress and abort
- Can I abort a running transcode? Yes. Click Abort in USDB Syncer. The addon detects the abort via [utils.is_aborted()](addons/video_transcoder/utils.py:84) and terminates FFMPEG immediately
- How quickly does abort take effect? Within ~500ms in most cases; shutdown may take up to a couple of seconds on some systems
- Will partial files be left behind? No. Temporary .transcoding* files are removed on abort or failure in [transcoder.process_video()](addons/video_transcoder/transcoder.py:189) and [transcoder.process_video()](addons/video_transcoder/transcoder.py:213)
- Does batch transcode respect abort? Yes. The active video is stopped immediately and the batch ends; rollback is offered if enabled

## AI assistance and attribution

This project was developed with significant assistance from AI coding tools and large language models. These tools were used to help draft and refine source code and tests, generate documentation, and suggest refactorings and implementation approaches. All AI-assisted outputs were reviewed, edited, and validated by human maintainers; responsibility for the final design and behavior remains with the maintainers.

Provenance and licensing
- All contributions—whether AI-assisted or human-authored—are provided under the MIT License. See [LICENSE](LICENSE:1).
- The maintainers take care to avoid incorporating third-party copyrighted material or code with incompatible licenses. If you believe any content infringes your rights or includes non-compliant material, please open an issue so we can investigate and remediate promptly.

Contributor guidance for AI-assisted changes
- Disclose AI assistance in your pull request description with a brief note (for example: AI-assisted: summary of how the tool was used) and ensure you can explain and justify the changes.
- Verify that outputs are original and free of material you do not have rights to include; provide links and attribution when adapting code from public sources.
- Keep commit and PR authors human; do not list AI systems as authors or co-authors. Use normal attribution for human collaborators.
- Review, run, and test AI-generated code locally before submitting.
- Avoid including secrets or sensitive data in prompts to third-party AI services.

Acknowledgment
- We acknowledge the role of modern AI tools in accelerating parts of the development and documentation process.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE:1) for details.
