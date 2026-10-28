"""Headless dry run of the playback checklist's API-observable rows.

No browser, no ears: this exercises rows 8, 9, 10, 11, 12, 13 over HTTP,
the same surface the browser uses. Ear rows (0-7) stay NOT RUN.
"""
import json, urllib.request, urllib.error, uuid, pathlib, sys

import os
BASE = os.environ.get("ABX_BASE", "http://127.0.0.1:5000")
OUT = pathlib.Path(os.environ.get("ABX_EVIDENCE", "playback_kit/dryrun_evidence"))
OUT.mkdir(parents=True, exist_ok=True)
KIT = pathlib.Path(os.environ.get("ABX_KIT", "playback_kit"))

def req(method, path, payload=None, upload=None):
    url = BASE + path
    data, headers = None, {}
    if upload is not None:
        boundary = uuid.uuid4().hex
        name, blob = upload
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{name}\"\r\nContent-Type: audio/wav\r\n\r\n").encode() \
               + blob + f"\r\n--{boundary}--\r\n".encode()
        data, headers["Content-Type"] = body, f"multipart/form-data; boundary={boundary}"
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

RESULTS = []
def check(row, desc, ok, detail=""):
    RESULTS.append((row, desc, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] row {row}: {desc}" + (f" | {detail}" if detail else ""))

S = {"planned_trials": 2, "sample_rate_mode": "native", "label_mapping_mode": "fixed",
     "canary_enabled": True,
     "stages_a": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}],
     "stages_b": [{"codec_id": "noop_passthrough", "bitrate_kbps": 0}]}

def prep(payload):
    st, _ = req("POST", "/api/trial/reset", {})
    st, r = req("POST", "/api/upload", upload=("identical_a.wav",
              (KIT / "identical_a.wav").read_bytes()))
    assert st == 200, r
    st, r = req("POST", "/api/config", payload); assert st == 200, r
    st, r = req("POST", "/api/preprocess", {}); assert st == 200, r
    import time
    for _ in range(120):
        st, r = req("GET", "/api/preprocess/status")
        if r["ready"] or r["error"]:
            break
        time.sleep(1)
    assert r["ready"] and not r["error"], r
    return r

def canary_pass():
    """Stage the calibration trial and land a correct answer; X is a fair coin,
    so we read the feedback. Returns the last answer payload."""
    last = None
    for _ in range(8):
        st, t = req("POST", "/api/trial/start", {})
        assert st == 200, t
        for guess in ("A", "B"):
            st, a = req("POST", "/api/trial/answer", {"answer": guess})
            last = a
            if a.get("trial", {}).get("correct") is True:
                return a
            # wrong: gate closed for THIS run; reset and re-draw the coin
            req("POST", "/api/trial/reset", {})
            st, t = req("POST", "/api/trial/start", {})
            assert st == 200, t
            # answer the fresh trial directly below (guess A path covers B next loop)
            break
    raise AssertionError("could not pass the canary in 8 draws (coin p<0.4%)")

print("== row 9: null gate, identical pipelines ==")
prep(S)
st, info = req("GET", "/api/session/info")
ng = info.get("null_gate", {})
(OUT / "row9_session.json").write_text(json.dumps(info, indent=2))
check(9, "identical pipelines -> null gate passed", ng.get("status") == "passed",
      f"status={ng.get('status')} depth={ng.get('residual_db', ng.get('depth_db'))}")

print("== row 11: config freeze from first staged trial ==")
st, t1 = req("POST", "/api/trial/start", {})   # trial 1 = canary (staged)
st, cf = req("POST", "/api/config", {**S, "planned_trials": 8})
(OUT / "row11_config_frozen.json").write_text(json.dumps(cf, indent=2))
check(11, "config write after first staged trial -> 409 ConfigLocked",
      st == 409 and "frozen" in json.dumps(cf), f"http={st}")

