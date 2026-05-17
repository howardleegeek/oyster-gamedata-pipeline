#!/usr/bin/env python3
"""Compare two buyer-spec tarballs and print a markdown diff table."""

import argparse, json, os, tarfile, tempfile, shutil


def extract_tarball(tar_path: str) -> str:
    """Extract a .tar.gz archive into a temporary directory.

    Creates a uniquely-named temporary directory, extracts the entire
    contents of the given tarball into it, and returns the path to that
    directory. The caller is responsible for cleaning up the temporary
    directory when finished.

    Args:
        tar_path: Filesystem path to a .tar.gz archive.

    Returns:
        The absolute path to the temporary directory containing the
        extracted archive contents.
    """
    tmp_dir = tempfile.mkdtemp(prefix="tarball_diff_")
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(tmp_dir)
    return tmp_dir


def count_action_camera_records(directory: str) -> int:
    """Count action_camera records in JSON files under a directory.

    Recursively walks the directory tree, reads all .json files, and counts
    records that have a "source" field equal to "action_camera".

    Args:
        directory: Path to the root directory to scan recursively.

    Returns:
        The number of action_camera records found across all JSON files.
    """
    count = 0
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith(".json"):
                try:
                    with open(os.path.join(root, f)) as fp:
                        data = json.load(fp)
                        items = data if isinstance(data, list) else [data]
                        count += sum(1 for i in items if isinstance(i, dict) and i.get("source") == "action_camera")
                except (json.JSONDecodeError, IOError):
                    pass
    return count


def get_video_duration(directory: str) -> float:
    """Sum the total video duration (seconds) from JSON records in a directory.

    Recursively walks the directory tree, reads all .json files, and sums
    the 'duration' field from any dict items found.

    Args:
        directory: Path to the root directory to scan recursively.

    Returns:
        The total duration in seconds across all matching records.
    """
    total = 0.0
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith(".json"):
                try:
                    with open(os.path.join(root, f)) as fp:
                        data = json.load(fp)
                        items = data if isinstance(data, list) else [data]
                        total += sum(i.get("duration", 0) for i in items if isinstance(i, dict))
                except (json.JSONDecodeError, IOError):
                    pass
    return total


def count_depth_files(directory: str) -> int:
    """Count files whose names indicate they are depth-map outputs.

    Scans the given directory tree and counts any file whose name contains
    .depth (case-insensitive), ends with .depth, or contains
    _depth.

    Args:
        directory: Path to the root directory to scan recursively.

    Returns:
        The number of files matching the depth-file naming convention.
    """
    return sum(1 for root, _, files in os.walk(directory) for f in files
               if ".depth" in f.lower() or f.endswith(".depth") or "_depth" in f.lower())


def format_duration(seconds):
    return f"{seconds:.2f}s" if seconds < 60 else f"{int(seconds // 60)}m {seconds % 60:.2f}s"


def main():
    parser = argparse.ArgumentParser(description="Compare two buyer-spec tarballs and print a markdown diff table.")
    parser.add_argument("--left", required=True, help="Path to the left (A) tarball")
    parser.add_argument("--right", required=True, help="Path to the right (B) tarball")
    args = parser.parse_args()

    left_dir = extract_tarball(args.left)
    right_dir = extract_tarball(args.right)

    try:
        left_records = count_action_camera_records(left_dir)
        right_records = count_action_camera_records(right_dir)
        left_depth = count_depth_files(left_dir)
        right_depth = count_depth_files(right_dir)
        left_duration = get_video_duration(left_dir)
        right_duration = get_video_duration(right_dir)

        print("| Metric | Left | Right | Diff |")
        print("|---|---|---|---|")
        print(f"| action_camera records | {left_records} | {right_records} | {right_records - left_records:+d} |")
        print(f"| depth files | {left_depth} | {right_depth} | {right_depth - left_depth:+d} |")
        print(f"| video duration | {format_duration(left_duration)} | {format_duration(right_duration)} | {format_duration(right_duration - left_duration)} |")
    finally:
        shutil.rmtree(left_dir, ignore_errors=True)
        shutil.rmtree(right_dir, ignore_errors=True)


if __name__ == "__main__":
    main()