# Transcoder — Configuration Guide

This guide explains every configuration option, default values, recommended settings, and provides ready-to-use examples for common goals.

Where the file lives
- The runtime config is created on addon load in the USDB Syncer data directory as `transcoder_config.json` by [`core.config.load_config()`](../core/config.py:204).
- Exact path varies by platform:
  - Windows: `C:\Users\<username>\AppData\Local\bohning\usdb_syncer\transcoder_config.json`
  - macOS: `~/Library/Application Support/bohning/usdb_syncer/transcoder_config.json`
  - Linux: `~/.local/share/bohning/usdb_syncer/transcoder_config.json`

Note: The repository includes [config.json.example](../config.json.example:1) as a template for reference. It is not the runtime config file.

How to edit
- **Recommended**: Use the GUI via **Tools → Transcoder Settings** in USDB Syncer.
- **Manual (advanced)**: Close USDB Syncer, edit `transcoder_config.json` in the USDB Syncer data directory (see paths above), then restart USDB Syncer.

Note: JSON does not support comments. Examples below include only the keys you need to change. Unspecified options keep their existing values.

## Top-level structure

Options are defined by [`core.config.TranscoderConfig`](../core/config.py:179):
- version: configuration schema version (int)
- auto_transcode_enabled: enable/disable automatic video transcoding after song downloads (bool)
- target_codec: which codec to encode to: h264, hevc, vp8, vp9, or av1
- h264: H.264-specific options from [`core.config.H264Config`](../core/config.py:22)
- hevc: HEVC-specific options from [`core.config.HEVCConfig`](../core/config.py:40)
- vp8: VP8-specific options from [`core.config.VP8Config`](../core/config.py:32)
- vp9: VP9-specific options from [`core.config.VP9Config`](../core/config.py:50)
- av1: AV1-specific options from [`core.config.AV1Config`](../core/config.py:59)
- audio: standalone audio transcoding options from [`core.config.AudioConfig`](../core/config.py:67)
- general: global options from [`core.config.GeneralConfig`](../core/config.py:153)
- usdb_integration: optional USDB Syncer settings integration from [`core.config.UsdbIntegrationConfig`](../core/config.py:171)
- verification: normalization verification options from [`core.config.VerificationConfig`](../core/config.py:121)

## Option reference and defaults

H.264 block [`core.config.H264Config`](../core/config.py:22)
- profile: baseline, main, or high. Default: high
- pixel_format: output pixel format. Default: yuv420p
- crf: quality control (lower = higher quality). Default: 18
  - Note: When using QuickSync (QSV), the CRF value is mapped to QSV's global quality (ICQ) parameter, which uses a different scale than x264/x265 CRF.
- preset: encoder speed/quality tradeoff. Default: fast
- container: output container extension. Default: mp4

HEVC block [`core.config.HEVCConfig`](../core/config.py:40)
- profile: main or main10. Default: main
- pixel_format: output pixel format. Default: yuv420p
- crf: quality control (lower = higher quality). Default: 18
- preset: encoder speed/quality tradeoff. Default: faster
- container: output container extension. Default: mp4

VP8 block [`core.config.VP8Config`](../core/config.py:32)
- crf: quality control (lower = higher quality). Default: 10
- cpu_used: speed/quality tradeoff: 0-5 (lower is higher quality, slower). Default: 4
- container: output container extension. Default: webm

VP9 block [`core.config.VP9Config`](../core/config.py:50)
- crf: quality control (lower = higher quality). Default: 20
- cpu_used: speed/quality tradeoff: 0-8. Default: 4
- deadline: good, best, or realtime. Default: good
- container: output container extension. Default: webm

AV1 block [`core.config.AV1Config`](../core/config.py:59)
- crf: quality control (lower = higher quality). Default: 20
- cpu_used: speed/quality tradeoff: 0-13. Default: 8
- container: output container extension. Default: mkv

General block [`core.config.GeneralConfig`](../core/config.py:153)
- hardware_encoding: enable hardware encoding if available. Default: true
- hardware_decode: allow hardware decoders. Default: true
  - ⚠️ **Hardware Decode Limitation:** Hardware decoding is primarily intended for use with hardware encoding. Using hardware decode with software encode may cause pipeline issues.
- backup_original: preserve the source file. Default: true
- backup_suffix: suffix inserted before the extension of the source; results in name-source.ext. Default: -source
- timeout_seconds: max time to allow FFMPEG to run. Default: 600
- verify_output: analyze output after encode; deletes bad outputs. Default: true
- force_transcode_video (boolean, default: false): When enabled, forces transcoding of all videos even if they already match the target codec and quality settings. Affects both single-file and batch operations
- force_transcode_audio: this setting lives under `audio.force_transcode_audio` (not `general`). See Audio block below.
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
- `general.timeout_seconds` remains the maximum wall-clock duration for a single FFMPEG run, regardless of live progress reporting
- Abort attempts graceful termination of the active FFmpeg process. If FFmpeg does not exit, the system will force-kill; `timeout_seconds` still terminates the process in [`core.transcoder._execute_ffmpeg()`](../core/transcoder.py:715)

