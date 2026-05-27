#!/usr/bin/env python3
"""tester_preflight — One-Click Self-Audit for v0.11.20 Internal Testers
=========================================================================

Tester finishes a Minecraft recording session and runs ONE command. This
script:

  1. Discovers the newest session under %USERPROFILE%\\Documents\\OysterClips\\
  2. Sanity-checks the raw recorder output (mp4, game_state.jsonl, etc.)
  3. Runs canonical_pipeline.py if post-processing artifacts are missing
  4. Runs prd_compliance_audit.py --json
  5. Emits a single GREEN / YELLOW / RED verdict + numbered failure list

Exit codes:
  0 = GREEN  (>= GREEN_FLOOR PASS — production-ready session)
  1 = YELLOW (>= YELLOW_FLOOR PASS — usable but with caveats)
  2 = RED    (< YELLOW_FLOOR PASS — record again)
  3 = PRE_CHECK_FAIL (no session / missing critical files)
  4 = AUDIT_CRASH (audit script itself errored)

Empirical thresholds (set 2026-05-26):
  GREEN_FLOOR  = 95/105   — full canonical pipeline run on real >=5min recording
  YELLOW_FLOOR = 80/105   — partial pipeline OR short recording
  Below 80     = RED — likely missing files / truncated mp4 / launcher bug

Pure stdlib. Designed to run inside the v0.11.20 installer's bundled Python
or on any Python 3.10+ install.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GREEN_FLOOR = 95
YELLOW_FLOOR = 80
AUDIT_TOTAL_EXPECTED = 105  # current PRD audit total

REQUIRED_RAW_FILES = ("recording.mp4", "game_state.jsonl", "inputs.jsonl", "metadata.json")
POST_PIPELINE_FILES = ("MANIFEST.json", "gameinfo.xlsx", "audio_check.json")

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin"
AUDIT_SCRIPT = BIN / "prd_compliance_audit.py"
CANONICAL_PIPELINE = BIN / "canonical_pipeline.py"

# Per-failure-category hint shown to tester
NEXT_STEP_HINTS = {
    "A": "原始录制文件缺失 — 检查 recorder 进程是否崩溃",
    "V": "recording.mp4 异常 — 确认录制 ≥5 分钟且未中断",
    "C": "frames.jsonl 字段缺失 — 检查 mod 是否加载（fabric-api + oyster-recorder-mod）",
    "D": "数据连续性问题 — 通常意味着录制中途断流",
    "E": "字段格式问题 — 单元/坐标系不符 PRD",
    "F": "gameinfo.xlsx 缺失 — canonical_pipeline.py 未跑或失败",
    "H": "depth 数据问题 — DA-V2 模型未运行或目录损坏",
    "M": "metadata.json 缺失字段 — recorder 版本太老",
    "Q": "操作多样性不够 — 录制时多按 WASD/F5/E/方向键",
    "QM": "音视频质量指标问题 — 检查 audio.flac + ffprobe",
    "SS": "时间戳一致性问题 — 通常 mp4 截断或 metadata 写入失败",
    "B": "音频或视频码率问题 — 检查录制配置",
    "AR": "反重放检查 — 录制时长不够",
    "U": "audio.flac 缺失 — 检查麦克风是否禁用",
    "G": "operator_id 缺失 — metadata 写入失败",
    "X": "gameinfo.xlsx 字段缺失",
}


def _color(text: str, code: str) -> str:
    """Return ANSI-colored text only if a real TTY supports it."""
    if not sys.stdout.isatty():
        return text
    if os.name == "nt" and "WT_SESSION" not in os.environ and "ANSICON" not in os.environ:
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def green(s: str) -> str:
    return _color(s, "32;1")


def yellow(s: str) -> str:
    return _color(s, "33;1")


def red(s: str) -> str:
    return _color(s, "31;1")


def bold(s: str) -> str:
    return _color(s, "1")


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def discover_session(explicit: Path | None) -> Path | None:
    """Locate the newest session_* under OysterClips, or honor the explicit path."""
    if explicit is not None:
        return explicit if explicit.is_dir() else None

    candidates: list[Path] = []
    # Windows default
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        candidates.append(Path(userprofile) / "Documents" / "OysterClips")
    # POSIX dev fallback
    home = Path.home()
    candidates.append(home / "Documents" / "OysterClips")
    candidates.append(home / "Downloads" / "OysterClips")

    for root in candidates:
        if not root.is_dir():
            continue
        sessions = sorted(
            (p for p in root.glob("session_*") if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if sessions:
            return sessions[0]

    return None


# ---------------------------------------------------------------------------
# Pre-checks
# ---------------------------------------------------------------------------


def precheck(session: Path) -> tuple[bool, list[str]]:
    """Return (ok, problems). ok=False blocks audit run."""
    problems: list[str] = []
    if not session.exists():
        problems.append(f"session 目录不存在: {session}")
        return False, problems

    for name in REQUIRED_RAW_FILES:
        path = session / name
        if not path.exists():
            problems.append(f"缺失关键文件: {name}")
        elif path.stat().st_size == 0:
            problems.append(f"文件大小为 0: {name}")

    mp4 = session / "recording.mp4"
    if mp4.exists() and mp4.stat().st_size < 1_000_000:
        problems.append(
            f"recording.mp4 只有 {mp4.stat().st_size} bytes — 录制极可能被截断"
        )

    state = session / "game_state.jsonl"
    if state.exists() and state.stat().st_size > 0:
        try:
            with state.open("r", encoding="utf-8") as fp:
                first = fp.readline()
                json.loads(first)
        except json.JSONDecodeError:
            problems.append("game_state.jsonl 第一行不是合法 JSON")

    # Note: post-pipeline files are NOT pre-check blockers; we'll try to
    # invoke canonical_pipeline.py to generate them.
    return (not problems), problems


# ---------------------------------------------------------------------------
# Canonical pipeline (best-effort)
# ---------------------------------------------------------------------------


def run_canonical_pipeline(session: Path) -> tuple[bool, str]:
    """Invoke canonical_pipeline.py if available. Best-effort; never raises."""
    if not CANONICAL_PIPELINE.exists():
        return True, "canonical_pipeline.py 不存在 — 跳过（仅审计现有产物）"

    missing = [f for f in POST_PIPELINE_FILES if not (session / f).exists()]
    if not missing:
        return True, "post-processing 产物已存在 — 跳过 canonical_pipeline"

    cmd = [sys.executable, str(CANONICAL_PIPELINE), str(session)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, check=False
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, f"canonical_pipeline 启动失败: {type(exc).__name__}: {exc}"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-400:]
        return False, f"canonical_pipeline 退出码 {proc.returncode}\n{tail}"

    return True, "canonical_pipeline 完成"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def run_audit(session: Path) -> dict | None:
    """Run prd_compliance_audit.py --json, return parsed dict or None on crash."""
    if not AUDIT_SCRIPT.exists():
        print(red(f"❌ FATAL: audit 脚本不在 {AUDIT_SCRIPT}"))
        return None

    cmd = [sys.executable, str(AUDIT_SCRIPT), str(session), "--json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(red(f"❌ 审计脚本启动失败: {type(exc).__name__}: {exc}"))
        return None

    out = proc.stdout or ""
    brace = out.find("{")
    if brace < 0:
        print(red("❌ 审计脚本没有产生 JSON 输出"))
        print(proc.stderr[-400:] if proc.stderr else "(no stderr)")
        return None
    try:
        return json.loads(out[brace:])
    except json.JSONDecodeError as exc:
        print(red(f"❌ JSON 解析失败: {exc}"))
        return None


# ---------------------------------------------------------------------------
# Verdict + report
# ---------------------------------------------------------------------------


def _category(item_id: str) -> str:
    """Map check ID to category prefix (A, V, C, ...)."""
    for prefix in ("QM", "SS", "AR"):
        if item_id.startswith(prefix):
            return prefix
    return item_id[:1] if item_id else "?"


def render_verdict(audit: dict, session: Path) -> int:
    """Print human verdict and return exit code (0/1/2)."""
    total = audit.get("total_items", 0)
    passed = audit.get("passed", 0)
    failed = audit.get("failed", 0)
    items = audit.get("items", [])

    pct = (100.0 * passed / total) if total else 0.0

    print()
    print(bold("━" * 60))
    if passed >= GREEN_FLOOR:
        verdict_line = green(f"✅ GREEN — {passed}/{total} PASS ({pct:.1f}%) — 可发货")
        code = 0
    elif passed >= YELLOW_FLOOR:
        verdict_line = yellow(
            f"⚠️ YELLOW — {passed}/{total} PASS ({pct:.1f}%) — 可用但有遗憾"
        )
        code = 1
    else:
        verdict_line = red(
            f"❌ RED — {passed}/{total} PASS ({pct:.1f}%) — 重录一次"
        )
        code = 2

    print(verdict_line)
    print(bold(f"  session: {session}"))
    print(bold("━" * 60))
    print()

    if failed > 0:
        # Group fails by category for digestible output
        by_cat: dict[str, list[dict]] = {}
        for it in items:
            if it.get("status") == "PASS":
                continue
            by_cat.setdefault(_category(it["id"]), []).append(it)

        print(bold(f"FAIL items ({failed}):"))
        for cat in sorted(by_cat):
            cat_items = by_cat[cat]
            hint = NEXT_STEP_HINTS.get(cat, "")
            print(f"\n  [{cat}] {len(cat_items)} 项 — {hint}")
            for it in cat_items[:6]:  # top 6 per category
                ev = (it.get("evidence") or "").replace("\n", " ")
                if len(ev) > 90:
                    ev = ev[:87] + "..."
                print(f"    • {it['id']:18s} {ev}")
            if len(cat_items) > 6:
                print(f"    … 还有 {len(cat_items) - 6} 项同类")
    print()

    # 1-line session summary
    state = session / "game_state.jsonl"
    inputs = session / "inputs.jsonl"
    mp4 = session / "recording.mp4"
    state_lines = sum(1 for _ in state.open(encoding="utf-8")) if state.exists() else 0
    input_lines = sum(1 for _ in inputs.open(encoding="utf-8")) if inputs.exists() else 0
    mp4_mb = (mp4.stat().st_size / 1024 / 1024) if mp4.exists() else 0
    print(
        f"录制摘要: mp4={mp4_mb:.1f}MB, game_state={state_lines} 行, "
        f"inputs={input_lines} 行"
    )
    print()

    if code != 0:
        print(bold("把这个 session 目录打包发回给 Howard:"))
        print(f"  tar -czf my_session.tar.gz -C {session.parent} {session.name}")
        print()

    return code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    # Parse args (stdlib only — keep it simple)
    args = [a for a in argv[1:] if a not in ("-h", "--help")]
    if any(a in argv for a in ("-h", "--help")):
        print(__doc__)
        return 0

    force = "--force" in args
    args = [a for a in args if a != "--force"]
    explicit: Path | None = Path(args[0]) if args else None

    print(bold("🦪 Oyster GameData Recorder — Tester Self-Audit"))
    print()

    session = discover_session(explicit)
    if session is None:
        print(red("❌ 找不到 session_* 目录"))
        print()
        print("请检查：")
        print("  1. 你已经运行过 OysterPlay 并完成了一次 Minecraft 录制")
        print("  2. session 目录在 %USERPROFILE%\\Documents\\OysterClips\\")
        print(
            "  3. 或直接传入路径: python tester_preflight.py <session_dir>"
        )
        return 3

    print(f"找到 session: {session}")
    print()

    ok, problems = precheck(session)
    if not ok:
        if force:
            print(yellow("⚠️ 预检发现问题但 --force 跳过:"))
            for p in problems:
                print(f"  • {p}")
            print()
        else:
            print(red("❌ 预检失败:"))
            for p in problems:
                print(f"  • {p}")
            print()
            print(bold("建议: 重录一次。常见原因:"))
            print("  - 启动器闪退（看任务管理器是否有 javaw.exe）")
            print("  - 录制器没检测到游戏窗口（窗口分辨率 <1280x720？）")
            print("  - mod 未加载（检查 mods/ 里有 fabric-api + oyster-recorder-mod）")
            print()
            print("调试用: 加 --force 跳过预检直接跑审计")
            return 3

    print(green("✅ 预检通过 — 关键文件存在"))
    print()

    print("正在跑后处理流水线（canonical_pipeline）...")
    pipe_ok, pipe_msg = run_canonical_pipeline(session)
    print(f"  → {pipe_msg}")
    print()

    print("正在跑 PRD 104+ 检查（prd_compliance_audit）...")
    audit = run_audit(session)
    if audit is None:
        return 4

    return render_verdict(audit, session)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
