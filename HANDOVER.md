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

Phase 0 and 0b are done, plus Phase 1 items 1 and 2 (canary gate, null gate):
`git log --oneline` → baseline, `Phase 0:`, `Phase 0b:`, `Phase 1:`.

- **177 tests pass** (111 before Phase 1, +66): 30 on the two gates in `core/`,
  14 on the web gate wiring, 12 on the desktop engine shim and sealed export,
  6 on report wording, 4 on the gates with **real ffmpeg audio**. Pipeline
  tests need ffmpeg: 13 of the 177 are `-m pipeline`.
- `core/` is the single source of truth for statistics, blinding, alignment,
  **validity gates** and report wording, shared by the PyQt6 desktop and Flask
  web shells.
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
5. **A run is gated before it counts.** `engine.gate_open` is false when the
   calibration trial was failed or the null gate failed; `new_trial()` then
   raises `TrialGateClosed` (desktop shim records it as text instead of raising,
   so a GUI never crashes on it), `run_valid` is false and the verdict is
   `VOID`. The calibration trial occupies index 1, is in the committed X
   sequence, and is **excluded from the binomial** — counting a free answer
   would inflate p.
6. **Gates fail closed, material failures fail open.** An identical pair with a
   missing/NaN null measurement blocks trials. A canary pair whose degradation
   is not audible disables the canary (status `unavailable`) instead of voiding
   valid runs at random.

## Verified vs NOT verified

| Claim | Status |
|---|---|
| Statistics (exact binomial, Clopper-Pearson CI, power→N, Holm) | verified — against published ABX tables and exact rational arithmetic; CI validated against Beta quantiles two independent ways |
| Strict codec refusal + opt-in disclosure | verified against real ffmpeg (`libldac` absent here, so the path is exercised) |
| Null depth / alignment / loudness numbers | verified — table in the `Phase 0b` commit message |
| Blinding fixes, no-leak contract, web API, export | verified by tests |
| Canary gate semantics (index 1, in the commitment, out of the binomial; fail → `run_valid=false`, verdict `VOID`, further trials refused) | verified by tests, both shells' engines (desktop via the shim) |
| Null gate semantics (identical pair must measure ≤ −60 dB; missing/NaN measurement fails **closed**; a genuinely different pair is `not_applicable` and never blocks) | verified by tests, incl. real ffmpeg audio (identical pipelines → perfect null → `passed`; opus vs lossless → `not_applicable`) |
| Canary material is audible, not a coin flip | verified numerically — 3 kHz band-limit + 10× drive clipped at ±0.45 measures **−2.68 dB** (mono) / **−2.18 dB** (stereo) residual on 48 kHz tonal+noise+transient test material, and **+0.79 dB** on audio through the real web pipeline, against a −30 dB audibility floor. Silent input → `not_audible`, canary disabled |
| Null gate threshold is on a real cliff, not a guess | verified numerically — level error 0.001 dB → −78.78, 0.0087 dB → **−60.00** (the chosen floor), 0.01 dB → −58.77, 0.1 dB → −38.73, 0.5 dB → −24.55; 1-sample misalignment → −13.92; identical → perfect (−inf) |
| Desktop canary audio swap + gate widgets in `app/main.py` | **UNVERIFIED.** PyQt6 imports fail on `libGL.so.1`. `_make_engine` / `_on_planned_trials_changed` (called at 4 sites) were **never defined** — the window raised `AttributeError` on construction. They are defined now and their logic is tested through `app/abx_engine`; the window itself has never been opened |
| Web browser UI (`#gateBanner`, canary badge, stats rows) | **UNVERIFIED in a browser.** `node --check` passes; the API contract behind it is tested (409 + reasons on a blocked gate, `kind` in exports) |
| **Desktop app has never been executed** | **UNVERIFIED.** Needs `libgl1` + `libportaudio2`. `main.py` is compile-checked; its logic is tested through the shim, not run |
| Web single-session limitation | documented, **not fixed** |
| Real listening test end-to-end by a human | never done — including passing a real canary trial |

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
- **Web CSV export raised on every real session too** — `/api/export/csv`
  hardcoded 9 fieldnames and fed them `core.Trial` dicts, so
  `dict contains fields not in fieldnames` fired the moment a trial existed
  (the same bug class as above, in the other shell). → rows and header now come
  from one builder. → `tests/test_web_gates.py`.
- **`MainWindow._make_engine` and `_on_planned_trials_changed` were never
  defined.** They are called from four sites (`__init__` included), so
  `MainWindow()` raised `AttributeError` — the desktop app has never been
  runnable, not just untested. Both are defined now, and engine construction
  happens in one place so the two gates cannot be skipped by one call site.
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
.venv/bin/python -m pytest -q                    # 177 passed (ffmpeg present)
.venv/bin/python -m pytest -q -m "not pipeline"  # 164 passed, 13 deselected — no ffmpeg
.venv/bin/python -m pytest -q -m pipeline        # 13 — needs ffmpeg + pyloudnorm + soundfile
.venv/bin/python -m compileall -q core app web tests
~/.local/bin/node --check web/static/js/app.js   # the web shell has no JS test runner
```

## Phase 1 — status and order

1. **DONE — Canary / calibration trial.** Trial index 1 is a known-different
   pair: the delivered Side A against the same material band-limited to 3 kHz,
   driven 10× and hard-clipped at ±0.45, built once in `core/canary.py` so both
   shells produce it identically. Failing it closes the gate: `run_valid=false`,
   verdict `VOID`, further trials refused (`TrialGateClosed`; the desktop shim
   records the reason as text so the GUI does not raise). It is in the committed
   X sequence (`reveal()["trials_covered"]` counts it) and out of the binomial.
   Desktop: the player's A/B slot swaps to the calibration pair for that trial
   (`_sync_engine_audio_mode`). Web: `/api/audio/{a,b}` resolve canary-aware,
   `/api/trial/start` returns 409 + reasons when gated, banner + stats rows in
   the page.
2. **DONE — Null test as an active gate.** `identical_pipeline_config` decides
   which pairs MUST null (stage lists and bandwidth limits compared, order
   significant); `null_gate_decision` measures them against −60 dB and fails
   closed on a missing/NaN measurement. Both pipelines run it at preparation,
   put the verdict in the export (`session.null_gate`) and refuse to start
   trials on a failure. Genuinely different pairs get `not_applicable`.
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
  - The same split shows in the calibration pair: the pristine side is a byte
    copy of the delivered (stereo PCM_16) Side A, the degraded side is built
    from the mono render. Measured residual +0.79 dB, far above the −30 dB
    floor, so the gate is sound — but the two sides are not channel-identical
    until the convention above is unified.
- The web pipeline still has no `LoudnessReport`; it reports pre-normalization
  delta and gains in metadata, and a residual of 0.0 (not re-measured).
- No git remote is configured; everything is local on `master`.
