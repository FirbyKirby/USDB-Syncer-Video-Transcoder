# Transcoder — Troubleshooting

This guide helps diagnose and resolve common problems with the transcoder addon.

## Is the addon running?

Checklist
- After starting USDB_Syncer, the log should include: Transcoder addon loaded
- The addon subscribes to the USDB Syncer hooks system in [__init__.py](../__init__.py)
- After a song download completes, look for log lines like: Analyzing video: ..., FFMPEG command: ..., Transcode completed in ...

Log file location
- Windows: %LocalAppData%/usdb_syncer/usdb_syncer.log
- macOS: ~/Library/Application Support/usdb_syncer/usdb_syncer.log
- Linux: ~/.local/share/usdb_syncer/usdb_syncer.log

Path is defined by the USDB Syncer application paths utility.

## Where are my settings stored?

Recommended editing method
- Use the GUI: **Tools → Media Transcoder Settings**

Runtime config file location (advanced)
- Windows: `C:\Users\<username>\AppData\Local\bohning\usdb_syncer\transcoder_config.json`
- macOS: `~/Library/Application Support/bohning/usdb_syncer/transcoder_config.json`
- Linux: `~/.local/share/bohning/usdb_syncer/transcoder_config.json`

Note: [config.json.example](../config.json.example:1) in the repository is a template for reference, not the runtime config file.

## Common errors and fixes

1) FFMPEG not available - skipping video transcode
- Cause: USDB_Syncer cannot find ffmpeg/ffprobe
- Fix: Install FFMPEG and ensure ffmpeg and ffprobe are in your PATH, or set the FFMPEG folder in USDB_Syncer settings
- Verify: Run ffmpeg -version and ffprobe -version in a terminal

Audio note
- The same FFMPEG installation is used for both video and audio transcoding.

2) Failed to analyze video file
- Cause: ffprobe could not parse the source file
- Fix: Ensure the downloaded file is a valid video. Try re-downloading the song. Check that your FFMPEG installation works
- Where it happens: analysis in [`core.video_analyzer.analyze_video()`](../core/video_analyzer.py:60)

2a) Failed to analyze audio file / no audio stream found
- Cause: ffprobe could not parse the file, or the file has no audio stream
- Fix:
  - Ensure the downloaded file is valid audio (or a container with an audio stream)
  - Re-download the song
  - Verify ffprobe can read the file
- Where it happens: analysis in [`core.audio_analyzer.analyze_audio()`](../core/audio_analyzer.py:43)

3) Insufficient disk space for transcoding
- Cause: Free space below min_free_space_mb
- Fix: Free up disk space or lower general.min_free_space_mb in **Tools → Transcoder Settings** (or by editing the runtime config file listed above)
 - Check setting: [`core.config.GeneralConfig`](../core/config.py:153)

4) FFMPEG encoding failed or FFMPEG timeout after Ns
- Cause: Encoder error or operation exceeded general.timeout_seconds
- Fix: Try a faster preset or higher CRF. Verify your FFMPEG build supports the selected encoder (e.g., h264_qsv). Increase timeout_seconds if needed
- Where it happens: command execution in [`core.transcoder.process_video()`](../core/transcoder.py:459)

4a) Audio codec encoder missing (libmp3lame / libvorbis / libopus)
- Symptoms:
  - FFMPEG fails immediately when transcoding audio
  - Log shows “Unknown encoder 'libmp3lame'” (or `libvorbis`, `libopus`)
- Cause: Your FFmpeg build does not include that encoder
- Fix:
  - Install an FFmpeg build that includes the desired encoder(s)
  - Or switch `audio.audio_codec` to a codec your FFmpeg supports
- Verify:
  - List encoders:
    - macOS/Linux: `ffmpeg -encoders | grep -E "libmp3lame|libvorbis|libopus|\s+aac\b"`
    - Windows: `ffmpeg -encoders | findstr libmp3lame libvorbis libopus aac`

4b) AAC output fails
- Cause: Some FFmpeg builds may have limited AAC encoder options
- Fix:
  - Verify `aac` encoder exists (`ffmpeg -encoders | grep aac`)
  - Try a different FFmpeg build if AAC output consistently fails

5) Transcoded output verification failed
- Cause: Output was produced but could not be parsed by ffprobe
- Fix: Re-try with verify_output left enabled. Consider a different preset/CRF, or switch to H.264 for maximum compatibility
- Verification step occurs after encode in [`core.transcoder.process_video()`](../core/transcoder.py:459)

5a) Transcoded audio output verification failed
- Cause: Output was produced but ffprobe could not read it, or duration is invalid
- Fix:
  - Keep `general.verify_output` enabled
  - Try a different audio codec/container (AAC/MP3/Vorbis/Opus)
  - Verify your FFmpeg build can both encode and decode the chosen format
