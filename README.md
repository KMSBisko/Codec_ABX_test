# Local Codec ABX Tester

Desktop ABX tool for local codec listening tests with strict preprocessing and validation controls.

## What This App Enforces

- Input: WAV/FLAC (FLAC converted to WAV)
- Sample-rate modes:
  - Native sample-rate (default): keep source sample-rate as target
  - Forced 48 kHz: explicit `soxr` resample before any codec stage
- Offline codec pipeline:
  - Encode -> decode -> PCM WAV
  - Explicit sample-rate control (`-ar`) to avoid hidden conversion
- Loudness:
  - EBU R128 LUFS normalization target: `-16 LUFS`
  - Gain-only normalization
  - No-clipping guard (global attenuation if needed)
- Validation checks before ABX:
  - Same sample-rate for A/B
  - Alignment lag estimate and compensation
  - Same effective length
  - Loudness delta report (target < 0.1 dB)
- ABX engine:
  - Randomized X each trial
  - Running score + one-tailed binomial p-value
- Playback:
  - Shared global timeline
  - Scrub preserves alignment
  - A/B/X switch uses a short click-suppression crossfade
  - Device selection + exclusive mode request (WASAPI exclusive supported when available)
- Logging:
  - Export JSON/CSV results

## Supported Codec Profiles

Direct:
- Lossless (Unprocessed Reference)
- Opus (`libopus`)
- AAC (`aac`)
- SBC (`sbc` if your ffmpeg build supports it)

Simulated Bluetooth labels (implemented through available codecs/bitrates):
- Simulated aptX
- Simulated aptX HD
- Simulated LDAC

## Prerequisites

1. Python 3.11+
2. ffmpeg + ffprobe on PATH

Check:

```powershell
ffmpeg -version
ffprobe -version
```

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python run_abx.py
```

## Example Test File Included

Generate bundled example input:

```powershell
python tools/generate_example_audio.py
```

This creates:
- `examples/example_abx_input.wav`

## Basic Usage

1. Select WAV/FLAC source.
2. Choose sample-rate mode (Native or Forced 48 kHz).
3. Choose codec A/B and bitrates.
  - You can choose **Lossless (Unprocessed Reference)** as either A or B.
  - Choose **A/B label mapping** mode:
    - Fixed labels: Play A is Codec A, Play B is Codec B.
    - Blinded labels: Play A/B mapping is randomized each session.
4. Click **Preprocess A/B** and verify status metrics.
  - Use **Cancel Preprocess** to stop preprocessing while ffmpeg jobs are running.
5. Select output device (enable exclusive mode if desired).
6. Perform ABX trials with Play A/B/X and answer X=A or X=B.
  - Use **Cancel ABX Session** to reset trial state and score.
7. Export results as JSON/CSV.

## Notes On Experimental Validity

- For strongest validity, keep all OS audio enhancements disabled.
- Keep system volume fixed during the test.
- Use wired output or known stable exclusive-mode path when possible.
- A/B/X are rendered offline before trials to reduce runtime variability.

## Quick Test

```powershell
python -m pytest -q
```

(Requires `pytest`; install with `pip install pytest`.)
