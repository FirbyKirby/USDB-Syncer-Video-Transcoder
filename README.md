# Transcoder Addon

Automatically converts downloaded media in [USDB Syncer](https://github.com/bohning/usdb_syncer) to compatible **video and audio** formats.

After each song download, the addon analyzes the downloaded files and, if needed:

- transcodes the **video** referenced by SyncMeta
- transcodes and/or normalizes the **standalone audio** referenced by SyncMeta (when present)

Tip: Once enabled, transcoding can run automatically after each download. No manual steps are needed after installation.

> **Hardware Encoding Limitations**
> When QuickSync hardware encoding is used, certain settings (like `pixel_format`) are constrained to hardware-supported formats (currently NV12). Software encoding respects all configured parameters.

## Overview

Why this exists
- Some video players and karaoke applications do not support, or have limited support, for some common web codecs/containers out of the box (e.g., AV1). One such application is [Melody Mania](https://melodymania.org/) based on the Unity Engine. This addon ensures downloaded videos play reliably by converting them to compatible formats.

What it does
- Transcodes videos to your configured target codec (and applies relevant codec settings and optional limits)
- Supports five target codecs: H.264, HEVC, VP8, VP9, and AV1
- Uses Intel QuickSync hardware encoding when available for much faster encodes
- For video outputs: copies audio when compatible with the target container; otherwise re-encodes to AAC (MP4/MOV) or Opus (WebM/MKV)
- For standalone audio outputs: transcodes audio to your configured audio codec (AAC/MP3/Vorbis/Opus)
- Optional audio normalization:
  - EBU R128 loudness normalization via FFmpeg `loudnorm` (two-pass)
  - ReplayGain tagging via FFmpeg `replaygain` (when supported by the output format/player)
  - Optional **normalization verification** (standalone audio)
  - Verifies normalization even when a codec/container transcode is required (for example, AAC/M4A → Vorbis/OGG)
  - Uses cached results to avoid re-running analysis
  - Can skip normalization work (and sometimes skip transcoding entirely) when audio is already within tolerance
  - Can save time and reduce unnecessary re-encoding
- Updates USDB_Syncer metadata to avoid re-download loops and updates the song’s #VIDEO tag
- Updates USDB_Syncer metadata for both media types to avoid re-download loops, and updates song headers:
  - `#VIDEO:` for video
  - `#AUDIO:` and `#MP3:` for standalone audio
- Can batch-transcode synchronized media from the GUI (Tools → Batch Media Transcode) using either the **batch wizard** or **legacy batch** flow. See [docs/BATCH_TRANSCODING.md](docs/BATCH_TRANSCODING.md)

Codec compatibility snapshot
- H.264/AVC (MP4): Best compatibility
- HEVC (MP4): Best quality/size ratio
- VP8/VP9 (WebM): Open formats
- AV1 (MKV/MP4): Next-gen efficiency

See also the architecture notes and compatibility table in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation

Prerequisites
- FFMPEG and FFPROBE must be available. USDB_Syncer can use system PATH or a folder set in its settings. Verification guidance is in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

 Steps
 1) Clone or download this repository from GitHub.
 2) Place or rename the folder as `transcoder` in your USDB_Syncer addons directory.
    - Windows: %LocalAppData%/usdb_syncer/addons
    - macOS: ~/Library/Application Support/usdb_syncer/addons
    - Linux: ~/.local/share/usdb_syncer/addons
 3) Restart USDB_Syncer. The log will include: Transcoder addon loaded. Hook registration occurs in [`__init__.py`](__init__.py) and uses the USDB Syncer hooks system.

### Alternative installation: `.zip` addon (loaded directly)

USDB Syncer also supports addons distributed as `.zip` files. This is useful if you prefer a single-file install.

Key requirements and behavior
- Addons can be a Python module, a package folder, **or a `.zip` file** containing one or more Python modules.
- The file must have a `.zip` extension.
- Place the `.zip` file **directly** in the USDB_Syncer addons directory (do not extract it).
- USDB Syncer loads the zip by adding it to `sys.path` (the zip is imported directly and is not unpacked).
- If the addon is a package, the zip should contain a top-level directory with an `__init__.py` file (standard Python package convention).
- No special manifest or metadata file is required.