- Verification step occurs after encode in [`core.transcoder.process_audio()`](../core/transcoder.py:42)

6) Could not backup original
- Cause: File permission issues or destination in use
- Fix: Close media players, ensure write permissions on the song folder. Originals are renamed to name-source.ext using the suffix from [`core.config.GeneralConfig.backup_suffix`](../core/config.py:153)

7) Could not update .txt #VIDEO header
- Cause: The song text file couldn’t be modified
- Fix: Ensure the .txt is writable. The update is performed by [`core.sync_meta_updater.update_txt_video_header()`](../core/sync_meta_updater.py:226)

8) Hardware encoding requested but no suitable accelerator found. Falling back to software
- Cause: No supported accelerator detected (only Intel QuickSync is currently supported) while `general.hardware_encoding` is enabled
- Fix: Ensure an Intel iGPU with drivers is present and your FFMPEG build includes QSV encoders (h264_qsv, hevc_qsv, vp9_qsv, av1_qsv). Otherwise, encoding proceeds in software. You can also disable hardware encoding globally.
- Detection/selection logic: [`core.hwaccel.get_best_accelerator()`](../core/hwaccel.py:79), QuickSync implementation [`core.hwaccel.QuickSyncAccelerator`](../core/hwaccel.py:121)

## Abort during transcode

Symptoms
- You clicked Abort and saw Transcode aborted by user in the log
- The video stopped quickly but you want to confirm cleanup or rollback
- Abort seems to take longer than expected

Explanation
- Abort attempts graceful termination of the active FFmpeg process. Response time is usually quick but can be delayed if FFmpeg isn't producing output. The system attempts SIGTERM and then force-kills if needed. Implementation: [`core.transcoder._execute_ffmpeg()`](../core/transcoder.py:715)
- In a batch operation, selected videos that weren't processed are marked Aborted. Unselected videos are not included in the results report. If you choose rollback, successfully transcoded videos are restored and marked Rolled Back.

What to do
- Response time is usually quick, but can be delayed if FFmpeg isn't producing output. Give the UI a moment to refresh
- If it has not stopped after a couple of seconds, check the log for Transcode aborted by user and any FFmpeg shutdown messages
- If FFMPEG becomes unresponsive, the general timeout still applies as a hard cap: `general.timeout_seconds`. On timeout, the process is terminated in [`core.transcoder._execute_ffmpeg()`](../core/transcoder.py:715)

Cleanup behavior
- Temporary .transcoding* files are removed automatically on abort or failure. Completed outputs remain. If rollback is enabled, you will be prompted to restore processed media

## Why was my already-H.264 (or HEVC/VP8) video transcoded?

The addon now uses strict matching against your configuration. It will transcode even if the source codec is already Unity-compatible when:
- The codec does not match your target_codec (e.g., source is HEVC but target is h264)
- General caps are exceeded: resolution, FPS, or bitrate higher than configured maximums
- Codec settings do not match your target (per codec):
  - H.264: pixel_format or profile differ from your config
  - HEVC: pixel_format or profile differ from your config
  - VP8/VP9/AV1: must match the target codec

What you can do
- If you want to keep existing H.264s as-is, set your H.264 profile/pixel_format to match them
- Otherwise, keep your preferred settings and let the addon standardize the library during the next transcode pass

## How to check hardware encoding status

From logs
- When active, you will see a message about hardware encoding being used. Otherwise, a warning about falling back to software appears from [`core.transcoder.process_video()`](../core/transcoder.py:459)

Note: If you set general.max_resolution or general.max_fps, the addon may disable hardware decoding for that run (it will log this decision) to avoid hardware decode + filter pipeline issues, while keeping hardware encoding enabled when possible.

AV1 specifics
- With hardware encoding enabled, AV1 uses QSV when available; otherwise it falls back to software encoders in order: libsvtav1 → libaom-av1. If your FFMPEG lacks SVT-AV1, expect libaom-av1 or software-only operation

## Batch transcoding (existing library)

If you already have a library of downloaded songs and want to convert them in bulk:
- Use the GUI menu: Tools → Batch Media Transcode
- A preview and selection dialog appears with filtering and live statistics
  - Orchestrator: [`batch/orchestrator.py`](../batch/orchestrator.py)
  - Preview dialog: [`gui/batch/preview_dialog.py`](../gui/batch/preview_dialog.py)
  - Progress and abort: [`gui/batch/progress_dialog.py`](../gui/batch/progress_dialog.py)
  - Results and export: [`gui/batch/results_dialog.py`](../gui/batch/results_dialog.py)
  - Estimation and space checks: [`batch/estimator.py`](../batch/estimator.py)
  - Optional rollback protection: [`batch/rollback.py`](../batch/rollback.py)

Common issues in the new workflow