print("== row 12: mid-run export is REDACTED ==")
st, mid = req("GET", "/api/export/json")
v = mid.get("session", {}).get("verifiability", {})
(OUT / "row12_midrun.json").write_text(json.dumps(mid, indent=2))
blob = json.dumps(mid)
import re as _re
leaks = [m for m in _re.findall(r'"x_source":\s*([^,}]+)', blob) if m.strip() != "null"]
check(12, "mid-run export redacted, no secret for pending trial",
      v.get("redacted") is True and not leaks,
      f"redacted={v.get('redacted')} leaked_x_source={leaks}")

print("== row 13a: seal -> verifiable, SEALED ==")
cp = canary_pass()                                           # coin-judged from feedback
st, t2 = req("POST", "/api/trial/start", {})                  # first scored trial
st, ans2 = req("POST", "/api/trial/answer", {"answer": "A"})  # 1 of 2 scored (N=2, 1 answered)
st, seal = req("POST", "/api/trial/seal", {"reason": "headless dry-run seal"})
st, sealex = req("GET", "/api/export/json")
(OUT / "row13a_sealed.json").write_text(json.dumps(sealex, indent=2))
sv = sealex.get("session", {}).get("verifiability", {})
stats = sealex.get("stats", sealex.get("session", {}))
blob = json.dumps(sealex)
check("13a", "sealed export verifiable + SEALED label, not complete",
      sv.get("redacted") is False and "SEALED" in blob and sealex.get("stats", {}).get("is_final") is False,
      f"redacted={sv.get('redacted')}")

print("== row 10 + 13b: complete exactly at N ==")
st, r = req("POST", "/api/trial/reset", {})
st, r = req("POST", "/api/config", {**S, "planned_trials": 2})
canary_pass()                                                 # calibration gate open
st, t2 = req("POST", "/api/trial/start", {})
st, a2 = req("POST", "/api/trial/answer", {"answer": "A"})
st, t3 = req("POST", "/api/trial/start", {})
st, a3 = req("POST", "/api/trial/answer", {"answer": "A"})    # N=2 reached
st, over = req("POST", "/api/trial/start", {})
(OUT / "row10_overrun.json").write_text(json.dumps(over, indent=2))
check(10, "past N: start refused 409 TrialLimitReached",
      st == 409 and over.get("error"), f"http={st} error={over.get('error','')[:60]}")
st, fin = req("GET", "/api/export/json")
(OUT / "row13b_completed.json").write_text(json.dumps(fin, indent=2))
fv = fin.get("session", {}).get("verifiability", {})
fstats = fin.get("stats", {})
check("13b", "completed export: not redacted, is_final, significant_verdict present",
      fv.get("redacted") is False and fstats.get("is_final") is True
      and "significant_verdict" in fstats,
      f"is_final={fstats.get('is_final')} verdict={fstats.get('verdict')}")

print("== row 8: calibration FAIL closes the gate ==")
ok8 = False
for attempt in range(6):
    prep(S)
    st, t1 = req("POST", "/api/trial/start", {})
    st, a1 = req("POST", "/api/trial/answer", {"answer": "A"})   # coin; we want the fail
    if a1.get("trial", {}).get("correct") is False:
        ok8 = True
        break
bst, blocked = req("POST", "/api/trial/start", {})
st8, inf8 = req("GET", "/api/session/info")
stx, voidx = req("GET", "/api/export/json")
(OUT / "row8_void.json").write_text(json.dumps(voidx, indent=2))
check(8, "failed canary: further trial refused 409, run_valid false, VOID, export ok",
      ok8 and bst == 409 and inf8.get("run_valid") is False
      and "VOID" in json.dumps(voidx),
      f"canary_failed={ok8} http={bst} run_valid={inf8.get('run_valid')}")

print("\n== summary ==")
fails = [r for r in RESULTS if not r[2]]
print(f"{len(RESULTS) - len(fails)}/{len(RESULTS)} rows pass headless")
sys.exit(1 if fails else 0)
