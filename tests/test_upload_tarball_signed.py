"""
tests/test_upload_tarball_signed.py — Unit + integration tests for the Gap #8
direct-to-Supabase signed-URL upload client (bin/upload_tarball_signed.py).

What we test:
  - sha256 streaming hash matches stdlib for a multi-MB file.
  - HMAC header format is "v1 <tester_id> <ts_ms> <hex>" with the canonical
    payload tester_id || '\n' || ts_ms || '\n' || sha256(body).
  - sign() / put_blob() / finalize() POST/PUT the right shapes.
  - upload() orchestrates the three calls in order, including the
    "already_uploaded" short-circuit that skips the PUT.
  - ClientError vs ServerError mapping for 4xx vs 5xx responses.
  - CLI main() returns the right exit codes.

We exercise the real network code by spinning up `http.server.ThreadingHTTPServer`
on a free port and routing /api/upload-tarball/sign, the signed-URL PUT path,
and /api/upload-tarball/finalize to a single in-memory handler. No mocks of
urllib — actual sockets, actual JSON.

Howard 2026-05-13.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bin import upload_tarball_signed as ut

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _StubServerState:
    """Mutable container the handler can read/write from across requests."""

    def __init__(self) -> None:
        self.sign_calls: list[dict] = []
        self.finalize_calls: list[dict] = []
        self.put_payloads: list[bytes] = []
        # Programmable responses
        self.sign_response: dict | tuple[int, dict] = (200, {})
        self.put_status: int = 200
        self.finalize_response: dict | tuple[int, dict] = (200, {})

    def reset(self) -> None:
        self.sign_calls.clear()
        self.finalize_calls.clear()
        self.put_payloads.clear()
        self.sign_response = (200, {})
        self.put_status = 200
        self.finalize_response = (200, {})


class _StubHandler(BaseHTTPRequestHandler):
    state: _StubServerState | None = None  # injected per-test

    # silence default access log
    def log_message(self, *_args: object) -> None:  # noqa: D401
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length > 0 else b""

    def _respond(self, status: int, body_obj: dict | None = None) -> None:
        body = json.dumps(body_obj or {}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler protocol)
        state = self.state
        assert state is not None
        body = self._read_body()
        if self.path == "/api/upload-tarball/sign":
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self._respond(400, {"error": "bad json"})
                return
            state.sign_calls.append(
                {
                    "headers": dict(self.headers),
                    "body": payload,
                }
            )
            resp = state.sign_response
            if isinstance(resp, tuple):
                status, obj = resp
                self._respond(status, obj)
            else:
                self._respond(200, resp)
            return
        if self.path == "/api/upload-tarball/finalize":
            payload = json.loads(body)
            state.finalize_calls.append({"headers": dict(self.headers), "body": payload})
            resp = state.finalize_response
            if isinstance(resp, tuple):
                status, obj = resp
                self._respond(status, obj)
            else:
                self._respond(200, resp)
            return
        self._respond(404, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        state = self.state
        assert state is not None
        body = self._read_body()
        state.put_payloads.append(body)
        if state.put_status >= 400:
            self._respond(state.put_status, {"error": "stub fail"})
        else:
            self.send_response(state.put_status)
            self.send_header("Content-Length", "0")
            self.end_headers()


@pytest.fixture
def stub_server():
    state = _StubServerState()

    class Handler(_StubHandler):
        pass

    Handler.state = state
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def sample_tarball(tmp_path: Path) -> tuple[Path, str]:
    """Write 5 MiB of pseudo-random bytes to a .tar.gz, return (path, sha256)."""
    p = tmp_path / "vendor-001_batch-2026-05-A_clip-00001_v1.tar.gz"
    data = b"\x1f\x8b" + bytes(range(256)) * (5 * 1024 * 4)  # ~5 MB, deterministic
    p.write_bytes(data)
    return p, hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Unit: compute_sha256_streaming
# ---------------------------------------------------------------------------


def test_compute_sha256_streaming_matches_stdlib(sample_tarball: tuple[Path, str]) -> None:
    path, expected = sample_tarball
    got = ut.compute_sha256_streaming(path)
    assert got == expected
    assert len(got) == 64


# ---------------------------------------------------------------------------
# Unit: build_hmac_header
# ---------------------------------------------------------------------------


def test_build_hmac_header_shape_and_verifiable() -> None:
    tester_id = str(uuid.uuid4())
    secret = "super-secret-key"
    body = b'{"hello":"world"}'
    header = ut.build_hmac_header(tester_id, secret, body)

    # Shape
    parts = header.split()
    assert parts[0] == "v1"
    assert parts[1] == tester_id
    assert parts[2].isdigit()
    assert len(parts[3]) == 64  # hex sha256

    # The signature must be reproducible from the same inputs
    ts_ms = int(parts[2])
    expected = hmac.new(
        secret.encode(),
        f"{tester_id}\n{ts_ms}\n{hashlib.sha256(body).hexdigest()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert parts[3] == expected


def test_build_hmac_header_changes_with_body() -> None:
    tid = str(uuid.uuid4())
    h1 = ut.build_hmac_header(tid, "secret", b"one").split()[3]
    h2 = ut.build_hmac_header(tid, "secret", b"two").split()[3]
    assert h1 != h2


# ---------------------------------------------------------------------------
# Integration: sign() against stub server
# ---------------------------------------------------------------------------


def test_sign_posts_expected_json(stub_server) -> None:
    state, base = stub_server
    tarball_id = str(uuid.uuid4())
    state.sign_response = (
        200,
        {
            "tarball_id": tarball_id,
            "signed_url": f"{base}/signed/abc",
            "storage_bucket": "tarball-uploads",
            "storage_path": "tid/abc.tar.gz",
            "expires_at": "2026-05-13T20:00:00Z",
            "ttl_seconds": 900,
        },
    )

    tid = str(uuid.uuid4())
    resp = ut.sign(
        base_url=base,
        tester_id=tid,
        filename="foo.tar.gz",
        size_bytes=12345,
        sha256="a" * 64,
        duration_seconds=600,
        auth_secret="topsecret",
    )

    assert resp.tarball_id == tarball_id
    assert resp.signed_url.endswith("/signed/abc")
    assert resp.storage_bucket == "tarball-uploads"
    assert not resp.already_uploaded

    # Server saw the right body + the HMAC header
    assert len(state.sign_calls) == 1
    body = state.sign_calls[0]["body"]
    headers = state.sign_calls[0]["headers"]
    assert body == {
        "tester_id": tid,
        "filename": "foo.tar.gz",
        "size_bytes": 12345,
        "sha256": "a" * 64,
        "duration_seconds": 600,
    }
    auth = headers.get("X-Tester-Auth") or headers.get("x-tester-auth")
    assert auth and auth.startswith(f"v1 {tid} ")


def test_sign_raises_client_error_on_400(stub_server) -> None:
    state, base = stub_server
    state.sign_response = (400, {"error": "Invalid body"})
    with pytest.raises(ut.ClientError) as exc:
        ut.sign(
            base_url=base,
            tester_id=str(uuid.uuid4()),
            filename="f.tar.gz",
            size_bytes=1,
            sha256="b" * 64,
            duration_seconds=1,
            auth_secret="",
        )
    assert exc.value.status == 400


def test_sign_raises_server_error_on_500(stub_server) -> None:
    state, base = stub_server
    state.sign_response = (500, {"error": "boom"})
    with pytest.raises(ut.ServerError) as exc:
        ut.sign(
            base_url=base,
            tester_id=str(uuid.uuid4()),
            filename="f.tar.gz",
            size_bytes=1,
            sha256="c" * 64,
            duration_seconds=1,
            auth_secret="",
        )
    assert exc.value.status == 500


# ---------------------------------------------------------------------------
# Integration: finalize()
# ---------------------------------------------------------------------------


def test_finalize_posts_sha256_and_hmac(stub_server) -> None:
    state, base = stub_server
    tid = str(uuid.uuid4())
    tarball_id = str(uuid.uuid4())
    state.finalize_response = (200, {"id": tarball_id, "accepted": True})

    out = ut.finalize(
        base_url=base,
        tester_id=tid,
        tarball_id=tarball_id,
        sha256="d" * 64,
        auth_secret="zzz",
    )
    assert out["accepted"] is True
    body = state.finalize_calls[0]["body"]
    assert body == {"tarball_id": tarball_id, "sha256": "d" * 64}
    auth = state.finalize_calls[0]["headers"].get("X-Tester-Auth")
    assert auth and auth.startswith(f"v1 {tid} ")


# ---------------------------------------------------------------------------
# Integration: upload() orchestrates all three calls
# ---------------------------------------------------------------------------


def test_upload_runs_three_steps_in_order(stub_server, sample_tarball: tuple[Path, str]) -> None:
    state, base = stub_server
    path, sha = sample_tarball
    tid = str(uuid.uuid4())
    tarball_id = str(uuid.uuid4())

    state.sign_response = (
        200,
        {
            "tarball_id": tarball_id,
            "signed_url": f"{base}/storage/v1/object/upload/sign/abc",
            "storage_bucket": "tarball-uploads",
            "storage_path": f"{tid}/{sha}.tar.gz",
            "expires_at": "2026-05-13T20:00:00Z",
            "ttl_seconds": 900,
        },
    )
    state.put_status = 200
    state.finalize_response = (
        200,
        {
            "id": tarball_id,
            "tester_id": tid,
            "sha256": sha,
            "size_bytes": path.stat().st_size,
            "accepted": True,
        },
    )

    result = ut.upload(
        base_url=base,
        tester_id=tid,
        path=path,
        duration_seconds=1800,
        auth_secret="hmacsecret",
    )

    assert result["accepted"] is True
    assert len(state.sign_calls) == 1
    assert len(state.put_payloads) == 1
    assert len(state.finalize_calls) == 1
    # Recorder uploaded the right bytes
    assert hashlib.sha256(state.put_payloads[0]).hexdigest() == sha


def test_upload_skips_put_when_already_uploaded(
    stub_server, sample_tarball: tuple[Path, str]
) -> None:
    state, base = stub_server
    path, sha = sample_tarball
    tid = str(uuid.uuid4())
    tarball_id = str(uuid.uuid4())

    state.sign_response = (
        200,
        {
            "tarball_id": tarball_id,
            "already_uploaded": True,
            "sha256": sha,
            "size_bytes": path.stat().st_size,
        },
    )
    state.finalize_response = (
        200,
        {"id": tarball_id, "duplicate": True, "accepted": True, "sha256": sha},
    )

    result = ut.upload(
        base_url=base,
        tester_id=tid,
        path=path,
        duration_seconds=1800,
        auth_secret="hmacsecret",
    )

    assert result["duplicate"] is True
    assert len(state.sign_calls) == 1
    assert state.put_payloads == []  # PUT was skipped
    assert len(state.finalize_calls) == 1


def test_upload_raises_when_tarball_missing(tmp_path: Path) -> None:
    with pytest.raises(ut.ClientError):
        ut.upload(
            base_url="http://127.0.0.1:1",
            tester_id=str(uuid.uuid4()),
            path=tmp_path / "does-not-exist.tar.gz",
            duration_seconds=1,
        )


def test_upload_put_failure_propagates(stub_server, sample_tarball: tuple[Path, str]) -> None:
    state, base = stub_server
    path, sha = sample_tarball
    tid = str(uuid.uuid4())
    tarball_id = str(uuid.uuid4())

    state.sign_response = (
        200,
        {
            "tarball_id": tarball_id,
            "signed_url": f"{base}/signed",
            "storage_bucket": "tarball-uploads",
            "storage_path": f"{tid}/{sha}.tar.gz",
            "expires_at": "x",
            "ttl_seconds": 900,
        },
    )
    state.put_status = 503

    with pytest.raises(ut.ServerError):
        ut.upload(
            base_url=base,
            tester_id=tid,
            path=path,
            duration_seconds=1,
            auth_secret="z",
        )
    # finalize must NOT have been called
    assert state.finalize_calls == []


# ---------------------------------------------------------------------------
# CLI: exit codes
# ---------------------------------------------------------------------------


def test_cli_returns_1_when_missing_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UPLOAD_BASE_URL", raising=False)
    p = tmp_path / "a.tar.gz"
    p.write_bytes(b"x")
    rc = ut.main(["--tester-id", str(uuid.uuid4()), "--duration-seconds", "1", str(p)])
    assert rc == 1


def test_cli_returns_1_when_tester_id_not_uuid(tmp_path: Path) -> None:
    p = tmp_path / "a.tar.gz"
    p.write_bytes(b"x")
    rc = ut.main(
        [
            "--tester-id",
            "not-a-uuid",
            "--duration-seconds",
            "1",
            "--base-url",
            "http://example.invalid",
            str(p),
        ]
    )
    assert rc == 1


def test_cli_returns_2_on_4xx(
    stub_server, sample_tarball: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    state, base = stub_server
    state.sign_response = (400, {"error": "bad"})
    path, _ = sample_tarball
    rc = ut.main(
        [
            "--tester-id",
            str(uuid.uuid4()),
            "--duration-seconds",
            "1",
            "--base-url",
            base,
            str(path),
        ]
    )
    assert rc == 2


def test_cli_success_prints_json(
    stub_server,
    sample_tarball: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    state, base = stub_server
    path, sha = sample_tarball
    tid = str(uuid.uuid4())
    tarball_id = str(uuid.uuid4())
    state.sign_response = (
        200,
        {
            "tarball_id": tarball_id,
            "signed_url": f"{base}/signed",
            "storage_bucket": "tarball-uploads",
            "storage_path": f"{tid}/{sha}.tar.gz",
            "expires_at": "x",
            "ttl_seconds": 900,
        },
    )
    state.put_status = 200
    state.finalize_response = (
        200,
        {"id": tarball_id, "tester_id": tid, "sha256": sha, "accepted": True},
    )
    rc = ut.main(
        [
            "--tester-id",
            tid,
            "--duration-seconds",
            "60",
            "--base-url",
            base,
            "--sha256",
            sha,
            str(path),
        ]
    )
    out = capsys.readouterr().out.strip()
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["accepted"] is True
    assert parsed["sha256"] == sha
