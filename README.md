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

Additional bitrate notes:
- Lower bitrate options are available for direct codecs (Opus/AAC/SBC) to stress-test artifact audibility.
- Simulated LDAC now uses AAC at 330/660/990 kbps for more reliable high-bitrate preprocessing across ffmpeg builds.

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

## Build A Single Windows EXE (No Python Needed For End Users)

This project includes a PyInstaller setup for a one-file GUI executable.

1. Put ffmpeg binaries in:
  - `third_party/ffmpeg/bin/ffmpeg.exe`
  - `third_party/ffmpeg/bin/ffprobe.exe`

2. Build:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows_exe.ps1
```

3. Share this file with users:
  - `dist/run_abx.exe`

Notes:
- End users do not need Python installed.
- On first launch, some antivirus tools may scan the self-extracting exe.
- If SmartScreen warns on unsigned apps, users may need to click "More info" -> "Run anyway".

Build troubleshooting:
- Run the build from a normal (non-admin) terminal.
- If you previously hit `PermissionError` in `build/run_abx/...`, close any app/process that may lock files (Explorer preview, antivirus scan in progress), then run the build script again.
- The script now uses a fresh PyInstaller work folder each run to avoid stale lock issues.

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
    - Blinded labels: Play A/B mapping is randomized each trial (may stay same or swap).
      To reduce deterministic behavior, swap probability increases gradually with no-change streak length.
4. Click **Preprocess A/B** and verify status metrics.
  - Use **Cancel Preprocess** to stop preprocessing while ffmpeg jobs are running.
5. Select output device (enable exclusive mode if desired).
6. Perform ABX trials with Play A/B/X and answer X=A or X=B.
  - Use **Cancel ABX Session** to reset trial state and score.
7. Use **Show/Refresh Diagnostics** to review in-app post-session details:
  - A/B label mapping mode (non-revealing in top summary)
  - Trial-by-trial X label/source, answer label/source, and correctness
  - Whether mapping changed for the next trial
  - Full mapping audit is placed in a lower section so it is not immediately visible
8. Export results as JSON/CSV.

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
