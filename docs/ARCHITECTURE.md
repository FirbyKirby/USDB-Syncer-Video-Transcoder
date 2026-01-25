# Transcoder Addon Architecture

This document is the canonical architecture reference for the Transcoder addon (a USDB Syncer addon). It is written for maintainers who need to:

- understand the runtime call flows (automatic transcode, batch transcode, backup management)
- make safe changes around file replacement and SyncMeta updates
- extend codecs or hardware acceleration without breaking the existing pipeline

## Goals and non-goals

Goals

- Transcode downloaded media (video + standalone audio) into configured target codecs and containers.
- Preserve USDB Syncer synchronization correctness by updating SyncMeta and the song text file.
- Provide user-facing safety mechanisms:
  - persistent backups of originals
  - optional rollback protection for batch operations
  - abort support for both automatic and batch workflows

Non-goals

- No attempt is made to keep an internal job queue or resume partially completed transcodes.
- No migration documentation is provided; this project is pre-release.

## Glossary

- SyncMeta: USDB Syncer per-song metadata file and database record (see USDB Syncer codebase). The addon reads and updates the video `ResourceFile` attached to `sync_meta.video`.
- Resource ID: `sync_meta.video.file.resource` identifies the remote asset. The addon must preserve it when changing the local filename.
- SongLogger: USDB Syncer per-song logger, created via [`song_logger()`](../__init__.py:37).

## Code map (modules and responsibilities)

| Area | Module(s) | Responsibility |
|---|---|---|
| Addon entrypoint | [`__init__.py`](../__init__.py) | Hook registration, config bootstrap, GUI menu wiring |
| Config | [`core/config.py`](../core/config.py) | Dataclasses, JSON load/save, config path |
| Analysis + decision (video) | [`core/video_analyzer.py`](../core/video_analyzer.py) | `ffprobe` analysis and decision logic |
| Analysis + decision (audio) | [`core/audio_analyzer.py`](../core/audio_analyzer.py) | `ffprobe` analysis and decision logic for standalone audio |
| Normalization (audio) | [`core/audio_normalizer.py`](../core/audio_normalizer.py) | Optional EBU R128 `loudnorm` (two-pass) and ReplayGain tagging |
| Transcoding engine | [`core/transcoder.py`](../core/transcoder.py) | Orchestrate analysis, command build, execute ffmpeg, file replacement, SyncMeta update (video + audio entry points) |
| Codec command builder | [`core/codecs.py`](../core/codecs.py) | Registry and per-codec `ffmpeg` argument builders (video + audio) |
| Hardware acceleration | [`core/hwaccel.py`](../core/hwaccel.py) | Accelerator registry + QuickSync probing |
| SyncMeta + song text update | [`core/sync_meta_updater.py`](../core/sync_meta_updater.py) | Preserve resource ID, update filename + mtime, update or insert `#VIDEO:` and `#AUDIO:`/`#MP3:` |
| Abort + progress parsing | [`core/utils.py`](../core/utils.py) | Abort signal aggregation, ffmpeg progress parsing helpers |
| Batch workflow (GUI) | [`batch/orchestrator.py`](../batch/orchestrator.py), [`batch/worker.py`](../batch/worker.py) | Scan, selection UI, worker thread, progress/abort, results |
| Rollback (batch) | [`batch/rollback.py`](../batch/rollback.py), [`workers/rollback_backup_worker.py`](../workers/rollback_backup_worker.py), [`gui/batch/rollback_backup_progress_dialog.py`](../gui/batch/rollback_backup_progress_dialog.py) | Pre-transcode temp backups (non-blocking creation), manifest, restore-on-abort |
| Persistent backup management (GUI) | [`core/backup_manager.py`](../core/backup_manager.py) plus dialogs | Discover, delete, restore persistent backups |

## Configuration model

The addon stores configuration in a JSON file in the USDB Syncer data directory. The path is computed by [`core.config.get_config_path()`](../core/config.py:198) and created on first load by [`core.config.load_config()`](../core/config.py:204).

The root configuration object is [`core.config.TranscoderConfig`](../core/config.py:179). Two design choices are important when changing settings behavior:

- Global operational toggles live under [`core.config.GeneralConfig`](../core/config.py:153) (timeouts, free-space guard, hardware toggles, backup toggles).
- Codec-specific quality and container settings live under per-codec dataclasses (for example [`core.config.H264Config`](../core/config.py:22)).
- Standalone audio settings live under [`core.config.AudioConfig`](../core/config.py:67) (audio codec, codec-specific quality controls, normalization).

## Runtime entry points

### Addon load

On import, the addon:

