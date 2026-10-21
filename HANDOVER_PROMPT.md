# Handover prompt — Codec ABX tester, next session

Start the next session with: **`@HANDOVER_PROMPT.md` — do the task described here.**
Everything below is self-contained; no earlier conversation is assumed.

## Repository and starting state

Repo: `/home/triet/Project/Codec_ABX_test`. Read [HANDOVER.md](HANDOVER.md) first,
especially the Verified vs NOT verified table, invariants 1–9 and Phase 3 notes.

Phase 2 (study lifecycle/playback mapping) and Phase 3 (unified preparation) are
committed together as **`Phase 2/3: bounded studies and shared audio preparation`**,
following `4da8fb7` (Phase 1). Confirm the actual hash and clean status; do not
assume runtime facts or line numbers survived the session boundary.

```bash
pwd
git log --oneline -3 && git status --short
.venv/bin/python -m pytest -q                      # 274 passed (~90 s)
.venv/bin/python -m pytest -q -m "not pipeline"    # 245 passed, 29 deselected
.venv/bin/python -m pytest -q -m pipeline          # 29 passed, 245 deselected
.venv/bin/python -m compileall -q core app web tests
~/.local/bin/node --check web/static/js/app.js
git diff --check
```

These counts were verified locally with real ffmpeg. They are NOT CI, browser,
desktop-launch or listening validation.

## Next task: make verification honest and playback validation reproducible

Real playback/listening validation is the next product gate, not another feature.
This machine could not launch desktop and the previous session was constrained
not to start servers/browsers or install system packages. Do not work around
those restrictions or claim a listening test from API bytes/static tests.

Do the following bounded, actionable work:

1. **Fix the CI verification gap first.** Inspect
   [.github/workflows/ci.yml](.github/workflows/ci.yml). Its pipeline job currently
   installs only the minimal dev dependencies, omitting the audio extras, and
   masks failure with `|| echo "pipeline suite pending"`. Ensure the pipeline
   job installs [requirements-audio.txt](requirements-audio.txt), actually
   collects/runs audio tests, and propagates failures. Preserve the lightweight
   core/API matrix (no Qt or playback backend). Add a regression guard that
   catches failure masking, omitted audio dependencies and an empty/skipped
   pipeline suite; demonstrate the relevant red test before fixing it. Do not
   claim GitHub Actions ran unless it actually did. No remote is configured.
2. **Prepare a reproducible manual playback acceptance kit**, with deterministic
   mono/stereo/channel-distinct samples and a concise checklist. Cover fixed and
   blind-random A/B/X mapping, left/right routing, instantaneous switching, loop
   segments, calibration pass/fail, identical-pair null gate, stop exactly at N,
   config freeze from the first staged trial, mid-run redacted export, and sealed
   versus completed export. Define which observations need a browser/operator
   and which are already established from served PCM/tests. Avoid a checklist
   that reveals X before answering; inspect the answer key only after sealing or
   completing the run. Keep new generators/tests small and dependency-light.
3. If the user explicitly authorizes a suitable runtime and a human operator is
   available, execute the applicable checklist and record actual evidence. If
   unavailable, deliver the kit and mark those checks **NOT RUN**, with concrete
   prerequisites; do not fabricate success or install missing system libraries.
4. Update [HANDOVER.md](HANDOVER.md): changes, exact test counts, what was and was
   not executed, remaining risks and the next recommended task. Do not commit
   without a fresh user request.

Do not begin the per-session state migration, Bluetooth/output-path work, study
specification or UI redesign in the same pass. Once playback is actually
validated, the next architectural task is replacing the process-global web
session with per-session state.

## Architecture and contracts to preserve

- [core/abx.py](core/abx.py) owns `configured → running → completed | aborted`,
  the fixed N, plan freeze, prepared-audio identity, gates and secret redaction.
  Calibration never counts toward N; `significant_verdict` is the claimable pass.
  Completed/sealed runs remain frozen until explicit reset.
- [web/app.py](web/app.py) serves mapped bytes: `side_audio_path()` is the ONE
  owner of A/B display mapping, canary included. Browser maps nothing and caches
  per prepared identity + calibration mode + trial mapping.
