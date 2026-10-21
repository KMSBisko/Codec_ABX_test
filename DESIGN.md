# Local Codec ABX Tester — Design Document

## 1. Product Overview

Desktop application for conducting locally-executed ABX listening tests to compare the perceptual quality of two audio codec processing pipelines (Side A vs Side B). Runs entirely offline with no network dependency, using offline pre-processing to render A/B/X audio before trials and enforcing strict validation (sample-rate matching, alignment, loudness normalization) before allowing trials.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────┐
│                  PySide6 GUI (main.py)               │
│  Input Config │ Codec Config │ Playback │ Diagnostics│
├──────────────────────────────────────────────────────┤
│  AudioPipeline   │ ABXEngine    │ SynchronizedABXPlayer │ ExperimentLogger │
│  (preprocess)    │ (trials)     │ (playback)              │ (export)        │
├──────────────────────────────────────────────────────┤
│                  Data Models (models.py)             │
├──────────────────────────────────────────────────────┤
│  ffmpeg / ffprobe │ sounddevice │ numpy │ pysilence  │
└──────────────────────────────────────────────────────┘
```

**Tech Stack:** Python 3.11+, PySide6, sounddevice (PortAudio), numpy, pysilence, ffmpeg/ffprobe. Packaged via PyInstaller for Windows EXE distribution.

---

## 3. Data Models (`models.py`)

### TrialResult
Per-trial outcome: trial index, what X actually was (A/B), user answer, correctness flag, UTC timestamp.

### PreprocessStatus
Tracks preprocessing state: phase label, output paths for A and B, sample rates, durations, LUFS values, alignment lag in samples, ready flag, status message.

### SampleRateMode
- `NATIVE` — keep source audio's native sample rate
- `FORCE_48K` — resample to 48 kHz before codec processing

### CodecProfile & Catalogs
Each profile defines: id, UI display name, ffmpeg encoder name, container extension, available bitrates, optional simulated label, extra FFmpeg args, decode input format hint.

**Available Codecs and Bitrate Options:**

| Codec | Encoder | Container | Available Bitrates (kbps) | Notes |
|-------|---------|-----------|--------------------------|-------|
| No-op / Lossless passthrough | pcm_s16le | wav | 0 (unprocessed) | Pipeline noop; no encode/decode |
| Opus | libopus | opus | 16, 24, 32, 48, 64, 96, 128, 160, 192, 256, 320 | CBR (`-vbr off`) |
| AAC | aac | m4a | 48, 64, 80, 96, 128, 160, 192, 256, 320 | — |
| Ogg Vorbis | libvorbis | ogg | 64, 96, 128, 160, 192, 256, 320 | — |
| SBC | sbc | sbc | 96, 128, 160, 192, 256, 320 | Explicit `-f sbc` format |
| aptX | aptx | aptx | 352 | Fixed bitrate, explicit `-f aptx` |
| aptX HD | aptx_hd | aptx_hd | 576 | Fixed bitrate, explicit `-f aptx_hd` |
| LDAC | libldac | ldac | 330, 660, 990 | Explicit `-f ldac` |

**Simulated Codecs (AAC-based fallback):** When the native encoder is unavailable in the installed FFmpeg build, simulated profiles fall back to AAC at equivalent bitrates:
- Simulated aptX → AAC at 256, 320, or 352 kbps
- Simulated aptX HD → AAC at 384, 512, or 576 kbps
- Simulated LDAC → AAC at 330, 660, or 990 kbps

Pipeline stages additionally include a "noop" passthrough option for multi-stage chains.

---

## 4. Audio Pipeline (`audio_pipeline.py`)

Orchestrates offline preprocessing: source WAV/FLAC → two processed WAV files through configurable multi-stage codec pipelines.

### Processing Phases (sequential)

| Phase | Description |
|-------|-------------|
| **1. Input Prep** | Convert FLAC to WAV if needed via ffmpeg |
| **2. Resample** | Optional 48 kHz resample (soxr preferred) when FORCE_48K mode selected |
| **3. Codec Pipeline** | Per-side, multi-stage encode→decode chains (up to 4 stages). Each stage outputs PCM WAV that feeds the next stage. Supports noop passthrough and lossless copy. aptX/aptX HD processed at 48 kHz internally. |
| **4. Bandwidth Limit** | Optional per-side low-pass filter at 14, 16, or 18 kHz cutoff |
| **5. Alignment** | Cross-correlation (FFT-based) detects codec-induced latency difference between A and B; pads or trims to compensate |
| **6. Length Trim** | Both sides trimmed to minimum length for sample-level alignment |
| **7. Loudness Normalize** | EBU R128 gain adjustment to -16 LUFS target with no-clipping guard; reports loudness delta (target < 0.1 dB) |
| **8. Validation** | Checks: sample rate match, length match, non-zero duration. Sets `ready = true` on pass |

Runs in a background thread. Supports mid-processing cancellation and isolated temp directory cleanup.

---

## 5. ABX Engine (`abx_engine.py`)

Manages trial logic: randomizes X assignment (50/50 A or B), records answers, tracks running score, computes one-tailed binomial p-value for statistical significance. Supports session reset to clear all trials and counters.

---

## 6. Audio Playback (`player.py`)

`SynchronizedABXPlayer` provides low-latency playback via sounddevice with:
- Shared global timeline — A, B, X play from the same position
- Instantaneous source switching with 256-sample linear crossfade to prevent clicks
- Loop mode with configurable start/end region
- Timeline scrubbing support
- Device selection with optional WASAPI exclusive mode on Windows

---

## 7. Experiment Logger (`logger.py`)

Records session metadata and trial results. Exports to JSON (full structured data) or CSV (trial_index, x_is, answer, correct, timestamp_utc).

---

## 8. GUI Application (`main.py`)

### Layout

```
┌─────────────────────────────────────────────────────┐
│ [EN / VI]  [Dark Mode] [OLED] [Zoom +/-] [Fullscreen] │  ← Toolbar
├─────────────────────────────────────────────────────┤
│ ╔══ Input & Configuration ══╗                       │
│ ║ File: [__________] [Browse]  SR Mode: [Native ▼]  │
│ ║ [Preprocess A/B] [Cancel] [Export JSON] [CSV]     │
│ ║ Device: [_______▼] [Refresh] ☐ Exclusive           │
│ ╚════════════════════════════╝                       │
├─────────────────────────────────────────────────────┤
│ ╔══ Codec Configuration ══╗                          │
│ ║ Side A: [Codec▼] [Bitrate▼]                        │
│ ║ Side B: [Codec▼] [Bitrate▼]                        │
│ ║ Label Mapping: [Fixed ▼]                            │
│ ║ Stages A: [-][1][+] ☐ BW Limit [14kHz▼]             │
│ ║ Stages B: [-][1][+] ☐ BW Limit [14kHz▼]             │
│ ║ Pipeline Stage Table (up to 4 stages)               │
│ ╚════════════════════════════╝                       │
├─────────────────────────────────────────────────────┤
│ ╔══ ABX Playback Controls ══╗                        │
│ ║ [Play A] [Play B] [Play X] [Pause] [Stop]          │
│ ║ ───timeline slider───  00:00.000 / 00:00.000       │
│ ║ ☐ Loop  Start: [__s]  End: [__s]                   │
│ ║ Trial: 0  Score: 0/0  p-value: 1.0000              │
│ ║ [X=A] [X=B] [Cancel Session]                        │
│ ╚════════════════════════════╝                       │
├─────────────────────────────────────────────────────┤
│ ╔══ Diagnostics ══╗                                  │
│ ║ [preprocess results, trial details, audit trail...] │
│ ╚════════════════════════════╝                       │
├─────────────────────────────────────────────────────┤
│  Status: Ready                                      │  ← Status Bar
└─────────────────────────────────────────────────────┘
```

### Features

- **Language:** English and Vietnamese via dictionary-based translation; all UI strings update dynamically on language switch
- **Themes:** Light (default), Dark, OLED (pure black) — toggled independently
- **Zoom:** 70%–150% font scaling with +/-/reset controls
- **A/B Label Mapping:** Fixed mode (A always = codec A) or Blind Random mode (labels may swap each trial, tracked in diagnostics audit trail)
- **Pipeline Stages:** Up to 4 configurable stages per side; stage count controlled via +/- buttons that show/hide rows

### Diagnostics Panel Content

The diagnostics panel displays four sections:

**1. Session Summary** — Input file path, sample rate mode (native/force_48k), processing mode (single_stage/cascaded_pipeline), pipeline stage counts for A and B, bandwidth limit enabled/disabled per side with cutoff frequency in Hz, target sample rate, resample engine used, label mapping mode (fixed/blind_random), total trials, correct trials, one-tailed p-value.

**2. Pipeline Stage Details** — For each stage on Side A and Side B: codec name, bitrate, input→output sample rate conversion, FFmpeg encode engine identifier, FFmpeg decode engine identifier. Stages are labeled A1, A2... and B1, B2... for identification.

**3. Trial Details** — Per-trial log showing: trial number, what X was labeled as (A/B), user's answer (A/B), correctness flag. Empty state shows "no trials yet".

**4. Mapping Audit Trail** — Current display-to-source mapping (e.g., `A→source_A | B→source_B`). Per-trial audit showing: trial number, X label with resolved source, answer with resolved source, active mapping at time of trial (A→?, B→?), whether the mapping changed for the next trial (Yes/No/?). This provides full traceability of blind-random label swaps.

### User Workflow

1. Select input file (WAV/FLAC), configure sample rate mode
2. Configure codec pipelines: select codecs, bitrates, stage counts, bandwidth limits, mapping mode
3. Click **Preprocess A/B** — background processing with status updates; review validation in diagnostics
4. Select output device, optionally enable exclusive mode
5. Conduct trials: play A, B, X references → use timeline/loop for comparison → submit answer (X=A or X=B) → score and p-value update immediately
6. Export results as JSON or CSV

### Application States

`IDLE → CONFIGURED → PROCESSING → VALIDATED/WARNING → TRIAL_ACTIVE → RESULTS`

---

## 9. File Structure

```
Codec_ABX_test/
├── run_abx.py              # Entry point
├── requirements.txt        # Python dependencies
├── app/
│   ├── main.py             # PySide6 GUI, workflow orchestration
│   ├── models.py           # Dataclasses, enums, codec catalog
│   ├── audio_pipeline.py   # FFmpeg multi-stage preprocessing
│   ├── abx_engine.py       # Trial randomization, scoring, p-value
│   ├── player.py           # Synchronized low-latency playback
│   └── logger.py           # Session logging, JSON/CSV export
├── third_party/ffmpeg/bin/ # Bundled ffmpeg/ffprobe binaries
├── tools/                  # Build scripts (PyInstaller), audio generator
└── examples/               # Example input audio file
```

---

## 10. Dependencies

| Dependency | Purpose |
|-----------|---------|
| PySide6 | GUI framework |
| sounddevice | Audio playback (PortAudio, WASAPI exclusive) |
| numpy | Array operations, cross-correlation, crossfade ramps |
| pysilence | EBU R128 LUFS loudness measurement |
| ffmpeg / ffprobe | Codec encode/decode chains, format conversion, filtering |
| PyInstaller | Windows single-file EXE packaging (build-time) |