#!/usr/bin/env python3
"""Tests for spectator_follow.py"""

import signal
import struct
from unittest.mock import Mock, call, patch

import pytest

from bin.spectator_follow import PacketType, RconClient, get_player_uuid, main, spectate_loop


def test_packet_construct():
    """Verify byte layout for cmd packet."""
    # Test packet construction
    body = "test command"
    body_bytes = body.encode("utf-8") + b"\x00"

    # Expected packet structure
    packet_id = 1
    packet_type = PacketType.COMMAND
    packet_length = len(body_bytes) + 10  # id(4) + type(4) + body + nulls

    # Create RconClient and mock the _send_packet to capture what it builds
    client = RconClient("localhost", 25575, "password")

    with patch.object(client, "sock", Mock()):
        # Call _send_packet which will construct the packet
        client._send_packet(packet_type, body)

        # Get the actual packet sent
        actual_packet = client.sock.sendall.call_args[0][0]

        # Verify packet structure
        # Parse length, id, type from packet
        actual_length = struct.unpack("<i", actual_packet[0:4])[0]
        actual_id = struct.unpack("<i", actual_packet[4:8])[0]
        actual_type = struct.unpack("<i", actual_packet[8:12])[0]
        actual_body = actual_packet[12:-1].decode("utf-8")  # Remove null terminator

        assert actual_length == packet_length
        assert actual_id == packet_id
        assert actual_type == packet_type
        assert actual_body == body


def test_authenticate_success():
    """Mock socket recv → success packet."""
    client = RconClient("localhost", 25575, "password")

    # Mock socket
    mock_sock = Mock()
    client.sock = mock_sock

    # Mock recv to return success response (two packets: auth response and empty response)
    recv_calls = []

    def mock_recv(size):
        recv_calls.append(size)
        if len(recv_calls) == 1:  # First call gets length of auth response
            return struct.pack("<i", 10)  # Length: id(4) + type(4) + 2 nulls = 10 (empty body)
        elif len(recv_calls) == 2:  # Second call gets auth response packet
            # Success response: id=1, type=0, body="\x00\x00"
            return struct.pack("<ii", 1, 0) + b"\x00\x00"
        elif len(recv_calls) == 3:  # Third call gets length of empty response
            return struct.pack("<i", 10)  # Length: id(4) + type(4) + 2 nulls = 10 (empty body)
        else:  # Fourth call gets empty response packet
            # Empty response: id=1, type=0, body="\x00\x00"
            return struct.pack("<ii", 1, 0) + b"\x00\x00"

    mock_sock.recv.side_effect = mock_recv

    # Authenticate should succeed
    result = client.authenticate()
    assert result is True

    # Verify send was called with auth packet
    assert mock_sock.sendall.called


def test_authenticate_fails_on_bad_password():
    """Mock socket recv → -1 id."""
    client = RconClient("localhost", 25575, "wrongpassword")

    # Mock socket
    mock_sock = Mock()
    client.sock = mock_sock

    # Mock recv to return failure response (id=-1)
    recv_calls = []

    def mock_recv(size):
        recv_calls.append(size)
        if len(recv_calls) == 1:  # First call gets length
            return struct.pack(
                "<i", 10
            )  # Length: id(4) + type(4) + 2 nulls = 10 (empty body) of response packet
        else:  # Second call gets packet data (10 bytes total: 4+4+2)
            # Failure response: id=-1, type=0, body="\x00\x00"
            return struct.pack("<ii", -1, 0) + b"\x00\x00"  # 10 bytes
        # No second packet for failed auth

    mock_sock.recv.side_effect = mock_recv

    # Authenticate should fail
    result = client.authenticate()
    assert result is False


def test_send_retries_on_socket_error():
    """Monkeypatch socket.send raise then succeed."""
    # This test is for the retry logic in spectate_loop, not RconClient.send
    # We'll test the retry logic separately in test_spectate_loop_retry_logic
    pass