USDB integration block [`core.config.UsdbIntegrationConfig`](../core/config.py:171)
- use_usdb_resolution: if true, uses USDB Syncer Settings → Video max resolution. Default: true
- use_usdb_fps: if true, uses USDB Syncer Settings → Video max FPS. Default: true

Warning: If you disable verify_output, corrupt outputs may slip through if FFMPEG succeeds but writes an unreadable file.

## Audio block (standalone audio) [`core.config.AudioConfig`](../core/config.py:67)

This section controls transcoding and normalization of **standalone audio files referenced by SyncMeta**.

Important notes

- This does **not** change the audio track inside videos. Video transcoding handles audio streams inside the video output as part of the video codec handlers.
- Audio processing runs:
  - automatically after download (when `audio.audio_transcode_enabled` is true), and
  - during batch operations (Tools → Batch Media Transcode) when enabled.

### Audio enable/force settings

- `audio_transcode_enabled` (boolean, default: true)
  - Enables processing of standalone audio files.
  - When false, audio files are left as-downloaded.

- `force_transcode_audio` (boolean, default: false)
  - Forces re-encoding even if the input already matches the target codec/container.
  - Also disables stream-copy optimization.

When to use `force_transcode_audio`

- You changed codec or quality settings and want to standardize your library.
- You enabled normalization and want to rewrite files.
- You suspect an audio file is damaged or inconsistent.

### Audio codec selection

- `audio_codec` (string, default: `"aac"`)
  - Allowed values: `"mp3"`, `"vorbis"`, `"aac"`, `"opus"`

Containers (how the output filename extension is chosen)

- `aac` → `.m4a`
- `mp3` → `.mp3`
- `vorbis` → `.ogg`
- `opus` → `.opus`

Note: for AAC, `.mp4` is also treated as container-compatible for stream-copy operations.

Codec selection recommendations

- **AAC (`aac`)**: best “default” for broad compatibility while keeping good quality at moderate sizes.
- **MP3 (`mp3`)**: widest compatibility (very old players/devices); often slightly larger for the same perceived quality.
- **Vorbis (`vorbis`)**: open format; support depends on player.
- **Opus (`opus`)**: very efficient; great quality/size ratio, but requires newer decoder support in some environments.

### Audio codec quality settings

Audio quality controls are codec-specific. Only the relevant setting for your chosen codec is used.

- `mp3_quality` (integer, default: 0)
  - Range: `0` to `9`
  - Lower is better quality (larger files).
  - Maps to FFmpeg/LAME VBR quality (`-q:a`).
  - Recommended:
    - `0–2`: transparent/very high quality
    - `3–5`: smaller files, still good for most content

- `vorbis_quality` (number, default: 10.0)
  - Range: `-1.0` to `10.0`
  - Higher is better quality (larger files).
  - Maps to FFmpeg Vorbis quality scale (`-q:a`).
  - Recommended:
    - `8.0–10.0`: very high quality
    - `5.0–7.0`: smaller files

- `aac_vbr_mode` (integer, default: 5)
  - Range: `1` to `5`
  - Higher is better quality (larger files).
  - Maps to FFmpeg AAC VBR mode (`-vbr`).
  - Recommended:
    - `5`: highest quality
    - `3`: balanced

- `opus_bitrate_kbps` (integer, default: 160)
  - Range: `6` to `510`
  - Target bitrate in kbps.
  - Maps to `-b:a <kbps>k`.
  - Recommended:
    - `96–128`: good quality for most music
    - `160`: very high quality for music

### Audio normalization settings

Normalization makes tracks play at a more consistent perceived loudness.

- `audio_normalization_enabled` (boolean, default: false)
  - When enabled, audio transcoding may be skipped if the file is already normalized (see smart skipping below).

- `audio_normalization_method` (string, default: `"loudnorm"`)
  - Allowed values: `"loudnorm"`, `"replaygain"`
  - `loudnorm` uses a two-pass EBU R128 workflow (measure, then apply). Files matching target format are assumed normalized.
  - `replaygain` writes ReplayGain tags when supported. Files with existing tags are skipped.

