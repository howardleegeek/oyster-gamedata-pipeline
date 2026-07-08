"""
buyer_spec_v2_action_chunks.py
==============================
Cluster A: action_chunk_next_4 + action_chunk_next_10 packing for
diffusion-decoded policies (Octo / OpenVLA / Pi-0).

Packs, validates, and serialises action chunks from VLA models into
buyer-spec v2 format.  Supports 4-step and 10-step horizons with
optional temporal interpolation.

Usage:
    python -m src.oyster_agent_runner.buyer_spec_v2_action_chunks \
        pack --input actions.npy --output packed.json --policy octo --horizon 4
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

import numpy as np

VALID_POLICIES: tuple[str, ...] = ("octo", "openvla", "pi0")
VALID_HORIZONS: tuple[int, ...] = (4, 10)
SPEC_VERSION: str = "v2"


class ActionChunk:
    """Immutable wrapper around a single action chunk tensor."""

    __slots__ = ("_data", "_horizon", "_policy", "_step_id")

    def __init__(
        self,
        data: np.ndarray,
        horizon: int,
        policy: str,
        step_id: int = 0,
    ) -> None:
        if horizon not in VALID_HORIZONS:
            raise ValueError(f"horizon must be in {VALID_HORIZONS}")
        if policy not in VALID_POLICIES:
            raise ValueError(f"policy must be in {VALID_POLICIES}")
        if data.ndim != 2 or data.shape[0] != horizon:
            raise ValueError(f"data must be 2-D with shape[0]=={horizon}")
        self._data = data.astype(np.float32)
        self._horizon = horizon
        self._policy = policy
        self._step_id = step_id

    @property
    def data(self) -> np.ndarray:
        """The action chunk data as a 2D float32 numpy array.

        Returns:
            A 2D numpy array of shape (horizon, action_dim) containing
            the action values for each step in the chunk.
        """
        return self._data

    @property
    def horizon(self) -> int:
        return self._horizon

    @property
    def policy(self) -> str:
        return self._policy

    @property
    def step_id(self) -> int:
        return self._step_id

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "spec_version": SPEC_VERSION,
            "policy": self._policy,
            "horizon": self._horizon,
            "step_id": self._step_id,
            "shape": list(self._data.shape),
            "dtype": str(self._data.dtype),
            "data": self._data.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionChunk:
        """Deserialize an ActionChunk from a JSON-compatible dict.

        Args:
            d: Dictionary with keys 'data', 'horizon', 'policy', and optionally 'step_id'.

        Returns:
            A new ActionChunk instance.

        Raises:
            KeyError: If required keys are missing.
        """
        return cls(
            data=np.array(d["data"], dtype=np.float32),
            horizon=d["horizon"],
            policy=d["policy"],
            step_id=d.get("step_id", 0),
        )

    def __repr__(self) -> str:
        return (
            f"ActionChunk(policy={self._policy!r}, horizon={self._horizon}, "
            f"step_id={self._step_id}, shape={self._data.shape})"
        )


def pack_chunks(
    chunks: Sequence[ActionChunk],
    *,
    pad_to_max: bool = True,
) -> dict[str, Any]:
    """Pack a sequence of ActionChunks into a buyer-spec v2 bundle."""
    if not chunks:
        raise ValueError("chunks must be non-empty")
    max_h = max(c.horizon for c in chunks)
    dim = chunks[0].data.shape[1]
    for c in chunks:
        if c.data.shape[1] != dim:
            raise ValueError(f"Inconsistent action dim: expected {dim}")
    packed: list[np.ndarray] = []
    for c in chunks:
        arr = c.data
        if pad_to_max and arr.shape[0] < max_h:
            arr = np.concatenate(
                [arr, np.zeros((max_h - arr.shape[0], dim), dtype=np.float32)],
                axis=0,
            )
        packed.append(arr)
    stacked = np.stack(packed, axis=0)
    return {
        "spec_version": SPEC_VERSION,
        "n_chunks": len(chunks),
        "horizon": max_h,
        "action_dim": dim,
        "policies": list({c.policy for c in chunks}),
        "step_ids": [c.step_id for c in chunks],
        "packed_shape": list(stacked.shape),
        "packed_data": stacked.tolist(),
    }


def unpack_chunks(bundle: dict[str, Any]) -> list[ActionChunk]:
    """Reverse of :func:`pack_chunks`."""
    data = np.array(bundle["packed_data"], dtype=np.float32)
    policies = bundle.get("policies", ["octo"])
    step_ids = bundle.get("step_ids", list(range(bundle["n_chunks"])))
    return [
        ActionChunk(
            data=data[i],
            horizon=bundle["horizon"],
            policy=policies[i % len(policies)],
            step_id=step_ids[i],
        )
        for i in range(bundle["n_chunks"])
    ]


def interpolate_horizon(chunk: ActionChunk, target_horizon: int) -> ActionChunk:
    """Resample an action chunk to a different horizon via linear interpolation."""
    if chunk.horizon == target_horizon:
        return chunk
    src_x = np.linspace(0, 1, chunk.horizon)
    dst_x = np.linspace(0, 1, target_horizon)
    resampled = np.empty((target_horizon, chunk.data.shape[1]), dtype=np.float32)
    for d in range(chunk.data.shape[1]):
        resampled[:, d] = np.interp(dst_x, src_x, chunk.data[:, d])
    return ActionChunk(
        data=resampled,
        horizon=target_horizon,
        policy=chunk.policy,
        step_id=chunk.step_id,
    )


def load_npy_actions(path: str) -> np.ndarray:
    """Load action data from a NumPy .npy file.

    Args:
        path: Filesystem path to the .npy file containing action arrays.

    Returns:
        A NumPy array loaded from the file, with pickle loading disabled
        for safety.
    """
    return np.load(path, allow_pickle=False)


def save_bundle(bundle: dict[str, Any], path: str) -> None:
    """Save a buyer-spec v2 bundle to a JSON file.

    Args:
        bundle: Dictionary containing the action chunk bundle data.
        path: Filesystem path to the output JSON file.

    Returns:
        None.
    """
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2)


def load_bundle(path: str) -> dict[str, Any]:
    """Load a buyer-spec v2 bundle from a JSON file.

    Args:
        path: Filesystem path to the input JSON file.

    Returns:
        Dictionary containing the bundle data.
    """
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pack/unpack action chunks for VLA policies (buyer-spec v2).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pack", help="Pack action chunks into a bundle")
    p.add_argument("--input", "-i", required=True, help="Input .npy path")
    p.add_argument("--output", "-o", required=True, help="Output JSON path")
    p.add_argument("--policy", choices=VALID_POLICIES, default="octo")
    p.add_argument("--horizon", type=int, choices=VALID_HORIZONS, default=4)
    p.add_argument("--target-horizon", type=int, choices=VALID_HORIZONS, default=None)
    u = sub.add_parser("unpack", help="Unpack a bundle to .npy")
    u.add_argument("--input", "-i", required=True, help="Input JSON bundle")
    u.add_argument("--output", "-o", required=True, help="Output .npy path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point for CLI usage."""
    args = _build_parser().parse_args(argv)
    if args.command == "pack":
        raw = load_npy_actions(args.input)
        if raw.ndim == 2:
            raw = raw[np.newaxis, ...]
        chunks: list[ActionChunk] = []
        for idx in range(raw.shape[0]):
            chunk = ActionChunk(
                data=raw[idx],
                horizon=args.horizon,
                policy=args.policy,
                step_id=idx,
            )
            if args.target_horizon is not None:
                chunk = interpolate_horizon(chunk, args.target_horizon)
            chunks.append(chunk)
        save_bundle(pack_chunks(chunks), args.output)
        print(f"Packed {len(chunks)} chunks -> {args.output}")
    elif args.command == "unpack":
        bundle = load_bundle(args.input)
        chunks = unpack_chunks(bundle)
        np.save(args.output, np.stack([c.data for c in chunks], axis=0), allow_pickle=False)
        print(f"Unpacked {len(chunks)} chunks -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
