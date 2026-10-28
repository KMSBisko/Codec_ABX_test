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

Phase 0 and 0b are done, plus Phase 1 items 1 and 2 (canary gate, null gate),
Phase 2 (study lifecycle), and Phase 3 (shared audio preparation).
Phase 2 and Phase 3 are committed together, at the user's request, as
`Phase 2/3: bounded studies and shared audio preparation`, following `4da8fb7`
(`Phase 1:`). The verification pass below (CI honesty + playback acceptance
kit + headless dry run) is committed as `Phase 3 follow-up: CI honesty and
playback acceptance kit`. Use `git log -1` for the exact revision. The refreshed
[HANDOVER_PROMPT.md](HANDOVER_PROMPT.md) is the next-session entry point:
execute the ear rows with an authorized runtime and a human operator, then
per-session web state.

- **290 pytest tests** (288 before the playback fixes, +2 runtime-suite/CI
  checks). **29** are `-m pipeline` (ffmpeg); **261** are non-pipeline.
  The runtime check executes **16 Node subtests** against real client functions
  with fake DOM/Web Audio/network boundaries. This is not browser execution or
  listening; desktop UI coverage remains static/shim-based.
- `core/` is the single source of truth for statistics, blinding, alignment,
  **validity gates**, **the study lifecycle** and report wording, shared by the
  PyQt6 desktop and Flask web shells.
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
7. **A run is a bounded, named thing with one ending.** `ABXEngine.lifecycle`
   is `configured → running → completed | aborted`, and the transitions are
   engine-owned, not UI-owned:
   - scored trials stop **exactly at the pre-registered N** (calibration never
     counts toward it); `new_trial()` and `submit_answer()` raise
     `TrialLimitReached` past it, and `can_accept_answers()` says so;
   - the plan (`planned_trials`, `label_mode`, `alpha`, `canary_enabled`) is
     **frozen from the first staged trial** — the moment a commitment is
     published and audio is playable. A write is `ConfigLocked`, enforced in the
     engine (`__setattr__`), so a shell cannot edit around it;
   - the run is locked to a **prepared-audio identity** (`set_prepared_audio`:
     digests of the delivered pair + the fingerprint of the configuration that
     rendered it). Changing that configuration drops the pair: trials refuse to
     start until it is re-rendered;
   - `reveal()` is **REDACTED while the run can accept further answers** — the
     secret derives X for every trial not yet answered. Only a `completed` run
     (at N) or a permanently **sealed** one (`abort`, a distinct label) releases
     it, and `verifiable`/`redacted` in the payload name which one you hold;
   - `is_final` is true only at N, `verdict()` prints `SEALED`/`PROVISIONAL`
     otherwise, and `significant_verdict` (not the raw `significant`) is the
     claimable pass. An incomplete run can never be stamped as a result.
   A void/sealed run stays **exportable**: a failed calibration is a result
   worth publishing, and the way failed runs get quietly dropped is by having
   no way out of the tool.
8. **The display mapping has exactly one owner: the server that serves the
   bytes.** `side_audio_path()` resolves a display label through
   `plan.display_mapping` (canary pair included); the browser plays what it is
   given and maps nothing. Mapping twice inverts it.
9. **One preparation policy, measured delivery.** Both shells use the same
   headless preparation implementation and backend-free core loudness policy.
   Preserve mono/stereo; original-rate disclosure comes from probing the source.
   Gain matching is followed by common peak trim, never a hidden noise gate.
   Loudness residuals and null decisions are measured on delivered PCM16.

## Verified vs NOT verified

