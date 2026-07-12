#!/usr/bin/env python3
"""
Red Team Attack Scenarios v2.

Implements 5 adversarial scenarios for testing robustness:
- quat_drift: Gradual quaternion drift in orientation data
- timestamp_regression: Non-monotonic timestamp ordering
- partial_stuck_key: Simulated stuck keyboard key behavior
- exr_wrong_channel: Swapped/misnamed EXR image channels
- tarball_extras: Hidden extra files in tar archives
"""

import argparse
import random
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import List, Optional

_numpy = None
_PIL = None


def _np():
    """Lazy import numpy."""
    global _numpy
    if _numpy is None:
        import numpy as _numpy
    return _numpy


def _img():
    """Lazy import PIL.Image."""
    global _PIL
    if _PIL is None:
        from PIL import Image as _PIL
    return _PIL


def attack_quat_drift(inp: str, out: str, rate: float = 0.001, steps: int = 100) -> int:
    """Apply gradual quaternion drift to orientation data."""
    np = _np()
    try:
        quats = np.load(inp) if inp.endswith('.npy') else np.loadtxt(inp, delimiter=',')
        mod = quats.copy().astype(float)
        for i in range(len(mod)):
            q = mod[i].copy()
            q_norm = q / np.linalg.norm(q)
            angle = rate * (i % steps)
            rot = np.array([0, np.sin(angle / 2), 0, np.cos(angle / 2)])
            q_new = np.array([
                rot[3] * q_norm[0] + rot[0] * q_norm[3] + rot[1] * q_norm[2] - rot[2] * q_norm[1],
                rot[3] * q_norm[1] - rot[0] * q_norm[2] + rot[1] * q_norm[3] + rot[2] * q_norm[0],
                rot[3] * q_norm[2] + rot[0] * q_norm[1] - rot[1] * q_norm[0] + rot[2] * q_norm[3],
                rot[3] * q_norm[3] - rot[0] * q_norm[0] - rot[1] * q_norm[1] - rot[2] * q_norm[2]
            ])
            mod[i] = q_new / np.linalg.norm(q_new)
        np.save(out, mod) if out.endswith('.npy') else np.savetxt(out, mod, delimiter=',')
        return 0
    except Exception as e:
        print(f"quat_drift error: {e}", file=sys.stderr)
        return 1


def attack_timestamp_regression(inp: str, out: str, factor: float = 0.5) -> int:
    """Introduce non-monotonic timestamp ordering."""
    try:
        lines = Path(inp).read_text().splitlines()
        if not lines:
            return 1
        header = lines[0].split(',')
        ts_idx = next(
            (i for i, c in enumerate(header) if 'ts' in c.lower() or 'time' in c.lower()),
            0,
        )
        out_lines, prev_ts = [lines[0]], None
        for line in lines[1:]:
            if not line.strip():
                out_lines.append(line)
                continue
            parts = line.split(',')
            try:
                ts = float(parts[ts_idx])
            except (ValueError, IndexError):
                out_lines.append(line)
                continue
            if prev_ts is not None and ts > prev_ts and hash((ts, prev_ts)) % 10 < 3:
                parts[ts_idx] = str(prev_ts - abs(ts - prev_ts) * factor)
                out_lines.append(','.join(parts))
                prev_ts = float(parts[ts_idx])
            else:
                prev_ts = ts
                out_lines.append(line)
        Path(out).write_text('\n'.join(out_lines))
        return 0
    except Exception as e:
        print(f"timestamp_regression error: {e}", file=sys.stderr)
        return 1


def attack_partial_stuck_key(inp: str, out: str, key: str = 'a', prob: float = 0.1) -> int:
    """Simulate partial stuck key behavior in text input."""
    try:
        content = Path(inp).read_text(encoding='utf-8', errors='replace')
        result = []
        for char in content:
            result.append(char)
            if char.lower() == key.lower() and random.random() < prob:
                result.append(char)
        Path(out).write_text(''.join(result), encoding='utf-8')
        return 0
    except Exception as e:
        print(f"partial_stuck_key error: {e}", file=sys.stderr)
        return 1