1. ensures a default config exists by calling [`core.config.load_config()`](../core/config.py:204) from [`__init__.py`](../__init__.py:19)
2. registers the download hook via [`hooks.SongLoaderDidFinish.subscribe()`](../__init__.py:71)
3. attempts to register GUI menu items (if USDB Syncer GUI is available) via [`_register_gui_hooks()`](../__init__.py:74)

### Automatic transcode after download

USDB Syncer triggers the hook handler [`on_download_finished()`](../__init__.py:35) after a song finishes downloading.

The handler:

- loads current settings (`auto_transcode_enabled`)
- verifies `ffmpeg` is available
- locates the current video path from `song.sync_meta`
- delegates the actual work to [`core.transcoder.process_video()`](../core/transcoder.py:459)
- if a standalone audio file is present in `song.sync_meta.audio` and audio transcoding is enabled, delegates to [`core.transcoder.process_audio()`](../core/transcoder.py:42)

### GUI entry points

When running in GUI mode, the addon adds tools menu actions for:

- settings: [`gui.settings_gui.show_settings()`](../gui/settings_gui.py:1)
- batch transcode: [`batch.orchestrator.BatchTranscodeOrchestrator.start_batch_workflow()`](../batch/orchestrator.py:243)
- backup management: [`gui.backup.dialog_orchestrator.BackupDialogOrchestrator.start_workflow()`](../gui/backup/dialog_orchestrator.py:1)

## High-level data flow

```mermaid
flowchart TD
  A[SongLoaderDidFinish hook] --> B[transcoder.process_video]
  B --> C[video_analyzer.analyze_video]
  C --> D[video_analyzer.needs_transcoding]
  D -->|skip| E[return success no-op]
  D -->|transcode| F[codecs.*Handler.build_encode_command]
  F --> G[transcoder._execute_ffmpeg]
  G --> H{verify_output enabled}
  H -->|yes| I[video_analyzer.analyze_video output]
  H -->|no| J[finalize files]
  I --> J
  J --> K[sync_meta_updater.update_sync_meta_video]
```

Audio flow (standalone audio)

```mermaid
flowchart TD
  A[SongLoaderDidFinish hook] --> B[transcoder.process_audio]
  B --> C[audio_analyzer.analyze_audio]
  C --> D{needs audio work?}
  D -->|skip (stream copy)| E[finalize files]
  D -->|encode| F[codecs.get_audio_codec_handler.build_encode_command]
  F --> G{normalization enabled}
  G -->|yes| H[audio_normalizer.analyze loudnorm pass 1]
  H --> I[audio_normalizer.inject loudnorm pass 2 filter]
  G -->|no| J[encode without normalization]
  I --> K[transcoder._execute_ffmpeg]
  J --> K
  K --> L{verify_output enabled}
  L -->|yes| M[audio_analyzer.analyze_audio output]
  L -->|no| N[finalize files]
  M --> N
  N --> O[sync_meta_updater.update_sync_meta_audio]
```

## Core transcoding pipeline

The orchestration logic lives in:

- video: [`core.transcoder.process_video()`](../core/transcoder.py:459)
- audio: [`core.transcoder.process_audio()`](../core/transcoder.py:42)

### Step 1: Analyze input video

The addon runs `ffprobe` and parses metadata into a [`VideoInfo`](../core/video_analyzer.py:22) struct via [`video_analyzer.analyze_video()`](../core/video_analyzer.py:60).

Important details

- Container is derived from the file extension (`path.suffix`) in [`_parse_ffprobe_output()`](../core/video_analyzer.py:105). This is pragmatic but means a mismatched extension can produce incorrect container decisions.

### Step 2: Resolve effective limits

Before deciding whether to transcode, the addon may translate USDB Syncer settings into effective limits via [`core.transcoder._apply_limits()`](../core/transcoder.py:921):

- if `use_usdb_resolution` is enabled, it pulls the resolution from USDB Syncer settings
- if `use_usdb_fps` is enabled, it pulls FPS from USDB Syncer settings
- to avoid unintended upscaling, it only applies these limits when the source exceeds them

### Step 3: Decide whether transcoding is needed

Decision logic is implemented in [`video_analyzer.needs_transcoding()`](../core/video_analyzer.py:232).

The file is transcoded if any of the following are true:

- codec mismatch for the configured target codec
- container mismatch with the configured codec container
- general caps exceeded (or exact mismatch depending on USDB integration flags):
  - resolution
  - fps
  - bitrate
- handler reports incompatibility via [`CodecHandler.is_compatible()`](../core/codecs.py:61)

Force mode