Example layouts

- Single-module addon:
  - `my_addon.zip` containing `my_addon.py` at the top level
- Package addon:
  - `my_addon.zip` containing `my_addon/__init__.py` (and any other package files)

For this repository, a typical zip install would be `transcoder.zip` containing `transcoder/__init__.py` and the rest of this addon’s Python files under the `transcoder/` directory.

After copying the zip into the addons directory, restart USDB_Syncer. You should see the same addon-loaded log message as with a folder install.

## Configuration

The addon can be configured graphically via **Tools → Media Transcoder Settings** in USDB Syncer.

The runtime configuration file is stored in the USDB Syncer data directory (outside the addon folder). This is required for `.zip`-based addon installs and ensures settings persist across addon upgrades.

Runtime config file location (exact path varies by platform)
- Windows: `C:\Users\<username>\AppData\Local\bohning\usdb_syncer\transcoder_config.json`
- macOS: `~/Library/Application Support/bohning/usdb_syncer/transcoder_config.json`
- Linux: `~/.local/share/bohning/usdb_syncer/transcoder_config.json`

Manual editing (advanced)
1) Close USDB Syncer.
2) Edit `transcoder_config.json` at the path above.
3) Restart USDB Syncer.

Note: The repository contains [config.json.example](config.json.example:1) as a template for reference. It is not the runtime config file that USDB Syncer reads.

Default config excerpt
```json
{
  "version": 2,
  "auto_transcode_enabled": false,
  "target_codec": "h264",
  "audio": {
    "audio_transcode_enabled": false,
    "force_transcode_audio": false,
    "audio_codec": "aac",
    "mp3_quality": 0,
    "vorbis_quality": 10.0,
    "aac_vbr_mode": 5,
    "opus_bitrate_kbps": 160,
    "audio_normalization_enabled": false,
    "audio_normalization_method": "loudnorm",
    "audio_normalization_use_usdb_defaults": true,
    "audio_normalization_target": -18.0,
    "audio_normalization_true_peak": -2.0,
    "audio_normalization_lra": 11.0
  },
  "h264": { "profile": "high", "pixel_format": "yuv420p", "crf": 18, "preset": "fast", "container": "mp4" },
  "vp8": { "crf": 10, "cpu_used": 4, "container": "webm" },
  "hevc": { "profile": "main", "pixel_format": "yuv420p", "crf": 18, "preset": "faster", "container": "mp4" },
  "vp9": { "crf": 20, "cpu_used": 4, "deadline": "good", "container": "webm" },
  "av1": { "crf": 20, "cpu_used": 8, "container": "mkv" },
  "general": { "hardware_encoding": true, "hardware_decode": true, "backup_original": true, "backup_suffix": "-source", "timeout_seconds": 600, "verify_output": true, "min_free_space_mb": 500, "force_transcode_video": false, "max_resolution": null, "max_fps": null, "max_bitrate_kbps": null },
  "usdb_integration": { "use_usdb_resolution": true, "use_usdb_fps": true },
  "verification": { "enabled": false, "tolerance_preset": "balanced", "custom_i_tolerance": null, "custom_tp_tolerance": null, "custom_lra_tolerance": null }
}
```

Important paths and behavior
- Runtime config file location: USDB Syncer data directory `transcoder_config.json` (created on first run by [`core.config.load_config()`](core/config.py:204))
  - Windows: `C:\Users\<username>\AppData\Local\bohning\usdb_syncer\transcoder_config.json`
  - macOS: `~/Library/Application Support/bohning/usdb_syncer/transcoder_config.json`
  - Linux: `~/.local/share/bohning/usdb_syncer/transcoder_config.json`
