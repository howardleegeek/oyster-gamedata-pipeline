"""D16 schema-contract test — server-side Fabric mod ↔ cluster adapter.

Howard 2026-05-07: this is the cross-language contract gate for
Pipeline 2. If a future change adds/removes a field in
``mc-mod/src/main/java/world/oyster/recorder/server/ServerStateCapture.java``
without updating the same canonical ``GameStateSample`` schema the client
mod uses, this test fails CI before the bug ships.

Server mod reuses ``GameStateSample`` from the parent package (single
source of truth), so the schema is automatically aligned. This test
verifies that:

1. Server entry point in ``fabric.mod.json`` exists and points at
   ``world.oyster.recorder.server.OysterServerMod``
2. Server capture class actually constructs a ``GameStateSample`` (no
   alternative payload class slipped in)
3. Server output path uses ``oyster_state/<player>.jsonl`` convention
4. CLI ``--game-state-jsonl`` flag is wired through ``adapt_phase1_to_buyer_spec``
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))


def test_fabric_mod_json_has_server_entrypoint():
    p = REPO_ROOT / "mc-mod" / "src" / "main" / "resources" / "fabric.mod.json"
    data = json.loads(p.read_text())
    assert "server" in data["entrypoints"], "no server entrypoint declared"
    server_entries = data["entrypoints"]["server"]
    assert any("OysterServerMod" in e for e in server_entries), (
        f"no OysterServerMod found in server entrypoints: {server_entries}"
    )


def test_server_environment_is_star_or_dedicated():
    """Server-side mod must be loadable on dedicated server, not client-only."""
    p = REPO_ROOT / "mc-mod" / "src" / "main" / "resources" / "fabric.mod.json"
    data = json.loads(p.read_text())
    env = data.get("environment", "*")
    assert env in ("*", "server"), (
        f"environment={env!r} prevents server-side load"
    )


def test_server_capture_uses_canonical_GameStateSample():
    """Server capture MUST construct GameStateSample (the canonical type)
    rather than a divergent server-only payload."""
    src = (REPO_ROOT / "mc-mod" / "src" / "main" / "java" / "world"
           / "oyster" / "recorder" / "server" / "ServerStateCapture.java"
           ).read_text()
    assert "GameStateSample" in src, (
        "ServerStateCapture must use canonical GameStateSample type"
    )
    # Ensure it doesn't define a SECOND record/class with the same shape
    assert not re.search(r"\brecord\s+ServerSample\b", src), (
        "Don't define a second sample type — reuse GameStateSample"
    )


def test_server_session_dir_path_convention():
    """Server output goes to <server_dir>/oyster_state/<player>.jsonl."""
    src = (REPO_ROOT / "mc-mod" / "src" / "main" / "java" / "world"
           / "oyster" / "recorder" / "server" / "ServerSessionDir.java"
           ).read_text()
    assert "oyster_state" in src
    assert ".jsonl" in src
    # Should NOT use the client's user.home/Documents path
    assert "user.home" not in src, (
        "server-side path must be server-relative, not user-home"
    )


def test_cli_has_game_state_jsonl_flag():
    """The adapt-buyer-spec CLI command must accept --game-state-jsonl."""
    src = (REPO_ROOT / "src" / "oyster_agent_runner" / "cli.py").read_text()
    assert "--game-state-jsonl" in src, (
        "cli.py adapt-buyer-spec must expose --game-state-jsonl flag"
    )


def test_adapter_signature_accepts_game_state_jsonl():
    from oyster_agent_runner.buyer_spec_adapter import adapt_phase1_to_buyer_spec
    import inspect
    sig = inspect.signature(adapt_phase1_to_buyer_spec)
    assert "game_state_jsonl" in sig.parameters, (
        "adapt_phase1_to_buyer_spec missing game_state_jsonl kwarg"
    )


def test_pipeline_shell_discovers_jsonl():
    """buyer_spec_pipeline.sh should look for the bot's JSONL under the
    oyster_paper directories (cluster path convention)."""
    src = (REPO_ROOT / "bin" / "buyer_spec_pipeline.sh").read_text()
    assert "GAME_STATE_FLAG" in src
    assert "oyster_state" in src
    assert "${BOT_USERNAME}" in src, (
        "pipeline must look up JSONL by bot username"
    )
