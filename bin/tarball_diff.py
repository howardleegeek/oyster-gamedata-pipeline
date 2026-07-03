#!/usr/bin/env python3
"""Compare two buyer-spec tarballs and print a markdown diff table."""

import argparse
import json
import logging
import os
import shutil
import tarfile
import tempfile

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def extract_tarball(tar_path):
    tmp_dir = tempfile.mkdtemp(prefix="tarball_diff_")
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(tmp_dir)
    return tmp_dir


def count_action_camera_records(directory):
    count = 0
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith(".json"):
                try:
                    with open(os.path.join(root, f)) as fp:
                        data = json.load(fp)
                        items = data if isinstance(data, list) else [data]
                        count += sum(1 for i in items if isinstance(i, dict) and i.get("source") == "action_camera")
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning("Failed to parse JSON in %s: %s", f, e)
    return count


def get_video_duration(directory):
    total = 0.0
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith(".json"):
                try:
                    with open(os.path.join(root, f)) as fp:
                        data = json.load(fp)
                        items = data if isinstance(data, list) else [data]
                        total += sum(i.get("duration", 0) for i in items if isinstance(i, dict))
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning("Failed to parse JSON in %s: %s", f, e)
    return total


def count_depth_files(directory):
    return sum(1 for root, _, files in os.walk(directory) for f in files
               if ".depth" in f.lower() or f.endswith(".depth") or "_depth" in f.lower())


def format_duration(seconds):
    return f"{seconds:.2f}s" if seconds < 60 else f"{int(seconds // 60)}m {seconds % 60:.2f}s"


def main():
    parser = argparse.ArgumentParser(description="Compare two buyer-spec tarballs and print a markdown diff table.")
    parser.add_argument("--left", required=True, help="Path to the left (A) tarball")
    parser.add_argument("--right", required=True, help="Path to the right (B) tarball")
    args = parser.parse_args()

    for p in [args.left, args.right]:
        if not os.path.exists(p):
            print(f"Error: Tarball not found: {p}")
            return 1

    left_dir, right_dir = extract_tarball(args.left), extract_tarball(args.right)
    try:
        lr, rr = count_action_camera_records(left_dir), count_action_camera_records(right_dir)
        ld, rd = get_video_duration(left_dir), get_video_duration(right_dir)
        lf, rf = count_depth_files(left_dir), count_depth_files(right_dir)

        print("\n## Tarball Comparison\n")
        print(f"**Left:** `{os.path.basename(args.left)}`\n**Right:** `{os.path.basename(args.right)}`\n")
        print("| Metric | Left | Right | Diff |\n|--------|------|-------|------|")
        print(f"| Action Camera Records | {lr} | {rr} | {rr - lr:+d} |")
        print(f"| Video Duration | {format_duration(ld)} | {format_duration(rd)} | {rd - ld:+.2f}s |")
        print(f"| Depth Files | {lf} | {rf} | {rf - lf:+d} |\n")
    finally:
        shutil.rmtree(left_dir, ignore_errors=True)
        shutil.rmtree(right_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    exit(main())
