"""The exported report is the citation artifact: it must not lie."""
import csv
import json

import pytest

from app.logger import EXPORT_SCHEMA, ExperimentLogger
from app.models import TrialResult


def _trial(i, correct=True, **kw):
    base = dict(
        trial_index=i,
        x_is="A",
        answer="A",
        correct=correct,
        timestamp_utc=f"2026-01-0{i}T00:00:00+00:00",
        mapping_a_to="A",
        mapping_b_to="B",
        x_source="A",
        answer_source="A",
        mapping_changed_for_next_trial=None,
    )
    base.update(kw)
    return TrialResult(**base)


@pytest.fixture()
def logger():
    lg = ExperimentLogger()
    lg.set_session_info({"codec_a": "aptX", "codec_b": "LDAC"})
    for i in range(1, 6):
        lg.add_trial(_trial(i, correct=(i % 2 == 1)))
    return lg


def test_csv_export_includes_every_trial_field(logger, tmp_path):
    """Regression: the exporter hardcoded 5 columns while TrialResult grew
    blind-mapping audit fields, so CSV export raised on every real session."""
    out = tmp_path / "r.csv"
    logger.export_csv(str(out))
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 5
    assert set(rows[0]) == {
        "trial_index", "x_is", "answer", "correct", "timestamp_utc",
        "mapping_a_to", "mapping_b_to", "x_source", "answer_source",
        "mapping_changed_for_next_trial",
    }
    assert rows[0]["x_source"] == "A"


def test_csv_export_survives_missing_optional_fields(logger, tmp_path):
    lg = ExperimentLogger()
    lg.add_trial(
        TrialResult(
            trial_index=1, x_is="B", answer="B", correct=True,
            timestamp_utc="t",
        )
    )
    out = tmp_path / "r.csv"
    lg.export_csv(str(out))
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["mapping_a_to"] == "", "None must not serialise as the string 'None'"


def test_csv_export_with_zero_trials_writes_header(logger, tmp_path):
    lg = ExperimentLogger()
    out = tmp_path / "empty.csv"
    lg.export_csv(str(out))
    assert out.read_text().strip() != ""
    assert len(list(csv.DictReader(out.open()))) == 0


def test_json_export_round_trips(logger, tmp_path):
    out = tmp_path / "r.json"
    logger.export_json(str(out))
    data = json.loads(out.read_text())
    assert data["session"]["codec_a"] == "aptX"
    assert len(data["trials"]) == 5
    assert data["trials"][0]["x_is"] == "A"


def test_seal_attaches_disclosure_and_verifiability(logger):
    logger.seal(
        disclosure={"codec_fallbacks": [
            {"requested_codec_name": "LDAC", "actual_codec_name": "Simulated LDAC"}
        ]},
        verifiability={"commitment": "deadbeef", "verifiable": True},
        statistics={"planned_trials": 16, "is_final": True},
    )
    assert logger.session_info["export_schema"] == EXPORT_SCHEMA
    fb = logger.session_info["disclosure"]["codec_fallbacks"][0]
    assert fb["requested_codec_name"] == "LDAC"
    assert fb["actual_codec_name"] == "Simulated LDAC", (
        "a substitution must be visible in the exported report"
    )
    assert logger.session_info["verifiability"]["commitment"] == "deadbeef"
    assert logger.session_info["tool"] == "codec-abx-tester"


def test_seal_ignores_none_blocks(logger):
    logger.seal(disclosure=None, statistics={"is_final": False})
    assert "disclosure" not in logger.session_info
    assert logger.session_info["statistics"]["is_final"] is False


def test_seal_is_json_serialisable_with_infinity(logger, tmp_path):
    import math

    logger.seal(statistics={"null_depth_db": float("-inf"), "ci": [0.5, 1.0]})
    out = tmp_path / "s.json"
    logger.export_json(str(out))
    parsed = json.loads(out.read_text())
    depth = parsed["session"]["statistics"]["null_depth_db"]
    assert math.isinf(depth) and depth < 0, "perfect null must survive export"