- Backup originals: when enabled, originals are preserved as name-source.ext (see default [`core.config.GeneralConfig`](core/config.py:153))
- Automatic run: executes after each download via the USDB Syncer hooks system
- Force transcode (video): set `general.force_transcode_video` to true to force video transcoding even if the input already matches the target codec and settings. Useful for testing or ensuring fresh encodes. Applies to both automatic and batch workflows
- Force transcode (audio): set `audio.force_transcode_audio` to force audio re-encoding even when codec/container already match (disables stream-copy optimization)
- Optional limits: max_resolution/max_fps/max_bitrate_kbps are applied by codec handlers (see [`core/codecs.py`](core/codecs.py))
- Optional USDB settings integration: use_usdb_resolution/use_usdb_fps can read values from USDB Syncer settings for display and per-file transcode operations (see [`core.transcoder._apply_limits()`](core/transcoder.py:921))
  - Batch note: batch candidate discovery uses your configured general.max_resolution/general.max_fps values directly. If you leave those as null, videos that only exceed USDB limits will not appear as batch candidates

## Audio codec support (standalone audio)

The addon can transcode standalone audio referenced by SyncMeta to these output codecs:

- AAC (`aac`) — common default; typically written as `.m4a`
- MP3 (`libmp3lame`) — widest compatibility; written as `.mp3`
- Vorbis (`libvorbis`) — open format; written as `.ogg`
- Opus (`libopus`) — very efficient; written as `.opus`

Note: These codecs require a working FFmpeg build. Some builds omit certain encoders (see troubleshooting).

## Audio normalization (standalone audio)

When audio normalization is enabled, the addon adjusts perceived loudness so tracks play at a more consistent volume.

Two methods are supported:

- **EBU R128 loudnorm (two-pass)**: rewrites audio to a target integrated loudness (LUFS) and true-peak (dBTP)
- **ReplayGain**: writes ReplayGain tags when supported by the output container and your player

User-friendly terms

- **LUFS**: a measurement of perceived loudness (more negative = quieter)
- **EBU R128**: a standard for measuring/normalizing loudness
- **True Peak (dBTP)**: a peak measurement that helps avoid clipping after normalization

## Transcoding decisions (what triggers a transcode)

The addon now uses strict matching against your configuration rather than a loose “Unity-compatible” check. A file will be transcoded if any of the following is true:
- Target codec mismatch (e.g., file is VP9 but target_codec is h264)
- General caps exceeded: resolution, FPS, or bitrate higher than configured maximums
- Codec setting mismatch for the selected target codec
  - H.264: pixel_format or profile does not match your configuration
  - HEVC: pixel_format or profile does not match your configuration
  - VP8/VP9/AV1: only the codec must match; additional properties are not checked for decision

Notes
> **Note:** The container format is part of the transcoding decision. If the file extension doesn't match the target container configured for the codec, the video will be transcoded.
>
> **Note:** Container format is determined from file extension. If a file has the wrong extension, container mismatch detection may be inaccurate.

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
- Global-only controls: toggle [`core.config.GeneralConfig.hardware_encoding`](core/config.py:153) and [`core.config.GeneralConfig.hardware_decode`](core/config.py:153) to affect all codecs
- Auto-selection: when hardware encoding is enabled, the addon selects the best available accelerator via [`core.hwaccel.get_best_accelerator()`](core/hwaccel.py:79)
- Current support: Intel QuickSync only, implemented by [`core.hwaccel.QuickSyncAccelerator`](core/hwaccel.py:121). The architecture permits future accelerators
- AV1 behavior: if targeting AV1 and hardware encoding is enabled, QSV is used when available; otherwise encoding falls back to software AV1 encoders (prefers libsvtav1, then libaom-av1). Encoder selection code path: [`core.codecs.AV1Handler.build_encode_command()`](core/codecs.py:521)

Note: If you set max_resolution or max_fps, the addon may disable hardware decoding for that run to avoid hardware decode + filter pipeline issues, while still using hardware encoding when possible.

## Live progress, abort, and enhanced logging

