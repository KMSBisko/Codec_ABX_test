"""CI contract: the workflow must verify what it claims to verify.

The Phase 2/3 pipeline suite was verified LOCALLY (real ffmpeg + the shared
audio extras): 29/29. The CI `pipeline-smoke` job verified nothing:

* it installed only ``requirements-dev.txt``, so the pipeline modules'
  module-level ``pytest.importorskip("pyloudnorm")`` skipped every test;
* it ran pytest under ``-m pipeline || echo "pipeline suite pending"``, which
  swallows real failures AND the all-skipped exit path;
* nothing proved the suite collected tests, so an empty ``-m pipeline`` run
  looked green.

A green job that verifies nothing is worse than a red one: it is a citation
surface that lies. This file pins the contract. It is deliberately text-based
(no PyYAML in requirements-dev) so it runs in the lightweight core matrix.
"""
import pathlib
import re

WORKFLOW = (
    pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
)


def _text() -> str:
    assert WORKFLOW.exists(), f"CI workflow moved or renamed: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _jobs() -> dict:
    """Job-name -> job body, split on the 2-space indentation under `jobs:`."""
    lines = _text().splitlines()
    jobs: dict[str, list[str]] = {}
    current = None
    in_jobs = False
    for line in lines:
        if line == "jobs:":
            in_jobs = True
            continue
        if in_jobs:
            top = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if top:
                current = top.group(1)
                jobs[current] = []
                continue
        if current is not None:
            jobs[current].append(line)
    # Comment lines (e.g. the prose preamble above the next job) are not part
    # of a job's executable contract.
    return {
        name: "\n".join(
            line for line in body if not line.lstrip().startswith("#")
        )
        for name, body in jobs.items()
    }


def _job(name: str) -> str:
    jobs = _jobs()
    assert name in jobs, f"job {name!r} missing from ci.yml: {sorted(jobs)}"
    return jobs[name]


# ── failure masking ──────────────────────────────────────────────────────────

MASKING = [
    (r"\|\|\s*echo\b", "`|| echo ...` swallows the command's exit code"),
    (r"\|\|\s*true\b", "`|| true` swallows the exit code"),
    (r"\|\|\s*:\s*$", "`|| :` swallows the exit code"),
    (r"\|\|\s*exit\s+0\b", "`|| exit 0` swallows the exit code"),
    (r"continue-on-error:\s*true", "continue-on-error turns red steps green"),
    (r"allow_failure:\s*true", "allow_failure turns red checks green"),
]


def test_no_failure_masking_anywhere_in_the_workflow():
    offenders = []
    for i, line in enumerate(_text().splitlines(), start=1):
        for pattern, why in MASKING:
            if re.search(pattern, line):
                offenders.append(f"line {i}: {line.strip()!r} — {why}")
    assert not offenders, "CI masks failures:\n" + "\n".join(offenders)


# ── the pipeline job must be a real audio job ────────────────────────────────

def test_pipeline_job_installs_the_shared_audio_extras():
    """requirements-dev omits pyloudnorm/soundfile/scipy; with only dev deps
    every pipeline module is import-skipped, so the suite verifies nothing."""
    section = _job("pipeline-smoke")
    assert re.search(r"pip install[^\n]*requirements-audio\.txt", section), (
        "pipeline-smoke must install requirements-audio.txt; the pipeline "
        "suite import-skips entirely on requirements-dev.txt alone"
    )


def test_pipeline_job_installs_ffmpeg():
    section = _job("pipeline-smoke")
    assert re.search(r"apt-get install -y ffmpeg", section), (
        "the codec pipeline is meaningless without ffmpeg"
    )


def test_pipeline_suite_actually_runs_and_fails_the_job():
    section = _job("pipeline-smoke")
    assert re.search(r"pytest\b[^\n]*-m\s+pipeline", section), (
        "pipeline-smoke must run `pytest -m pipeline`"
    )
    # A pytest run whose tests all skip exits 0; a run that collects nothing
    # is a vacuous green. Demand a runtime summary check: junit counts, then
    # non-zero exit when the suite is empty, skipped, or failing.
    assert "--junitxml" in section, (
        "pipeline run must emit a junit summary so emptiness/skips are judged"
    )
    assert re.search(r"tests == 0", section), (
        "the summary check must fail the job when the suite collected no tests"
    )
    assert re.search(r"skipped", section) and re.search(r"sys\.exit", section), (
        "the summary check must fail the job when pipeline tests were skipped "
        "(missing audio deps or ffmpeg on the runner)"
    )
    pytest_lines = [
        line.strip() for line in section.splitlines() if "pytest" in line and "run" not in line
    ]
    for line in pytest_lines:
        assert not re.search(r"\|\|", line), f"pipeline pytest call masked: {line!r}"


# ── the lightweight matrix stays lightweight and explicit ────────────────────

def test_core_matrix_is_lightweight_and_excludes_the_pipeline_marker():
    section = _job("core-and-web")
    assert re.search(r"pip install[^\n]*requirements-dev\.txt", section)
    for forbidden in ("requirements-audio.txt", "PyQt6", "sounddevice"):
        assert forbidden not in section, (
            f"core-and-web must stay lightweight (no Qt/audio backend): found {forbidden!r}"
        )
    assert re.search(r"pytest\b[^\n]*-m\s+\"not pipeline\"", section), (
        'the lightweight matrix must run `-m "not pipeline"` explicitly, so '
        "pipeline exclusion is a marker decision, not an import-skip accident"
    )
    versions = re.search(r"python-version:\s*\[([^\]]+)\]", section)
    assert versions and len(versions.group(1).split(",")) >= 3, (
        "the core/API matrix must keep its multi-version coverage"
    )
