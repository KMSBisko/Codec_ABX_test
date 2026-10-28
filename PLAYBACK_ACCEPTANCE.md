# Playback acceptance — manual kit and checklist

Status: **NOT RUN** — no authorized runtime and no human operator were available
in the pass that produced this kit. Prerequisites: the dependency install and
server start below, a browser, an output device whose channels you know, and a
listener. Nothing here was executed on real sound; the automated columns name
what is already established from served PCM and tests, nothing more.

This kit exists because a passing `pytest` cannot hear. Everything a machine
proved stays marked machine-proved; the rows marked **needs operator** are the
ones a human must sign off before playback is called validated.

## The one rule: no X before the answer

The checklist never reveals X. Mid-run exports are REDACTED by the engine
(invariant 7, `core/abx.py`); `reveal()` unlocks only when the run is
`completed` (at N) or sealed. The kit's own secret, `ANSWER_KEY.md`, follows
the same contract: open it **only after sealing or completing** the offline
run it belongs to. Opening it early voids the blind the kit exists to verify —
record the run as VOID and rerun.

## Setup

```bash
# 1. dependencies (web needs the shared audio extras for real preparation)
.venv/bin/python -m pip install -r requirements-dev.txt -r requirements-audio.txt

# 2. kit material (deterministic; hashes in playback_kit/manifest.txt)
.venv/bin/python tools/make_playback_kit.py          # -> playback_kit/

# 3. ffmpeg must be on PATH for the app's real pipelines
ffprobe -version | head -1

# 4. start the web shell  (server start requires explicit user authorization)
FLASK_DEBUG=0 .venv/bin/python web/app.py            # http://localhost:5000
```

Operator setup: headphones (or speakers) whose **left channel you can verify
physically**, volume set once before item 1, quiet room. Record browser +
version and output device in the log below. Single browser tab: the web
session is process-global (known limitation, Phase 1 item 4).

`playback_kit/` contents: `mono_identify.wav` (melody, mono),
`stereo_routing.wav` (L 440 Hz first half, R 880 Hz second half, click at
t=0 in both), `pair_a.wav` / `pair_b.wav` (identical body; 12 kHz vs 6 kHz
shimmer), `identical_a.wav` + `identical_b.wav` (byte-identical),
`canary_source.wav` + `canary_degraded.wav` (app's own 3 kHz + 10× drive +
clip-at-0.45 recipe), `loop_seam.wav` (click at sample 0, fade to silence at
end), `ANSWER_KEY.md` (**sealed first**), `manifest.txt`.

## Checklist

Machine-proved rows cite tests as they exist today; if a cited test disappears,
the row's status must be re-derived, not inherited.