def attack_exr_wrong_channel(inp: str, out: str, swap: str = "R,B") -> int:
    """Swap or misname EXR image channels."""
    try:
        Image = _img()
        import numpy as np
        img = Image.open(inp)
        arr = np.array(img)
        if len(arr.shape) < 3 or arr.shape[2] < 3:
            print("exr_wrong_channel: image lacks sufficient channels", file=sys.stderr)
            return 1
        ch_map = {'R': 0, 'G': 1, 'B': 2, 'A': 3}
        parts = swap.upper().split(',')
        if len(parts) != 2:
            print(f"exr_wrong_channel: invalid swap spec '{swap}'", file=sys.stderr)
            return 1
        c1, c2 = ch_map.get(parts[0].strip()), ch_map.get(parts[1].strip())
        if c1 is None or c2 is None or c1 >= arr.shape[2] or c2 >= arr.shape[2]:
            print("exr_wrong_channel: channel index out of bounds", file=sys.stderr)
            return 1
        temp = arr[:, :, c1].copy()
        arr[:, :, c1] = arr[:, :, c2]
        arr[:, :, c2] = temp
        Image.fromarray(arr).save(out)
        return 0
    except Exception as e:
        print(f"exr_wrong_channel error: {e}", file=sys.stderr)
        return 1


def attack_tarball_extras(inp: str, out: str, extra_count: int = 3) -> int:
    """Add hidden extra files to tar archives."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            extra_names = ['.hidden', '../escape.txt', 'subdir/../../../etc/passwd']
            extra_files = []
            for i, name in enumerate(extra_names[:extra_count]):
                safe_name = f"extra_{i}_{name.replace('/', '_').replace('.', '')}"
                fpath = tmppath / safe_name
                fpath.write_text(f"REDTEAM_EXTRA_FILE_{i}\n")
                extra_files.append((name, fpath))
            with tarfile.open(out, 'w') as out_tar:
                if Path(inp).exists():
                    with tarfile.open(inp, 'r') as in_tar:
                        for member in in_tar.getmembers():
                            out_tar.addfile(member, in_tar.extractfile(member))
                for arcname, fpath in extra_files:
                    info = tarfile.TarInfo(name=arcname)
                    info.size = fpath.stat().st_size
                    with open(fpath, 'rb') as f:
                        out_tar.addfile(info, f)
            return 0
    except Exception as e:
        print(f"tarball_extras error: {e}", file=sys.stderr)
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(description='Red Team Attack Scenarios v2')
    subparsers = parser.add_subparsers(dest='attack', required=True)
    
    p_quat = subparsers.add_parser('quat_drift', help='Apply quaternion drift')
    p_quat.add_argument('input', help='Input file')
    p_quat.add_argument('output', help='Output file')
    p_quat.add_argument('--rate', type=float, default=0.001)
    p_quat.add_argument('--steps', type=int, default=100)
    
    p_ts = subparsers.add_parser('timestamp_regression', help='Regress timestamps')
    p_ts.add_argument('input', help='Input CSV')
    p_ts.add_argument('output', help='Output CSV')
    p_ts.add_argument('--factor', type=float, default=0.5)
    
    p_key = subparsers.add_parser('partial_stuck_key', help='Simulate stuck key')
    p_key.add_argument('input', help='Input text')
    p_key.add_argument('output', help='Output text')
    p_key.add_argument('--key', default='a')
    p_key.add_argument('--prob', type=float, default=0.1)
    
    p_exr = subparsers.add_parser('exr_wrong_channel', help='Swap EXR channels')
    p_exr.add_argument('input', help='Input image')
    p_exr.add_argument('output', help='Output image')
    p_exr.add_argument('--swap', default='R,B')
    
    p_tar = subparsers.add_parser('tarball_extras', help='Add extra files to tarball')
    p_tar.add_argument('input', help='Input tar')
    p_tar.add_argument('output', help='Output tar')
    p_tar.add_argument('--count', type=int, default=3)
    
    args = parser.parse_args(argv)
    
    if args.attack == 'quat_drift':
        return attack_quat_drift(args.input, args.output, args.rate, args.steps)
    elif args.attack == 'timestamp_regression':
        return attack_timestamp_regression(args.input, args.output, args.factor)
    elif args.attack == 'partial_stuck_key':
        return attack_partial_stuck_key(args.input, args.output, args.key, args.prob)
    elif args.attack == 'exr_wrong_channel':
        return attack_exr_wrong_channel(args.input, args.output, args.swap)
    elif args.attack == 'tarball_extras':
        return attack_tarball_extras(args.input, args.output, args.count)
    return 1


if __name__ == '__main__':
    sys.exit(main())