1) Preview generation is taking a long time
- Cause: The system scans synchronized songs and runs ffprobe analysis per video; large libraries or slow disks increase time
- Fix:
  - Let the initial scan complete once; subsequent runs are faster with fewer candidates
  - Reduce the number of candidates by tightening your configuration so fewer videos qualify
  - Ensure ffprobe is on a fast local disk and your antivirus is not scanning video files
- Where it happens: preview generation in [`batch.orchestrator.BatchTranscodeOrchestrator._generate_preview()`](../batch/orchestrator.py:259), analysis in [`core.video_analyzer.analyze_video()`](../core/video_analyzer.py:60)

2) Disk space estimate seems inaccurate
- Explanation: Estimates are heuristic and based on codec, CRF, resolution, and bitrate limits
- Fix:
  - Verify your CRF and preset choices; higher quality settings increase size
  - If you have max_bitrate_kbps set, the estimate will clamp to that value
  - Leave extra headroom beyond the estimate; the dialog disables Start if free space is below the required total
- Implementation: see [`batch/estimator.py`](../batch/estimator.py)

3) Rollback didn’t restore all videos
- Cause:
  - Backup files were moved or deleted outside the tool
  - Rollback protection was disabled, or permanent backups were not available
- Fix:
  - Re-run the batch and enable rollback protection in the preview dialog
  - Check the log for missing backup warnings during rollback
  - If permanent backups are enabled via configuration, verify originals with the configured suffix still exist
- Implementation: [`RollbackManager.rollback_all()`](../batch/rollback.py:153), manifest handling in [`RollbackManager.enable_rollback()`](../batch/rollback.py:70)

4) Export to CSV failed
- Cause: Destination not writable or file locked by another application
- Fix:
  - Choose a writable location (e.g., your Documents folder)
  - Close any application holding the CSV open and retry
  - Retry with a different filename
- Implementation: see [`gui/batch/results_dialog.py`](../gui/batch/results_dialog.py)

5) Progress window does not update or abort seems delayed
- Explanation: UI updates depend on encoder output cadence, so the UI can lag even when an abort has been requested
- Fix:
  - Give the UI a moment to refresh after the abort
  - If it does not stop within a couple of seconds, check the log for Transcode aborted by user and any FFmpeg shutdown messages
- Implementation: progress is emitted by [`batch/worker.py`](../batch/worker.py) and abort is handled by the orchestrator in [`batch/orchestrator.py`](../batch/orchestrator.py)

From command line
- List hardware encoders: ffmpeg -encoders | findstr qsv (Windows) or ffmpeg -encoders | grep qsv (macOS/Linux)
- Quick test encode: ffmpeg -f lavfi -i nullsrc=s=64x64:d=0.1 -c:v h264_qsv -f null -

## Restoring from Video Backups — troubleshooting

Entry point: Tools → Manage Media Backups...; run scan, select backups, then click Restore Selected in the selection dialog.

Symptoms
- The confirmation step warns about overwriting active videos
- Total Space to Reclaim is still shown (it is informational during restore)
- A restore failed on one or more items

What restore does
- Replaces the active transcoded video with the selected backup file
- Before replacement, the current active media is saved alongside it with a .safety-[timestamp] suffix; creation occurs in [`core.backup_manager.restore_backup()`](../core/backup_manager.py:288). This safety backup is temporary and is automatically deleted after a successful restore
- After a successful restore, the selected backup file is deleted. If you want the option to restore again later, keep a separate copy of the backup file

Common issues and fixes
1) Restore failed: Backup file missing
- Cause: The backup file no longer exists at the recorded path
- Fix: Re-scan in Tools → Manage Media Backups.... Verify the backup still exists next to the song’s media file, then choose Restore Selected again

2) Restore failed: Permission denied
- Cause: The song folder or target file is read-only or locked by another app
- Fix: Close media players, ensure write permission to the song folder, and retry

3) I restored the wrong version — how do I undo?
- You generally cannot undo a completed restore using the safety backup, because it is automatically deleted after a successful restore
- If you want the option to restore again, keep the original backup file (or a copy of it) before running restore, then run restore again using that file

4) Why is Total Space to Reclaim hidden?
- Total Space to Reclaim is always shown. During restore it is informational only (space reclaim applies to deletion)

Access
- Single entry point: Tools → Manage Media Backups... (choose Delete Selected or Restore Selected in the selection dialog)

## Videos won’t play

 Try these steps
- Set target_codec to h264 in **Tools → Media Transcoder Settings** (or in the runtime config file) and use high profile, pixel_format yuv420p in your H.264 settings. With strict matching, the addon will convert non-conforming inputs to these exact settings
- Ensure the file extension is .mp4 and the song’s #VIDEO header points to the new filename
- Confirm the addon updated metadata: the original was renamed to name-source.ext and the new file exists
- Re-run the download so the addon processes the video again

