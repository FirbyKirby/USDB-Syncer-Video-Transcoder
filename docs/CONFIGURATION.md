# Video Transcoder — Configuration Guide

This guide explains every configuration option, default values, recommended settings, and provides ready-to-use examples for common goals.

Where the file lives
- Created on addon load at [config.json](../config.json) by [config.load_config()](../config.py:110)

How to edit
- **Recommended**: Use the GUI via **Tools → Transcoder Settings...** in USDB Syncer.
- **Manual**: Close USDB_Syncer, edit the JSON file with a text editor, then restart USDB_Syncer.

Note: JSON does not support comments. Examples below include only the keys you need to change. Unspecified options keep their existing values.

## Top-level structure

Options are defined by [config.TranscoderConfig](../config.py:91):
- version: configuration schema version (int)
- auto_transcode_enabled: enable/disable automatic video transcoding after song downloads (bool)
- target_codec: which codec to encode to: h264, hevc, vp8, vp9, or av1
- h264: H.264-specific options from [config.H264Config](../config.py:19)
- hevc: HEVC-specific options from [config.HEVCConfig](../config.py:37)
- vp8: VP8-specific options from [config.VP8Config](../config.py:29)
- vp9: VP9-specific options from [config.VP9Config](../config.py:47)
- av1: AV1-specific options from [config.AV1Config](../config.py:56)
- general: global options from [config.GeneralConfig](../config.py:64)
- usdb_integration: optional USDB Syncer settings integration from [config.UsdbIntegrationConfig](../config.py:83)

## Option reference and defaults

H.264 block [config.H264Config](../config.py:19)
- profile: baseline, main, or high. Default: high
- pixel_format: output pixel format. Default: yuv420p
- crf: quality control (lower = higher quality). Default: 18
  - Note: When using QuickSync (QSV), the CRF value is mapped to QSV's global quality (ICQ) parameter, which uses a different scale than x264/x265 CRF.
- preset: encoder speed/quality tradeoff. Default: fast
- container: output container extension. Default: mp4

HEVC block [config.HEVCConfig](../config.py:37)
- profile: main or main10. Default: main
- pixel_format: output pixel format. Default: yuv420p
- crf: quality control (lower = higher quality). Default: 18
- preset: encoder speed/quality tradeoff. Default: faster
- container: output container extension. Default: mp4

VP8 block [config.VP8Config](../config.py:29)
- crf: quality control (lower = higher quality). Default: 10
- cpu_used: speed/quality tradeoff: 0-5 (lower is higher quality, slower). Default: 4
- container: output container extension. Default: webm

VP9 block [config.VP9Config](../config.py:47)
- crf: quality control (lower = higher quality). Default: 20
- cpu_used: speed/quality tradeoff: 0-8. Default: 4
- deadline: good, best, or realtime. Default: good
- container: output container extension. Default: webm

AV1 block [config.AV1Config](../config.py:56)
- crf: quality control (lower = higher quality). Default: 20
- cpu_used: speed/quality tradeoff: 0-13. Default: 8
- container: output container extension. Default: mkv

General block [config.GeneralConfig](../config.py:64)
- hardware_encoding: enable hardware encoding if available. Default: true
- hardware_decode: allow hardware decoders. Default: true
  - ⚠️ **Hardware Decode Limitation:** Hardware decoding is primarily intended for use with hardware encoding. Using hardware decode with software encode may cause pipeline issues.
- backup_original: preserve the source file. Default: true
- backup_suffix: suffix inserted before the extension of the source; results in name-source.ext. Default: -source
- timeout_seconds: max time to allow FFMPEG to run. Default: 600
- verify_output: analyze output after encode; deletes bad outputs. Default: true
- force_transcode (boolean, default: false): When enabled, forces transcoding of all videos even if they already match the target codec and quality settings. Affects both single-file and batch operations
- min_free_space_mb: abort if free space below this value. Default: 500
- max_resolution: optional resolution rule. Default: null
- max_fps: optional FPS rule. Default: null
- max_bitrate_kbps: optional bitrate cap (maxrate/bufsize). Default: null

Resolution and FPS behavior (important)
- When USDB integration is enabled (`use_usdb_resolution` / `use_usdb_fps`): acts as a maximum limit (videos below stay unchanged)
- When USDB integration is disabled: acts as an exact target (videos will be scaled/padded to match)
  - Note: VP9/AV1 only downscale without padding. If you configure an exact resolution larger than some sources, those sources will remain smaller after transcoding and will continue to be considered non-conforming.

