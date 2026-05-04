#!/usr/bin/env python3
"""
Camera Rotation Synthesizer - generates synthetic camera rotation data.
Per PDF p3 spec. NOT a player - synthesizes rotation trajectories only.
"""

import argparse, json, math, random, sys, tempfile
from pathlib import Path
from typing import List, Optional, Tuple


class CameraRotationSynthesizer:
    """Synthesizes camera rotation data for testing and simulation."""

    def __init__(self, seed: Optional[int] = None) -> None:
        if seed is not None:
            random.seed(seed)

    @staticmethod
    def _rand_vec() -> Tuple[float, float, float]:
        theta, z = random.uniform(0, 6.283185), random.uniform(-1, 1)
        r = math.sqrt(1 - z * z)
        return (r * math.cos(theta), r * math.sin(theta), z)

    @staticmethod
    def _axis_angle_to_q(axis: Tuple[float, float, float], angle: float) -> Tuple[float, float, float, float]:
        h, s = angle / 2, math.sin(h := angle / 2)
        return (math.cos(h), axis[0] * s, axis[1] * s, axis[2] * s)

    @staticmethod
    def _q_mult(q1: Tuple, q2: Tuple) -> Tuple:
        w1, x1, y1, z1 = q1; w2, x2, y2, z2 = q2
        return (w1*w2 - x1*x2 - y1*y2 - z1*z2, w1*x2 + x1*w2 + y1*z2 - z1*y2,
                w1*y2 - x1*z2 + y1*w2 + z1*x2, w1*z2 + x1*y2 - y1*x2 + z1*w2)

    @staticmethod
    def _normalize_q(q: Tuple) -> Tuple:
        w, x, y, z = q; m = math.sqrt(w*w + x*x + y*y + z*z)
        return (w/m, x/m, y/m, z/m)

    @staticmethod
    def _q_to_mat(q: Tuple) -> List[List[float]]:
        w, x, y, z = q
        return [[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)],
                [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)],
                [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]]

    @staticmethod
    def _mat_to_euler(m: List[List[float]]) -> Tuple[float, float, float]:
        sy = math.sqrt(m[0][0]**2 + m[1][0]**2)
        if sy > 1e-6:
            return (math.atan2(m[2][1], m[2][2]), math.atan2(-m[2][0], sy), math.atan2(m[1][0], m[0][0]))
        return (math.atan2(-m[1][2], m[1][1]), math.atan2(-m[2][0], sy), 0)

    def generate(self, num_frames: int = 100, smooth: float = 0.1, max_angle: float = 1.5708,
                 out_type: str = "quaternion") -> List:
        """Generate rotation trajectory."""
        q = (1.0, 0.0, 0.0, 0.0)
        traj = []
        for _ in range(num_frames):
            traj.append(q)
            angle = random.uniform(-max_angle * smooth, max_angle * smooth)
            q = self._normalize_q(self._q_mult(q, self._axis_angle_to_q(self._rand_vec(), angle)))
        if out_type == "matrix": return [self._q_to_mat(x) for x in traj]
        if out_type == "euler": return [self._mat_to_euler(self._q_to_mat(x)) for x in traj]
        return traj

    def save(self, traj: List, path: Path, fmt: str = "json") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            json.dump(traj, open(path, "w"), indent=2)
        else:
            with open(path, "w") as f:
                for i, item in enumerate(traj):
                    if isinstance(item[0], (int, float)):
                        f.write(f"{i}: {', '.join(f'{x:.6f}' for x in item)}\n")
                    else:
                        f.write(f"Frame {i}:\n")
                        for r in item: f.write(f"  {', '.join(f'{x:.6f}' for x in r)}\n")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Generate synthetic camera rotation trajectories.")
    p.add_argument("-o", "--output", type=Path, default=Path(tempfile.mkdtemp()) / "traj.json")
    p.add_argument("-f", "--frames", type=int, default=100)
    p.add_argument("-s", "--smoothness", type=float, default=0.1)
    p.add_argument("-m", "--max-angle", type=float, default=1.5708)
    p.add_argument("-t", "--type", choices=["quaternion", "matrix", "euler"], default="quaternion")
    p.add_argument("--format", choices=["json", "txt"], default="json")
    p.add_argument("--seed", type=int, default=None)
    a = p.parse_args(argv)
    syn = CameraRotationSynthesizer(a.seed)
    syn.save(syn.generate(a.frames, a.smoothness, a.max_angle, a.type), a.output, a.format)
    print(f"Generated {a.frames} frames -> {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
