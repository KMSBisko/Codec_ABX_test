# Handover — state of the Codec ABX tester

Working notes for whoever picks this up. Read this before `DESIGN.md`.

## The goal

Make this the defacto codec ABX testing tool. "Defacto" in this domain is earned
by three things, not by UI polish:

1. **Reproducible methodology** — a study spec is the citation unit.
2. **Verifiable results** — a posted result can be checked, not just asserted.
3. **Trust** — the tool cannot lie about what it ran, and cannot let a tester
   stop early because the p-value looked good.

Landscape for context: `foo_abx` (foobar2000) is the local/instrument standard;
`abxtests.com` (github.com/jaakkopasanen/ABX, YAML study file + shareable link
+ results by email), `listeningtest.com` and `abx.digitalfeed.net` (applies
Bonferroni) are the published-study standard.

## Current state

Phase 0 and 0b are done: `git log --oneline` → baseline, `Phase 0:`, `Phase 0b:`.

- **111 tests pass**, 7 of them real-ffmpeg pipeline + 2 web end-to-end.
- `core/` is the single source of truth for statistics, blinding, alignment and
  report wording, shared by the PyQt6 desktop and Flask web shells.
- The desktop and web shells previously carried **independent trial engines**
  with divergent randomness and divergent blinding heuristics. They cannot
  diverge silently again — `tests/test_core_discipline.py` enforces the import
  rule.

## The invariants `core/` enforces

1. **Scoring is blinding-independent.** `fixed` and `blind_random` produce
   identical arithmetic. The display mapping is presentation only.
2. **No home-brewed swap heuristics.** X is an independent fair coin per trial.
3. **The answer never leaves the server before the answer.** `TrialPlan.public()`
   is an allow-list; X audio is served by opaque token, resolved server-side.
4. **A pre-registered N gates the verdict.** `is_final` is true only at N; the
   pass criterion is pinned to N from trial 1, never to the trials taken so far.

## Verified vs NOT verified

| Claim | Status |
|---|---|
| Statistics (exact binomial, Clopper-Pearson CI, power→N, Holm) | verified — against published ABX tables and exact rational arithmetic; CI validated against Beta quantiles two independent ways |
| Strict codec refusal + opt-in disclosure | verified against real ffmpeg (`libldac` absent here, so the path is exercised) |
| Null depth / alignment / loudness numbers | verified — table in the `Phase 0b` commit message |
| Blinding fixes, no-leak contract, web API, export | verified by tests |
| **Desktop app has never been executed** | **UNVERIFIED.** Needs `libgl1` + `libportaudio2`. PyQt6 imports fail on `libGL.so.1`. `main.py` is compile-checked; its logic is tested through the shim, not run |
| Web single-session limitation | documented, **not fixed** |
| Real listening test end-to-end by a human | never done |

## Bugs found and fixed (regression contract)

Each has a test. Do not "simplify" these away:

- **Silent codec substitution.** Missing `aptx`/`ldac` fell back to AAC, shown in
  the UI, **absent from the export** — an "aptX" result that encoded AAC was
  citable as aptX. Now a hard error; opt-in records the real encoder.
- **Loudness validation was a tautology.** LUFS compared *after* both sides were
  gain-matched to −16, so the "<0.1 dB" gate printed `0.0000 dB` for every run
  and could not fail. opus32 vs opus128 measured 0.578 dB pre-normalization and
  passed as perfect. → `LoudnessReport`, `tests/test_pipeline_ffmpeg.py`.
- **Web blind mode scored a perfect listener INCORRECT** (display answer
  compared against source-level X; mapping applied backwards).
  → `tests/test_legacy_blind_bug.py`.
- **Web alignment zero-padded the head**, shifting musical content apart between
  A and B at the same timeline position. Unbounded ±5 s search also locked onto
  periodic peaks. → `core/alignment.py`.
- **`/api/trial/start` shipped `x_is` to the browser** — DevTools ended the
  blinding. → token-based `/api/audio/x`.
- **CSV export raised on every real session** (5 hardcoded columns vs 10
  dataclass fields). → fieldnames derived from the dataclass.
- **Streak-based blinding was predictable.** web `0.1*streak` cap 0.5; desktop
  `0.5 + 0.07*streak` cap **0.88** — above 50% the mapping is anti-persistent.
- Metric conventions that were traps: perfect null returned `+inf` (deepest null
  printed as the biggest number); Clopper-Pearson lower bound inverted; the pass
  threshold exposed a peeking criterion.

## Environment on this machine

- Python 3.14.8. **No system pip**; `python3 -m ensurepip` works. `.venv` exists.
- `ffmpeg`/`ffprobe` at `~/.local/bin` (v7.x). Encoders present: `aac`, `aptx`,
  `aptx_hd`, `libopus`, `sbc`, `libvorbis`. **`libldac` absent** — that is what
  makes the strict-refusal path testable.
- Missing system packages: `libgl1`, `libegl1` (blocks PyQt6 import),
  `libportaudio2` (blocks desktop playback).
- `imageio-ffmpeg` is installed in the venv for tests only. It bundles ffmpeg
  with **no ffprobe** and a minimal codec set — never let the app use it.

## Commands

```bash
.venv/bin/python -m pytest -q              # 111 tests, no ffmpeg/Qt needed
.venv/bin/python -m pytest -q -m pipeline  # needs ffmpeg + pyloudnorm
.venv/bin/python -m compileall -q core app web
```

## Phase 1 — do in this order

1. **Canary / calibration trial.** A deliberately-easy known-different trial
   gates the session: fail it, the run is void. Cheapest credibility feature in
   this domain and no competitor has it. Start in `core/abx.py` (a trial kind
   flag + a gate on the verdict), surface in `core/report.py`, then both shells.
2. **Null test as an active gate.** The number is measured; refuse to start
   trials when a pair that should null does not. Files: `core/alignment.py`
   (`null_depth_db`), `app/audio_pipeline.py`, `web/app.py`.
3. **Output-path class detection.** Users testing aptX/LDAC wear Bluetooth
   cans, and the OS re-encodes on output, so the test measures the Bluetooth
   chain, not the file pipeline. Detect the device class, warn, and stamp the
   export "uncontrolled output". WASAPI exclusive is driver-dependent and
   meaningless for BT endpoints. Desktop `app/player.py` + device listing.
4. **Web per-session state.** `web/app.py` has one module-level
   `session = SessionState()`; two tabs share it and `--workers 2` gives two
   independent sessions. Prerequisite for real multi-listener studies.
5. **Study spec file** (the "defacto" moat): input hash + pipeline + N + seed →
   shareable, byte-reproducible run. Then commit-reveal is already in place to
   make published results verifiable.

## Open items / non-goals

- `holm_bonferroni` is in `core/stats.py` and tested, but **not wired** —
  multiplicity correction needs a UI concept of a comparison family (Phase 1+).
- Bandwidth-limit cutoffs are fixed at 14/16/18 kHz by design.
- Web audio is mono internally (`load_wav_float` forces `-ac 1`); desktop is
  stereo. Unify before claiming stereo results on web.
- The web pipeline still has no `LoudnessReport`; it reports pre-normalization
  delta and gains in metadata, and a residual of 0.0 (not re-measured).
- No git remote is configured; everything is local on `master`.