Smart skipping behavior
- **R128 (loudnorm)**: Files that match the target codec/container are assumed to be already normalized and transcoding is skipped.
- **ReplayGain**: Files that match the target codec/container are checked for existing ReplayGain tags. If tags are present, transcoding is skipped.
- To force re-normalization, enable `force_transcode_audio`.

### Normalization targets: USDB defaults vs explicit values

The addon supports two target modes:

- `audio_normalization_use_usdb_defaults` (boolean, default: true)
  - When true, the addon uses USDB Syncer-aligned targets:
    - Opus output: `-23.0` LUFS
    - Other outputs: `-18.0` LUFS
  - True peak and LRA use FFmpeg defaults.

- When false, the addon uses explicit targets from:
  - `audio_normalization_target` (LUFS)
  - `audio_normalization_true_peak` (dBTP)
  - `audio_normalization_lra` (LU)

- `audio_normalization_target` (number, default: -18.0)
  - Target integrated loudness in **LUFS**.
  - More negative is quieter.
  - Common music/karaoke targets are around `-18.0` LUFS.

- `audio_normalization_true_peak` (number, default: -2.0)
  - Target true peak in **dBTP**.
  - Helps avoid clipping after normalization.

- `audio_normalization_lra` (number, default: 11.0)
  - Target loudness range in **LU**.
  - Higher values preserve more dynamics; lower values reduce dynamics.

User-friendly terms

- **LUFS**: a measurement of perceived loudness (integrated loudness over time).
- **EBU R128**: the standard that defines how loudness is measured/normalized.
- **dBTP (true peak)**: peak measurement that better predicts clipping than simple sample peaks.

### Normalization verification settings

Normalization verification checks whether a file is already normalized *well enough* to match your configured loudness targets, before spending time re-encoding it.

Benefits

- Can **save time** by skipping work when audio is already within tolerance.
- Can **preserve quality** by avoiding unnecessary lossy-to-lossy re-encodes.

Trade-off

- Verification requires decoding the whole file (typically **about realtime**), so it can add **2–5 minutes per song** depending on duration and hardware.

## Verification block (standalone audio loudness verification)

This section controls whether the addon should measure loudness first to decide whether normalization is needed.

#### Enable verification

- `verification.enabled` (boolean, default: false)
  - Enables normalization verification for standalone audio processing.
  - When enabled, verification runs even if a transcode is required for codec/container reasons.
  - Verification is used to decide whether **normalization work** is necessary.
    - If the file is **within tolerance**, the addon skips normalization.
    - If the file is **out of tolerance**, the addon proceeds with normalization.
  - For `audio_normalization_method: loudnorm`, verification uses FFmpeg `loudnorm` in analysis mode and compares the results to your targets.
  - For `audio_normalization_method: replaygain`, verification evaluates ReplayGain state (tags and/or computed loudness) against the configured target.

Note

- Verification is also available in the **batch wizard** as an optional step. See [`docs/BATCH_TRANSCODING.md`](BATCH_TRANSCODING.md).
- For a user-friendly explanation of verification behavior, see [`docs/AUDIO_TRANSCODING.md`](AUDIO_TRANSCODING.md).

#### Tolerance presets (recommended)

- `verification.tolerance_preset` (string, default: `"balanced"`)
  - Allowed values: `"strict"`, `"balanced"`, `"relaxed"`
  - This controls how close a file must be to your targets to be considered **within tolerance**.

Preset meanings (plain language)

- **Strict**: closest match to targets. Best when you want the tightest consistency.
- **Balanced (default)**: recommended for most users. Small differences are rarely noticeable.
- **Relaxed**: least picky. Skips more files, but may allow slightly larger differences.

Technical mapping (for reference)

- Strict: ±1.0 LU integrated, +0.3 dB true peak, ±2 LU loudness range
- Balanced: ±1.5 LU integrated, +0.5 dB true peak, ±3 LU loudness range
- Relaxed: ±2.0 LU integrated, +0.8 dB true peak, ±4 LU loudness range

Most users should use presets. Only use custom tolerances if you know why you need them.

#### Advanced: custom tolerances

These settings override the selected preset when set. Leave them unset for preset behavior.

- `verification.custom_i_tolerance` (number, default: null)
  - Integrated loudness tolerance (in LU). Smaller values are stricter.

- `verification.custom_tp_tolerance` (number, default: null)
  - True peak tolerance (in dB). Controls how much measured peak may exceed your target ceiling.

- `verification.custom_lra_tolerance` (number, default: null)
  - Loudness range tolerance (in LU). Smaller values are stricter.

#### Cache behavior (automatic)

Verification measurements are cached in a local **SQLite database** so repeat runs are faster.

- First verification of a file: slow (full decode)
- Later verification: fast (cache hit) if the file has not changed

Reuse during normalization