- Web and desktop share the headless
  [AudioPipeline](app/audio_pipeline.py). [core/preparation.py](core/preparation.py)
  owns `LoudnessReport`, channel/rate policy, normalization/common peak trim and
  delivered remeasurement. `core/` imports stdlib + numpy only. Do not restore a
  second web pipeline, ffmpeg loudness parser, hardcoded residual or `agate`.
- Preserve mono/stereo, reject unsupported/channel-changing material. Probe the
  ORIGINAL source before choosing native versus force-48k. Delivered WAVs are
  PCM16. Reload them for post-LUFS, null gates and desktop playback arrays.
- Web metadata/JSON `loudness` includes measured pre/post LUFS, gains, common
  trim, target and deltas. Legacy loudness keys retain PRE-normalization meaning.
  A common trim may leave the actual output below -16 LUFS; report it honestly.
- Preprocess/upload may not erase a frozen run. Old audio is invalidated before
  rendering; upload/config changes are locked during preparation. Cancelled or
  stale work cannot publish. Keep the new route regressions intact.
- [requirements-audio.txt](requirements-audio.txt) is shared by web/desktop and
  excludes Qt/sounddevice. Web imports audio dependencies lazily. Docker now
  copies `core/` and the shared audio requirements, but was NOT built here.

## Test and working discipline

- Write/observe a failing regression before each bug fix. Never weaken gates or
  assertions just to pass. Preserve unrelated user changes.
- Read existing files with the read tool before editing; use glob/grep to locate
  paths and code. Re-read after a script rewrites a file. Inspect bash exit codes.
- `app/abx_engine.py` must not import `random` or use `comb`; web must not use
  `comb` or `_blind_no_change_streak`. EN/VI translation dictionaries stay parallel.
- The web session is still a module-level singleton. Isolate tests by replacing
  it with a fresh `SessionState` via monkeypatch (restored afterward), or reset
  ALL configuration, identity, paths and gates. Reset a closed run before config.
- Keep the established engine contracts: 2000 `new_trial()` calls without answers
  remain supported; VOID exports are intentionally unredacted; raw statistical
  significance/is_final can remain true on VOID runs, but not significant_verdict.
- Static UI contracts are not browser/desktop validation. Never infer heard
  playback from a WAV comparison or claim end-to-end listening without an ear.

## Environment and remaining risks

- Python 3.14 in `.venv`; no system pip. ffmpeg/ffprobe/node in `~/.local/bin`.
  Encoders seen: aac, aptx, aptx_hd, libopus, sbc, libvorbis; libldac absent.
  Native 44.1-kHz Opus/aptX and 96-kHz FLAC parity were tested.
- PyQt6 import fails on missing `libGL.so.1`; desktop playback also needs system
  audio libraries. Do not install system packages. Ruff is absent.
- Never use the tests' imageio-ffmpeg binary for the app (no ffprobe/minimal codecs).
- No servers, browsers or system installations were used in the previous pass;
  do not start them without explicit user authorization. Respect current sandbox
  policy; do not request escalation when approval prompts are disabled.
- Canary degradation audibility metadata is measured before its PCM16 write;
  served-PCM tests confirm gross degradation, but the metadata is not a reread.
- Null residual energy includes ALL channels. Lag estimation still uses a mono
  probe; strongly anti-phase stereo can produce a weak estimate/warning.
- `true_peak_dbfs` is a legacy SAMPLE-peak field, not oversampled true peak; this
  limitation is disclosed. Silent/below-gate/too-short audio is refused, not given
  a fabricated finite loudness report.
- Successful render directory retention is not fully managed. Web sessions remain
  process-global. Calibration-only CSV is header-only; JSON is the artifact.
- Sealing exports first before clearing the in-memory run. Blind swaps refetch
  A/B because served bytes differ. Holm is tested but not wired to a study family.
- Later: per-session web state → output-path/Bluetooth disclosure → reproducible
  study spec/provenance → Holm UI → README/DESIGN refresh.
