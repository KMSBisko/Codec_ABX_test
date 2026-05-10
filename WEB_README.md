# Codec ABX Tester — Web Edition

A web-based ABX listening test tool for comparing audio codec pipelines. Upload an audio file, configure multi-stage codec processing chains for Side A and Side B, preprocess the audio, then perform blind ABX trials to determine whether you can distinguish between the two processed versions.

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Stage Codec Pipelines** | Chain multiple codecs per side (A / B), each with selectable bitrate |
| **Available Codecs** | Opus, AAC, Ogg Vorbis, SBC, aptX, aptX HD, LDAC, plus simulated variants of aptX, aptX HD, and LDAC (via AAC) |
| **Bandwidth Limiting** | Optional low-pass filter per pipeline side |
| **Sample Rate Modes** | Native (keep original) or Force 48 kHz (with SoXR resampler) |
| **Label Mapping** | Fixed (A→A, B→B) or Blind Random (streak-based swap probability) |
| **Preprocessing Pipeline** | 8-phase pipeline: input prep → probe → resample → codec encode/decode → bandwidth limit → cross-correlation alignment → length trim → loudness normalization (-16 LUFS target) |
| **ABX Playback** | Pre-loaded AudioBuffers, shared timeline, seamless side switching (A / X / B) without interrupting playback |
| **Play/Pause Control** | Dedicated play/pause button independent of side selection |
| **Loop Region** | Set start/end seconds for repeated playback within a region |
| **Timeline Scrubber** | Real-time seek with visual feedback |
| **Auto-Advance Trials** | After submitting an answer, the next trial starts automatically while audio keeps playing |
| **Trial Management** | Start trials, submit answers, get instant correct/incorrect feedback |
| **Statistics** | Live p-value (one-tailed binomial test), trial count, score tracking |
| **Export** | JSON or CSV export of trial results |
| **Diagnostics Panel** | Full session metadata including loudness measurements, alignment lag, codec chain details, validation notes |
| **Themes** | Light / Dark / OLED — persisted per browser via localStorage |
| **Zoom** | 75% – 150% UI scaling, persisted |
| **i18n** | English / Vietnamese toggle |
| **Keyboard Shortcuts** | `Space` = play/pause, `A` / `X` / `B` to switch side, `1` / `2` to answer |

## Quick Start — Docker (Recommended)

```bash
# Build and start
docker compose up -d --build

# Open browser
open http://localhost:5000    # macOS
start http://localhost:5000   # Windows
```

The server runs at `http://localhost:5000`. Upload a WAV or FLAC file, configure codec pipelines for Side A and Side B, click **Preprocess & Validate**, then start ABX trials.

### Stop / Restart

```bash
docker compose down          # stop & remove container
docker compose up -d         # restart
```

## Quick Start — Local Python

Prerequisites: **Python 3.10+** and **ffmpeg** on PATH (or use the bundled `third_party/ffmpeg/`).

```bash
# Install dependencies
pip install -r requirements-web.txt

# Run the web app
python -m web.app
```

Then open `http://localhost:5000`.

## How It Works

### Preprocessing Pipeline

When you click **Preprocess & Validate**, the server runs an 8-phase pipeline:

1. **Input Prep** — Convert FLAC to WAV if needed, normalize to PCM
2. **Probe** — Detect sample rate, duration, channel count via ffprobe
3. **Resample** — If Force 48 kHz mode is enabled and source differs, resample using SoXR
4. **Codec Pipeline (Side A & B)** — For each stage in the configured pipeline: encode with the selected codec at the chosen bitrate, then decode back to WAV
5. **Bandwidth Limit** — Apply optional low-pass filter per side
6. **Alignment** — Cross-correlate Side A and Side B (using first 5 seconds) to find offset, then pad/trim for sample-level alignment
7. **Length Trim** — Trim both sides to equal length
8. **Loudness Normalization** — Measure integrated loudness via ffmpeg's `ebur128` filter, apply gain to target -16 LUFS with auto-gain control (AGC) to prevent clipping

### Audio Playback Model

After preprocessing completes:
- Both Side A and Side B audio buffers are pre-loaded into the browser via Web Audio API (`AudioContext.decodeAudioData`)
- If both sides use identical codec configurations, a single shared buffer is used for all three sides (A, X, B) to ensure zero perceptual difference
- The **A / X / B** buttons switch which buffer plays at the current timeline position without stopping or pausing playback
- A dedicated **Play/Pause** button controls global playback state independently
- X is randomly assigned to either Side A or Side B audio for each trial

### Blind Random Mode

In Blind Random label mapping mode:
- The labels shown as "A" and "B" in the UI may not correspond to the actual Side A / Side B audio sources
- Label swap probability increases with the number of consecutive trials without a swap (10% base, capped at 50%)
- This ensures the tester does not know which underlying source each label maps to

## Project Structure (Web)