| Claim | Status |
|---|---|
| Statistics (exact binomial, Clopper-Pearson CI, power→N, Holm) | verified — against published ABX tables and exact rational arithmetic; CI validated against Beta quantiles two independent ways |
| Strict codec refusal + opt-in disclosure | verified against real ffmpeg (`libldac` absent here, so the path is exercised) |
| Null depth / alignment / loudness numbers | verified — historical table in `Phase 0b`; Phase 3 now remeasures serialized delivery and measures null energy across all channels |
| Shared desktop/web preparation, mono/stereo and same-channel canary, real loudness residual, quiet-tail/bandwidth parity, original 96 kHz FLAC native/48k modes, codec-internal rate adaptation, clipping guard | verified against real ffmpeg/served PCM and independent LUFS remeasurement in Phase 3 tests; not listening validation |
| Blinding fixes, no-leak contract, web API, export | verified by tests |
| Canary gate semantics (index 1, in the commitment, out of the binomial; fail → `run_valid=false`, verdict `VOID`, further trials refused) | verified by tests, both shells' engines (desktop via the shim) |
| Null gate semantics (identical pair must measure ≤ −60 dB; missing/NaN measurement fails **closed**; a genuinely different pair is `not_applicable` and never blocks) | verified by tests, incl. real ffmpeg audio (identical pipelines → perfect null → `passed`; opus vs lossless → `not_applicable`) |
| Canary material is audible, not a coin flip | verified numerically — 3 kHz band-limit + 10× drive clipped at ±0.45 measures **−2.68 dB** (mono) / **−2.18 dB** (stereo) residual on 48 kHz tonal+noise+transient test material, against a −30 dB audibility floor (historical fixture measurements). The old web +0.79 dB mono/stereo mismatch is superseded by Phase 3: both canary files now preserve the delivered channels. Silent canary material → `not_audible`, canary disabled; silent input preparation itself is refused because finite integrated LUFS is unavailable |
| Null gate threshold is on a real cliff, not a guess | verified numerically — level error 0.001 dB → −78.78, 0.0087 dB → **−60.00** (the chosen floor), 0.01 dB → −58.77, 0.1 dB → −38.73, 0.5 dB → −24.55; 1-sample misalignment → −13.92; identical → perfect (−inf) |
| Desktop canary audio swap + gate widgets in `app/main.py` | **UNVERIFIED.** PyQt6 imports fail on `libGL.so.1`. `_make_engine` / `_on_planned_trials_changed` (called at 4 sites) were **never defined** — the window raised `AttributeError` on construction. They are defined now and their logic is tested through `app/abx_engine`; the window itself has never been opened |
| Web browser UI (`#gateBanner`, canary badge, stats rows) | **UNVERIFIED in a browser.** `node --check` passes; the API contract behind it is tested (409 + reasons on a blocked gate, `kind` in exports) |
| Study lifecycle (N bound, plan freeze at the first staged trial, prepared-audio identity, mid-run redaction, seal vs complete, `significant_verdict`, export markers) | verified by tests — core engine, the Flask HTTP layer, the desktop shim, and the ffmpeg pipeline (redact → seal → verifiable, `SEALED` in `validity_lines`) |
| Web served-audio mapping (A/B/X judged **from the bytes served**, 16-trial perfect listener scored entirely from audio, calibration pair follows the mapping, `/api/audio/info` publishes the cache identity) | verified by tests against real WAV bytes |
| Browser client lifecycle wiring (per-trial buffer cache, stop at N, seal control, redaction note) | **UNVERIFIED in a browser.** No page was opened, no audio was played; `node --check` passes and the contract is asserted statically in `tests/test_web_client_lifecycle.py` |
| Desktop lifecycle wiring (seal button, N/canary freeze, score panel, export stamps, prepared-audio hash, export guard) | **UNVERIFIED — the app cannot be launched here** (`libGL.so.1`). `app/main.py` parses, its i18n keys are parity-checked, and its structure is asserted in `tests/test_desktop_lifecycle_ui.py`; `ABXStats` fields it reads are checked through the shim |
| CI pipeline job installs audio extras, propagates failures, and refuses an empty/skipped suite | contract tests observed red→green; junit checker exercised locally: real ffmpeg run 29/0/0 green, missing dep → all-skipped red, broken package red. **GitHub Actions itself has never run here** (no remote) |
| Playback acceptance kit material (determinism, channel routing, pair bands, byte-identical null pair, canary recipe, loop seam) | verified by byte-level tests (`tests/test_playback_kit.py`); the kit is inputs, not a playback claim |
| Headless dry run of checklist rows 8/9/10/11/12/13 over the live server | **executed 2026-10-21**, 7/7 pass, evidence in `playback_kit/dryrun_evidence/`. Server-only: no browser, no ears — certifies the API surface, not playback |
| Real playback of the correctly-mapped audio by a human ear | **never performed** — the web inversion is proven on served bytes, not on heard sound |
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
- **The web served the trial's A/B audio WITHOUT that trial's display mapping.**
  The engine produced a per-trial mapping, `SessionState.side_audio_path()`
  resolved the raw physical paths, and the browser cached one decoded A/B pair for
  the whole session. In `blind_random` a swapped trial played the *other* pair: a
  listener who identified X perfectly by ear was scored wrong (reproduced with
  real bytes: `the listener identified X by ear from the served audio and was
  scored wrong`). The calibration pair had the same inversion, so the known-answer
  gate was inverted for half of blind runs. → the serving layer resolves the
  mapping once (canary included), and the client caches buffers per
  (prepared audio, calibration mode, mapping).
  → `tests/test_web_playback_mapping.py`, `tests/test_web_client_lifecycle.py`.
