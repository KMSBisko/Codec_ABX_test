from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .models import TrialResult


class ExperimentLogger:
    def __init__(self) -> None:
        self.session_info: Dict[str, object] = {}
        self.trials: List[TrialResult] = []

    def set_session_info(self, info: Dict[str, object]) -> None:
        self.session_info = dict(info)

    def add_trial(self, trial: TrialResult) -> None:
        self.trials.append(trial)

    def utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, object]:
        return {
            "exported_at_utc": self.utc_now_iso(),
            "session": self.session_info,
            "trials": [asdict(t) for t in self.trials],
        }

    def export_json(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    def export_csv(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        with p.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "trial_index",
                    "x_is",
                    "answer",
                    "correct",
                    "timestamp_utc",
                ],
            )
            writer.writeheader()
            for trial in self.trials:
                writer.writerow(asdict(trial))
