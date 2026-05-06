import math
import unittest
from typing import Any, Dict, List


def r01_quat_norm(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Verify quaternion norm is 1.0"""
    quat = rec.get("camera_rotation_quaternion")
    if quat is None:
        quat = rec.get("player_rotation_quaternion")
    if quat is None:
        return {"name": "r01_quat_norm", "passed": False, "residual": 0.0, "threshold": 0.0}
    norm = math.sqrt(quat[0]**2 + quat[1]**2 + quat[2]**2 + quat[3]**2)
    residual = abs(norm - 1.0)
    return {"name": "r01_quat_norm", "passed": residual < 1e-6, "residual": residual, "threshold": 1e-6}


def r02_euler_quat_consistency(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Verify euler angles (ZYX intrinsic) match quaternion"""
    euler = rec.get("camera_rotation_oula")
    quat = rec.get("camera_rotation_quaternion")
    if euler is None or quat is None:
        return {"name": "r02_euler_quat_consistency", "passed": False, "residual": 0.0, "threshold": 0.0}

    # ZYX intrinsic euler to quaternion
    yaw = math.radians(euler[1])
    pitch = math.radians(euler[0])
    roll = math.radians(euler[2])

    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)

    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy
    qw = cr * cp * cy + sr * sp * sy

    residual = (abs(qx - quat[0]) + abs(qy - quat[1]) +
                abs(qz - quat[2]) + abs(qw - quat[3]))
    return {"name": "r02_euler_quat_consistency", "passed": residual < 1e-3, "residual": residual, "threshold": 1e-3}


def r03_kinematics(rec_n: Dict[str, Any], rec_n1: Dict[str, Any], fps: float) -> Dict[str, Any]:
    """Verify position delta matches speed * dt"""
    dt = 1.0 / fps
    pos_n = rec_n.get("camera_position")
    pos_n1 = rec_n1.get("camera_position")
    speed_n = rec_n.get("camera_speed")

    if pos_n is None or pos_n1 is None or speed_n is None:
        return {"name": "r03_kinematics", "passed": False, "residual": 0.0, "threshold": 0.0}

    dx = pos_n[0] - pos_n1[0]
    dy = pos_n[1] - pos_n1[1]
    dz = pos_n[2] - pos_n1[2]

    expected_dx = speed_n[0] * dt
    expected_dy = speed_n[1] * dt
    expected_dz = speed_n[2] * dt

    residual = math.sqrt((dx - expected_dx)**2 + (dy - expected_dy)**2 + (dz - expected_dz)**2)
    return {"name": "r03_kinematics", "passed": residual < 0.1, "residual": residual, "threshold": 0.1}


def r04_mouse_dx_diff(rec_n: Dict[str, Any], rec_n1: Dict[str, Any]) -> Dict[str, Any]:
    """Verify mouse_dx is difference of mouse_x"""
    mx_n = rec_n.get("mouse_x", [0.0])[0]
    mx_n1 = rec_n1.get("mouse_x", [0.0])[0]
    mdx_n = rec_n.get("mouse_dx", [0.0])[0]

    expected_mdx = mx_n - mx_n1
    residual = abs(mdx_n - expected_mdx)
    return {"name": "r04_mouse_dx_diff", "passed": residual < 1e-3, "residual": residual, "threshold": 1e-3}


def r05_dt(rec_n: Dict[str, Any], rec_n1: Dict[str, Any]) -> Dict[str, Any]:
    """Verify frame dt matches fps"""
    fps_n = rec_n.get("fps", 30.0)
    dt = 1.0 / fps_n

    time_n = rec_n.get("time", "")
    time_n1 = rec_n1.get("time", "")

    if not time_n or not time_n1:
        return {"name": "r05_dt", "passed": False, "residual": 0.0, "threshold": 0.0}

    from datetime import datetime
    t_n = datetime.strptime(time_n, "%Y-%m-%d %H:%M:%S.%f")
    t_n1 = datetime.strptime(time_n1, "%Y-%m-%d %H:%M:%S.%f")
    actual_dt = (t_n - t_n1).total_seconds()

    residual = abs(actual_dt - dt)
    return {"name": "r05_dt", "passed": residual < 0.05, "residual": residual, "threshold": 0.05}


