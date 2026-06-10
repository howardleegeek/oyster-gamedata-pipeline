from __future__ import annotations

import json

import pytest

import oyster_agent_runner.environments.beamng_drive as beamng_drive
from oyster_agent_runner.environments.beamng_drive import (
    BEAMNGPY_MISSING_ERROR,
    BeamNGDriveEnvironment,
    BeamNGDriveExtractor,
    SensorData,
)

REQUIRED_OBSERVATION_KEYS = {
    "timestamp",
    "ego_pose",
    "camera",
    "vehicle_sensors",
    "source",
}


def assert_json_serializable(value: object) -> None:
    json.dumps(value)


def test_mock_reset_returns_unified_observation() -> None:
    env = BeamNGDriveEnvironment(mode="mock", frequency_hz=20.0)

    observation = env.reset(seed=123)

    assert set(observation) == REQUIRED_OBSERVATION_KEYS
    assert observation["source"] == "mock"
    assert observation["timestamp"] == 0.0
    assert observation["ego_pose"] == {
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "roll": 0.0,
        "pitch": 0.0,
        "yaw": 0.0,
    }
    assert observation["camera"] == {
        "rgb_shape": [480, 640, 3],
        "depth_shape": [480, 640],
        "rgb_available": True,
        "depth_available": True,
    }
    assert observation["vehicle_sensors"]["seed"] == 123
    assert observation["vehicle_sensors"]["step"] == 0
    assert_json_serializable(observation)


def test_mock_step_advances_vehicle_and_stays_json_serializable() -> None:
    env = BeamNGDriveEnvironment(mode="mock", frequency_hz=10.0, done_after_steps=3)
    env.reset(seed=7)

    observation, reward, done, info = env.step({"throttle": 0.5, "steering": 0.25})

    assert set(observation) == REQUIRED_OBSERVATION_KEYS
    assert observation["source"] == "mock"
    assert observation["timestamp"] == 0.1
    assert observation["ego_pose"]["x"] > 0.0
    assert observation["ego_pose"]["yaw"] > 0.0
    assert observation["vehicle_sensors"]["speed_mps"] > 0.0
    assert observation["vehicle_sensors"]["throttle"] == 0.5
    assert observation["vehicle_sensors"]["steering"] == 0.25
    assert reward == observation["vehicle_sensors"]["speed_mps"]
    assert done is False
    assert info == {
        "mode": "mock",
        "step_count": 1,
        "action": {"throttle": 0.5, "steering": 0.25},
    }
    assert_json_serializable({"observation": observation, "info": info})


def test_mock_done_and_shutdown_are_deterministic() -> None:
    env = BeamNGDriveEnvironment(mode="mock", done_after_steps=1)
    env.reset()

    _, _, done, info = env.step({"brake": 1.0})
    assert done is True
    assert info["step_count"] == 1

    env.shutdown()
    with pytest.raises(RuntimeError, match="shut down"):
        env.step({})


def test_real_mode_missing_sdk_raises_clear_runtime_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(beamng_drive, "HAS_BEAMNG", False)

    env = BeamNGDriveEnvironment(mode="beamngpy", beamng_home=tmp_path)

    with pytest.raises(RuntimeError, match="BeamNGpy SDK is required"):
        env.reset()


def test_extractor_missing_sdk_raises_clear_runtime_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(beamng_drive, "HAS_BEAMNG", False)

    with pytest.raises(RuntimeError, match="mode='mock'"):
        BeamNGDriveExtractor(tmp_path)

    assert "python -m pip install beamngpy" in BEAMNGPY_MISSING_ERROR


def test_real_mode_requires_beamng_home_before_launch(monkeypatch) -> None:
    monkeypatch.setattr(beamng_drive, "HAS_BEAMNG", True)

    env = BeamNGDriveEnvironment(mode="beamngpy")

    with pytest.raises(RuntimeError, match="beamng_home is required"):
        env.reset()


def test_sensor_data_to_dict_uses_unified_json_schema() -> None:
    sample = SensorData(
        timestamp=12.5,
        ego_pose={"x": 1.0, "y": 2.0, "z": 3.0, "roll": 0.0, "pitch": 0.1, "yaw": 0.2},
        camera_depth=None,
        camera_rgb=None,
        vehicle_sensors={"speed_mps": 4.2, "gear": 2},
        source="beamngpy",
    )

    data = sample.to_dict()

    assert set(data) == REQUIRED_OBSERVATION_KEYS
    assert data["source"] == "beamngpy"
    assert data["camera"] == {
        "rgb_shape": None,
        "depth_shape": None,
        "rgb_available": False,
        "depth_available": False,
    }
    assert data["vehicle_sensors"] == {"speed_mps": 4.2, "gear": 2}
    assert_json_serializable(data)