- **Nothing bounded a run.** Trials continued past the pre-registered N, the
  planned N / label mode / calibration switch / pipelines were editable while a
  run was under way (N only after the *first answer*, so a staged — already
  exposed — trial was editable), a mid-run export leaked the secret for the trials
  still to come, and a run stopped early at n=7 of 16 printed as
  `PROVISIONAL`/`significant` with no marker in the export saying it was not a
  finished study. → lifecycle in `core/abx.py` (invariant 7): stop at N, freeze
  the plan at the first staged trial, redact while answers are due, `SEALED` for a
  run stopped early, `significant_verdict` as the only claimable pass, and
  `POST /api/trial/seal` as the honest early-export path.
  → `tests/test_study_lifecycle.py`, `tests/test_pipeline_ffmpeg.py`.
- **A rendered pair survived the configuration that produced it.** Changing the
  pipeline (or the input) after preprocessing left the old WAVs in place, so the
  null gate, the calibration material and the answers all described audio the new
  configuration no longer produces. → `/api/config` fingerprints the render and
  drops the pair (with the reason) when it changes; N and the label mode are
  deliberately outside the fingerprint, so registering a different N does not
  force a re-render.
- **The desktop blocked the export of a calibration-only run.** `if not
  self.logger.trials` is empty when a run dies at the calibration trial (the
  canary is recorded in the session block, not the scored table) — precisely the
  run that must stay exportable. → the guard reads `engine.all_trials()`.
- **Streak-based blinding was predictable.** web `0.1*streak` cap 0.5; desktop
  `0.5 + 0.07*streak` cap **0.88** — above 50% the mapping is anti-persistent.
- **Web answered an out-of-band answer with a 500.** `POST /api/trial/answer`
  with no trial in flight (e.g. scripted client after a gated 409 on start) hit
  the engine's `RuntimeError("no trial in progress")`, which the route did not
  translate: unstructured 500 HTML. Found by the headless dry run; the route
  now answers 409 in the engine's words. →
  `tests/test_study_lifecycle.py::test_web_answer_with_no_trial_in_flight_is_409_not_a_500`.
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
.venv/bin/python -m pytest -q                    # 290 tests; ffmpeg + Node present
.venv/bin/python -m pytest -q -m "not pipeline"  # 261 tests, 29 deselected
.venv/bin/python -m pytest -q -m pipeline        # 29 tests, 261 deselected; audio deps required
.venv/bin/python -m compileall -q core app web tests tools
node --check web/static/js/app.js
node --test tests/js/web_playback.test.cjs       # 16 runtime regressions; fake Web Audio, NOT ears

.venv/bin/python tools/make_playback_kit.py      # deterministic kit -> playback_kit/
.venv/bin/python -m pytest -q tests/test_ci_contract.py  # CI contract guards

# headless dry run (server start requires explicit user authorization):
FLASK_DEBUG=0 .venv/bin/python -m web.app        # http://localhost:5000
.venv/bin/python tools/playback_dry_run.py       # rows 8-13 API-observable; NOT playback
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

## Phase 2 — study lifecycle and the playback contract (DONE)

Correctness before features: a run is now a bounded, named, auditable thing, and
the audio the listener hears is the audio the score describes.

1. **Lifecycle in `core/abx.py`** (invariant 7): `configured → running →
   completed | aborted`; stop exactly at N; plan frozen from the first staged
   trial (engine-enforced, `__setattr__` + named setters); prepared-audio
   identity frozen for the run; `reveal()` redacted while answers are due;
   `abort`/`seal` as the distinct, labelled early ending; `is_final` only at N;
   `significant_verdict` as the claimable pass. The shells enforce nothing
   themselves — the desktop shim converts the same rules into status-bar text,
   the web returns the same rules as 409 + `reasons`.
2. **The seal is the honest early export.** Mid-run export = REDACTED
   (commitment + answered rows, no secret). Sealed export = verifiable and
   labelled `SEALED … not a result`. `POST /api/trial/seal` + the web/desktop
   controls.
