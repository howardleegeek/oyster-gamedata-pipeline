#!/usr/bin/env python3
"""
Tests for bin/circuit_breaker.py - S3 operation circuit breaker.

Validates: CircuitState enum, CircuitBreaker class (state transitions,
failure counting, persistence, recovery timeout), and main() CLI.
"""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from bin.circuit_breaker import CircuitBreaker, CircuitState, main


class TestCircuitState:
    """Test CircuitState enum values."""

    def test_state_values(self):
        """Verify all circuit states are defined correctly."""
        assert CircuitState.CLOSED.value == "CLOSED"
        assert CircuitState.OPEN.value == "OPEN"
        assert CircuitState.HALF_OPEN.value == "HALF_OPEN"
        assert len(CircuitState) == 3


class TestCircuitBreakerInit:
    """Test CircuitBreaker initialization and defaults."""

    def test_default_values(self):
        """Verify default threshold and timeout values."""
        cb = CircuitBreaker()
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 60.0
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._trip_time is None

    def test_custom_threshold_and_timeout(self):
        """Verify custom threshold and timeout are applied."""
        cb = CircuitBreaker(failure_threshold=10, recovery_timeout=120.0)
        assert cb.failure_threshold == 10
        assert cb.recovery_timeout == 120.0

    def test_loads_existing_state_file(self):
        """Verify state is loaded from existing state file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {"state": "OPEN", "failure_count": 3, "trip_time": time.time()}, f
            )
            state_file = Path(f.name)

        try:
            cb = CircuitBreaker(state_file=state_file)
            assert cb._state == CircuitState.OPEN
            assert cb._failure_count == 3
            assert cb._trip_time is not None
        finally:
            state_file.unlink()

    def test_ignores_malformed_state_file(self):
        """Verify malformed JSON in state file is handled gracefully."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("not valid json{{{")
            state_file = Path(f.name)

        cb = CircuitBreaker(state_file=state_file)
        # Should fall back to defaults
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        state_file.unlink()


class TestCircuitBreakerStateTransitions:
    """Test state transition logic."""

    def test_allows_when_closed(self):
        """Verify operations are allowed in CLOSED state."""
        cb = CircuitBreaker()
        assert cb.is_allowed() is True

    def test_blocks_when_open(self):
        """Verify operations are blocked in OPEN state."""
        cb = CircuitBreaker()
        cb._state = CircuitState.OPEN
        cb._trip_time = time.time()
        assert cb.is_allowed() is False

    def test_transitions_to_half_open_after_timeout(self):
        """Verify OPEN -> HALF_OPEN transition after recovery timeout."""
        cb = CircuitBreaker(recovery_timeout=0.1)
        cb._state = CircuitState.OPEN
        cb._trip_time = time.time() - 1.0  # Past timeout
        assert cb.is_allowed() is True
        assert cb._state == CircuitState.HALF_OPEN

    def test_allows_in_half_open(self):
        """Verify operations are allowed in HALF_OPEN state."""
        cb = CircuitBreaker()
        cb._state = CircuitState.HALF_OPEN
        assert cb.is_allowed() is True


class TestCircuitBreakerRecordSuccess:
    """Test success recording and recovery."""

    def test_resets_failure_count_on_success(self):
        """Verify failure count resets on success."""
        cb = CircuitBreaker()
        cb._failure_count = 3
        cb.record_success()
        assert cb._failure_count == 0

    def test_transitions_to_closed_from_half_open_on_success(self):
        """Verify HALF_OPEN -> CLOSED on successful operation."""
        cb = CircuitBreaker()
        cb._state = CircuitState.HALF_OPEN
        cb.record_success()
        assert cb._state == CircuitState.CLOSED

    def test_preserves_closed_state_on_success(self):
        """Verify CLOSED state stays CLOSED on success."""
        cb = CircuitBreaker()
        cb._state = CircuitState.CLOSED
        cb.record_success()
        assert cb._state == CircuitState.CLOSED


class TestCircuitBreakerRecordFailure:
    """Test failure recording and circuit tripping."""

    def test_increments_failure_count(self):
        """Verify failure count increments on each failure."""
        cb = CircuitBreaker()
        cb.record_failure()
        assert cb._failure_count == 1
        cb.record_failure()
        assert cb._failure_count == 2

    def test_trips_at_threshold(self):
        """Verify circuit trips at failure threshold."""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb._state == CircuitState.CLOSED
        cb.record_failure()
        assert cb._state == CircuitState.OPEN
        assert cb._trip_time is not None

    def test_does_not_trip_below_threshold(self):
        """Verify circuit stays closed below threshold."""
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb._state == CircuitState.CLOSED

    def test_does_not_trip_if_already_open(self):
        """Verify circuit does not re-trip if already OPEN."""
        cb = CircuitBreaker(failure_threshold=3)
        cb._state = CircuitState.OPEN
        cb._trip_time = time.time()
        # Clear the trip mock to ensure it's not called again
        with patch.object(cb, "_save_state"):
            cb.record_failure()
        # Should still be OPEN, not re-tripped
        assert cb._state == CircuitState.OPEN


class TestCircuitBreakerReset:
    """Test manual reset functionality."""

    def test_resets_to_closed_state(self):
        """Verify reset returns to CLOSED state."""
        cb = CircuitBreaker()
        cb._state = CircuitState.OPEN
        cb._failure_count = 5
        cb._trip_time = time.time()
        cb.reset()
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._trip_time is None


class TestCircuitBreakerStatus:
    """Test status reporting."""

    def test_returns_status_dict(self):
        """Verify status returns complete state dictionary."""
        cb = CircuitBreaker(failure_threshold=10)
        cb._state = CircuitState.OPEN
        cb._failure_count = 7
        cb._trip_time = 1234567890.0
        status = cb.status()
        assert status["state"] == "OPEN"
        assert status["failure_count"] == 7
        assert status["failure_threshold"] == 10
        assert status["trip_time"] == 1234567890.0


class TestCircuitBreakerPersistence:
    """Test state file persistence."""

    def test_saves_state_on_success(self):
        """Verify state is saved after success."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            state_file = Path(f.name)

        try:
            cb = CircuitBreaker(state_file=state_file)
            cb.record_success()
            data = json.loads(state_file.read_text())
            assert data["state"] == "CLOSED"
            assert data["failure_count"] == 0
        finally:
            state_file.unlink()

    def test_saves_state_on_failure(self):
        """Verify state is saved after failure."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            state_file = Path(f.name)

        try:
            cb = CircuitBreaker(state_file=state_file)
            cb.record_failure()
            data = json.loads(state_file.read_text())
            assert data["failure_count"] == 1
        finally:
            state_file.unlink()


class TestCircuitBreakerAlertCallback:
    """Test alert callback functionality."""

    def test_calls_callback_on_trip(self):
        """Verify alert callback is called when circuit trips."""
        callback = MagicMock()
        cb = CircuitBreaker(failure_threshold=1, alert_callback=callback)
        cb.record_failure("Test error")
        callback.assert_called_once()
        call_args = callback.call_args[0][0]
        assert "TRIPPED" in call_args
        assert "1" in call_args

    def test_handles_callback_exception(self):
        """Verify callback exception doesn't crash circuit breaker."""
        callback = MagicMock(side_effect=RuntimeError("alert failed"))
        cb = CircuitBreaker(failure_threshold=1, alert_callback=callback)
        # Should not raise
        cb.record_failure("Test error")
        assert cb._state == CircuitState.OPEN


class TestMainCLI:
    """Test main() CLI entry point."""

    def test_status_command(self):
        """Verify --status prints current status."""
        with patch("bin.circuit_breaker.CircuitBreaker") as MockCB:
            instance = MagicMock()
            instance.status.return_value = {
                "state": "CLOSED",
                "failure_count": 0,
                "failure_threshold": 5,
                "trip_time": None,
            }
            MockCB.return_value = instance

            with patch("sys.argv", ["circuit_breaker", "--status"]):
                result = main()
                assert result == 0
                instance.status.assert_called_once()

    def test_reset_command(self):
        """Verify --reset resets circuit breaker."""
        with patch("bin.circuit_breaker.CircuitBreaker") as MockCB:
            instance = MagicMock()
            MockCB.return_value = instance

            with patch("sys.argv", ["circuit_breaker", "--reset"]):
                result = main()
                assert result == 0
                instance.reset.assert_called_once()

    def test_test_failure_command(self):
        """Verify --test-failure simulates failure."""
        with patch("bin.circuit_breaker.CircuitBreaker") as MockCB:
            instance = MagicMock()
            instance._state = CircuitState.CLOSED
            MockCB.return_value = instance

            with patch("sys.argv", ["circuit_breaker", "--test-failure"]):
                result = main()
                assert result == 0
                instance.record_failure.assert_called_once()

    def test_allows_operation_when_closed(self):
        """Verify exit code 0 when operations allowed."""
        with patch("bin.circuit_breaker.CircuitBreaker") as MockCB:
            instance = MagicMock()
            instance.is_allowed.return_value = True
            instance._state = CircuitState.CLOSED
            MockCB.return_value = instance

            with patch("sys.argv", ["circuit_breaker"]):
                result = main()
                assert result == 0

    def test_blocks_operation_when_open(self):
        """Verify exit code 1 when operations blocked."""
        with patch("bin.circuit_breaker.CircuitBreaker") as MockCB:
            instance = MagicMock()
            instance.is_allowed.return_value = False
            instance._state = CircuitState.OPEN
            instance._trip_time = time.time()
            MockCB.return_value = instance

            with patch("sys.argv", ["circuit_breaker"]):
                result = main()
                assert result == 1

    def test_custom_threshold_and_timeout(self):
        """Verify custom threshold and timeout are passed to constructor."""
        with patch("bin.circuit_breaker.CircuitBreaker") as MockCB:
            instance = MagicMock()
            instance.is_allowed.return_value = True
            instance._state = CircuitState.CLOSED
            MockCB.return_value = instance

            with patch(
                "sys.argv",
                ["circuit_breaker", "--threshold", "10", "--timeout", "30"],
            ):
                result = main()
                assert result == 0
                # Check the key params were passed (default args also included)
                call_kwargs = MockCB.call_args.kwargs
                assert call_kwargs["failure_threshold"] == 10
                assert call_kwargs["recovery_timeout"] == 30.0
