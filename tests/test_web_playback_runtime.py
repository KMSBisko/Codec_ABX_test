"""Execute real client playback functions with Node's built-in test runner.

DOM, Web Audio and network boundaries are fakes: no browser, server or sound
is started. These regressions check which delivered buffer the source owns,
not whether switching sounds seamless. CI installs Node explicitly.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_ci_installs_node_for_client_runtime_regressions():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    core_job = workflow.split("  core-and-web:", 1)[1].split("  pipeline-smoke:", 1)[0]
    assert core_job.index("actions/setup-node@v4") < core_job.index("python -m pytest")
    assert 'node-version: "22"' in core_job


def test_client_playback_runtime():
    node = shutil.which("node")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is required for the client playback regressions in CI")
        pytest.skip("Node.js 22+ is required for client playback runtime regressions")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests/js/web_playback.test.cjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