```
web/
├── __init__.py          # Package marker
├── app.py               # Flask application: routes, preprocessing, ABX engine
├── models.py            # Data models: codec catalog, enums, trial results
├── templates/
│   └── index.html       # Single-page UI template (Flask/Jinja2)
└── static/
    ├── css/
    │   └── style.css    # Themes (Light / Dark / OLED), responsive CSS
    └── js/
        └── app.js       # Frontend logic: audio playback, API calls, i18n
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/` | Serve the main HTML page |
| `GET`  | `/api/health` | Health check — returns `{"status": "ok"}` |
| `GET`  | `/api/catalog` | Codec catalog (id, name, bitrate options, noop flag) |
| `POST` | `/api/upload` | Upload audio file (multipart/form-data; WAV or FLAC only) |
| `POST` | `/api/config` | Submit pipeline configuration (JSON body) |
| `GET`  | `/api/config` | Return current session configuration |
| `POST` | `/api/preprocess` | Start preprocessing in background thread |
| `GET`  | `/api/preprocess/status` | Poll preprocessing progress (phase, %, message, ready flag) |
| `POST` | `/api/preprocess/cancel` | Cancel running preprocessing |
| `GET`  | `/api/audio/info` | Audio metadata (duration, sample rate, buffer identity flag) |
| `GET`  | `/api/audio/a` | Stream Side A processed audio (WAV) |
| `GET`  | `/api/audio/b` | Stream Side B processed audio (WAV) |
| `GET`  | `/api/audio/<side>/segment` | Stream a time segment of audio (query params: `start`, `end`) |
| `POST` | `/api/trial/start` | Start a new ABX trial (returns X assignment and label mapping) |
| `GET`  | `/api/trial/stats` | Current trial statistics (total, correct, p-value) |
| `POST` | `/api/trial/answer` | Submit answer for current trial (JSON: `{"answer": "A" or "B"}`) |
| `POST` | `/api/trial/reset` | Reset all trials and score |
| `GET`  | `/api/trials` | Return all trial results with statistics |
| `GET`  | `/api/export/json` | Export full session as JSON file (metadata + trials + stats) |
| `GET`  | `/api/export/csv` | Export trial results as CSV file |
| `GET`  | `/api/diagnostics` | Full diagnostics: metadata, trials, stats, label mapping state |
| `GET`  | `/api/session/info` | Basic session info (has input, filename, preprocess ready, label mode) |

## Codec Catalog

The following codecs are available for pipeline stages:

| Codec ID | Display Name | Bitrate Options | Notes |
|----------|-------------|-----------------|-------|
| `noop_passthrough` | No-op / Lossless passthrough | N/A | Reference side, no encoding loss |
| `opus` | Opus | 16–320 kbps | CBR mode |
| `aac` | AAC | 48–320 kbps | AAC-LC |
| `ogg_vorbis` | Ogg Vorbis | 64–320 kbps | — |
| `sbc` | SBC | 96–320 kbps | A2DP default codec |
| `aptx` | aptX | 352 kbps | Fixed bitrate |
| `aptx_hd` | aptX HD | 576 kbps | Fixed bitrate, 24-bit |
| `ldac` | LDAC | 330/660/990 kbps | Sony high-resolution codec |
| `sim_aptx` | Simulated aptX (AAC) | 256/320/352 kbps | AAC used as aptX proxy |
| `sim_aptx_hd` | Simulated aptX HD (AAC) | 384/512/576 kbps | AAC used as aptX HD proxy |
| `sim_ldac` | Simulated LDAC (AAC) | 330/660/990 kbps | AAC used as LDAC proxy |

## Deployment Options

### Option 1: Docker (any Linux server)

```bash
docker compose up -d --build
```

### Option 2: Python + Gunicorn (production)

```bash
pip install gunicorn
gunicorn --bind 0.0.0.0:5000 --workers 2 "web.app:app"
```

### Option 3: Heroku / Railway / Render

1. Push repo to Git provider
2. Connect to hosting platform
3. Build command: `pip install -r requirements-web.txt`
4. Start command: `gunicorn --bind 0.0.0.0:$PORT "web.app:app"`

### Option 4: Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name abx.example.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        client_max_body_size 200M;   # Large audio uploads
    }

    location /static/ {
        proxy_cache_valid 200 1d;
        add_header Cache-Control "public, max-age=86400";
    }
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_ENV` | `production` | `development` or `production` |
| `FLASK_DEBUG` | `1` | Set to `1` to enable Flask debug mode |
| `PORT` | `5000` | Port to bind to |

## Browser Compatibility

- **Chrome 90+** (recommended)
- **Firefox 88+**
- **Safari 14+**
- **Edge 90+**

Requires Web Audio API support. Audio playback uses `AudioContext.decodeAudioData()` with pre-loaded buffers for instant switching between sides.

## Architecture Notes

- **Single-user, in-memory state**: The web app maintains all session state (upload, config, preprocessing results, trials) in Python in-memory objects protected by threading locks. There is no database or persistent storage.
- **No WebSocket**: Preprocessing progress is reported via HTTP polling (`/api/preprocess/status` polled every 500ms).
- **Background thread processing**: Audio preprocessing runs in a daemon thread to avoid blocking the Flask request cycle.
- **FFmpeg dependency**: All audio processing (format conversion, codec encode/decode, loudness measurement, gain application) relies on FFmpeg. A bundled copy is checked first (`third_party/ffmpeg/`), then falls back to system PATH.

## Differences from Desktop App

| Aspect | Desktop App | Web Edition |
|--------|-------------|-------------|
| Audio Playback | Pygame / sounddevice | Web Audio API (browser) |
| File Upload | File dialog | Drag & drop / browse in browser |
| Processing | Local CPU | Server-side (Flask + threads) |
| State | In-memory | In-memory (single-user, no persistence) |
| Progress Updates | Direct callback | HTTP polling |
| Themes | System native | CSS-based (Light/Dark/OLED) |
| Deployment | EXE / Python script | Docker / any web host |

## License

Same as the desktop application. See `LICENSE` in project root.