Compatibility notes
- H.264/AVC in MP4 is the safest choice
- HEVC/H.265 offers better compression
- VP8/VP9/AV1 are excellent open alternatives

## Verify FFMPEG is working

Run these in a terminal
- ffmpeg -version
- ffprobe -version
- ffmpeg -encoders | grep -E "h264_qsv|hevc_qsv|vp9_qsv|av1_qsv"  # Check for QSV support

Audio encoders (optional checks)
- macOS/Linux: `ffmpeg -encoders | grep -E "libmp3lame|libvorbis|libopus|\s+aac\b"`
- Windows: `ffmpeg -encoders | findstr libmp3lame libvorbis libopus aac`

## Audio normalization troubleshooting

### Loudnorm (EBU R128) fails

Symptoms
- Logs show “Audio normalization failed; continuing without normalization”
- Output is created but normalization is skipped

Common causes
- The file is very short or silent, so the `loudnorm` analysis cannot produce valid measurements
- Your FFmpeg build has issues with the `loudnorm` filter

Fixes
- Try ReplayGain instead (`audio.audio_normalization_method: "replaygain"`)
- Disable normalization to confirm basic transcoding works
- Verify loudnorm filter exists:
  - macOS/Linux: `ffmpeg -filters | grep loudnorm`
  - Windows: `ffmpeg -filters | findstr loudnorm`

Where it happens
- Two-pass analysis and filter injection in [`core/audio_normalizer.py`](../core/audio_normalizer.py)

### ReplayGain tagging doesn’t show up in my player

Explanation
- ReplayGain support depends on both the output container and your playback software.
- Some players ignore tags entirely.

Fixes
- Prefer loudnorm if you need consistent loudness across players.
- Verify your player supports ReplayGain for the chosen format.

### Verification appears to do nothing

Symptoms

- You enabled “Verify normalization before transcoding”, but the log does not mention verification.
- Audio is always normalized (or always transcoded) even when you expect skipping.

What to check

1. Confirm the setting is enabled in the Transcoder settings dialog.
2. Look for verification-related lines in the log around the song processing.

Notes

- Verification runs to decide whether **normalization work** is necessary.
- Verification can run even when the addon must transcode due to codec/container mismatch (for example, `aac/.m4a` to `vorbis/.ogg`). In that case, a “within tolerance” result means the addon should transcode **without** applying normalization.
- For `replaygain` method, loudness consistency depends on player support for ReplayGain tags.

## Audio quality issues

### Quality seems worse than expected

Likely causes
- Lossy-to-lossy transcoding (e.g., MP3 → MP3, or AAC → MP3) can compound quality loss
- Using conservative/low-quality settings

Recommendations
- Prefer AAC or Opus for good quality at moderate sizes
- For MP3, try `mp3_quality: 0–2`
- For Opus, try `opus_bitrate_kbps: 128–160`

## FAQ (audio)

### Does audio transcoding change the audio inside videos?

No. Standalone audio transcoding only affects the file referenced by SyncMeta audio. Video transcoding may copy or re-encode the *video’s* audio stream depending on container compatibility.

### Why did the addon re-encode audio that already looked correct?

Common reasons
- `force_transcode_audio` is enabled
- normalization is enabled (filters require re-encoding)
- the container/extension does not match the target output container

If not found, install FFMPEG and add it to your PATH, or set an explicit FFMPEG directory in USDB_Syncer settings. USDB_Syncer’s availability check is implemented by the USDB Syncer utilities module.

## How to report issues

Include the following in your report
- USDB_Syncer version and OS
- CPU/GPU details (especially whether you have Intel QuickSync)
- The contents of the Transcoder runtime config file `transcoder_config.json` (especially auto_transcode_enabled)
  - Preferred: Open **Tools → Media Transcoder Settings** and copy relevant settings
  - If you need to find the file on disk:
    - Windows: `C:\Users\<username>\AppData\Local\bohning\usdb_syncer\transcoder_config.json`
    - macOS: `~/Library/Application Support/bohning/usdb_syncer/transcoder_config.json`
    - Linux: `~/.local/share/bohning/usdb_syncer/transcoder_config.json`
- A short excerpt of usdb_syncer.log around the time of the failure (redact personal paths if needed)
- The exact error message (copy from the log)

Where to look in code
- Transcode pipeline: [`core/transcoder.py`](../core/transcoder.py)
- Analysis: [`core/video_analyzer.py`](../core/video_analyzer.py)
- Codec command builders: [`core/codecs.py`](../core/codecs.py)
- Hardware selection: [`core.hwaccel.get_best_accelerator()`](../core/hwaccel.py:79), [`core.hwaccel.QuickSyncAccelerator`](../core/hwaccel.py:121)
- Sync updates: [`core.sync_meta_updater.update_sync_meta_video()`](../core/sync_meta_updater.py:25)