- If [`core.config.GeneralConfig.force_transcode_video`](../core/config.py:153) is enabled, the addon will transcode even when `needs_transcoding` returns false (but it still performs analysis and disk space checks).

### Step 4: Select codec handler and build the ffmpeg command

Codec handlers are registered in [`codecs.CODEC_REGISTRY`](../core/codecs.py:98) and retrieved via [`codecs.get_codec_handler()`](../core/codecs.py:108).

Each handler builds a full `ffmpeg` command line in `build_encode_command` (for example [`codecs.H264Handler.build_encode_command()`](../core/codecs.py:140)).

Common conventions across handlers

- always uses `ffmpeg -y -hide_banner`
- enforces constant frame rate output via `-vsync cfr`
- for MP4 and MOV outputs, enables fast-start when applicable (`-movflags +faststart`)
- optionally applies:
  - scaling and padding filters for resolution
  - `fps=` filter for frame rate
  - `-maxrate` and `-bufsize` when `max_bitrate_kbps` is set
- audio strategy depends on container compatibility:
  - MP4/MOV: prefer copying `aac/mp3/alac`, otherwise encode to AAC
  - WebM/MKV: prefer copying `opus/vorbis`, otherwise encode to Opus

Standalone audio strategy

- The audio handler controls the output container and codec.
- When `audio_normalization_enabled` is false and `force_transcode_audio` is false, the audio pipeline may use stream copy when codec and container already match.
- When normalization is enabled, stream copy is disabled because filters require re-encoding.

### Step 5: Hardware acceleration (encode and decode)

Hardware acceleration selection happens inside [`core.transcoder.process_video()`](../core/transcoder.py:459) using:

- encoder selection: [`hwaccel.get_best_accelerator()`](../core/hwaccel.py:79)
- decoder selection (when encoding is software): [`hwaccel.get_best_decoder_accelerator()`](../core/hwaccel.py:104)

Important behavior

- Encode and decode are independently controlled by:
  - [`core.config.GeneralConfig.hardware_encoding`](../core/config.py:153)
  - [`core.config.GeneralConfig.hardware_decode`](../core/config.py:153)
- If hardware encoding is enabled and resolution or FPS filters are requested, hardware decoding is explicitly disabled for that run to avoid hardware-surface filter pipeline issues.

Current implementation

- Only Intel QuickSync is implemented via [`hwaccel.QuickSyncAccelerator`](../core/hwaccel.py:121).
- QuickSync is only supported on `win32` and `linux` as declared by [`QuickSyncAccelerator.capabilities()`](../core/hwaccel.py:125).
- Availability is probed by running a short `ffmpeg` encode attempt.

Availability probing and caching

- QuickSync availability is cached in-process via the `_qsv_available` module variable in [`hwaccel.py`](../core/hwaccel.py:117).

Codec-level implications

- When QuickSync encoding is active, handlers force `-pix_fmt nv12` (hardware-friendly) even if the configured pixel format differs.

### Step 6: Execute ffmpeg with progress, timeout, and abort

Execution is implemented in [`core.transcoder._execute_ffmpeg()`](../core/transcoder.py:715).

Key behaviors

- Parses stderr lines containing `time=` using [`utils.parse_ffmpeg_progress()`](../core/utils.py:111) and logs progress periodically.
- Supports a UI progress callback (used by batch UI) to show percent, FPS, speed, and ETA.
- Enforces a hard timeout via [`core.config.GeneralConfig.timeout_seconds`](../core/config.py:153).

Abort behavior

- The abort signal is aggregated by [`utils.is_aborted()`](../core/utils.py:118), which checks:
  - USDB Syncer per-download job abort flags
  - batch abort flags from [`BatchAbortRegistry`](../batch/worker.py:30)
- On abort:
  - Windows: kills the full process tree via `taskkill /F /T`
  - POSIX: sends terminate then escalates to kill if needed

Temporary file cleanup

- The engine writes to a temp path like `MyVideo.transcoding.mp4` and removes partial files on failure or abort.
- Deletion uses retries and an optional rename-first strategy in [`transcoder._safe_unlink()`](../core/transcoder.py:881) to cope with Windows file locking.

Subprocess environment hygiene

- All `ffmpeg` and `ffprobe` subprocess invocations run under USDB Syncer’s [`LinuxEnvCleaner`](../core/transcoder.py:15) wrapper, which helps provide a consistent environment for child processes.

### Step 7: Verify output (optional)

If [`core.config.GeneralConfig.verify_output`](../core/config.py:153) is enabled, the addon re-runs `ffprobe` on the temporary output via [`video_analyzer.analyze_video()`](../core/video_analyzer.py:60) and fails the operation if the output cannot be analyzed.

