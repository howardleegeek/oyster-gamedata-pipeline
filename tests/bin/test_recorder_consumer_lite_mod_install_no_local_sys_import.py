#!/usr/bin/env python3
"""
Regression test: bin/recorder_consumer_lite.py should NOT re-import `sys`
inside the _try_install_mod_first_launch() inner except handler, because
`sys` is already module-level imported and a local `import sys` makes
Python treat `sys` as a function-local name — which then triggers ruff
F823 (referenced before assignment) on the earlier `hasattr(sys, "_MEIPASS")`
call in the same function.

Round 343: Fix F823 lint error in _try_install_mod_first_launch by removing
the redundant local `import sys`.
"""

import re
import subprocess
from pathlib import Path


MODULE_PATH = Path("bin/recorder_consumer_lite.py")


def _get_function_body(src: str, func_name: str) -> str:
    """Return the indented body of `func_name`, from the line after its
    `def ...` signature up to (but not including) the next top-level def
    or module-level statement."""
    lines = src.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {func_name}("):
            start = i + 1
            break
    assert start is not None, f"Could not find function {func_name}"
    # body is everything indented under the def
    body_lines = []
    for line in lines[start:]:
        if line.strip() == "":
            body_lines.append(line)
            continue
        if not line.startswith(("    ", "\t")):
            break
        body_lines.append(line)
    return "\n".join(body_lines)


def _get_inner_except_body(body: str) -> str:
    """Return the body lines belonging to the inner
    `except Exception as inner_exc:  # noqa: BLE001` block in
    _try_install_mod_first_launch — i.e. everything from the line after
    the inner except header until the next dedent."""
    # Find the inner except (the one that catches `inner_exc`).
    header_re = re.compile(
        r"^(\s+)except\s+Exception\s+as\s+inner_exc:\s*#\s*noqa:\s*BLE001\s*$",
        re.MULTILINE,
    )
    m = header_re.search(body)
    assert m is not None, (
        "Could not locate `except Exception as inner_exc: # noqa: BLE001` in "
        "_try_install_mod_first_launch"
    )
    header_indent = m.group(1)
    body_indent = header_indent + "    "
    lines = body.split("\n")
    # Find the index of the header line
    header_line_idx = body[: m.start()].count("\n")
    collected = []
    for i in range(header_line_idx + 1, len(lines)):
        line = lines[i]
        if line.strip() == "":
            collected.append(line)
            continue
        if line.startswith(body_indent) or line.startswith("\t"):
            collected.append(line)
        else:
            break
    return "\n".join(collected)


def test_mod_install_inner_except_does_not_reimport_sys():
    """The inner except must not shadow the module-level `sys` import."""
    src = MODULE_PATH.read_text()
    body = _get_function_body(src, "_try_install_mod_first_launch")
    inner_body = _get_inner_except_body(body)

    # The inner body must NOT contain a local `import sys` line.
    local_import = re.search(r"^\s*import\s+sys\s*$", inner_body, re.MULTILINE)
    assert local_import is None, (
        "Inner except must not contain `import sys`; `sys` is already "
        "module-level imported and a local import shadows it, triggering "
        "ruff F823. Inner body was:\n" + inner_body
    )


def test_mod_install_inner_except_still_prints_to_stderr():
    """The inner except must continue to surface the failure to stderr."""
    src = MODULE_PATH.read_text()
    body = _get_function_body(src, "_try_install_mod_first_launch")
    inner_body = _get_inner_except_body(body)
    # The inner except block must include a `print(..., file=sys.stderr)` call.
    assert "file=sys.stderr" in inner_body, (
        "Mod-install inner except block must still write to stderr so the "
        "failure is not silently swallowed. Inner body was:\n" + inner_body
    )
    assert "mod-install logging failed" in inner_body, (
        "Mod-install inner except block must still log the failure. "
        "Inner body was:\n" + inner_body
    )


def test_module_passes_ruff_f823_check():
    """The module must be ruff-clean (no F823 local-variable-before-assignment)."""
    result = subprocess.run(
        [".venv/bin/ruff", "check", str(MODULE_PATH), "--select", "F823"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff F823 still failing on {MODULE_PATH}:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_module_passes_ruff_ble001_check_on_inner_except():
    """The inner except must keep its `# noqa: BLE001` marker so ruff
    does not flag the broad `except Exception` (broad except is
    intentional: this is a last-ditch logging fallback that must never
    crash the recorder)."""
    src = MODULE_PATH.read_text()
    body = _get_function_body(src, "_try_install_mod_first_launch")
    header_re = re.compile(
        r"except\s+Exception\s+as\s+inner_exc:\s*#\s*noqa:\s*BLE001",
    )
    assert header_re.search(body), (
        "Inner `except Exception as inner_exc` must keep its "
        "`# noqa: BLE001` marker to suppress the broad-except warning."
    )


def test_module_compiles():
    """Verify the module still parses cleanly after the edit."""
    import py_compile

    py_compile.compile(str(MODULE_PATH), doraise=True)
