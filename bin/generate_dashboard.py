#!/usr/bin/env python3
"""Generate a self-contained STATUS.html dashboard from sprint data."""
import argparse
import os
import re
import subprocess
from datetime import datetime


def read_sprint_report(path="SPRINT_REPORT.md"):
    """Parse key-value metrics from SPRINT_REPORT.md."""
    metrics = {}
    if not os.path.isfile(path):
        return metrics
    with open(path, "r") as f:
        for line in f:
            m = re.match(r"-\s+\*\*(.+?)\*\*:\s*(.+)", line)
            if m:
                metrics[m.group(1).strip()] = m.group(2).strip()
    return metrics


def get_git_log():
    """Return recent git commits as list of dicts."""
    try:
        out = subprocess.check_output(
            ["git", "log", "--pretty=format:%h|%an|%ad|%s", "--date=short", "-20"],
            stderr=subprocess.DEVNULL, text=True,
        )
        commits = []
        for line in out.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"hash": parts[0], "author": parts[1],
                                "date": parts[2], "subject": parts[3]})
        return commits
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def count_test_files(root="src/tests"):
    """Walk test directory and return file counts by extension."""
    counts = {}
    total = 0
    if os.path.isdir(root):
        for _dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                ext = os.path.splitext(fn)[1] or "(no ext)"
                counts[ext] = counts.get(ext, 0) + 1
                total += 1
    return counts, total


def build_html(metrics, commits, file_counts, total_files):
    """Return a complete HTML string with inlined CSS."""
    sprint = metrics.get("Sprint", "N/A")
    rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items()
    )
    commit_rows = "".join(
        f"<tr><td><code>{c['hash']}</code></td><td>{c['author']}</td>"
        f"<td>{c['date']}</td><td>{c['subject']}</td></tr>"
        for c in commits
    ) or "<tr><td colspan='4'>No commits found</td></tr>"
    file_rows = "".join(
        f"<tr><td>{ext}</td><td>{cnt}</td></tr>" for ext, cnt in sorted(file_counts.items())
    )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Sprint {sprint} Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#f4f6f9;color:#1a1a2e;padding:2rem}}
h1{{font-size:1.8rem;margin-bottom:.3rem}}
h2{{font-size:1.2rem;margin:1.5rem 0 .5rem;color:#444}}
.subtitle{{color:#666;margin-bottom:1.5rem;font-size:.9rem}}
.card{{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.1);
  padding:1.5rem;margin-bottom:1.5rem}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:.5rem .75rem;border-bottom:1px solid #eee}}
th{{background:#f8f9fa;font-size:.8rem;text-transform:uppercase;color:#555}}
td{{font-size:.9rem}}
code{{background:#eef;padding:2px 6px;border-radius:3px;font-size:.85rem}}
.badge{{display:inline-block;background:#4caf50;color:#fff;padding:2px 8px;
  border-radius:10px;font-size:.75rem}}
</style></head><body>
<h1>📊 Sprint {sprint} Dashboard</h1>
<p class="subtitle">Generated {now}</p>

<div class="card"><h2>📋 Sprint Metrics</h2>
<table><thead><tr><th>Metric</th><th>Value</th></tr></thead>
<tbody>{rows}</tbody></table></div>

<div class="card"><h2>🔀 PR Commits (recent)</h2>
<table><thead><tr><th>Hash</th><th>Author</th><th>Date</th><th>Subject</th></tr></thead>
<tbody>{commit_rows}</tbody></table></div>

<div class="card"><h2>📁 Test File Counts <span class="badge">{total_files} total</span></h2>
<table><thead><tr><th>Extension</th><th>Count</th></tr></thead>
<tbody>{file_rows}</tbody></table></div>
</body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate sprint status dashboard")
    parser.add_argument("--output", default="STATUS.html", help="Output HTML file path")
    args = parser.parse_args()

    metrics = read_sprint_report()
    commits = get_git_log()
    file_counts, total_files = count_test_files()

    html = build_html(metrics, commits, file_counts, total_files)
    with open(args.output, "w") as f:
        f.write(html)
    print(f"Dashboard written to {args.output} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