Note: Hardware decoding is automatically disabled when both hardware encoding is enabled and resolution/FPS filters are requested (max_resolution and/or max_fps), to avoid decoder/encoder compatibility issues.

Timeout and abort behavior
- [config.GeneralConfig.timeout_seconds](../config.py:76) remains the maximum wall-clock duration for a single FFMPEG run, regardless of live progress reporting
- Abort attempts graceful termination of the active FFmpeg process. Response time is usually quick but can be delayed if FFmpeg isn't producing output. If FFmpeg does not exit, the system will force-kill; timeout_seconds still terminates the process in [transcoder._execute_ffmpeg()](../transcoder.py:396)

USDB integration block [config.UsdbIntegrationConfig](../config.py:83)
- use_usdb_resolution: if true, uses USDB Syncer Settings → Video max resolution. Default: true
- use_usdb_fps: if true, uses USDB Syncer Settings → Video max FPS. Default: true

Warning: If you disable verify_output, corrupt outputs may slip through if FFMPEG succeeds but writes an unreadable file.

### Backup management: how backup_suffix is used

The backup manager uses your configuration to find persistent user backups created during transcoding, for both deletion and restoration.

- Primary match: if a song’s sync data contains an exact source filename (transcoder_source_fname), that file is used as the backup if it exists
- Fallback pattern: otherwise the manager searches next to the active video for files matching
  - <active_video_stem><backup_suffix>*
  - With the default suffix -source, examples include MySong-source.mp4 or MySong-source.mkv
- Safety checks: the active video is never considered a backup, and missing or non-file paths are ignored
- Suffix changes: exact filenames stored in sync data are honored even if you later change backup_suffix; pattern-based discovery uses the current backup_suffix value
- Scope: only persistent backups in your song folders are targeted. Temporary rollback backups from batch transcoding live in a system temp directory managed by [rollback.py](../rollback.py) and are not affected

Restore-specific behavior
- What restore does: replaces the active transcoded video with the selected backup file
- Safety backup: just before replacement, the current active video is saved alongside it with a .safety-[timestamp] suffix. Implementation: [backup_manager.restore_backup()](../backup_manager.py:231)

Tip: To stop creating new persistent backups, set general.backup_original to false. Existing backups remain on disk until removed or restored via Tools → Manage Video Backups... (choose Delete Selected or Restore Selected in the selection dialog).

### Hardware acceleration behavior

- Two global toggles govern all codecs: [config.GeneralConfig.hardware_encoding](../config.py:64) and [config.GeneralConfig.hardware_decode](../config.py:64)
- When hardware encoding is enabled, the addon auto-selects the best available accelerator via [hwaccel.get_best_accelerator()](../hwaccel.py:79)
- Currently supported accelerator: Intel QuickSync, implemented by [hwaccel.QuickSyncAccelerator](../hwaccel.py:121)
- AV1 auto-selection: AV1 attempts QSV first; if unavailable, falls back to software encoders in order: libsvtav1 → libaom-av1. See [codecs.AV1Handler.build_encode_command()](../codecs.py:521)

Note: Hardware decoding is automatically disabled when both hardware encoding is enabled and resolution/FPS filters are requested (max_resolution and/or max_fps), to avoid decoder/encoder compatibility issues.

## How matching works (strict checks)

The addon treats your configuration as the exact target. During analysis, it compares the input video against your selected target_codec and the relevant codec settings. A transcode is triggered if there is a mismatch or if any general limits are exceeded.

What is compared
- General caps: max_resolution, max_fps, max_bitrate_kbps. If the input exceeds any cap, it will be transcoded
- Container: input must match the target container (e.g., .mp4, .mkv, .webm)
- H.264 target: input must already be H.264 and match pixel_format and profile
- HEVC target: input must already be HEVC and match pixel_format and profile
- VP8/VP9/AV1 target: input must already match the target codec

Notes and edge cases
- Container matching: If the source has the correct encoding but is in the wrong container, it will be transcoded to the target container.
- Note: Container format is determined from file extension, not ffprobe analysis.
- If you want to avoid re-encoding existing H.264 files, set your H.264 profile/pixel_format to match those files. Otherwise, the addon will standardize them to your chosen settings
- Resolution exactness is codec-dependent. Note: H.264, VP8, and HEVC use pad-to-exact dimensions. VP9 and AV1 currently use max-cap scaling (downscale only, no padding)