### Step 8: Finalize files and update SyncMeta

File finalization is deliberately conservative:

- output is written to a temporary `.transcoding` path first
- on success, the output is moved into place via `Path.replace` (atomic replacement semantics)
- optional persistent backup of the original is done by renaming the original to `stem + backup_suffix + original_extension`
- if no backup is taken and the output extension differs from the input extension, the old source file is deleted after the new output is successfully in place

SyncMeta update

- After placing the final output, the addon updates USDB Syncer metadata via [`sync_meta_updater.update_sync_meta_video()`](../core/sync_meta_updater.py:25).
- The call preserves the original resource ID and updates:
  - video filename (`fname`)
  - microsecond mtime (`mtime`) via USDB Syncer `get_mtime`
  - the song text file `#VIDEO:` header via [`update_txt_video_header()`](../core/sync_meta_updater.py:226)
- It persists changes using `sync_meta.synchronize_to_file()` then `sync_meta.upsert()`.

Custom data keys

The addon stores operational metadata in `sync_meta.custom_data`:

- `transcoder_source_fname`
- `transcoder_output_fname`
- `transcoder_codec`
- `transcoder_profile`
- `transcoder_timestamp`

These values are expected to be strings (see [`update_sync_meta_video()`](../core/sync_meta_updater.py:25)).

Related helper

- [`sync_meta_updater.check_already_transcoded()`](../core/sync_meta_updater.py:317) provides a quick check based on stored `custom_data` and the existence of the output file.

## Persistent backups (original preservation)

The addon supports user-visible persistent backups next to the song files.

- Video backups use `transcoder_source_fname` in `sync_meta.custom_data`.
- Audio backups use `transcoder_audio_source_fname` in `sync_meta.custom_data`.

Creation paths

- Automatic and batch transcodes back up the original file in [`core.transcoder.process_video()`](../core/transcoder.py:459) by renaming it before replacing the output.
- [`sync_meta_updater.update_sync_meta_video()`](../core/sync_meta_updater.py:25) also has an optional `backup_source` mode (kept for flexibility), but the main pipeline currently performs the backup itself and calls the updater with `backup_source` disabled.

Discovery and management

- Backups are discovered via:
  1. exact filename stored in `transcoder_source_fname`
  2. fallback glob search using `backup_suffix` next to the active video
- The implementation is in [`backup_manager.discover_backups()`](../core/backup_manager.py:60).

## Batch transcoding architecture

Batch transcoding is a GUI-driven workflow orchestrated by [`BatchTranscodeOrchestrator`](../batch/orchestrator.py:224).

It can include:

- video candidates (analyzed via [`video_analyzer.analyze_video()`](../core/video_analyzer.py:60))
- standalone audio candidates (analyzed via [`audio_analyzer.analyze_audio()`](../core/audio_analyzer.py:43))

There is also a non-GUI batch helper module, [`batch/batch.py`](../batch/batch.py), which exposes iterator-style discovery via [`find_videos_needing_transcode()`](../batch/batch.py:45). The current GUI workflow does not call this module directly.

### Phase 1: Scan

- Runs in a background thread [`batch.orchestrator.ScanWorker`](../batch/orchestrator.py:112)
- Enumerates SyncMeta records in the song directory, analyzes media, and selects candidates using:
  - [`video_analyzer.analyze_video()`](../core/video_analyzer.py:60)
  - [`video_analyzer.needs_transcoding()`](../core/video_analyzer.py:232)
  - [`audio_analyzer.analyze_audio()`](../core/audio_analyzer.py:43)

### Phase 2: Preview and selection

- The preview UI is [`BatchPreviewDialog`](../gui/batch/preview_dialog.py:1).
- The orchestrator builds estimates using [`BatchEstimator`](../batch/estimator.py:1).

### Phase 3: Execute

- If rollback protection is enabled, the orchestrator first creates pre-transcode rollback backups via [`BatchTranscodeOrchestrator._create_rollback_backups()`](../batch/orchestrator.py:476). Backup copies are created on a background thread (see [`RollbackBackupWorker.run()`](../workers/rollback_backup_worker.py:1)) while a modal progress dialog is shown (see [`RollbackBackupProgressDialog`](../gui/batch/rollback_backup_progress_dialog.py:1)). The user can cancel this phase, which aborts the batch before transcoding begins.
- Work is performed on a `QThread` in [`BatchWorker.run()`](../batch/worker.py:112).
- For each selected candidate, the worker calls the appropriate engine:
  - [`core.transcoder.process_video()`](../core/transcoder.py:459)
  - [`core.transcoder.process_audio()`](../core/transcoder.py:42)
  and forwards progress updates to the UI.

