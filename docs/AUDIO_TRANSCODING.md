# Audio Transcoding — Guide

This guide describes how the Transcoder addon processes **standalone audio files** referenced by USDB Syncer SyncMeta (the audio resource for a song).

If you are looking for the full configuration reference (including defaults), see [`docs/CONFIGURATION.md`](CONFIGURATION.md).

## What “audio transcoding” means in this addon

The addon can:

- detect the current audio codec/container via `ffprobe`
- transcode audio to a configured target codec/container
- optionally apply audio normalization
- update SyncMeta and song headers (`#AUDIO:` and `#MP3:`) to prevent re-download loops

Important scope note

- This guide is about **standalone audio files** (SyncMeta audio).
- It does not “replace the audio track inside video files.” Video transcoding may copy or re-encode the video’s embedded audio stream as part of the video output.

## Supported audio output codecs

The addon supports these target codecs (configured via `audio.audio_codec`):

| Setting | Codec | Typical file extension | Notes |
|---|---|---|---|
| `aac` | AAC (FFmpeg `aac`) | `.m4a` | Good default for broad compatibility |
| `mp3` | MP3 (FFmpeg `libmp3lame`) | `.mp3` | Widest compatibility; can be larger at equal quality |
| `vorbis` | Ogg Vorbis (FFmpeg `libvorbis`) | `.ogg` | Open format; player support varies |
| `opus` | Opus (FFmpeg `libopus`) | `.opus` | Very efficient; requires newer decoders in some environments |

Encoder availability

- These outputs depend on your FFmpeg build.
- If you get errors like “Unknown encoder 'libmp3lame'”, see [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

## Codec selection recommendations

Use these practical defaults:

- **Choose `aac`** if you want a simple default with good compatibility.
- **Choose `mp3`** if you prioritize maximum playback compatibility.
- **Choose `opus`** if you want great quality at smaller sizes and your playback environment is modern.
- **Choose `vorbis`** if you want an open codec and your player supports it.

Lossy-to-lossy warning

If your inputs are already lossy (AAC/MP3/Vorbis/Opus), transcoding them again can reduce quality. The addon tries to stream-copy when possible, but normalization or force flags will re-encode.

## Quality settings (by codec)

Only the setting for your selected codec is used.

### MP3 (`mp3_quality`)

- Range: `0` to `9` (lower is better quality)
- Uses LAME VBR quality (`-q:a`)

Recommended

- `0–2`: transparent/very high quality
- `3–5`: smaller files

### Vorbis (`vorbis_quality`)

- Range: `-1.0` to `10.0` (higher is better quality)

Recommended

- `8.0–10.0`: very high quality
- `5.0–7.0`: smaller files

### AAC (`aac_vbr_mode`)

- Range: `1` to `5` (higher is better quality)

Recommended

- `5`: highest quality
- `3`: balanced

### Opus (`opus_bitrate_kbps`)

- Range: `6` to `510`
- Target bitrate in kbps

Recommended

- `96–128`: good quality for most music
- `160`: very high quality for music

## Normalization guide

Normalization aims to make tracks play at a consistent perceived loudness.

### Key terms (user-friendly)

- **LUFS**: “how loud it sounds” (more negative means quieter)
- **EBU R128**: a standard way to measure/normalize loudness
- **True Peak (dBTP)**: helps avoid clipping after normalization

### Methods

Configure these in `audio.audio_normalization_*`.

#### 1) EBU R128 loudnorm (`audio_normalization_method: "loudnorm"`)

How it works

- Pass 1 measures loudness for the file.
- Pass 2 applies normalization using those measurements.

Why you’d use it

- You want consistent loudness across different players (it rewrites the audio).

Recommended starting point

- `audio_normalization_target`: `-18.0` LUFS
- `audio_normalization_true_peak`: `-2.0` dBTP
- `audio_normalization_lra`: `11.0` LU

#### 2) ReplayGain tagging (`audio_normalization_method: "replaygain"`)

How it works

- Writes ReplayGain tags into the output when the container/player supports it.

Why you’d use it

- You prefer tag-based adjustment (player-controlled) rather than rewriting samples.

Important limitation

- Support depends on your player and output format.

## Example configurations

### Example: MP3 for maximum compatibility

```json
{
  "audio": {
    "audio_transcode_enabled": true,
    "audio_codec": "mp3",
    "mp3_quality": 2,
    "audio_normalization_enabled": false
  }
}
```

### Example: Opus + loudnorm normalization (consistent loudness)

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

### Example: Force re-encode even if already correct

```json
{
  "audio": {
    "audio_transcode_enabled": true,
    "audio_codec": "aac",
    "force_transcode_audio": true
  }
}
```

## Troubleshooting quick links

- Encoder missing (`libmp3lame`, `libvorbis`, `libopus`): [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- Loudnorm failures / invalid measurements: [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