def test_spectate_loop_respects_duration():
    """Mock RconClient, time.time monotonic mock, assert sent N times.

    Howard 2026-05-08: the previous tail `_it.repeat(100.0)` caused an
    infinite loop in CI. The inner sleep-polling loop in spectate_loop is
    `while time.time() < sleep_end:` where sleep_end = time.time() + 5.
    Once time.time() pinned to 100.0 forever, sleep_end pinned to 105.0,
    and `100 < 105` stayed true forever. Replaced with `_it.count(11.0)`
    so each subsequent call advances and the inner loop can exit.
    """
    mock_rcon = Mock()
    mock_rcon.send.return_value = "OK"

    # Mock time: start + 2 elapsed checks (1.0, 6.0) that allow 2 sends,
    # then count up from 11.0 so the third elapsed check breaks the duration
    # gate AND the inner sleep-polling loop sees monotonically-advancing
    # values that eventually exceed any sleep_end.
    import itertools as _it

    time_iter = _it.chain(
        [0.0],  # start_time = time.time()
        [1.0, 1.05, 1.1],  # iter 1: elapsed=1.0 (send), then sleep-loop polls
        [6.0, 6.05, 6.1],  # iter 2: elapsed=6.0 (send), then sleep-loop polls
        _it.count(start=11.0),  # advances each call → inner loop + outer both exit
    )

    with patch("time.time", side_effect=time_iter), patch("time.sleep"):
        with patch("logging.info"):
            with patch("logging.debug"):
                # Run for 10 seconds with 5 second interval
                commands_sent = spectate_loop(
                    rcon=mock_rcon,
                    bot_username="Bot",
                    spectator_username="Spectator",
                    interval_sec=5.0,
                    duration_sec=10.0,
                )

    # Should send 2 commands (at t=0, t=5) not 3 because duration is 10s
    # Actually with our time values:
    # t=0 (send), t=1 (sleep done), t=6 (send), t=11 (check duration > 10, break)
    assert commands_sent == 2
    assert mock_rcon.send.call_count == 2

    # Verify correct command format
    expected_calls = [
        call("spectate Bot Spectator"),
        call("spectate Bot Spectator"),
    ]
    mock_rcon.send.assert_has_calls(expected_calls)


def test_get_player_uuid_parses_response():
    """Mock send → "<player> has UUID xxxxxxxx-..."."""
    mock_rcon = Mock()

    # Test with UUID in response
    uuid_response = "DataPilot has UUID: 12345678-1234-1234-1234-123456789abc"
    mock_rcon.send.return_value = uuid_response

    uuid = get_player_uuid(mock_rcon, "DataPilot")
    assert uuid == "12345678-1234-1234-1234-123456789abc"

    # Test with no UUID found
    mock_rcon.send.return_value = "Some other response"
    uuid = get_player_uuid(mock_rcon, "DataPilot")
    assert uuid is None

    # Test with exception
    mock_rcon.send.side_effect = Exception("Test error")
    uuid = get_player_uuid(mock_rcon, "DataPilot")
    assert uuid is None


def test_main_argparse_required_args():
    """Test that argparse requires necessary arguments."""
    # Test missing required args - should raise SystemExit
    with pytest.raises(SystemExit):
        main([])

    # Test with all required args
    test_args = [
        "--rcon-host",
        "localhost",
        "--rcon-password",
        "testpass",
        "--bot",
        "Bot",
        "--spectator",
        "Spectator",
    ]

    # Mock everything to avoid actual connections
    with patch("bin.spectator_follow.RconClient") as mock_rcon_class:
        mock_rcon = Mock()
        mock_rcon.authenticate.return_value = True
        mock_rcon_class.return_value.__enter__.return_value = mock_rcon

        with patch("bin.spectator_follow.spectate_loop", return_value=1):
            with patch("bin.spectator_follow.signal.signal"):
                with patch("logging.basicConfig"):
                    result = main(test_args)

    assert result == 0


def test_spectate_loop_graceful_interrupt():
    """Test that spectate_loop handles SIGINT gracefully.

    Howard 2026-05-08: previous test was structurally broken — it mocked
    signal.signal to capture the handler but never invoked it, so
    duration_sec=None ran forever (CI timeout >120s). Now we capture the
    real handler and trigger it from inside the rcon.send mock to
    simulate SIGINT arriving mid-execution. This actually exercises the
    stop_requested branch in spectate_loop.
    """
    # Capture the signal handler that spectate_loop registers.
    captured_handler: list = []

    def mock_signal(sig, handler):
        if sig == signal.SIGINT:
            captured_handler.append(handler)

    # rcon.send fires the captured handler on its first invocation,
    # simulating SIGINT delivery inside the spectate command call.
    send_calls = {"count": 0}

    def mock_send(cmd):
        send_calls["count"] += 1
        if send_calls["count"] == 1 and captured_handler:
            captured_handler[0](signal.SIGINT, None)
        return "OK"

    mock_rcon = Mock()
    mock_rcon.send.side_effect = mock_send

    # Mock time so the inner sleep-poll loop has bounded iterations even
    # if it runs (defence in depth — stop_requested should exit first).
    time_counter = [0.0]

    def mock_time():
        val = time_counter[0]
        time_counter[0] += 1.0
        return val

    with (
        patch("time.time", side_effect=mock_time),
        patch("time.sleep"),
        patch("logging.info"),
        patch("logging.debug"),
        patch("signal.signal", mock_signal),
    ):
        commands_sent = spectate_loop(
            rcon=mock_rcon,
            bot_username="Bot",
            spectator_username="Spectator",
            interval_sec=5.0,
            duration_sec=None,
        )

    # First send fired SIGINT → stop_requested → outer break before iter 2.
    assert commands_sent == 1
    # Confirm a SIGINT handler was actually registered.
    assert len(captured_handler) == 1