def r06_angle_range(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Verify euler angles in [-180, 180]"""
    euler = rec.get("camera_rotation_oula")
    if euler is None:
        return {"name": "r06_angle_range", "passed": False, "residual": 0.0, "threshold": 0.0}

    violations = []
    for angle in euler:
        if angle < -180 or angle > 180:
            violations.append(abs(angle))

    if violations:
        residual = max(violations)
        return {"name": "r06_angle_range", "passed": False, "residual": residual, "threshold": 0.0}

    return {"name": "r06_angle_range", "passed": True, "residual": 0.0, "threshold": 0.0}


def r07_mouse_range(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Verify mouse_x, mouse_y in [0, 1]"""
    mx = rec.get("mouse_x", [0.0])[0]
    my = rec.get("mouse_y", [0.0])[0]

    violations = []
    if mx < 0 or mx > 1:
        violations.append(abs(mx - 0.5))
    if my < 0 or my > 1:
        violations.append(abs(my - 0.5))

    if violations:
        residual = max(violations)
        return {"name": "r07_mouse_range", "passed": False, "residual": residual, "threshold": 0.0}

    return {"name": "r07_mouse_range", "passed": True, "residual": 0.0, "threshold": 0.0}


def r08_fx_eq_fy(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Verify camera_intrinsics fx == fy"""
    intrinsics = rec.get("camera_intrinsics")
    if intrinsics is None:
        return {"name": "r08_fx_eq_fy", "passed": False, "residual": 0.0, "threshold": 0.0}

    fx = intrinsics.get("fx")
    fy = intrinsics.get("fy")
    if fx is None or fy is None:
        return {"name": "r08_fx_eq_fy", "passed": False, "residual": 0.0, "threshold": 0.0}

    residual = abs(fx - fy)
    return {"name": "r08_fx_eq_fy", "passed": residual < 1e-3, "residual": residual, "threshold": 1e-3}


def r09_keycode_vk(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Verify keyCode values are valid Windows VK codes"""
    keycodes = rec.get("keyCode", [])
    if not keycodes:
        return {"name": "r09_keycode_vk", "passed": True, "residual": 0.0, "threshold": 0.0}

    VK_TO_KEY = {
        112: 'F1', 113: 'F2', 114: 'F3', 115: 'F4',
        116: 'F5', 117: 'F6', 118: 'F7', 119: 'F8',
        120: 'F9', 121: 'F10', 122: 'F11', 123: 'F12',
        27: 'ESC',
        192: '`',
        48: '0', 49: '1', 50: '2', 51: '3', 52: '4',
        53: '5', 54: '6', 55: '7', 56: '8', 57: '9',
        81: 'Q', 87: 'W', 69: 'E', 82: 'R', 84: 'T',
        89: 'Y', 85: 'U', 73: 'I', 79: 'O', 80: 'P',
        65: 'A', 83: 'S', 68: 'D', 70: 'F', 71: 'G',
        72: 'H', 74: 'J', 75: 'K', 76: 'L',
        90: 'Z', 88: 'X', 67: 'C', 86: 'V', 66: 'B',
        78: 'N', 77: 'M',
        9: 'TAB',
        20: 'CAPS',
        16: 'LSHIFT', 160: 'LSHIFT', 161: 'RSHIFT',
        17: 'LCTRL', 162: 'LCTRL', 163: 'RCTRL',
        18: 'LALT', 164: 'LALT', 165: 'RALT',
        32: 'SPACE',
    }

    invalid_codes = [kc for kc in keycodes if kc not in VK_TO_KEY]

    if invalid_codes:
        residual = max(invalid_codes)
        return {"name": "r09_keycode_vk", "passed": False, "residual": float(residual), "threshold": 0.0}

    return {"name": "r09_keycode_vk", "passed": True, "residual": 0.0, "threshold": 0.0}


def r10_speed_max(rec: Dict[str, Any], vmax: float = 50.0) -> Dict[str, Any]:
    """Verify camera_speed magnitude <= vmax"""
    speed = rec.get("camera_speed")
    if speed is None:
        return {"name": "r10_speed_max", "passed": False, "residual": 0.0, "threshold": 0.0}

    magnitude = math.sqrt(speed[0]**2 + speed[1]**2 + speed[2]**2)
    residual = max(0.0, magnitude - vmax)
    return {"name": "r10_speed_max", "passed": residual < 1e-3, "residual": residual, "threshold": 1e-3}


def r12_fps_range(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Verify fps in [29, 31]"""
    fps = rec.get("fps", 0.0)
    if fps < 29 or fps > 31:
        residual = abs(fps - 30)
        return {"name": "r12_fps_range", "passed": False, "residual": residual, "threshold": 0.0}
    return {"name": "r12_fps_range", "passed": True, "residual": 0.0, "threshold": 0.0}


if __name__ == "__main__":
    unittest.main()