What you will see during a transcode
- The addon parses FFMPEG stderr in real time to extract progress from lines like time=HH:MM:SS.xx.
- Progress is logged roughly every 10% completion. Logging occurs inside [`core.transcoder._execute_ffmpeg()`](core/transcoder.py:715) when it encounters time=...
- Before the encode starts, the log includes video properties and the exact FFMPEG command. See [`core.transcoder.process_video()`](core/transcoder.py:459)
  - Example entries: Analyzing video..., Video analysis: codec=..., resolution=..., FFMPEG command: ..., Transcoding video (...)
- On completion, a summary is printed with total wall time and approximate realtime speed.

How to abort an in-progress transcode
- Click Abort in USDB Syncer. The addon observes both single-transcode and batch-transcode abort sources.
- Abort attempts graceful termination of the active FFmpeg process. Response time is usually quick but can be delayed if FFmpeg isn't producing output. The system will attempt SIGTERM then force-kill if needed. Implementation: [`core.transcoder._execute_ffmpeg()`](core/transcoder.py:715)
- Partial outputs are cleaned automatically: temporary .transcoding* files are removed on abort or failure.
- Batch transcode uses the same underlying termination behavior for the active FFmpeg process via the batch worker in [`batch/worker.py`](batch/worker.py).

Notes
- Abort response is usually quick, but can be delayed if FFmpeg isn't producing output.
- The general timeout still applies as a hard cap: `general.timeout_seconds`. On timeout, the process is terminated in [`core.transcoder._execute_ffmpeg()`](core/transcoder.py:715)

Performance expectation
- On modest Intel iGPU hardware, a 3-minute video typically encodes in about 1 minute with H.264 QuickSync

Check your environment
- CLI: ffmpeg -encoders | findstr qsv (Windows) or ffmpeg -encoders | grep qsv (macOS/Linux) should list h264_qsv and hevc_qsv
- Logs: when active you will see hardware encoding messages; otherwise a software fallback warning

Warning: On macOS, QuickSync is not used by this addon. It will fall back to software encoding.

## Batch Transcoding (existing library)

Batch transcoding converts an existing library of synchronized media (video + standalone audio) to your configured targets and limits.

Key points
- Preview + selection UI with filtering and live statistics
- Disk space estimate; Start is disabled when the estimate exceeds current free space
- Optional rollback protection for the batch
- Results summary + CSV export

Full walkthrough and gotchas (including filtering behavior) are documented in [docs/BATCH_TRANSCODING.md](docs/BATCH_TRANSCODING.md).

Rollback vs backups
- Rollback protection creates temporary copies in a system temp folder for the current batch operation
- Independent of rollback, the `backup_original` setting controls persistent backups stored next to your video files

## Managing Backups (Tools → Manage Media Backups...)

Use a single unified workflow to delete or restore backups from one place: scan → select → choose action (Delete or Restore) → confirm → execute → results.

How backups are discovered
  - The manager first looks for an exact stored filename in each song’s sync data:
    - `transcoder_source_fname` (video)
    - `transcoder_audio_source_fname` (audio)
  - If present and the file exists, it is treated as the backup
