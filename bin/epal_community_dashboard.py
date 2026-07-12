#!/usr/bin/env python3
"""EPal Community Dashboard Widget.

Companion dashboard for EPal platform showing:
- Clips contributed this week
- Total bonus earned
- Leaderboard rank within community

Designed for embedding in EPal companion app via iframe or native view.
Outputs HTML (for iframe) or JSON (for native view).

Usage:
    python3 epal_community_dashboard.py --user-id u123 --output html
    python3 epal_community_dashboard.py --user-id u123 --output json --stdout
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


@dataclass
class ClipEntry:
    """A single clip contribution record."""
    clip_id: str
    user_id: str
    title: str
    created_at: str
    views: int = 0
    likes: int = 0
    bonus_amount: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class UserStats:
    """Aggregated statistics for a single user."""
    user_id: str
    display_name: str
    total_clips: int = 0
    total_bonus: float = 0.0
    clips_this_week: int = 0
    bonus_this_week: float = 0.0


@dataclass
class DashboardSnapshot:
    """Complete dashboard data for rendering."""
    user_id: str
    display_name: str
    week_label: str
    clips_this_week: int
    total_bonus_earned: float
    leaderboard_rank: int
    total_community_members: int
    recent_clips: List[Dict[str, Any]] = field(default_factory=list)
    leaderboard_top5: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()

    def to_json(self, indent: Optional[int] = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self), indent=indent, default=str)


# ---------------------------------------------------------------------------
# Data Loading Utilities
# ---------------------------------------------------------------------------

def _parse_datetime(dt_str: str) -> datetime:
    """Parse ISO format datetime string with fallbacks."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(dt_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _load_clips_json(path: Path) -> List[ClipEntry]:
    """Load clips from JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw if isinstance(raw, list) else raw.get("clips", [])
    return [ClipEntry(**item) for item in items if isinstance(item, dict)]


def _load_clips_csv(path: Path) -> List[ClipEntry]:
    """Load clips from CSV file."""
    clips: List[ClipEntry] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            clips.append(ClipEntry(
                clip_id=row.get("clip_id", ""),
                user_id=row.get("user_id", ""),
                title=row.get("title", ""),
                created_at=row.get("created_at", ""),
                views=int(row.get("views", 0) or 0),
                likes=int(row.get("likes", 0) or 0),
                bonus_amount=float(row.get("bonus_amount", 0.0) or 0.0),
            ))
    return clips


def load_clips(data_dir: Path) -> List[ClipEntry]:
    """Load clips from data directory (JSON or CSV)."""
    json_path = data_dir / "clips.json"
    csv_path = data_dir / "clips.csv"
    if json_path.is_file():
        return _load_clips_json(json_path)
    if csv_path.is_file():
        return _load_clips_csv(csv_path)
    return []


def load_users(data_dir: Path) -> Dict[str, str]:
    """Load user ID to display name mapping."""
    users_path = data_dir / "users.json"
    if users_path.is_file():
        with open(users_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


# ---------------------------------------------------------------------------
# Aggregation Logic
# ---------------------------------------------------------------------------

def get_week_bounds() -> tuple[datetime, datetime]:
    """Get start and end of current week (Monday to Sunday, UTC)."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=now.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start, end


def aggregate_user_stats(clips: List[ClipEntry], user_id: str) -> UserStats:
    """Compute statistics for a specific user."""
    user_clips = [c for c in clips if c.user_id == user_id]
    week_start, week_end = get_week_bounds()

    clips_this_week = 0
    bonus_this_week = 0.0
    total_bonus = 0.0

    for clip in user_clips:
        total_bonus += clip.bonus_amount
        try:
            created = _parse_datetime(clip.created_at)
            if week_start <= created < week_end:
                clips_this_week += 1
                bonus_this_week += clip.bonus_amount
        except Exception as exc:
            logger.debug(
                "Skipping clip %s for user %s: datetime parse/compare failed: %s",
                clip.clip_id, user_id, exc,
            )

    return UserStats(
        user_id=user_id,
        display_name=user_id,
        total_clips=len(user_clips),
        total_bonus=total_bonus,
        clips_this_week=clips_this_week,
        bonus_this_week=bonus_this_week,
    )


def compute_leaderboard(clips: List[ClipEntry]) -> List[UserStats]:
    """Compute leaderboard rankings based on total bonus earned."""
    user_data: Dict[str, UserStats] = {}
    for clip in clips:
        uid = clip.user_id
        if uid not in user_data:
            user_data[uid] = UserStats(user_id=uid, display_name=uid)
        user_data[uid].total_clips += 1
        user_data[uid].total_bonus += clip.bonus_amount

    leaderboard = sorted(user_data.values(), key=lambda u: u.total_bonus, reverse=True)
    return leaderboard


def get_user_rank(leaderboard: List[UserStats], user_id: str) -> int:
    """Get 1-indexed rank for user (0 if not found)."""
    for idx, stats in enumerate(leaderboard, start=1):
        if stats.user_id == user_id:
            return idx
    return 0


# ---------------------------------------------------------------------------
# Dashboard Generation
# ---------------------------------------------------------------------------