| # | Check | Action | Must observe | Who it needs | Already established (machine) |
|---|-------|--------|--------------|--------------|-------------------------------|
| 0 | Offline triangle (ears + chain sanity) | Play `pair_a.wav` and `pair_b.wav` on two players; pick which is X per `ANSWER_KEY.md` **after** sealing the answer sheet | You can reliably tell 12 kHz from 6 kHz shimmer on your device | Operator (ears); key opened post-seal | Kit files verified by `tests/test_playback_kit.py` (content, determinism, byte-identity) |
| 1 | Mono routing sanity | Upload `mono_identify.wav`, pipeline A=B=pass-through, play side A | Melody audible in **both** ears, note ticks crisp, no muting | Operator (ears) | Channels preserved in delivery — `tests/test_preparation_parity.py` |
| 2 | L/R routing | Upload `stereo_routing.wav`, play | Click at t=0 on **both** sides; tone LEFT for first second, RIGHT for second; swap left/right headphones → sides swap (proves it is routing, not memory) | Operator (ears) | Same-channel delivery — `tests/test_preparation_parity.py` |
| 3 | Fixed A/B/X mapping | Upload `mono_identify.wav`; A pipeline = pass-through, B = 14 kHz bandwidth limit; label mode **fixed**; canary on; start trial 2+; switch A/B/X | X (one of the two) identified correctly repeatedly; A is always the brighter side; switching is gapless | Operator (ears) + browser | Served bytes carry the mapping, canary included — `tests/test_web_playback_mapping.py`; byte-level perfect-listener scoring from audio alone |
| 4 | Blind-random mapping | Repeat 3 with label mode **blind_random**, N=8, answer from what you hear, finish at N, export JSON | Score matches your answers; JSON (post-completion) reveals the mapping that produced them; no trial is audible-identical to the previous one in a pattern you can predict | Operator (ears) + browser | Same mapping owner + `tests/test_legacy_blind_bug.py`, `tests/test_study_lifecycle.py` |
| 5 | Instant switching | Fixed mode, both sides preloaded; click A↔B rapidly (≤300 ms apart) | No gaps, clicks, stalls, or double-starts; volume identical between sides | Operator (ears) + browser | Buffer preload + per-identity cache contract asserted statically — `tests/test_web_client_lifecycle.py` (static, not browser) |
| 6 | Loop segment | Upload `loop_seam.wav`; enable loop, start=0, end=file end; listen ≥5 laps | Exactly **one** click per lap, period = 1.2 s; no seam double-click, no truncation; try a sub-segment loop (e.g. 0.2–1.0 s) and count laps in 5 s | Operator (ears) + browser | `/api/audio/<side>/segment` served bytes — route exists; seamlessness is an ear judgement, unproved |
| 7 | Calibration PASS | Canary enabled, pipelines A=B=pass-through, real preprocess; answer trial 1 (canary) correctly | Trial 1 sounds **grossly** different (telephone-band, distorted) from the clean side; gate opens; trial 2 starts; stats show canary out of the binomial | Operator (ears) + browser | Gate semantics + −2.68 dB/−2.18 dB residual floor — `tests/test_canary_gate.py`, `core/canary.py` |
| 8 | Calibration FAIL | New run, answer the canary **wrong** on purpose | `#gateBanner` shows the gate closed; trial 2 refused (409 + reasons); stats show `run_valid=false`, verdict `VOID`; export still downloads | Browser (no ear) | `tests/test_canary_gate.py`, `tests/test_web_gates.py` |
| 9 | Null gate (identical pair) | Upload `identical_a.wav`; set A pipeline == B pipeline (pass-through); preprocess; open diagnostics + gate banner | Null gate = **passed** with a deep (≤ −60 dB, shown as perfect) measurement; A/B audition is indistinguishable by ear; then deliberately change B (e.g. +level) and confirm the pair no longer passes with `not_applicable`/failure semantics per config | Browser + operator (audition row only) | Identical pipelines → perfect null → `passed` on real ffmpeg audio — `tests/test_pipeline_ffmpeg.py` |
| 10 | Stop exactly at N | N=4, blind mode; answer all 4 (plus canary); press "next trial" once more | No trial 5; UI stops advancing; `can_accept_answers=false`; stats final at 4 | Browser | `tests/test_study_lifecycle.py` (`TrialLimitReached` at N, calibration excluded) |
| 11 | Config freeze from first staged trial | After trial 1 is staged (audio playable), edit N / label mode / canary / pipelines and save | Save refused (`ConfigLocked` → 409); UI shows the config frozen; "start a new run"/reset unlocks it | Browser | Freeze-at-first-staged-trial + `/api/config` lock — `tests/test_study_lifecycle.py`, `tests/test_preparation_lifecycle.py` |
| 12 | Mid-run redacted export | At trial 2 of 4, Export JSON; open the file | `redacted: true`; answered rows present; **no** `x_is`/mapping for the trials still to come; commitment hash present | Operator opens a file (no ear) | `tests/test_study_lifecycle.py`, `tests/test_web_api.py` |
| 13 | Sealed vs completed export | At trial 2 of 4, click **End run now (seal)**, export; then run a fresh run to N=4 and export | Sealed file: labelled `SEALED … not a result`, verifiable (full secret); completed file: `is_final`, `significant_verdict` present; the two are visibly different artifacts | Operator opens files (no ear) | Redact → seal → verifiable, `SEALED` in `validity_lines` — `tests/test_pipeline_ffmpeg.py`, `tests/test_study_lifecycle.py` |