- If not present, it searches for files next to the active video that match: <active_video_stem><backup_suffix>*
   - The backup_suffix is configured in [docs/CONFIGURATION.md](docs/CONFIGURATION.md) and defaults to -source
     - Example with default suffix: MySong.mp4 → MySong-source.mkv or MySong-source.mp4
  - Discovery and validation logic: [`core/backup_manager.py`](core/backup_manager.py)
  
  Access
  - Tools → Manage Media Backups → [`gui/backup/dialog_orchestrator.py`](gui/backup/dialog_orchestrator.py)
  
  Unified phases
  1) Scan
     - A small progress dialog scans your library for backups. You can cancel the scan
  2) Select
     - A table lists each discovered backup with columns: Title, Artist, Backup File, Size, Date
  - Includes per-row checkboxes, a text filter, Select All / Deselect All, and live Selected count
      - Dialog implementation: [`gui.backup.selection_dialog.BackupSelectionDialog`](gui/backup/selection_dialog.py)
      - The dialog provides two actions so you can decide after selecting: Delete Selected or Restore Selected
  3) Confirm (depends on chosen action)
     - Delete: shows how many files will be deleted and the total space to be freed
     - Restore: warns that active videos will be overwritten and that a safety backup of the current video will be created first
  4) Execute
     - Delete: per-file deletion with progress and Cancel — UI: [`gui/backup/deletion_progress_dialog.py`](gui/backup/deletion_progress_dialog.py)
     - Restore: per-file restoration with progress and Cancel — UI: [`gui/backup/restore_progress_dialog.py`](gui/backup/restore_progress_dialog.py)
  5) Results
     - Delete: summary of succeeded/failed deletions and space freed — UI: [`gui/backup/deletion_results_dialog.py`](gui/backup/deletion_results_dialog.py)
     - Restore: summary of successfully restored/failed items — UI: [`gui/backup/restore_results_dialog.py`](gui/backup/restore_results_dialog.py)
  
  Restore behavior and safeguards
  - What restore does: replaces the active transcoded video with the selected backup file
  - Safety backup (temporary): before replacement, the current active video is saved next to it with a .safety-[timestamp] suffix.
  - Metadata update: after restoration, the song’s sync data is updated to reflect the active filename and stored source reference.
  - Backup deletion: after a successful restore, the selected backup file is deleted. Keep a separate copy of the backup file if you want the option to restore again later
- Space stats: the dialog always shows Total Space to Reclaim; during restore it is informational only

Deletion safeguards (unchanged)
- Multi-level confirmation: selection screen → explicit irreversible warning prompt → progress dialog
- Validation before deletion ensures the target is not the active video and that the file exists and is writable
- Space reclaim estimate updates live before you commit to deletion
- Cancel is available during both scanning and deletion; completed deletions are not reverted
- Sync metadata is updated to clear the stored transcoder_source_fname when its backup is removed
- Active transcoded videos are never deleted by this workflow

Scope and limitations
- Affects only persistent user backups created alongside your song files using the configured backup_suffix
- Does not touch temporary rollback backups created by Batch Media Transcode; those live in a separate system temp folder managed by [`batch/rollback.py`](batch/rollback.py)
- Respects your current backup_suffix setting when searching by pattern; exact matches saved in sync data are honored even if you later change the suffix

Tip: To reduce future backup accumulation, you can disable keeping new originals by setting general.backup_original to false in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). This does not affect existing backups; use the manager to remove or restore them.

## Troubleshooting

Common fixes and checks are documented in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Technical details (for advanced users)

High-level flow
1) Analyze with [`core/video_analyzer.py`](core/video_analyzer.py)
2) Decide if work is needed using strict matching against your configured settings
3) Build the FFMPEG command from the codec handler in [`core/codecs.py`](core/codecs.py)
4) Optionally enable hardware decode/encode via accelerators in [`core/hwaccel.py`](core/hwaccel.py)
5) Execute and verify; then update sync metadata and the song’s #VIDEO tag via [`core/sync_meta_updater.py`](core/sync_meta_updater.py)

Entry points and config
- Hook: USDB Syncer hooks system triggers [`core.transcoder.process_video()`](core/transcoder.py:459)
- Configuration dataclass: [`core/config.py`](core/config.py)
- Batch module: [`batch/batch.py`](batch/batch.py)

Sync tracking (critical)
- USDB_Syncer uses file mtime to decide if resources are in sync. The addon updates filenames and mtimes accordingly to prevent re-download loops. Details: [`core/sync_meta_updater.py`](core/sync_meta_updater.py)

## FAQ

