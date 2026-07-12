"""buyer_spec_v2_vpt_mouse.py — VPT-compatible mouse action schema (Cluster A).

Extends the base mouse spec with scaledX/Y, dwheel, buttons bitmask, and
newButtons edge-triggered fields following the OpenAI VPT / MineWorld schema.
Provides drop-in VPT compatibility with encoding, decoding, and validation.

Usage:
    python -m src.oyster_agent_runner.buyer_spec_v2_vpt_mouse --help
    python -m src.oyster_agent_runner.buyer_spec_v2_vpt_mouse encode --x 0.5 --y -0.3 \\
        --buttons 1
    python -m src.oyster_agent_runner.buyer_spec_v2_vpt_mouse decode --payload \\
        '{"scaledX":0.5,"scaledY":-0.3,"buttons":1,"newButtons":1}'
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import asdict, dataclass
from typing import Any

# Mouse button bit positions (VPT / MineWorld convention)
BUTTON_LEFT, BUTTON_RIGHT, BUTTON_MIDDLE, BUTTON_4, BUTTON_5 = 0, 1, 2, 3, 4
_BUTTON_NAMES: dict[int, str] = {
    BUTTON_LEFT: "left",
    BUTTON_RIGHT: "right",
    BUTTON_MIDDLE: "middle",
    BUTTON_4: "button4",
    BUTTON_5: "button5",
}
_SCALED_MIN, _SCALED_MAX = -1.0, 1.0
_BINARY_FORMAT: str = "<ffhBB"
_BINARY_SIZE: int = struct.calcsize(_BINARY_FORMAT)


@dataclass(frozen=True)
class VPTMouseAction:
    """Immutable VPT-compatible mouse action.

    Attributes:
        scaledX: Horizontal displacement normalised to [-1.0, 1.0].
        scaledY: Vertical displacement normalised to [-1.0, 1.0].
        dwheel:   Mouse wheel delta (signed 16-bit).
        buttons:  Current button bitmask (which buttons are held).
        newButtons: Edge-triggered bitmask — bits set only for buttons that
            transitioned from released to pressed since the previous frame.
    """

    scaledX: float = 0.0
    scaledY: float = 0.0
    dwheel: int = 0
    buttons: int = 0
    newButtons: int = 0

    def __post_init__(self) -> None:
        if not (_SCALED_MIN <= self.scaledX <= _SCALED_MAX):
            raise ValueError(f"scaledX={self.scaledX} out of [{_SCALED_MIN}, {_SCALED_MAX}]")
        if not (_SCALED_MIN <= self.scaledY <= _SCALED_MAX):
            raise ValueError(f"scaledY={self.scaledY} out of [{_SCALED_MIN}, {_SCALED_MAX}]")
        if not (-32768 <= self.dwheel <= 32767):
            raise ValueError(f"dwheel={self.dwheel} out of int16 range")
        if not (0 <= self.buttons <= 0xFF):
            raise ValueError(f"buttons={self.buttons} out of uint8 range")
        if not (0 <= self.newButtons <= 0xFF):
            raise ValueError(f"newButtons={self.newButtons} out of uint8 range")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Return a compact JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def to_bytes(self) -> bytes:
        """Pack into the VPT binary payload format."""
        return struct.pack(
            _BINARY_FORMAT, self.scaledX, self.scaledY, self.dwheel, self.buttons, self.newButtons
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VPTMouseAction:
        """Construct from a dict (JSON-decoded)."""
        return cls(
            scaledX=float(data.get("scaledX", 0.0)),
            scaledY=float(data.get("scaledY", 0.0)),
            dwheel=int(data.get("dwheel", 0)),
            buttons=int(data.get("buttons", 0)),
            newButtons=int(data.get("newButtons", 0)),
        )

    @classmethod
    def from_json(cls, payload: str) -> VPTMouseAction:
        """Construct from a JSON string."""
        return cls.from_dict(json.loads(payload))

    @classmethod
    def from_bytes(cls, raw: bytes) -> VPTMouseAction:
        """Construct from a binary VPT payload."""
        if len(raw) < _BINARY_SIZE:
            raise ValueError(f"payload too short: {len(raw)} bytes, expected {_BINARY_SIZE}")
        sx, sy, dw, btn, nbtn = struct.unpack(_BINARY_FORMAT, raw[:_BINARY_SIZE])
        return cls(scaledX=sx, scaledY=sy, dwheel=dw, buttons=btn, newButtons=nbtn)

    def button_names(self) -> list[str]:
        """Return list of currently-pressed button names."""
        return [n for b, n in _BUTTON_NAMES.items() if self.buttons & (1 << b)]

    def new_button_names(self) -> list[str]:
        """Return list of edge-triggered (newly pressed) button names."""
        return [n for b, n in _BUTTON_NAMES.items() if self.newButtons & (1 << b)]

    def compute_new_buttons(self, previous_buttons: int) -> int:
        """Derive newButtons bitmask from current and previous states (rising edge)."""
        return self.buttons & (~previous_buttons & 0xFF)


def clamp_scaled(value: float) -> float:
    """Clamp a raw coordinate into the [-1.0, 1.0] scaled range."""
    return max(_SCALED_MIN, min(_SCALED_MAX, value))


def pixel_to_scaled(pixel_dx: int, pixel_dy: int, width: int, height: int) -> tuple[float, float]:
    """Convert pixel deltas to VPT scaled coordinates."""
    sx = clamp_scaled(2.0 * pixel_dx / max(width, 1) - 1.0) if width else 0.0
    sy = clamp_scaled(2.0 * pixel_dy / max(height, 1) - 1.0) if height else 0.0
    return sx, sy


def build_action(
    scaled_x: float = 0.0,
    scaled_y: float = 0.0,
    dwheel: int = 0,
    buttons: int = 0,
    previous_buttons: int = 0,
) -> VPTMouseAction:
    """Convenience factory that auto-computes newButtons."""
    action = VPTMouseAction(
        scaledX=clamp_scaled(scaled_x),
        scaledY=clamp_scaled(scaled_y),
        dwheel=dwheel,
        buttons=buttons & 0xFF,
    )
    object.__setattr__(action, "newButtons", action.compute_new_buttons(previous_buttons))
    return action


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buyer_spec_v2_vpt_mouse",
        description="VPT-compatible mouse action encoder/decoder (Cluster A).",
    )
    sub = parser.add_subparsers(dest="command")
    enc = sub.add_parser("encode", help="Encode mouse action to JSON or binary")
    enc.add_argument("--x", type=float, default=0.0, help="scaledX [-1,1]")
    enc.add_argument("--y", type=float, default=0.0, help="scaledY [-1,1]")
    enc.add_argument("--dwheel", type=int, default=0, help="wheel delta")
    enc.add_argument("--buttons", type=int, default=0, help="button bitmask")
    enc.add_argument("--prev-buttons", type=int, default=0, help="previous button bitmask")
    enc.add_argument("--binary", action="store_true", help="output binary (hex)")
    dec = sub.add_parser("decode", help="Decode JSON or hex payload")
    dec.add_argument("--payload", type=str, required=True, help="JSON string or hex bytes")
    dec.add_argument("--binary", action="store_true", help="input is hex-encoded binary")
    val = sub.add_parser("validate", help="Validate a JSON action")
    val.add_argument("--payload", type=str, required=True, help="JSON string")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on error."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "encode":
        action = build_action(
            scaled_x=args.x,
            scaled_y=args.y,
            dwheel=args.dwheel,
            buttons=args.buttons,
            previous_buttons=args.prev_buttons,
        )
        if args.binary:
            sys.stdout.buffer.write(action.to_bytes())
        else:
            print(action.to_json())
    elif args.command == "decode":
        try:
            action = (
                VPTMouseAction.from_bytes(bytes.fromhex(args.payload))
                if args.binary
                else VPTMouseAction.from_json(args.payload)
            )
            print(json.dumps(action.to_dict(), indent=2))
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"decode error: {exc}", file=sys.stderr)
            return 1
    elif args.command == "validate":
        try:
            action = VPTMouseAction.from_json(args.payload)
            print(f"valid — buttons={action.button_names()}, new={action.new_button_names()}")
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"invalid: {exc}", file=sys.stderr)
            return 1
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