## Desktop items (deferred, cannot run on this machine)

| # | Check | Prerequisite |
|---|-------|--------------|
| D1 | Desktop launch, canary gate widgets, seal prompt, export dialogs | `libgl1` + `libegl1` installed, PyQt6 importable |
| D2 | Desktop real playback (player arrays, canary slot swap) | `libportaudio2`/sounddevice + a sound card; D1 first |

These stay **NOT RUN**. No system packages will be installed by an automated
pass, and `app/main.py` has never been opened on a window.

## Headless dry run — executed 2026-10-21 (server: YES, browser: NO, ears: NO)

User authorization covered starting the Flask server for a scripted headless
pass. `tools/playback_dry_run.py` drove the live server (port 5000) through
the API-observable rows against kit material (`identical_a.wav`, N=2,
canary on, passthrough pipelines):

| Row | Result | Observed |
|-----|--------|----------|
| 9 null gate | PASS | identical pipelines → `status=passed` in `/api/session/info` |
| 11 config freeze | PASS | config write after first staged trial → 409, "configuration is frozen during a run" |
| 12 mid-run export | PASS | `redacted=true`; every `x_source` in the file is `null` — no secret for the pending trial |
| 13a seal | PASS | sealed export `redacted=false` (verifiable), contains `SEALED`, `is_final=false` |
| 10 stop at N | PASS | start past N → 409 `pre-registered N=2 reached` |
| 13b completed | PASS | completed export not redacted, `is_final=true`, `significant_verdict` present |
| 8 canary FAIL | PASS | answered canary wrong → start refused 409, `run_valid=false`, export carries `VOID` |

Evidence JSON: `playback_kit/dryrun_evidence/` (gitignored). Rerun:
`FLASK_DEBUG=0 .venv/bin/python -m web.app` then
`.venv/bin/python tools/playback_dry_run.py` (needs `playback_kit/` built).

Two real findings from the dry run:

* **Fixed:** answering with no trial in flight returned an unstructured HTTP
  500 (`RuntimeError: no trial in progress` escaping the route). The route
  now answers 409 in the engine's words, with regression test
  `test_web_answer_with_no_trial_in_flight_is_409_not_a_500`.
* **Method note:** the canary's X is a fair coin per trial (invariant 2), so
  a headless client must judge the calibration answer from the post-answer
  feedback; the known-answer property is *perceptual* (the degraded side is
  obvious by ear), not a fixed label. Nothing to fix — the checklist's ear
  rows are exactly the half a machine cannot do.

**This is not playback validation.** No browser was opened, no sound was
heard. Rows 0–7 (all ear rows) and the browser-side observations of rows 8–13
stay **NOT RUN** below.

## Evidence log (fill during an authorized run — do not prefill)

| # | PASS / FAIL / NOT RUN | Browser + device | Operator, date | Notes / export files |
|---|-----------------------|------------------|----------------|----------------------|
| 0 | | | | |
| 1–13 | | | | |
| D1–D2 | NOT RUN | — | — | missing libs, no operator |

Save every export JSON from the run next to `playback_kit/manifest.txt`; the
manifest hashes make the material citable. A VOID run (item 8, key opened
early) is a valid outcome to publish — the tool is designed to export it.

## What this kit does NOT cover

Output-path/Bluetooth disclosure (OS re-encodes on output), per-session web
state, multi-listener studies, Holm families. Those are separate tasks in
HANDOVER.md; none of them are gates for this checklist.