3. **Rendered audio is owned by the configuration that rendered it**: fingerprint
   → invalidate → trials refuse to start until re-render; prepared-audio digest +
   `run_lock` stamped into every export.
4. **One mapping owner** (invariant 8): serving layer maps, client plays bytes,
   browser caches per (prepared audio, calibration mode, mapping).

**Accepted UX tightening, deliberate:** the config freeze triggers at the first
*staged* trial (a published commitment and playable audio), stricter than the old
desktop rule "an answer exists"; N, label mode, calibration switch and pipelines
are all editable only before the first trial starts or after an explicit reset.
Completed and sealed runs stay frozen until reset; preprocessing and upload
cannot silently discard that frozen run.

## Phase 3 — unified audio preparation (DONE)

The previous preparation handover scope is implemented; the refreshed
[HANDOVER_PROMPT.md](HANDOVER_PROMPT.md) now describes the next task. Regression
tests were written and observed failing before fixing channels, loudness,
processing order, and FLAC probing. No browser, server, desktop window or human
listening test was run.

- **One headless pipeline:** the web worker now calls
  [AudioPipeline](app/audio_pipeline.py), the same preparation implementation as
  desktop, with explicit enum/stage adaptation. The independent web codec,
  bandwidth, alignment, loudness-parser and gain/`agate` implementations are gone.
- **Shared backend-free policy:** [core/preparation.py](core/preparation.py) owns
  `LoudnessReport`, channel checks, sample-rate selection, normalization/common
  trim, delivered remeasurement, and the exported processing order. It imports
  stdlib + numpy only. [app/models.py](app/models.py) re-exports the report type
  for compatibility. DSP backends remain outside core.
- **Channels:** `preserve_source_mono_or_stereo`; unsupported inputs or decoder
  channel-count changes fail rather than silently downmix. A/B and both canary
  files preserve the same channels. The convention is disclosed in both shells;
  web JSON also stamps it beside the delivered channel count.
- **Real loudness:** both pipelines measure with `pyloudnorm` (BS.1770), apply
  independent constant gains and ONE common sample-peak trim, write PCM16, then
  reload the delivered files. Post-LUFS, residual, peaks, null gate, desktop
  playback arrays and canary source refer to that delivered PCM, not ideal
  pre-serialization floats. There is no gain clamp, amplitude gate or limiter.
  One PCM16 quantum of positive headroom prevents writer clipping. Common trim
  may leave delivery below the -16 LUFS target: the actual levels and trim are
  reported, not overwritten with the target.
- **Web citation artifact:** `session_metadata.loudness` and the JSON session's
  `loudness` contain pre/post LUFS, gains, common trim, target, `pre_delta_db` and
  the measured `residual_delta_db` without rounding. Diagnostics consume this
  report directly; absent measurement means absent loudness lines, never a
  fabricated zero. Legacy loudness fields keep their pre-normalization meaning.
- **Order:** probe source → select target rate → working PCM16 → codec chains →
  bounded alignment/crop → bandwidth limits → pre-LUFS → constant gains → common
  peak trim → PCM16 write/reload → delivered measurements/validity gates → canary
  from delivered A. No web-only `agate` remains. The order is exported.
- **FLAC:** probe the original input before decoding. Native 96 kHz stays 96 kHz;
  force-48k delivers 48 kHz while still exporting original 96 kHz. Codec-internal
  rate constraints use the desktop implementation and resampler disclosure.
- **Stereo null measurements:** residual energy now includes every delivered
  channel. Downmixing could cancel anti-phase stereo material and hide a
  residual; a regression catches this. The lag estimator still uses a mono
  correlation probe; strongly anti-phase material can yield a weak estimate and
  warning (not a claim of confirmed alignment).
- **Lifecycle preserved at preparation:** preprocess refuses staged/running,
  completed and sealed runs until explicit reset. It drops old playable audio
  synchronously. Upload/config changes are locked during preparation; upload is
  also locked for a frozen run. Cancellation reaches the active pipeline, and a
  late cancellation or config mismatch cannot publish audio. Unpublished work
  directories are removed. Successful render directories still live for the
  process/session lifetime; there is no full storage-retention policy yet.
- **Dependencies:** [requirements-audio.txt](requirements-audio.txt) is shared by
  web and desktop without pulling Qt/playback into web. Worker imports are lazy,
  preserving API-only imports without audio extras. The Docker recipe copies the
  shared requirements and `core/`; no container was built or run here.
