import sys
from pathlib import Path

# Run against the source tree without requiring an install step.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
