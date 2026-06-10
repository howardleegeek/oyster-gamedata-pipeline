"""Schema contract test — pins the field set the Fabric mod emits against
the field set the Python recorder consumes.

Howard 2026-05-07: this is the single point of failure for the cross-language
real-game-state pipeline. If a future change adds/removes a field in
``mc-mod/src/main/java/world/oyster/recorder/GameStateSample.java`` without
updating ``bin/game_state_overlay.py``, this test fails CI before the bug
ships. Either side can be the source of truth as long as they agree.

Reads the Java file as text (no JVM needed) and the Python module via import,
then asserts the canonical field set is identical.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

import game_state_overlay  # noqa: E402

# The canonical field set that both sides must support, in JSON-line order
# matching GameStateSample.toJsonLine().
EXPECTED_FIELDS = {
    "tick",
    "timestamp_ms",
    "x",
    "y",
    "z",
    "yaw_deg",
    "pitch_deg",
    "look_x",
    "look_y",
    "look_z",
    "velocity_x",
    "velocity_y",
    "velocity_z",
    "on_ground",
    "sneaking",
    "sprinting",
    "paused",
    "dimension",
    "game_mode",
}


def _java_field_set() -> set[str]:
    """Parse the JSON keys actually emitted by GameStateSample.toJsonLine."""
    java = (
        REPO_ROOT
        / "mc-mod"
        / "src"
        / "main"
        / "java"
        / "world"
        / "oyster"
        / "recorder"
        / "GameStateSample.java"
    ).read_text()
    # Match every appendKv*(sb, "key", ...) call inside toJsonLine.
    keys = re.findall(r'appendKv\w*\(sb,\s*"([^"]+)",', java)
    return set(keys)


def _python_field_set() -> set[str]:
    """The field set apply_to_record + lookup_at_ms read from a sample."""
    fn_src = "\n".join(
        [
            Path(game_state_overlay.__file__).read_text(),
        ]
    )
    # Heuristic: every sample["FIELD"] or sample.get("FIELD", ...) in the module.
    direct = set(re.findall(r'sample\["([^"]+)"\]', fn_src))
    indirect = set(re.findall(r'sample\.get\("([^"]+)"', fn_src))
    return direct | indirect


def test_java_emits_the_expected_field_set():
    actual = _java_field_set()
    missing = EXPECTED_FIELDS - actual
    extra = actual - EXPECTED_FIELDS
    assert not missing, f"Java GameStateSample.java is MISSING fields: {missing}"
    assert not extra, (
        f"Java GameStateSample.java emits EXTRA fields not in canonical set: "
        f"{extra}. If this is intentional, update EXPECTED_FIELDS in this test."
    )


def test_python_consumes_the_expected_field_set():
    actual = _python_field_set()
    # Python only needs to USE the fields it cares about — extra mod fields are
    # tolerated. But every field Python reads MUST be in the canonical set,
    # else the mod isn't producing it and lookup will KeyError at runtime.
    extra = actual - EXPECTED_FIELDS
    assert not extra, (
        f"bin/game_state_overlay.py reads sample fields not in canonical set: "
        f"{extra}. Either add to GameStateSample.java + EXPECTED_FIELDS, or "
        f"stop reading them in Python."
    )


def test_lookup_at_ms_returns_expected_fields():
    """Smoke test: round-trip a synthetic sample through lookup_at_ms +
    apply_to_record. Catches silent renames in either direction."""
    sample = {f: 0 for f in EXPECTED_FIELDS}
    sample["dimension"] = "minecraft:overworld"
    sample["game_mode"] = "SURVIVAL"
    sample["on_ground"] = True
    sample["sneaking"] = False
    sample["sprinting"] = False
    sample["paused"] = False
    sample["timestamp_ms"] = 1000

    samples = [sample]
    found = game_state_overlay.lookup_at_ms(samples, 0)
    assert found is sample

    record = {
        "camera_position": [0.0, 64.0, 0.0],
        "player_position": [0.0, 64.0, 0.0],
    }
    game_state_overlay.apply_to_record(record, sample)

    # All overridden fields MUST be present after apply.
    for k in [
        "camera_position",
        "camera_rotation_oula",
        "camera_rotation_quaternion",
        "camera_Follow Offset",
        "camera_speed",
        "player_position",
        "player_rotation_oula",
        "player_rotation_quaternion",
        "player_speed",
        "_real_game_state",
    ]:
        assert k in record, f"apply_to_record didn't produce {k}"

    # Authenticity tag must be True.
    assert record["_real_game_state"] is True


def test_load_returns_none_for_missing_file():
    """Recorder must not crash if mod isn't installed."""
    result = game_state_overlay.load(Path("/nonexistent/__never_exists__.jsonl"))
    assert result is None
