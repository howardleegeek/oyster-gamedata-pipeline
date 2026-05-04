"""
Buyer Spec V2: Action Token Discretization for VLA Training.

Cluster A — 256-bin per-dimension discretization of action vectors, following
the RT-1 / RT-2 "actions-as-language" paradigm for Vision-Language-Action (VLA)
model training. Continuous action components (end-effector pose deltas, gripper
commands, base velocities) are mapped into 256 discrete token IDs per dimension.

Typical action vector layout (7-D):
    [dx, dy, dz, droll, dpitch, dyaw, gripper]

Usage (CLI):
    python buyer_spec_v2_action_tokens.py tokenize --values 0.1 -0.2 0.0 0.0 0.0 0.0 0.5
    python buyer_spec_v2_action_tokens.py detokenize --tokens 192 64 128 128 128 128 255
    python buyer_spec_v2_action_tokens.py roundtrip --values 0.1 -0.2 0.0 0.0 0.0 0.0 0.5
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Tuple, Union

_numpy = None


def _np():
    """Return numpy module, importing lazily on first call."""
    global _numpy
    if _numpy is None:
        import numpy as _numpy  # noqa: F811
    return _numpy


NUM_BINS: int = 256

DEFAULT_BOUNDS: List[Tuple[float, float]] = [
    (-0.5, 0.5), (-0.5, 0.5), (-0.5, 0.5),  # dx, dy, dz (m)
    (-0.5, 0.5), (-0.5, 0.5), (-0.5, 0.5),  # droll, dpitch, dyaw (rad)
    (0.0, 1.0),                               # gripper (0=closed, 1=open)
]


class ActionTokenDiscretizer:
    """Maps continuous action vectors <-> discrete 256-bin token IDs.

    Each dimension is independently discretized into ``num_bins`` uniform bins.
    Token IDs are integers in ``[0, num_bins - 1]``.
    """

    def __init__(
        self,
        num_bins: int = NUM_BINS,
        bounds: Optional[List[Tuple[float, float]]] = None,
    ) -> None:
        """
        Args:
            num_bins: Discretization bins per dimension (default 256).
            bounds: List of ``(low, high)`` tuples, one per action dimension.
                If ``None``, uses ``DEFAULT_BOUNDS`` (7-D).
        """
        if num_bins < 2:
            raise ValueError(f"num_bins must be >= 2, got {num_bins}")
        self.num_bins = num_bins
        self.bounds: List[Tuple[float, float]] = (
            list(bounds) if bounds is not None else list(DEFAULT_BOUNDS)
        )
        self.dim = len(self.bounds)

    def tokenize(self, values: Union[List[float], "numpy.ndarray"]) -> List[int]:
        """Discretize continuous values into token IDs.

        Args:
            values: Continuous action vector of length ``self.dim``.
        Returns:
            List of integer token IDs in ``[0, num_bins - 1]``.
        """
        np = _np()
        arr = np.asarray(values, dtype=np.float64).ravel()
        if arr.shape[0] != self.dim:
            raise ValueError(f"Expected {self.dim} values, got {arr.shape[0]}")
        tokens: List[int] = []
        for i, v in enumerate(arr):
            low, high = self.bounds[i]
            v_clamped = max(low, min(high, float(v)))
            token = int((v_clamped - low) / (high - low) * (self.num_bins - 1) + 0.5)
            tokens.append(max(0, min(self.num_bins - 1, token)))
        return tokens

    def detokenize(self, tokens: List[int]) -> List[float]:
        """Reconstruct continuous values from token IDs (bin centers).

        Args:
            tokens: Integer token IDs in ``[0, num_bins - 1]``.
        Returns:
            List of reconstructed continuous values.
        """
        if len(tokens) != self.dim:
            raise ValueError(f"Expected {self.dim} tokens, got {len(tokens)}")
        values: List[float] = []
        for i, t in enumerate(tokens):
            low, high = self.bounds[i]
            if not (0 <= t < self.num_bins):
                raise ValueError(f"Token {t} out of range [0, {self.num_bins - 1}]")
            values.append(low + (t + 0.5) / self.num_bins * (high - low))
        return values

    def roundtrip(self, values: Union[List[float], "numpy.ndarray"]) -> List[float]:
        """Tokenize then detokenize; returns reconstructed values."""
        return self.detokenize(self.tokenize(values))

    def quantization_error(
        self, values: Union[List[float], "numpy.ndarray"]
    ) -> List[float]:
        """Per-dimension quantization error (original - reconstructed)."""
        np = _np()
        orig = np.asarray(values, dtype=np.float64).ravel()
        recon = np.asarray(self.roundtrip(values), dtype=np.float64)
        return (orig - recon).tolist()

    def to_dict(self) -> dict:
        """Serialize configuration to a JSON-compatible dict."""
        return {"num_bins": self.num_bins, "dim": self.dim, "bounds": self.bounds}

    @classmethod
    def from_dict(cls, cfg: dict) -> "ActionTokenDiscretizer":
        """Deserialize from a configuration dict."""
        return cls(
            num_bins=cfg.get("num_bins", NUM_BINS),
            bounds=[tuple(b) for b in cfg.get("bounds", DEFAULT_BOUNDS)],
        )

    def __repr__(self) -> str:
        return f"ActionTokenDiscretizer(num_bins={self.num_bins}, dim={self.dim})"


def tokenize_batch(
    discretizer: ActionTokenDiscretizer,
    actions: Union[List[List[float]], "numpy.ndarray"],
) -> List[List[int]]:
    """Tokenize a batch of action vectors."""
    np = _np()
    arr = np.asarray(actions, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2-D array, got {arr.ndim}-D")
    return [discretizer.tokenize(row) for row in arr]


def detokenize_batch(
    discretizer: ActionTokenDiscretizer,
    token_seqs: List[List[int]],
) -> List[List[float]]:
    """Detokenize a batch of token sequences."""
    return [discretizer.detokenize(seq) for seq in token_seqs]


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="Action token discretizer for VLA training (RT-1/RT-2 style)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_tok = sub.add_parser("tokenize", help="Continuous values -> token IDs")
    p_tok.add_argument("--values", type=float, nargs="+", required=True)
    p_tok.add_argument("--bounds", type=str, default=None,
                       help="JSON list of [low, high] pairs per dimension")

    p_detok = sub.add_parser("detokenize", help="Token IDs -> continuous values")
    p_detok.add_argument("--tokens", type=int, nargs="+", required=True)
    p_detok.add_argument("--bounds", type=str, default=None,
                         help="JSON list of [low, high] pairs per dimension")

    p_rt = sub.add_parser("roundtrip", help="Tokenize then detokenize")
    p_rt.add_argument("--values", type=float, nargs="+", required=True)
    p_rt.add_argument("--bounds", type=str, default=None,
                      help="JSON list of [low, high] pairs per dimension")

    sub.add_parser("info", help="Print default configuration")
    args = parser.parse_args(argv)

    bounds: Optional[List[Tuple[float, float]]] = None
    if getattr(args, "bounds", None):
        raw = json.loads(args.bounds)
        bounds = [tuple(pair) for pair in raw]

    discretizer = ActionTokenDiscretizer(bounds=bounds)

    if args.command == "tokenize":
        tokens = discretizer.tokenize(args.values)
        print(json.dumps({"tokens": tokens, "config": discretizer.to_dict()}))
    elif args.command == "detokenize":
        values = discretizer.detokenize(args.tokens)
        print(json.dumps({"values": values, "config": discretizer.to_dict()}))
    elif args.command == "roundtrip":
        reconstructed = discretizer.roundtrip(args.values)
        error = discretizer.quantization_error(args.values)
        print(json.dumps({
            "original": args.values, "reconstructed": reconstructed,
            "error": error, "config": discretizer.to_dict(),
        }))
    elif args.command == "info":
        print(json.dumps(discretizer.to_dict(), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
