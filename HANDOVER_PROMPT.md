# Handover prompt — Codec ABX tester, next session

Start the next session with: **`@HANDOVER_PROMPT.md` — do the task described here.**
Everything below is self-contained; no earlier conversation is assumed.

## Repository and starting state

Repo: `/home/triet/Project/Codec_ABX_test`. Read [HANDOVER.md](HANDOVER.md) first,
especially the Verified vs NOT verified table, invariants 1–9, the verification
pass section and Phase 3 notes.

Phase 2/3 is committed as **`ffb5aed Phase 2/3: bounded studies and shared
audio preparation`** (following `4da8fb7` Phase 1). The verification pass
(honest CI + playback acceptance kit + headless dry run + one 500→409 route
fix + doc refresh), plus the two browser playback fixes and Node runtime
regressions described in [HANDOVER.md](<HANDOVER.md>), are **uncommitted** in the working tree;
committing needs a fresh user request. Confirm the actual hash and `git status` — do not assume
runtime facts survived the session boundary.

```bash
pwd
git log --oneline -3 && git status --short
.venv/bin/python -m pytest -q                      # 290 tests; ffmpeg + Node present
.venv/bin/python -m pytest -q -m "not pipeline"    # 261 tests, 29 deselected
.venv/bin/python -m pytest -q -m pipeline          # 29 tests, 261 deselected
.venv/bin/python -m compileall -q core app web tests tools
node --check web/static/js/app.js
node --test tests/js/web_playback.test.cjs          # 16 tests; fake DOM/Web Audio, NOT ears
git diff --check
```

These counts were verified locally with real ffmpeg. They are NOT CI, browser,
desktop-launch or listening validation.

## Next task: execute the ear rows — the only remaining product gate for playback

Real playback/listening validation is the product gate. The verification pass
closed everything a machine can close: CI is honest and contract-guarded
(GitHub Actions itself has never run — no remote), the deterministic kit, the
checklist and a **7/7-passing headless dry run** (server YES, browser NO, ears
NO) exist. What is left cannot be automated: **an authorized runtime plus a
human listener**.

1. **With explicit user authorization and an available operator**: generate
   `playback_kit/` (`tools/make_playback_kit.py`), start the server
   (`FLASK_DEBUG=0 .venv/bin/python -m web.app`), walk
   [PLAYBACK_ACCEPTANCE.md](PLAYBACK_ACCEPTANCE.md) rows 0–7 plus the
   browser-side observations of 8–13, fill the evidence log honestly
   (browser, device, operator, exports). Ear rows are the point; never
   prefill, never infer hearing from bytes.
2. **Without them**: leave every ear/browser row **NOT RUN**. Do not start
   servers/browsers unprompted, do not install system packages (desktop D1/D2
   need `libgl1`/`libegl1`/`libportaudio2`, absent).
3. **Once (and only once) playback is validated**, the next architectural task
   is replacing the process-global web session with per-session state
   (`web/app.py` module-level singleton). Do not begin the per-session
   migration, Bluetooth/output-path work, study specification or UI redesign
   in the same pass as the checklist.

## Architecture and contracts to preserve

- [core/abx.py](core/abx.py) owns `configured → running → completed | aborted`,
  the fixed N, plan freeze, prepared-audio identity, gates and secret redaction.
  Calibration never counts toward N; `significant_verdict` is the claimable pass.
  Completed/sealed runs remain frozen until explicit reset. The canary's X is a
  fair coin per trial — the known-answer property is perceptual, not a label.
- [web/app.py](web/app.py) serves mapped bytes: `side_audio_path()` is the ONE
  owner of A/B display mapping, canary included. Browser maps nothing and caches
  per prepared identity + calibration mode + trial mapping. Lifecycle refusals
  are 409 + `reasons` at every edge, including answering with no trial in
  flight (fixed this pass: was an unstructured 500).
- Web and desktop share the headless
  [AudioPipeline](app/audio_pipeline.py). [core/preparation.py](core/preparation.py)
  owns `LoudnessReport`, channel/rate policy, normalization/common peak trim and
  delivered remeasurement. `core/` imports stdlib + numpy only. Do not restore a
  second web pipeline, ffmpeg loudness parser, hardcoded residual or `agate`.
- Preserve mono/stereo, reject unsupported/channel-changing material. Probe the
  ORIGINAL source before choosing native versus force-48k. Delivered WAVs are
  PCM16. Reload them for post-LUFS, null gates and desktop playback arrays.