Abort propagation

- The progress dialog emits an abort request that calls [`BatchWorker.abort()`](../batch/worker.py:104).
- The abort implementation sets a flag in [`BatchAbortRegistry`](../batch/worker.py:30) for the current song id.
- The core ffmpeg loop checks abort state through [`utils.is_aborted()`](../core/utils.py:118).

### Phase 4: Results

- Summary and export live in [`BatchResultsDialog`](../gui/batch/results_dialog.py:1).

## Rollback protection (batch-only)

Rollback is optional and only applies to batch operations.

Design

- Before any transcoding starts, the orchestrator enables rollback (creates a unique temp directory) via [`RollbackManager.enable_rollback()`](../batch/rollback.py:70), then creates per-media copies of the originals on a background thread via [`RollbackBackupWorker.run()`](../workers/rollback_backup_worker.py:1) while showing [`RollbackBackupProgressDialog`](../gui/batch/rollback_backup_progress_dialog.py:1). This keeps the UI responsive during large backup batches.
- Users can cancel backup creation, which aborts the batch before any transcoding begins.
- If rollback backup creation fails, the orchestrator prompts whether to continue the batch (potentially without rollback protection).
- After each successful transcode, it records an entry in the rollback manifest via [`RollbackManager.record_transcode()`](../batch/rollback.py:105).
- On user abort, the orchestrator offers rollback and performs restore operations via [`RollbackManager.rollback_all()`](../batch/rollback.py:153).

Rollback correctness details

- Rollback updates SyncMeta to point back to the restored original media by creating a new `ResourceFile` via [`ResourceFile.new()`](../batch/rollback.py:14).
- Rollback entries are applied in reverse order.

Backup preservation rule

After a successful batch (no abort), the orchestrator applies a special rule: if a persistent user backup already existed before the batch, it is overwritten with the pre-transcode version from the rollback backup directory. This is implemented in [`BatchTranscodeOrchestrator._apply_backup_preservation_rule()`](../batch/orchestrator.py:571).

## Backup management (delete/restore persistent backups)

Backup management is a separate GUI workflow, started by [`BackupDialogOrchestrator.start_workflow()`](../gui/backup/dialog_orchestrator.py:1).

Discover

- Runs in a worker thread and collects [`BackupInfo`](../core/backup_manager.py:17) records.

Delete

- Deletes selected backups and clears `transcoder_source_fname` when it exactly matches the deleted backup filename.
- Core logic is [`backup_manager.delete_backups_batch()`](../core/backup_manager.py:242).

Restore

- Restores by overwriting the active video with the selected backup.
- Creates a temporary safety copy of the current active video first (`.safety-<timestamp>`).
- Performs an atomic replacement pattern via `os.replace`.
- Updates SyncMeta and the song `#VIDEO:` header.
- Deletes the backup file after successful restore.
- Core logic is [`backup_manager.restore_backup()`](../core/backup_manager.py:288).

## Extension points

### Add a new target codec

1. Implement a new `CodecHandler` subclass.
2. Register it with [`codecs.register_codec`](../core/codecs.py:101).
3. Ensure `build_encode_command`:
   - supports the addon’s filters and audio strategy (or documents limitations)
   - respects `hw_encode_enabled` and `hw_decode_enabled` semantics
4. Update `TargetCodec` in [`core.config.TargetCodec`](../core/config.py:14) and GUI selectors if the codec is user-facing.

### Add a new hardware accelerator

1. Implement a `HardwareAccelerator` subclass.
2. Register it with [`hwaccel.register_hwaccel`](../core/hwaccel.py:63).
3. Implement:
   - platform support and availability probing
   - decoder mapping for relevant codecs via `get_decoder`
   - encoder availability checks if the accelerator has per-encoder constraints
4. Ensure priority ordering in [`hwaccel.get_best_accelerator()`](../core/hwaccel.py:79) matches desired selection behavior.

## Known constraints and design trade-offs

- Container detection for decision-making is based on the file extension, not `ffprobe` container metadata.
- Hardware decode is intentionally conservative and may be disabled when filters are active.
- QuickSync support is limited to Windows and Linux.
- Windows file locking is explicitly handled with retries in temp file cleanup.

## Related documentation

- Batch usage walkthrough: [`docs/BATCH_TRANSCODING.md`](BATCH_TRANSCODING.md)
- Configuration reference: [`docs/CONFIGURATION.md`](CONFIGURATION.md)
- Troubleshooting: [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- Audio guide: [`docs/AUDIO_TRANSCODING.md`](AUDIO_TRANSCODING.md)
