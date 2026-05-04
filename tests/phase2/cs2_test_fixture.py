"""CS2 demo-shape test fixture helpers."""
import math
import random


def make_synthetic_ticks_df(n: int = 100) -> dict:
    """Return a dict shaped like demoparser2.parse_ticks output."""
    rng = random.Random(42)
    return {
        "X": [rng.uniform(-1000, 1000) for _ in range(n)],
        "Y": [rng.uniform(-1000, 1000) for _ in range(n)],
        "Z": [rng.uniform(-1000, 1000) for _ in range(n)],
        "m_angEyeAngles": [
            [rng.uniform(-89.0, 89.0), rng.uniform(-180.0, 180.0)] for _ in range(n)
        ],
        "m_vecVelocity": [
            [rng.uniform(-500, 500) for _ in range(3)] for _ in range(n)
        ],
    }


def _unit_quat(rng: random.Random) -> list[float]:
    u1, u2, u3 = rng.random(), rng.random(), rng.random()
    s1, s2 = math.sqrt(1 - u1), math.sqrt(u1)
    return [s1*math.sin(6.2832*u2), s1*math.cos(6.2832*u2),
            s2*math.sin(6.2832*u3), s2*math.cos(6.2832*u3)]


def make_synthetic_buyer_frames(n: int = 100) -> list[dict]:
    """Generate n well-formed buyer-spec engine frame dicts."""
    rng = random.Random(42)
    return [{
        "position": [rng.uniform(-1000, 1000) for _ in range(3)],
        "rotation": _unit_quat(rng),
        "oula": [rng.uniform(-180, 180) for _ in range(3)],
        "velocity": [rng.uniform(-500, 500) for _ in range(3)],
        "timestamp": float(i) / 64.0, "tick": i,
    } for i in range(n)]


def assert_buyer_frames_well_formed(frames: list[dict]) -> None:
    """Assert each frame has the 6 buyer-spec fields with correct types/lengths,
    quaternion is unit norm, position is Vector3, oula in [-180, 180]."""
    required = {"position", "rotation", "oula", "velocity", "timestamp", "tick"}
    for i, f in enumerate(frames):
        assert isinstance(f, dict), f"Frame {i}: not a dict"
        assert set(f.keys()) == required, f"Frame {i}: keys mismatch"
        for field, length in [("position", 3), ("rotation", 4),
                              ("oula", 3), ("velocity", 3)]:
            v = f[field]
            assert isinstance(v, (list, tuple)), f"Frame {i}: {field} not list"
            assert len(v) == length, f"Frame {i}: {field} len {len(v)} != {length}"
            assert all(isinstance(x, (int, float)) for x in v), \
                f"Frame {i}: {field} non-numeric"
        norm = math.sqrt(sum(x*x for x in f["rotation"]))
        assert abs(norm - 1.0) < 1e-6, f"Frame {i}: quat norm {norm}"
        assert all(-180.0 <= x <= 180.0 for x in f["oula"]), \
            f"Frame {i}: oula out of range"
        assert isinstance(f["timestamp"], (int, float)), f"Frame {i}: ts not numeric"
        assert isinstance(f["tick"], int), f"Frame {i}: tick not int"