- Web metadata/JSON `loudness` includes measured pre/post LUFS, gains, common
  trim, target and deltas. Legacy loudness keys retain PRE-normalization
  meaning. A common trim may leave the actual output below -16 LUFS; report it
  honestly.
- Preprocess/upload may not erase a frozen run. Old audio is invalidated before
  rendering; upload/config changes are locked during preparation. Cancelled or
  stale work cannot publish. Keep the new route regressions intact.
- [requirements-audio.txt](requirements-audio.txt) is shared by web/desktop and
  excludes Qt/sounddevice. Web imports audio dependencies lazily. CI's
  `pipeline-smoke` installs it; [tests/test_ci_contract.py](tests/test_ci_contract.py)
  (text-based, runs in the lightweight matrix) pins: no failure masking, audio
  deps present, junit summary fails empty/skipped/failed suites, core matrix
  stays Qt/backend-free with explicit `-m "not pipeline"`.
- Playback kit: [tools/make_playback_kit.py](tools/make_playback_kit.py)
  (numpy-only, pinned seed 20240508, uses the app's own `core.canary` recipe)
  → gitignored `playback_kit/` + `ANSWER_KEY.md` (open only after seal/complete)
  + `manifest.txt`. [tools/playback_dry_run.py](tools/playback_dry_run.py)
  drives the live server's API-observable rows (no ears).
  [PLAYBACK_ACCEPTANCE.md](PLAYBACK_ACCEPTANCE.md) is the operator contract.

## Test and working discipline

- Write/observe a failing regression before each bug fix. Never weaken gates or
  assertions just to pass. Preserve unrelated user changes.
- Read existing files with the read tool before editing; use glob/grep to
  locate paths and code. Re-read after a script rewrites a file. Inspect bash
  exit codes.
- `app/abx_engine.py` must not import `random` or use `comb`; web must not use
  `comb` or `_blind_no_change_streak`. EN/VI translation dictionaries stay
  parallel.
- The web session is still a module-level singleton. Isolate tests by replacing
  it with a fresh `SessionState` via monkeypatch (restored afterward), or reset
  ALL configuration, identity, paths and gates. Reset a closed run before config.
- Keep the established engine contracts: 2000 `new_trial()` calls without
  answers remain supported; VOID exports are intentionally unredacted; raw
  statistical significance/is_final can remain true on VOID runs, but not
  significant_verdict.
- Static UI contracts are not browser/desktop validation. Never infer heard
  playback from a WAV comparison or claim end-to-end listening without an ear.
  A passing headless dry run is API evidence, not playback evidence.

## Environment and remaining risks

- Python 3.14 in `.venv`; no system pip (venv pip exists). ffmpeg/ffprobe/node
  in `~/.local/bin`. Encoders seen: aac, aptx, aptx_hd, libopus, sbc,
  libvorbis; libldac absent. Native 44.1-kHz Opus/aptX and 96-kHz FLAC parity
  were tested.
- `pyyaml` was added to `.venv` only as a one-off YAML syntax checker; it is NOT
  a project requirement — keep the CI contract tests text-based.
- PyQt6 import fails on missing `libGL.so.1`; desktop playback also needs system
  audio libraries. Do not install system packages. Ruff is absent.
- Never use the tests' imageio-ffmpeg binary for the app (no ffprobe/minimal
  codecs).
- Servers/browsers: on 2026-10-21 the Flask server was started under explicit
  user authorization for the headless dry run and stopped afterwards
  (port 5000). Do not start servers, browsers, or install system packages
  without explicit user authorization; respect current sandbox policy; do not
  request escalation when approval prompts are disabled.
- Canary degradation audibility metadata is measured before its PCM16 write;
  served-PCM tests confirm gross degradation, but the metadata is not a reread.
- Null residual energy includes ALL channels. Lag estimation still uses a mono
  probe; strongly anti-phase stereo can produce a weak estimate/warning.
- `true_peak_dbfs` is a legacy SAMPLE-peak field, not oversampled true peak;
  this limitation is disclosed. Silent/below-gate/too-short audio is refused,
  not given a fabricated finite loudness report.
- Successful render directory retention is not fully managed. Web sessions
  remain process-global. Calibration-only CSV is header-only; JSON is the
  artifact.
- Sealing exports first before clearing the in-memory run. Blind swaps refetch
  A/B because served bytes differ. Holm is tested but not wired to a study
  family.
- Later: per-session web state → output-path/Bluetooth disclosure →
  reproducible study spec/provenance → Holm UI → README/DESIGN refresh.