- Does this run automatically? Yes. The addon subscribes to the USDB Syncer hooks system and processes each newly downloaded song.
- Will my original file be kept? Yes, if general.backup_original is true. The original is renamed to name-source.ext.
- Does it re-encode audio inside videos? Sometimes. For video outputs, audio is copied when compatible with the target container; otherwise it is re-encoded (AAC for MP4/MOV, Opus for WebM/MKV).
- Does it transcode standalone audio? Yes, when SyncMeta includes an audio file and audio transcoding is enabled in settings.
- Which container will I get? Defaults are H.264 → .mp4, VP8/VP9 → .webm, HEVC → .mp4, AV1 → .mkv, but you can override per-codec container via the `container` config key.
- Where is the config file? The runtime config is stored in the USDB Syncer data directory as `transcoder_config.json` (created on first run by [config.load_config()](config.py:157)).
- Where is the config file? The runtime config is stored in the USDB Syncer data directory as `transcoder_config.json` (created on first run by [`core.config.load_config()`](core/config.py:204)).
  - Windows: `C:\Users\<username>\AppData\Local\bohning\usdb_syncer\transcoder_config.json`
  - macOS: `~/Library/Application Support/bohning/usdb_syncer/transcoder_config.json`
  - Linux: `~/.local/share/bohning/usdb_syncer/transcoder_config.json`
- How do I turn off automatic transcoding? Set auto_transcode_enabled to false in the config JSON. The hook still loads but exits early. Batch transcoding remains available via the Tools menu.
- My videos still don’t play. Start with H.264, ensure yuv420p and CFR, and review [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Audio transcoding guide

For a dedicated audio guide (codec selection, quality recommendations, and normalization), see [docs/AUDIO_TRANSCODING.md](docs/AUDIO_TRANSCODING.md).

When does the addon skip transcoding?
- Only when the input already matches your configured target codec and its checked settings, and does not exceed any configured limits (resolution, FPS, bitrate). For H.264, that includes profile/pixel_format; for HEVC, profile/pixel_format; for VP8/VP9/AV1, the codec must match.

New in this version — progress and abort
- Can I abort a running transcode? Yes. Click Abort in USDB Syncer. The addon detects the abort via [`core.utils.is_aborted()`](core/utils.py:118) and attempts to terminate the active FFmpeg process
- How quickly does abort take effect? Response time is usually quick, but can be delayed if FFmpeg isn't producing output
- Will partial files be left behind? No. Temporary .transcoding* files are removed on abort or failure in [`core.transcoder.process_video()`](core/transcoder.py:459)
- Does batch transcode respect abort? Yes. The active video is stopped immediately and the batch ends; rollback is offered if enabled

## Development

For information on the release process and how to package the addon, see [docs/RELEASE.md](docs/RELEASE.md).

## AI assistance and attribution

This project was developed with significant assistance from AI coding tools and large language models. These tools were used to help draft and refine source code and tests, generate documentation, and suggest refactorings and implementation approaches.

Notably, this repository used [Kilo Code by Kilocode.ai](https://kilocode.ai) as an AI coding assistant during development and documentation work.

All AI-assisted outputs were reviewed, edited, and validated by human maintainers; responsibility for the final design and behavior remains with the maintainers.

Provenance and licensing
- All contributions—whether AI-assisted or human-authored—are provided under the MIT License. See [LICENSE](LICENSE:1).
- The maintainers take care to avoid incorporating third-party copyrighted material or code with incompatible licenses. If you believe any content infringes your rights or includes non-compliant material, please open an issue so we can investigate and remediate promptly.

Contributor guidance for AI-assisted changes
- Disclose AI assistance in your pull request description with a brief note (for example: AI-assisted (Kilo Code / Kilocode.ai): summary of how the tool was used) and ensure you can explain and justify the changes.
- Verify that outputs are original and free of material you do not have rights to include; provide links and attribution when adapting code from public sources.
- Keep commit and PR authors human; do not list AI systems as authors or co-authors. Use normal attribution for human collaborators.
- Review, run, and test AI-generated code locally before submitting.
- Avoid including secrets or sensitive data in prompts to third-party AI services.

Acknowledgment
- We acknowledge the role of modern AI tools in accelerating parts of the development and documentation process.
- Kilo Code by Kilocode.ai was one of the AI tools used.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE:1) for details.