- **New evidence:** [tests/test_preparation_parity.py](tests/test_preparation_parity.py)
  uses real PCM and isolated session state; an injected asymmetric delivery gain
  proves the residual is remeasured rather than stamped. Native 44.1 kHz Opus
  and aptX round trips match desktop PCM exactly; missing libldac refuses rather
  than substituting. High-crest material exercises a real common peak trim, and
  silent/short inputs produce no fabricated report. Backend-free policies and
  preparation/lifecycle safety have separate regression modules. The existing
  web-canary decoder test helper now retains every channel, strengthening the
  check rather than hiding residuals in a mono downmix.

**Local verification this pass:** baseline 237/224/13 passed before changes.
Final full suite: **274 passed** (88.51 s); non-pipeline: **245 passed, 29
deselected** (13.88 s); pipeline: **29 passed, 245 deselected** (74.59 s).
`compileall` over core/app/web/tests, `node --check` and `git diff --check` pass.
A separate read-only review found no remaining scoped blocker. No Docker build,
browser, desktop launch, listening or system installation was performed.

Deliberate limits: mono/stereo only; no loudness artifact for silent/below-gate or
meter-too-short clips (preparation reports an error rather than exporting NaN or
an RMS approximation). The legacy `true_peak_dbfs` field is a **sample peak**, not
an oversampled true peak; the disclosure now names this limitation. No validity
threshold was loosened, and no previous assertion was weakened.

## Verification pass — honest CI + playback acceptance kit (DONE, NOT committed)

**CI contract.** The `pipeline-smoke` job previously installed only dev deps
(so every pipeline module import-skipped) and ran pytest under
`|| echo "pipeline suite pending"` — a green job verifying nothing. It now
installs `requirements-audio.txt`, runs `-m pipeline` with the exit code
propagating, and judges the run by a junit summary: **empty, skipped or
failing → job fails**. The core matrix pins `-m "not pipeline"` explicitly and
stays Qt/audio-backend-free. `tests/test_ci_contract.py` (5 guards, text-based
so it runs in the lightweight matrix) was **observed red against the old
workflow** (4/5 failing on masking, missing audio deps, vacuous suite) and
green after the fix. The junit checker was exercised locally three ways: real
run → `29 tests, 0 skipped, 0 failed` green; missing pyloudnorm → all-skipped
→ **red**; broken package → collection errors → **red**. **GitHub Actions
itself has never run** (no remote is configured); local CI-simulation is not a
CI run.

**Playback acceptance kit.** `tools/make_playback_kit.py` (numpy-only, pinned
seed) emits deterministic PCM16 material for every checklist row: mono
identify, L-then-R channel routing with a shared t=0 click, A/B pair differing
only by a 12 kHz vs 6 kHz shimmer, a byte-identical null pair, a canary pair
built by the app's OWN `core.canary` recipe, a click-and-fade loop-seam file,
`manifest.txt` (sha256) and `ANSWER_KEY.md` that must be opened only after
sealing — the offline analogue of the redacted mid-run export. Output is
gitignored under `playback_kit/`. `tests/test_playback_kit.py` (8 tests)
judges the written bytes: determinism, formats, channel dominance, band
differences, byte-identity, clip level, loop seam. `PLAYBACK_ACCEPTANCE.md` is
the operator checklist (13 rows + desktop D1/D2), each row naming what it
needs (ears/browser/files) and what is already machine-proved, with an
evidence log that must not be prefilled.

**Headless dry run — EXECUTED 2026-10-21 (server YES, browser NO, ears NO).**
User authorization covered starting the Flask server (`python -m web.app`,
port 5000, since stopped). `tools/playback_dry_run.py` drove the live API over
kit material: rows 9 (null gate `passed`), 11 (config frozen 409), 12
(mid-run export redacted, every `x_source` null), 13a (seal verifiable,
`SEALED`, not final), 10 (past-N start refused 409), 13b (completed,
`is_final`, `significant_verdict`), 8 (failed canary → 409 + `run_valid=false`
+ VOID export) — **7/7 pass**, evidence in `playback_kit/dryrun_evidence/`.
This is NOT playback validation: no sound was heard, no page opened. Ear rows
0–7 and the browser observations of 8–13 stay **NOT RUN**.

