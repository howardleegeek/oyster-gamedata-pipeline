"""Pytest configuration for phase2 tests."""
import sys
from pathlib import Path

# Add phase2 source directory to sys.path for bare imports like:
# from semantic_validator import ...
_phase2_src = Path(__file__).resolve().parents[2] / 'src' / 'oyster_agent_runner' / 'phase2'
if str(_phase2_src) not in sys.path:
    sys.path.insert(0, str(_phase2_src))