def build_dashboard(
    clips: List[ClipEntry],
    users: Dict[str, str],
    user_id: str,
    max_recent: int = 5,
) -> DashboardSnapshot:
    """Build complete dashboard snapshot for a user."""
    week_start, week_end = get_week_bounds()
    week_label = (
        f"{week_start.strftime('%b %d')} - "
        f"{(week_end - timedelta(seconds=1)).strftime('%b %d, %Y')}"
    )

    user_stats = aggregate_user_stats(clips, user_id)
    user_stats.display_name = users.get(user_id, user_id)

    leaderboard = compute_leaderboard(clips)
    rank = get_user_rank(leaderboard, user_id)

    # Recent clips for this user
    user_clips = sorted(
        [c for c in clips if c.user_id == user_id],
        key=lambda c: c.created_at,
        reverse=True,
    )[:max_recent]

    recent_clips = [c.to_dict() for c in user_clips]

    # Top 5 leaderboard entries
    top5 = []
    for idx, stats in enumerate(leaderboard[:5], start=1):
        top5.append({
            "rank": idx,
            "user_id": stats.user_id,
            "display_name": users.get(stats.user_id, stats.user_id),
            "total_bonus": stats.total_bonus,
            "total_clips": stats.total_clips,
        })

    return DashboardSnapshot(
        user_id=user_id,
        display_name=user_stats.display_name,
        week_label=week_label,
        clips_this_week=user_stats.clips_this_week,
        total_bonus_earned=user_stats.total_bonus,
        leaderboard_rank=rank,
        total_community_members=len(leaderboard),
        recent_clips=recent_clips,
        leaderboard_top5=top5,
    )


# ---------------------------------------------------------------------------
# Output Rendering
# ---------------------------------------------------------------------------

def render_html(snapshot: DashboardSnapshot) -> str:
    """Render dashboard as HTML for iframe embedding."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>EPal Community Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 16px; background: #1a1a2e; color: #eee; }}
    .dashboard {{ max-width: 400px; margin: 0 auto; }}
    .header {{ text-align: center; margin-bottom: 20px; }}
    .header h1 {{ font-size: 1.5rem; margin: 0; color: #00d9ff; }}
    .header .week {{ font-size: 0.85rem; color: #888; }}
    .stats-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }}
    .stat-card {{ background: #16213e; border-radius: 8px; padding: 12px; text-align: center; }}
    .stat-value {{ font-size: 1.5rem; font-weight: bold; color: #00d9ff; }}
    .stat-label {{ font-size: 0.75rem; color: #888; margin-top: 4px; }}
    .section {{ margin-bottom: 16px; }}
    .section h2 {{ font-size: 0.9rem; color: #888; margin: 0 0 8px 0; text-transform: uppercase; }}
    .clip-list {{ background: #16213e; border-radius: 8px; overflow: hidden; }}
    .clip-item {{ padding: 10px 12px; border-bottom: 1px solid #0f3460; }}
    .clip-item:last-child {{ border-bottom: none; }}
    .clip-title {{ font-size: 0.9rem; margin-bottom: 4px; }}
    .clip-meta {{ font-size: 0.75rem; color: #666; }}
    .leaderboard {{ background: #16213e; border-radius: 8px; overflow: hidden; }}
    .lb-item {{ display: flex; align-items: center; padding: 8px 12px; border-bottom: 1px solid #0f3460; }}
    .lb-item:last-child {{ border-bottom: none; }}
    .lb-rank {{ width: 24px; font-weight: bold; color: #ffd700; }}
    .lb-name {{ flex: 1; }}
    .lb-bonus {{ color: #00d9ff; }}
    .lb-item.highlight {{ background: #0f3460; }}
    .footer {{ text-align: center; font-size: 0.7rem; color: #555; margin-top: 16px; }}
  </style>
</head>
<body>
  <div class="dashboard">
    <div class="header">
      <h1>Community Dashboard</h1>
      <div class="week">{snapshot.week_label}</div>
    </div>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-value">{snapshot.clips_this_week}</div>
        <div class="stat-label">Clips This Week</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">${snapshot.total_bonus_earned:.2f}</div>
        <div class="stat-label">Total Bonus</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">#{snapshot.leaderboard_rank}</div>
        <div class="stat-label">Your Rank</div>
      </div>
    </div>
    <div class="section">
      <h2>Recent Clips</h2>
      <div class="clip-list">
        {''.join(f'<div class="clip-item"><div class="clip-title">{c.get("title", "Untitled")}</div><div class="clip-meta">{c.get("views", 0)} views · {c.get("likes", 0)} likes · ${c.get("bonus_amount", 0):.2f}</div></div>' for c in snapshot.recent_clips) if snapshot.recent_clips else '<div class="clip-item"><div class="clip-title">No clips yet</div></div>'}
      </div>
    </div>
    <div class="section">
      <h2>Leaderboard</h2>
      <div class="leaderboard">
        {''.join(f'<div class="lb-item {"highlight" if e["user_id"] == snapshot.user_id else ""}"><span class="lb-rank">{e["rank"]}</span><span class="lb-name">{e["display_name"]}</span><span class="lb-bonus">${e["total_bonus"]:.2f}</span></div>' for e in snapshot.leaderboard_top5)}
      </div>
    </div>
    <div class="footer">Generated {snapshot.generated_at} · {snapshot.total_community_members} members</div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        prog="epal_community_dashboard",
        description="EPal Community Dashboard Widget",
    )
    parser.add_argument(
        "--user-id", required=True,
        help="User ID to generate dashboard for",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path("."),
        help="Directory containing clips.json/clips.csv and users.json",
    )
    parser.add_argument(
        "--output", choices=["html", "json"], default="html",
        help="Output format (default: html)",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="Write output to stdout instead of file",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Output directory (default: temp dir)",
    )

    args = parser.parse_args(argv)

    # Load data
    clips = load_clips(args.data_dir)
    users = load_users(args.data_dir)

    # Build dashboard
    snapshot = build_dashboard(clips, users, args.user_id)

    # Render output
    if args.output == "json":
        content = snapshot.to_json()
        suffix = ".json"
    else:
        content = render_html(snapshot)
        suffix = ".html"

    if args.stdout:
        print(content)
        return 0

    # Write to file
    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="epal_dashboard_"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"dashboard_{args.user_id}{suffix}"

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    print(f"Dashboard written to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
