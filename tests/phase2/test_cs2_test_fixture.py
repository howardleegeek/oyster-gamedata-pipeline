"""Tests for cs2_test_fixture helpers."""
import math
import pytest
from cs2_test_fixture import (
    make_synthetic_ticks_df,
    make_synthetic_buyer_frames,
    assert_buyer_frames_well_formed,
)


def test_make_synthetic_ticks_df_shape():
    """Returned dict has exactly the 5 expected keys with correct lengths."""
    n = 50
    df = make_synthetic_ticks_df(n)
    assert set(df.keys()) == {"X", "Y", "Z", "m_angEyeAngles", "m_vecVelocity"}
    assert len(df["X"]) == n
    assert len(df["Y"]) == n
    assert len(df["Z"]) == n
    assert len(df["m_angEyeAngles"]) == n
    assert len(df["m_vecVelocity"]) == n


def test_make_synthetic_ticks_df_ranges():
    """All values fall within plausible CS2 ranges."""
    df = make_synthetic_ticks_df(200)
    for key in ("X", "Y", "Z"):
        for v in df[key]:
            assert -1000 <= v <= 1000, f"{key} value {v} out of range"
    for pair in df["m_angEyeAngles"]:
        assert len(pair) == 2
        assert -90 <= pair[0] <= 90, f"pitch {pair[0]} out of range"
        assert -180 <= pair[1] <= 180, f"yaw {pair[1]} out of range"
    for tri in df["m_vecVelocity"]:
        assert len(tri) == 3
        for v in tri:
            assert -500 <= v <= 500, f"velocity {v} out of range"


def test_assert_buyer_frames_well_formed_passes_good_data():
    """Well-formed frames should not raise."""
    frames = make_synthetic_buyer_frames(30)
    assert_buyer_frames_well_formed(frames)  # no exception


def test_assert_buyer_frames_well_formed_catches_bad_quaternion():
    """Non-unit quaternion should trigger AssertionError."""
    frames = make_synthetic_buyer_frames(1)
    frames[0]["rotation"] = [2.0, 0.0, 0.0, 0.0]  # norm = 2, not unit
    with pytest.raises(AssertionError, match="quat norm"):
        assert_buyer_frames_well_formed(frames)


def test_assert_buyer_frames_well_formed_catches_bad_oula():
    """Oula value outside [-180, 180] should trigger AssertionError."""
    frames = make_synthetic_buyer_frames(1)
    frames[0]["oula"] = [0.0, 200.0, 0.0]  # 200 > 180
    with pytest.raises(AssertionError, match="oula out of range"):
        assert_buyer_frames_well_formed(frames)


def test_assert_buyer_frames_well_formed_catches_missing_field():
    """Missing required field should trigger AssertionError."""
    frames = make_synthetic_buyer_frames(1)
    del frames[0]["timestamp"]
    with pytest.raises(AssertionError, match="keys mismatch"):
        assert_buyer_frames_well_formed(frames)


def test_assert_buyer_frames_well_formed_catches_wrong_position_length():
    """Position with wrong length should trigger AssertionError."""
    frames = make_synthetic_buyer_frames(1)
    frames[0]["position"] = [0.0, 0.0]  # only 2 elements
    with pytest.raises(AssertionError, match="position len"):
        assert_buyer_frames_well_formed(frames)
