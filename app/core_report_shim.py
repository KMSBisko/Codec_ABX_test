"""Path shim so `app/` can import the repo-root `core` package.

Kept separate from abx_engine's shim so `import app.main` works when launched
from a subdirectory or from a PyInstaller bundle.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.report import (  # noqa: E402,F401
    canary_report_block,
    null_depth_text,
    validity_lines,
)

__all__ = ["canary_report_block", "null_depth_text", "validity_lines"]
