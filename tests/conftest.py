"""Top-level pytest conftest.

Forces the repo root onto sys.path BEFORE any test module loads so that
top-level packages like `server`, `bin`, `dashboard` resolve correctly
regardless of how pytest was invoked or what state pip-editable leaves
in site-packages.

This duplicates `pyproject.toml`'s `[tool.pytest.ini_options].pythonpath`
defensively — on CI runners with `pip install -e .[test,exr,xlsx]`, the
editable install was occasionally shadowing `import server` with the
oyster_agent_runner.server submodule (pytest collection error:
"No module named 'server.marketplace_api'; 'server' is not a package").
Prepending the repo root in conftest defeats any such shadow.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Prepend (not append) so `./server/__init__.py` wins over any same-name
# module that pip-editable mode may have installed into site-packages.
_repo_root_str = str(_REPO_ROOT)
if _repo_root_str in sys.path:
    sys.path.remove(_repo_root_str)
sys.path.insert(0, _repo_root_str)