## Recommended settings

General recommendations
- Start with H.264 at CRF 18 preset fast with QuickSync enabled. Switch to HEVC for better efficiency.
- Keep verify_output: true and backup_original: true for safety

H.264 quick reference
- Highest quality: crf 16-18, preset medium
- Balanced: crf 20-22, preset fast
- Fastest: crf 22-26, preset veryfast

HEVC quick reference
- Highest quality: crf 16-18, preset slow
- Balanced: crf 20-22, preset faster
- Fastest: crf 24-28, preset veryfast

VP8 quick reference
- Highest quality: crf 8-12, cpu_used 0-1
- Balanced: crf 12-16, cpu_used 2
- Fastest: crf 16-22, cpu_used 4

## Examples by goal

1) I want maximum quality
```json
{
  "target_codec": "h264",
  "h264": { "crf": 16, "preset": "medium", "profile": "high" },
  "general": { "hardware_encoding": true }
}
```

Alternative for smallest loss at smaller sizes (ensure your environment supports HEVC):
```json
{
  "target_codec": "hevc",
  "hevc": { "crf": 16, "preset": "slow", "profile": "main" },
  "general": { "hardware_encoding": true }
}
```

2) I want fastest encoding
```json
{
  "target_codec": "h264",
  "h264": { "crf": 24, "preset": "fast" },
  "general": { "hardware_encoding": true }
}
```

3) I want smallest file sizes
```json
{
  "target_codec": "hevc",
  "hevc": { "crf": 26, "preset": "medium" },
  "general": { "hardware_encoding": true }
}
```

4) My hardware doesn’t support QuickSync
```json
{
  "target_codec": "h264",
  "general": { "hardware_encoding": false },
  "h264": { "crf": 22, "preset": "medium" }
}
```

Tip: If playback fails, switch to H.264 with profile high and pixel_format yuv420p.
With strict matching enabled, the addon will automatically transcode any non-conforming inputs to these exact settings.

## Full example configuration (annotated)

The following shows all keys. Values reflect defaults unless noted.

```json
{
  "version": 2,
  "auto_transcode_enabled": true,
  "target_codec": "h264",
  "h264": {
    "profile": "high",
    "pixel_format": "yuv420p",
    "crf": 18,
    "preset": "fast",
    "container": "mp4"
  },
  "hevc": {
    "profile": "main",
    "pixel_format": "yuv420p",
    "crf": 18,
    "preset": "faster",
    "container": "mp4"
  },
  "vp8": {
    "crf": 10,
    "cpu_used": 4,
    "container": "webm"
  },
  "vp9": {
    "crf": 20,
    "cpu_used": 4,
    "deadline": "good",
    "container": "webm"
  },
  "av1": {
    "crf": 20,
    "cpu_used": 8,
    "container": "mkv"
  },
  "general": {
    "hardware_encoding": true,
    "hardware_decode": true,
    "backup_original": true,
    "backup_suffix": "-source",
    "timeout_seconds": 600,
    "verify_output": true,
    "force_transcode": false,
    "min_free_space_mb": 500,
    "max_resolution": null,
    "max_fps": null,
    "max_bitrate_kbps": null
  },
  "usdb_integration": {
    "use_usdb_resolution": true,
    "use_usdb_fps": true
  }
}
```

Implementation details
 - The encode commands are built by codec handlers in [codecs.py](../codecs.py) and executed from [transcoder.process_video()](../transcoder.py:41)
 - Hardware accelerator selection is managed via [hwaccel.get_best_accelerator()](../hwaccel.py:79) and implemented for QuickSync by [hwaccel.QuickSyncAccelerator](../hwaccel.py:121)
 - Sync meta and #VIDEO updates are handled by [sync_meta_updater.update_sync_meta_video()](../sync_meta_updater.py:25)

Batch transcoding
- Use Tools → Batch Video Transcode to launch the dialog-driven workflow. The workflow is orchestrated by [batch_orchestrator.py](../batch_orchestrator.py) and presented through:
  - Preview and selection: [batch_preview_dialog.py](../batch_preview_dialog.py)
  - Real-time progress and abort: [batch_progress_dialog.py](../batch_progress_dialog.py)
  - Results reporting and export: [batch_results_dialog.py](../batch_results_dialog.py)
  - Estimation and space checks: [batch_estimator.py](../batch_estimator.py)
  - Optional rollback protection: [rollback.py](../rollback.py)
