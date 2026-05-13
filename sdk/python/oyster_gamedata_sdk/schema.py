"""Typed schema dataclasses for buyer-spec v1.

Source of truth: ``docs/BUYER_SPEC_V1.md`` (action_camera 20 fields,
systeminfo geometry, depth/*.exr layout).

Design rules
------------
* **No third-party deps** — pure stdlib dataclasses. The buyer must be able
  to import this module on a vanilla Python 3.10+ install without pulling
  pydantic / numpy / openpyxl.
* **Accept both formats** for Vector3 / Vector4. The PRD docs use the
  ``{"x": 0, "y": 0, "z": 0}`` object form, but the released sample
  tarball (``samples/buyer-spec-v1-rc1.tar.gz``) emits arrays
  ``[x, y, z]``. The schema normalises both into the same dataclass so
  downstream code can treat them uniformly.
* **`from_dict`** classmethods do the parsing and raise
  :class:`SchemaValidationError` on shape mismatch, so the buyer gets a
  precise error message instead of a generic KeyError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .errors import SchemaValidationError

# Type aliases — keep external surface readable.
Number = Union[int, float]
Vec3Like = Union[Sequence[Number], Dict[str, Number]]
Vec4Like = Union[Sequence[Number], Dict[str, Number]]


# ---------------------------------------------------------------------------
# Vectors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Vector3:
    """3D vector. Accepts both ``[x, y, z]`` array form and ``{"x":..,"y":..}`` form."""

    x: float
    y: float
    z: float

    @classmethod
    def from_any(cls, src: Vec3Like, *, field_name: str = "vector") -> "Vector3":
        if isinstance(src, dict):
            try:
                return cls(float(src["x"]), float(src["y"]), float(src["z"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise SchemaValidationError(
                    f"{field_name}: expected dict with x/y/z numeric, got {src!r}"
                ) from exc
        if isinstance(src, (list, tuple)) and len(src) == 3:
            try:
                return cls(float(src[0]), float(src[1]), float(src[2]))
            except (TypeError, ValueError) as exc:
                raise SchemaValidationError(
                    f"{field_name}: expected 3 numeric values, got {src!r}"
                ) from exc
        raise SchemaValidationError(
            f"{field_name}: expected Vector3 (dict x/y/z OR list of 3 numbers), got {type(src).__name__}"
        )

    def as_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass(frozen=True)
class Vector4:
    """4D vector, used for quaternions ``[x, y, z, w]``."""

    x: float
    y: float
    z: float
    w: float

    @classmethod
    def from_any(cls, src: Vec4Like, *, field_name: str = "vector") -> "Vector4":
        if isinstance(src, dict):
            try:
                return cls(float(src["x"]), float(src["y"]), float(src["z"]), float(src["w"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise SchemaValidationError(
                    f"{field_name}: expected dict with x/y/z/w numeric, got {src!r}"
                ) from exc
        if isinstance(src, (list, tuple)) and len(src) == 4:
            try:
                return cls(float(src[0]), float(src[1]), float(src[2]), float(src[3]))
            except (TypeError, ValueError) as exc:
                raise SchemaValidationError(
                    f"{field_name}: expected 4 numeric values, got {src!r}"
                ) from exc
        raise SchemaValidationError(
            f"{field_name}: expected Vector4 (dict x/y/z/w OR list of 4 numbers), got {type(src).__name__}"
        )

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.x, self.y, self.z, self.w)


# ---------------------------------------------------------------------------
# systeminfo.json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MapBounds:
    min_x: float
    min_z: float
    max_x: float
    max_z: float

    @classmethod
    def from_dict(cls, src: Dict[str, Any]) -> "MapBounds":
        try:
            return cls(
                float(src["min_x"]),
                float(src["min_z"]),
                float(src["max_x"]),
                float(src["max_z"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SchemaValidationError(f"map_bounds: missing/bad fields: {src!r}") from exc


@dataclass(frozen=True)
class Systeminfo:
    """systeminfo.json — file 1 of the PRD 5-file delivery layout."""

    game_process_name: str
    x: int
    y: int
    width: int
    height: int
    record_dpi: float
    map_scale: float
    map_bounds: MapBounds
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, src: Dict[str, Any]) -> "Systeminfo":
        required = ("gameProcessName", "x", "y", "width", "height", "recordDpi", "map_scale", "map_bounds")
        missing = [k for k in required if k not in src]
        if missing:
            raise SchemaValidationError(f"systeminfo.json missing fields: {missing}")
        try:
            bounds = MapBounds.from_dict(src["map_bounds"])
            extra = {k: v for k, v in src.items() if k not in set(required)}
            return cls(
                game_process_name=str(src["gameProcessName"]),
                x=int(src["x"]),
                y=int(src["y"]),
                width=int(src["width"]),
                height=int(src["height"]),
                record_dpi=float(src["recordDpi"]),
                map_scale=float(src["map_scale"]),
                map_bounds=bounds,
                extra=extra,
            )
        except (TypeError, ValueError) as exc:
            raise SchemaValidationError(f"systeminfo.json type error: {exc}") from exc


# ---------------------------------------------------------------------------
# action_camera.json — 20 fields per frame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraIntrinsics:
    """Pinhole camera intrinsics — `fx == fy` is a buyer-spec hard requirement."""

    fx: float
    fy: float
    cx: float
    cy: float

    @property
    def is_pinhole(self) -> bool:
        """True iff fx == fy (Spec gate criterion 8)."""
        return self.fx == self.fy

    @classmethod
    def from_dict(cls, src: Dict[str, Any]) -> "CameraIntrinsics":
        try:
            return cls(
                float(src["fx"]), float(src["fy"]), float(src["cx"]), float(src["cy"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SchemaValidationError(f"camera_intrinsics: {exc}") from exc


@dataclass(frozen=True)
class ActionCameraFrame:
    """A single frame from action_camera.json — 20 buyer-spec fields.

    Field order matches BUYER_SPEC_V1.md §"action_camera.json — 20 fields per frame".
    """

    frame: int
    time: str
    fps: float
    route_type: int
    mouse_x: float
    mouse_y: float
    mouse_dx: float
    mouse_dy: float
    key_code: Tuple[int, ...]
    camera_position: Vector3
    camera_rotation_oula: Vector3
    camera_rotation_quaternion: Vector4
    camera_follow_offset: Vector3
    camera_intrinsics: CameraIntrinsics
    camera_speed: Vector3
    player_position: Vector3
    player_rotation_oula: Vector3
    player_rotation_quaternion: Vector4
    player_speed: Vector3
    metric_scale: float

    @classmethod
    def from_dict(cls, src: Dict[str, Any]) -> "ActionCameraFrame":
        # keyCode is an array of ints; some emitters may send a single int.
        key_code_raw = src.get("keyCode", [])
        if isinstance(key_code_raw, int):
            key_code_raw = [key_code_raw]
        if not isinstance(key_code_raw, (list, tuple)):
            raise SchemaValidationError(
                f"frame {src.get('frame', '?')}: keyCode must be int or list[int], "
                f"got {type(key_code_raw).__name__}"
            )

        try:
            return cls(
                frame=int(src["frame"]),
                time=str(src["time"]),
                fps=float(src["fps"]),
                route_type=int(src["route_type"]),
                mouse_x=float(src["mouse_x"]),
                mouse_y=float(src["mouse_y"]),
                mouse_dx=float(src["mouse_dx"]),
                mouse_dy=float(src["mouse_dy"]),
                key_code=tuple(int(k) for k in key_code_raw),
                camera_position=Vector3.from_any(src["camera_position"], field_name="camera_position"),
                camera_rotation_oula=Vector3.from_any(
                    src["camera_rotation_oula"], field_name="camera_rotation_oula"
                ),
                camera_rotation_quaternion=Vector4.from_any(
                    src["camera_rotation_quaternion"], field_name="camera_rotation_quaternion"
                ),
                # NOTE: the spec key has an embedded space — preserved verbatim.
                camera_follow_offset=Vector3.from_any(
                    src["camera_Follow Offset"], field_name="camera_Follow Offset"
                ),
                camera_intrinsics=CameraIntrinsics.from_dict(src["camera_intrinsics"]),
                camera_speed=Vector3.from_any(src["camera_speed"], field_name="camera_speed"),
                player_position=Vector3.from_any(src["player_position"], field_name="player_position"),
                player_rotation_oula=Vector3.from_any(
                    src["player_rotation_oula"], field_name="player_rotation_oula"
                ),
                player_rotation_quaternion=Vector4.from_any(
                    src["player_rotation_quaternion"], field_name="player_rotation_quaternion"
                ),
                player_speed=Vector3.from_any(src["player_speed"], field_name="player_speed"),
                metric_scale=float(src["metric_scale"]),
            )
        except KeyError as exc:
            raise SchemaValidationError(
                f"frame {src.get('frame', '?')}: missing required field {exc}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise SchemaValidationError(
                f"frame {src.get('frame', '?')}: type error: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# gameinfo.xlsx — flexible because operator schema varies by batch.
# ---------------------------------------------------------------------------


@dataclass
class Gameinfo:
    """Operator-curated metadata from gameinfo.xlsx.

    The buyer-spec doc lists 14 typed fields but the released sample
    tarball uses a 6-column variant. We keep the SDK liberal — accept any
    column shape, expose ``.fields`` (the first data row keyed by header)
    and ``.all_rows`` for batch processing.
    """

    sheet_name: str
    columns: List[str]
    rows: List[Dict[str, Any]]

    @property
    def fields(self) -> Dict[str, Any]:
        """First data row as a dict (most clips contain a single row)."""
        return dict(self.rows[0]) if self.rows else {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)


def parse_action_camera(
    payload: Iterable[Dict[str, Any]],
    *,
    strict: bool = True,
) -> List[ActionCameraFrame]:
    """Parse the action_camera.json payload (list of dicts) into typed frames.

    Args:
        payload: deserialised JSON — a list of per-frame dicts.
        strict: if True, raise on the first malformed frame; if False,
            skip malformed frames and continue.

    Returns:
        List of ``ActionCameraFrame`` instances.
    """
    out: List[ActionCameraFrame] = []
    for i, item in enumerate(payload):
        try:
            out.append(ActionCameraFrame.from_dict(item))
        except SchemaValidationError:
            if strict:
                raise
            continue
    return out