**Bug found and fixed by the dry run:** answering with no trial in flight
(e.g. after a gated 409 on start) escaped the route as an unstructured HTTP
500 (`RuntimeError: no trial in progress`). The route now returns 409 with the
engine's words; regression `test_web_answer_with_no_trial_in_flight_is_409_not_a_500`
observed red (500 crash) before the fix. Method note: canary X is a fair coin
per trial, so a headless client must judge the calibration answer from the
post-answer feedback — the known-answer property is perceptual, which is
exactly why the ear rows cannot be automated.

**Local verification this pass:** 288 passed (99 s); `-m "not pipeline"` 259
passed, 29 deselected (14 s); `-m pipeline` 29 passed, 259 deselected (74 s);
`compileall` over core/app/web/tests/tools, `node --check`, `git diff --check`
all pass. No browser, no desktop launch, no listening, no system installation.

## Playback runtime fixes — uncommitted

The review found two client bugs missed by static contracts. Executable Node
regressions were added first: **10 of the original 13 subtests failed** against
the old client (three preserved-behavior checks passed), before either fix.

- Live A→B/X switching played the chosen buffer without saving `currentSide`;
  scrub/resume then silently returned to A. `startSource` now records the side
  alongside the source, so every restart shares one selection owner.
- Auto-advance restarted the old X before fetching the next token. Changing the
  cached buffers never changed the already-playing source. `startNewTrial` now
  rebinds the selected side only after loading next-trial A/B/X; the premature
  restart in `submitAnswer` is removed. This covers A/B blind swaps and the
  canary→normal transition too, preserving timeline/loop and paused/idle state.

The [JavaScript suite](<tests/js/web_playback.test.cjs>) now has **16 passing
runtime subtests**, including slow loading, pausing during loading, fixed-map
cache reuse and stopping at N. The [pytest bridge](<tests/test_web_playback_runtime.py>)
runs them under pytest and checks Node CI setup. The lightweight CI matrix installs Node 22; a missing Node locally skips
only the runtime check, while a missing Node in CI fails. No npm dependencies.

Local verification: **290 pytest tests passed** (87.34 s), including the 16 Node
subtests via the pytest bridge. JavaScript syntax, Python compilation and
`git diff --check` passed. GitHub Actions itself was not run.

These fakes prove buffer/source ownership, **not browser/audio-device behavior**.
No browser/server was started and no sound was heard in this fix pass. The
separately identified loading-error fail-open path and crossfade stop scheduling
remain outside these two fixes; do not infer seamless or failure-safe playback.

## Open items / non-goals

- Sealing a run is destructive to the session's in-memory trials: the web seal
  button exports the file first, and a closed run's configuration is frozen
  (the client offers "start a new run" over the same rendered pair). The desktop
  seal prompt, the button states and the export dialogs have never been
  exercised — `app/main.py` cannot be imported here (`libGL.so.1`).
- A CSV of a calibration-only run is a header with no rows: the scored-trial
  table is by definition empty. The JSON export is the citation artifact for
  that case (it carries `canary`, `gates`, `null_gate` and the verdict).
- Blind mode refetches the A/B WAVs when a trial's mapping swaps (the bytes
  differ), so the first play of a swapped trial costs one fetch+decode. Fixed
  mode keeps the instant-switch cache. If the 24-bit WAVs grow, that fetch is
  the thing to make streaming — not the cache.
- `holm_bonferroni` is in `core/stats.py` and tested, but **not wired** —
  multiplicity correction needs a UI concept of a comparison family (Phase 1+).
- Bandwidth-limit cutoffs are fixed at 14/16/18 kHz by design.
- Canary audibility metadata is still measured on the generated degraded float
  before its PCM16 serialization; served-PCM regressions confirm the gross
  difference, but the exported canary measurement is not a reread of both files.
  This is separate from post-normalization LUFS/null measurements, which now
  do use the delivered PCM.
- CI coverage is now honest (audio extras installed, failures propagate,
  empty/skipped suite fails the job) and contract-guarded — but **GitHub
  Actions has never actually run**: no remote is configured, so real CI
  evidence does not exist yet.
- The next recommended task is **executing the ear rows** of
  `PLAYBACK_ACCEPTANCE.md` (0–7, plus the browser-side observations of 8–13)
  with an authorized runtime and a human operator; the kit, the API dry run
  and the machine-proved half already exist. After that: per-session web
  state, output-path/Bluetooth disclosure, reproducible study
  specifications/provenance, Holm wiring, then README/DESIGN refresh.
- No git remote is configured; everything is local on `master`.