def test_spectate_loop_retry_logic():
    """Test exponential backoff and failure counting.

    Howard 2026-05-08: previous time_values=[0.0, 1.0, ...] with
    duration_sec=1.0 made the first elapsed check (1.0 - 0.0 = 1.0)
    >= duration immediately, so the loop broke before any retry
    happened — mock_sleep.call_count was 0 not >= 2. Fixed by giving
    duration_sec=5.0 head-room and using itertools.count after the
    planned values so monotonic advancement guarantees the inner
    sleep-poll loop and outer duration check both exit cleanly.
    """
    import itertools as _it

    # Make send fail 2 times then succeed.
    call_count = {"n": 0}

    def mock_send(cmd):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise Exception("Test failure")
        return "OK"

    mock_rcon = Mock()
    mock_rcon.send.side_effect = mock_send

    # 0.0 → start_time, 0.5 → iter1 elapsed (< 5 yes, enter retry),
    # then count from 10.0 so any further time.time() call advances
    # monotonically and exits both inner sleep loop and outer duration.
    time_iter = _it.chain([0.0, 0.5], _it.count(start=10.0))

    with (
        patch("time.time", side_effect=time_iter),
        patch("time.sleep") as mock_sleep,
        patch("logging.debug"),
        patch("logging.warning"),
        patch("logging.error"),
    ):
        commands_sent = spectate_loop(
            rcon=mock_rcon,
            bot_username="Bot",
            spectator_username="Spectator",
            interval_sec=2.0,
            duration_sec=5.0,
        )

    # Retry logic: attempt 0 fails (no sleep), attempt 1 sleeps 1s + fails,
    # attempt 2 sleeps 2s + succeeds → at least 2 sleep calls.
    assert mock_sleep.call_count >= 2
    # Exactly one successful send before duration breaks outer.
    assert commands_sent == 1


def test_rcon_client_context_manager():
    """Test that RconClient works as a context manager."""
    with patch("socket.socket") as mock_socket_class:
        mock_socket = Mock()
        mock_socket_class.return_value = mock_socket

        client = RconClient("localhost", 25575, "password")

        # Enter context
        with client as c:
            assert c is client
            assert client.sock is not None
            mock_socket.connect.assert_called_once_with(("localhost", 25575))

        # Exit context
        mock_socket.close.assert_called_once()


def test_rcon_client_send_receive():
    """Test basic send/receive functionality."""
    client = RconClient("localhost", 25575, "password")

    # Mock socket
    mock_sock = Mock()
    client.sock = mock_sock

    # Mock recv for response
    recv_calls = []

    def mock_recv(size):
        recv_calls.append(size)
        if len(recv_calls) == 1:  # First call gets length
            return struct.pack("<i", 20)  # Length
        else:  # Second call gets packet
            return struct.pack("<ii", 1, 0) + b"Test response\x00\x00"

    mock_sock.recv.side_effect = mock_recv

    response = client.send("test command")
    assert response == "Test response"
    assert mock_sock.sendall.called


def test_spectate_loop_stops_after_5_consecutive_failures():
    """Test that spectate_loop stops after 5 consecutive failures.

    Howard 2026-05-08: previous fixed list of 7 time values raised
    StopIteration once the inner sleep-poll loop consumed them. With
    `duration_sec=None` and 5 outer iterations × N inner polls, 7 isn't
    enough. Replaced with itertools.count(step=2.0) — each call advances
    2 seconds, immediately exiting any 1-second inner sleep window.
    """
    import itertools as _it

    mock_rcon = Mock()
    mock_rcon.send.side_effect = Exception("Always fails")

    # step=2.0 > interval_sec=1.0 means each inner-while check exits on
    # the first iteration; outer loop terminates via consecutive_failures>=5.
    time_iter = _it.count(start=0.0, step=2.0)

    with (
        patch("time.time", side_effect=time_iter),
        patch("time.sleep"),
        patch("logging.debug"),
        patch("logging.warning"),
        patch("logging.error") as mock_error,
    ):
        commands_sent = spectate_loop(
            rcon=mock_rcon,
            bot_username="Bot",
            spectator_username="Spectator",
            interval_sec=1.0,
            duration_sec=None,
        )

    assert commands_sent == 0
    assert any("Too many consecutive failures" in str(call) for call in mock_error.call_args_list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