- When verification determines a file is out of tolerance for `loudnorm`, the addon can reuse the verification measurements for the normalization run (so the loudnorm analysis does not have to run twice).

The cache is automatically invalidated when:

- the file changes (size or modified time), or
- you change targets/tolerances (the cache key includes these settings).

Cache location

- Stored in the USDB Syncer application data directory, next to `transcoder_config.json`.
- The file is created and managed automatically.

## Audio configuration examples

### Example: Keep the default AAC output, no normalization

```json
{
  "audio": {
    "audio_transcode_enabled": true,
    "audio_codec": "aac",
    "aac_vbr_mode": 5,
    "audio_normalization_enabled": false
  }
}
```

### Example: MP3 output for maximum compatibility

```json
{
  "audio": {
    "audio_transcode_enabled": true,
    "audio_codec": "mp3",
    "mp3_quality": 2
  }
}
```

### Example: Opus output + loudness normalization (recommended for consistent playback)

```json
{
  "audio": {
    "audio_transcode_enabled": true,
    "audio_codec": "opus",
    "opus_bitrate_kbps": 160,
    "audio_normalization_enabled": true,
    "audio_normalization_method": "loudnorm",
    "audio_normalization_target": -18.0,
    "audio_normalization_true_peak": -2.0,
    "audio_normalization_lra": 11.0
  }
}
```

### Backup management: how backup_suffix is used

The backup manager uses your configuration to find persistent user backups created during transcoding, for both deletion and restoration.

- Primary match: if a song’s sync data contains an exact source filename (transcoder_source_fname), that file is used as the backup if it exists
- Fallback pattern: otherwise the manager searches next to the active video for files matching
  - <active_video_stem><backup_suffix>*
  - With the default suffix -source, examples include MySong-source.mp4 or MySong-source.mkv
- Safety checks: the active video is never considered a backup, and missing or non-file paths are ignored
- Suffix changes: exact filenames stored in sync data are honored even if you later change backup_suffix; pattern-based discovery uses the current backup_suffix value
- Scope: only persistent backups in your song folders are targeted. Temporary rollback backups from batch transcoding live in a system temp directory managed by [`batch/rollback.py`](../batch/rollback.py) and are not affected

Restore-specific behavior
- What restore does: replaces the active transcoded video with the selected backup file
 - Safety backup: just before replacement, the current active media is saved alongside it with a .safety-[timestamp] suffix. Implementation: [`core.backup_manager.restore_backup()`](../core/backup_manager.py:288)

Tip: To stop creating new persistent backups, set general.backup_original to false. Existing backups remain on disk until removed or restored via Tools → Manage Media Backups... (choose Delete Selected or Restore Selected in the selection dialog).

### Hardware acceleration behavior

- Two global toggles govern all codecs: `general.hardware_encoding` and `general.hardware_decode`
- When hardware encoding is enabled, the addon auto-selects the best available accelerator via [`core.hwaccel.get_best_accelerator()`](../core/hwaccel.py:79)
- Currently supported accelerator: Intel QuickSync, implemented by [`core.hwaccel.QuickSyncAccelerator`](../core/hwaccel.py:121)
- AV1 auto-selection: AV1 attempts QSV first; if unavailable, falls back to software encoders in order: libsvtav1 → libaom-av1. See [`core.codecs.AV1Handler.build_encode_command()`](../core/codecs.py:530)

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
    "force_transcode_video": false,
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
 - The encode commands are built by codec handlers in [`core/codecs.py`](../core/codecs.py) and executed from [`core.transcoder.process_video()`](../core/transcoder.py:459)
 - Hardware accelerator selection is managed via [`core.hwaccel.get_best_accelerator()`](../core/hwaccel.py:79) and implemented for QuickSync by [`core.hwaccel.QuickSyncAccelerator`](../core/hwaccel.py:121)
 - Sync meta and #VIDEO updates are handled by [`core.sync_meta_updater.update_sync_meta_video()`](../core/sync_meta_updater.py:25)

Batch transcoding
- Use Tools → Batch Media Transcode to launch the dialog-driven workflow. The workflow is orchestrated by [`batch/orchestrator.py`](../batch/orchestrator.py) and presented through:
  - Preview and selection: [`gui/batch/preview_dialog.py`](../gui/batch/preview_dialog.py)
  - Real-time progress and abort: [`gui/batch/progress_dialog.py`](../gui/batch/progress_dialog.py)
  - Results reporting and export: [`gui/batch/results_dialog.py`](../gui/batch/results_dialog.py)
  - Estimation and space checks: [`batch/estimator.py`](../batch/estimator.py)
  - Optional rollback protection: [`batch/rollback.py`](../batch/rollback.py)
