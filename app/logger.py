from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .models import TrialResult

TOOL_NAME = "codec-abx-tester"
EXPORT_SCHEMA = "abx-session/1"


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

    def seal(self, **blocks: Dict[str, Any]) -> None:
        """Stamp the session at export time.

        Carries what the report must disclose to be citable:
          * disclosure   -- codec substitutions, resampler downgrades
          * verifiability -- commit-reveal (commitment + secret + X sequence)
          * statistics   -- planned N, final/provisional, CI, pass threshold
        """
        for key, payload in blocks.items():
            if payload is not None:
                self.session_info[key] = payload
        self.session_info["export_schema"] = EXPORT_SCHEMA
        self.session_info["tool"] = TOOL_NAME
        self.session_info["python"] = sys.version.split()[0]

    def _fieldnames(self) -> Sequence[str]:
        """Derived from the dataclass, never hardcoded.

        The previous hardcoded 5-column header crashed every CSV export with
        `dict contains fields not in fieldnames` as soon as the blind-mapping
        audit fields were added to TrialResult -- a documented feature that
        had never worked. See tests/test_logger_export.py.
        """
        if is_dataclass(TrialResult):
            return [f.name for f in fields(TrialResult)]
        return list(self.trials[0].__dict__.keys()) if self.trials else []

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

        fieldnames = list(self._fieldnames())
        with p.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for trial in self.trials:
                row = asdict(trial)
                # None -> "" so downstream readers (pandas, R, Excel) do not
                # parse the literal string "None" as a value.
                writer.writerow(
                    {
                        name: (
                            ""
                            if row.get(name) is None
                            else row.get(name)
                        )
                        for name in fieldnames
                    }
                )
