#!/usr/bin/env python3
"""Tests for bin/c2pa_signer.py — C2PA v2.1 Manifest Signer with AI/ML Assertion."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

# Import the module under test
import bin.c2pa_signer as c2pa_signer

# ---------------------------------------------------------------------------
# C2PASigner.__init__
# ---------------------------------------------------------------------------


class TestC2PASignerInit:
    """Test C2PASigner constructor."""

    def test_default_init(self):
        """C2PASigner() should default private_key_path and certificate_path to None."""
        signer = c2pa_signer.C2PASigner()
        assert signer.private_key_path is None
        assert signer.certificate_path is None

    def test_init_with_paths(self):
        """C2PASigner should accept private_key_path and certificate_path."""
        signer = c2pa_signer.C2PASigner(
            private_key_path="/tmp/key.pem", certificate_path="/tmp/cert.pem"
        )
        assert signer.private_key_path == "/tmp/key.pem"
        assert signer.certificate_path == "/tmp/cert.pem"

    def test_class_constants(self):
        """C2PA_VERSION and C2PA_CONTEXT should be set per C2PA v2.1 spec."""
        assert c2pa_signer.C2PASigner.C2PA_VERSION == "2.1"
        assert c2pa_signer.C2PASigner.C2PA_CONTEXT == "http://c2pa.org/contexts/v2.1"


# ---------------------------------------------------------------------------
# create_ai_ml_assertion
# ---------------------------------------------------------------------------


class TestCreateAIMLAssertion:
    """Test create_ai_ml_assertion method."""

    def test_basic_assertion(self):
        """create_ai_ml_assertion should return assertion with required fields."""
        signer = c2pa_signer.C2PASigner()
        assertion = signer.create_ai_ml_assertion(
            model_name="dall-e-3", model_version="3.0", is_synthetic=True
        )
        assert assertion["assertion"] == "ai.generation"
        assert assertion["version"] == "2.1"
        data = assertion["data"]
        assert data["model"]["name"] == "dall-e-3"
        assert data["model"]["version"] == "3.0"
        assert data["model"]["type"] == "generative"
        assert data["is_synthetic"] is True
        assert data["regulatory_compliance"]["eu_ai_act"] is True
        assert data["regulatory_compliance"]["ca_ab_2013"] is True
        # generation_timestamp is an ISO-8601 string
        assert "T" in data["generation_timestamp"]

    def test_non_synthetic_assertion(self):
        """is_synthetic=False should be preserved."""
        signer = c2pa_signer.C2PASigner()
        assertion = signer.create_ai_ml_assertion(
            model_name="real-cam", model_version="1.0", is_synthetic=False
        )
        assert assertion["data"]["is_synthetic"] is False

    def test_with_generation_parameters(self):
        """generation_parameters should be added to the assertion data when provided."""
        signer = c2pa_signer.C2PASigner()
        params = {"temperature": 0.7, "seed": 42}
        assertion = signer.create_ai_ml_assertion(
            model_name="m", model_version="1", is_synthetic=True, generation_parameters=params
        )
        assert assertion["data"]["generation_parameters"] == params

    def test_without_generation_parameters(self):
        """generation_parameters key should be absent when None."""
        signer = c2pa_signer.C2PASigner()
        assertion = signer.create_ai_ml_assertion(
            model_name="m", model_version="1", is_synthetic=False
        )
        assert "generation_parameters" not in assertion["data"]


# ---------------------------------------------------------------------------
# _compute_file_hash
# ---------------------------------------------------------------------------


class TestComputeFileHash:
    """Test _compute_file_hash method."""

    def test_sha256_default(self, tmp_path):
        """Default algorithm should be sha256 and produce 64 hex chars."""
        signer = c2pa_signer.C2PASigner()
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello world")
        digest = signer._compute_file_hash(str(f))
        assert len(digest) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", digest) is not None
        # Known sha256("hello world")
        assert digest == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_sha512(self, tmp_path):
        """sha512 should produce 128 hex chars."""
        import hashlib

        signer = c2pa_signer.C2PASigner()
        f = tmp_path / "data.bin"
        f.write_bytes(b"abc")
        digest = signer._compute_file_hash(str(f), algorithm="sha512")
        assert len(digest) == 128
        assert digest == hashlib.sha512(b"abc").hexdigest()

    def test_empty_file(self, tmp_path):
        """Hashing an empty file should return the known empty-digest for the algorithm."""
        signer = c2pa_signer.C2PASigner()
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        digest = signer._compute_file_hash(str(f))
        assert digest == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ---------------------------------------------------------------------------
# _detect_format
# ---------------------------------------------------------------------------


class TestDetectFormat:
    """Test _detect_format method."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("a.jpg", "image/jpeg"),
            ("a.jpeg", "image/jpeg"),
            ("a.JPG", "image/jpeg"),  # case-insensitive
            ("a.png", "image/png"),
            ("a.pdf", "application/pdf"),
            ("a.mp4", "video/mp4"),
            ("a.mov", "video/quicktime"),
            ("a.mp3", "audio/mpeg"),
            ("a.wav", "audio/wav"),
            ("a.xyz", "application/octet-stream"),
            ("a", "application/octet-stream"),
        ],
    )
    def test_format_detection(self, filename, expected):
        """_detect_format should map known extensions to MIME types and default to octet-stream."""
        signer = c2pa_signer.C2PASigner()
        assert signer._detect_format(filename) == expected


# ---------------------------------------------------------------------------
# create_manifest
# ---------------------------------------------------------------------------


class TestCreateManifest:
    """Test create_manifest method."""

    def test_unsigned_status(self, tmp_path):
        """With no key, signature_info.status should be 'unsigned'."""
        signer = c2pa_signer.C2PASigner()
        f = tmp_path / "img.png"
        f.write_bytes(b"\x89PNG\r\n")
        ai = signer.create_ai_ml_assertion("m", "1", True)
        manifest = signer.create_manifest(str(f), ai)
        assert manifest["signature_info"]["status"] == "unsigned"

    def test_ready_status(self, tmp_path):
        """With a private key, signature_info.status should be 'ready'."""
        signer = c2pa_signer.C2PASigner(private_key_path="/tmp/k.pem")
        f = tmp_path / "img.png"
        f.write_bytes(b"\x89PNG\r\n")
        ai = signer.create_ai_ml_assertion("m", "1", True)
        manifest = signer.create_manifest(str(f), ai)
        assert manifest["signature_info"]["status"] == "ready"

    def test_structure(self, tmp_path):
        """Manifest should have @context, claim, signature_info, ingredient."""
        signer = c2pa_signer.C2PASigner()
        f = tmp_path / "img.png"
        f.write_bytes(b"x")
        ai = signer.create_ai_ml_assertion("m", "1", False)
        manifest = signer.create_manifest(str(f), ai)
        assert manifest["@context"] == "http://c2pa.org/contexts/v2.1"
        claim = manifest["claim"]
        assert "img.png" in claim["dc:title"]
        assert claim["claim_generator"] == "G145-C2PA-Signer/1.0"
        assert "issued" in claim
        assert claim["assertions"][0]["label"] == "ai.generation"
        assert manifest["signature_info"]["algorithm"] == "ES384"
        assert manifest["ingredient"]["format"] == "image/png"
        assert len(manifest["ingredient"]["hash"]) == 64

    def test_custom_claim_generator(self, tmp_path):
        """Custom claim_generator should be honored."""
        signer = c2pa_signer.C2PASigner()
        f = tmp_path / "x.jpg"
        f.write_bytes(b"x")
        ai = signer.create_ai_ml_assertion("m", "1", True)
        manifest = signer.create_manifest(str(f), ai, claim_generator="custom/2.0")
        assert manifest["claim"]["claim_generator"] == "custom/2.0"


# ---------------------------------------------------------------------------
# sign_manifest
# ---------------------------------------------------------------------------


class TestSignManifest:
    """Test sign_manifest method."""

    def test_no_keys_sets_demo_status(self):
        """Without keys, status should be 'demo'."""
        signer = c2pa_signer.C2PASigner()
        m = {"signature_info": {"status": "unsigned", "algorithm": "ES384"}}
        out = signer.sign_manifest(m)
        assert out["signature_info"]["status"] == "demo"
        assert "timestamp" not in out["signature_info"]

    def test_with_keys_sets_signed_status(self):
        """With both keys, status should be 'signed' and a timestamp added."""
        signer = c2pa_signer.C2PASigner(
            private_key_path="/tmp/k", certificate_path="/tmp/c"
        )
        m = {"signature_info": {"status": "ready", "algorithm": "ES384"}}
        out = signer.sign_manifest(m)
        assert out["signature_info"]["status"] == "signed"
        assert "timestamp" in out["signature_info"]
        assert "T" in out["signature_info"]["timestamp"]

    def test_only_private_key(self):
        """With only private_key_path (no cert), status should still be 'demo'."""
        signer = c2pa_signer.C2PASigner(private_key_path="/tmp/k")
        m = {"signature_info": {"status": "ready", "algorithm": "ES384"}}
        out = signer.sign_manifest(m)
        assert out["signature_info"]["status"] == "demo"

    def test_only_cert(self):
        """With only certificate_path (no key), status should still be 'demo'."""
        signer = c2pa_signer.C2PASigner(certificate_path="/tmp/c")
        m = {"signature_info": {"status": "ready", "algorithm": "ES384"}}
        out = signer.sign_manifest(m)
        assert out["signature_info"]["status"] == "demo"


# ---------------------------------------------------------------------------
# embed_manifest
# ---------------------------------------------------------------------------


class TestEmbedManifest:
    """Test embed_manifest method."""

    def test_creates_sidecar_file(self, tmp_path):
        """embed_manifest should write a sidecar .c2pa file with JSON content."""
        signer = c2pa_signer.C2PASigner()
        src = tmp_path / "src.jpg"
        src.write_bytes(b"data")
        out = tmp_path / "out.jpg"
        m = {"a": 1, "b": [1, 2, 3]}
        ok = signer.embed_manifest(str(src), str(out), m)
        assert ok is True
        sidecar = tmp_path / "out.jpg.c2pa"
        assert sidecar.exists()
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
        assert loaded == m

    def test_returns_false_on_error(self, tmp_path, monkeypatch):
        """embed_manifest should return False (not raise) when open() fails."""
        signer = c2pa_signer.C2PASigner()
        # Force the open() call to raise IOError to verify the except branch returns False
        import builtins

        real_open = builtins.open

        def fake_open(*args, **kwargs):
            if str(args[0]).endswith(".c2pa"):
                raise IOError("simulated write failure")
            return real_open(*args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)
        ok = signer.embed_manifest("ignored", str(tmp_path / "out.jpg"), {"x": 1})
        assert ok is False


# ---------------------------------------------------------------------------
# parse_params
# ---------------------------------------------------------------------------


class TestParseParams:
    """Test parse_params function."""

    def test_json_object(self):
        """JSON object string should be parsed as dict."""
        result = c2pa_signer.parse_params('{"temperature": 0.7, "seed": 42}')
        assert result == {"temperature": 0.7, "seed": 42}

    def test_json_array(self):
        """JSON array string should be parsed as list."""
        result = c2pa_signer.parse_params("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_keyvalue_pairs(self):
        """Non-JSON key=value string should be parsed via comma split."""
        result = c2pa_signer.parse_params("temperature=0.7,seed=42,foo=bar")
        assert result == {"temperature": "0.7", "seed": "42", "foo": "bar"}

    def test_keyvalue_with_equals_in_value(self):
        """Only first '=' should split to allow '=' inside the value."""
        result = c2pa_signer.parse_params("expr=a=1,b=2")
        assert result == {"expr": "a=1", "b": "2"}

    def test_empty_string(self):
        """Empty string should return an empty dict (not valid JSON, no pairs)."""
        result = c2pa_signer.parse_params("")
        assert result == {}

    def test_garbage_falls_back_to_kv(self):
        """Invalid JSON containing '=' should be parsed via KV path.

        Pairs without '=' are silently dropped (so the leading 'not json'
        fragment is skipped), and the 'k=v' pair survives.
        """
        result = c2pa_signer.parse_params("not json, but k=v")
        assert result == {"but k": "v"}


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Test parse_args function."""

    def test_minimal_required_args(self):
        """Only --input, --model, --version should be required."""
        args = c2pa_signer.parse_args(
            ["--input", "/tmp/x.jpg", "--model", "m", "--version", "1"]
        )
        assert args.input == "/tmp/x.jpg"
        assert args.model == "m"
        assert args.version == "1"
        assert args.synthetic is False
        assert args.verbose is False
        assert args.params is None
        assert args.private_key is None
        assert args.certificate is None
        assert args.output is None
        assert args.claim_generator == "G145-C2PA-Signer/1.0"

    def test_synthetic_flag(self):
        """--synthetic should set args.synthetic to True."""
        args = c2pa_signer.parse_args(
            [
                "--input",
                "/tmp/x.jpg",
                "--model",
                "m",
                "--version",
                "1",
                "--synthetic",
            ]
        )
        assert args.synthetic is True

    def test_short_flags(self):
        """Short flags -i, -m, -v, -s, -V should work."""
        args = c2pa_signer.parse_args(["-i", "/tmp/x.jpg", "-m", "m", "-v", "1", "-s", "-V"])
        assert args.input == "/tmp/x.jpg"
        assert args.model == "m"
        assert args.version == "1"
        assert args.synthetic is True
        assert args.verbose is True

    def test_all_args(self):
        """All optional args should be parsed when given."""
        args = c2pa_signer.parse_args(
            [
                "--input",
                "/tmp/x.jpg",
                "--output",
                "/tmp/y.jpg",
                "--model",
                "m",
                "--version",
                "1",
                "--synthetic",
                "--params",
                "k=v",
                "--private-key",
                "/tmp/k",
                "--certificate",
                "/tmp/c",
                "--claim-generator",
                "custom/2.0",
                "--verbose",
            ]
        )
        assert args.output == "/tmp/y.jpg"
        assert args.params == "k=v"
        assert args.private_key == "/tmp/k"
        assert args.certificate == "/tmp/c"
        assert args.claim_generator == "custom/2.0"
        assert args.verbose is True

    def test_missing_required_fails(self):
        """Missing required --input should fail (SystemExit)."""
        with pytest.raises(SystemExit):
            c2pa_signer.parse_args(["--model", "m", "--version", "1"])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Test main entry function."""

    def test_missing_input_file_returns_1(self, capsys, tmp_path):
        """main() should return 1 and print an error when input file is missing."""
        missing = tmp_path / "nope.jpg"
        rc = c2pa_signer.main(["prog", "--input", str(missing), "--model", "m", "--version", "1"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Input file not found" in err

    def test_success_path(self, tmp_path, capsys):
        """main() should embed the manifest and print JSON when input exists."""
        src = tmp_path / "img.png"
        src.write_bytes(b"\x89PNG\r\n")
        out = tmp_path / "signed.png"
        rc = c2pa_signer.main(
            [
                "prog",
                "--input",
                str(src),
                "--output",
                str(out),
                "--model",
                "m",
                "--version",
                "1",
                "--synthetic",
            ]
        )
        assert rc == 0
        # sidecar manifest should exist
        assert (tmp_path / "signed.png.c2pa").exists()
        # stdout should contain JSON with signature_info
        captured = capsys.readouterr()
        loaded = json.loads(captured.out)
        assert loaded["signature_info"]["status"] == "demo"
        # AI assertion is nested: claim.assertions[0].data.data.is_synthetic
        ai_data = loaded["claim"]["assertions"][0]["data"]["data"]
        assert ai_data["is_synthetic"] is True

    def test_success_default_output_path(self, tmp_path, capsys):
        """When --output is omitted, sidecar is placed next to input as <name>.signed.c2pa."""
        src = tmp_path / "img.jpg"
        src.write_bytes(b"x")
        rc = c2pa_signer.main(["prog", "--input", str(src), "--model", "m", "--version", "1"])
        assert rc == 0
        assert (tmp_path / "img.jpg.signed.c2pa").exists()

    def test_embed_failure_returns_1(self, tmp_path, capsys, monkeypatch):
        """If embed_manifest returns False, main() should return 1 and print an error."""
        src = tmp_path / "img.png"
        src.write_bytes(b"x")
        out = tmp_path / "out.png"

        # Patch the C2PASigner.embed_manifest instance method to return False
        from bin.c2pa_signer import C2PASigner

        def fake_embed(self, source_path, output_path, manifest):  # noqa: ARG001
            return False

        monkeypatch.setattr(C2PASigner, "embed_manifest", fake_embed)
        rc = c2pa_signer.main(
            ["prog", "--input", str(src), "--output", str(out), "--model", "m", "--version", "1"]
        )
        assert rc == 1
        assert "Failed to embed manifest" in capsys.readouterr().err

    def test_verbose_flag(self, tmp_path, capsys):
        """--verbose should emit progress lines to stdout."""
        src = tmp_path / "img.png"
        src.write_bytes(b"x")
        c2pa_signer.main(
            [
                "prog",
                "--input",
                str(src),
                "--model",
                "m",
                "--version",
                "1",
                "--verbose",
            ]
        )
        captured = capsys.readouterr()
        assert "Creating AI/ML assertion" in captured.out
        assert "Creating C2PA manifest" in captured.out
        assert "Signing manifest" in captured.out

    def test_params_parsed_and_included(self, tmp_path, capsys):
        """--params should be parsed and embedded in the assertion data."""
        src = tmp_path / "img.png"
        src.write_bytes(b"x")
        c2pa_signer.main(
            [
                "prog",
                "--input",
                str(src),
                "--model",
                "m",
                "--version",
                "1",
                "--params",
                "temperature=0.5,seed=7",
            ]
        )
        out = capsys.readouterr().out
        loaded = json.loads(out)
        # AI assertion is nested: claim.assertions[0].data.data.generation_parameters
        params = loaded["claim"]["assertions"][0]["data"]["data"]["generation_parameters"]
        assert params == {"temperature": "0.5", "seed": "7"}


# ---------------------------------------------------------------------------
# CLI subprocess smoke test
# ---------------------------------------------------------------------------


class TestCLI:
    """Test command-line invocation via subprocess."""

    def test_cli_help(self):
        """--help should exit 0 and show usage."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.c2pa_signer", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "C2PA" in result.stdout
        assert "--input" in result.stdout

    def test_cli_missing_input(self, tmp_path):
        """CLI with missing input file should exit 1."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "bin.c2pa_signer",
                "--input",
                str(tmp_path / "nope.jpg"),
                "--model",
                "m",
                "--version",
                "1",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Input file not found" in result.stderr

    def test_cli_end_to_end(self, tmp_path):
        """CLI should produce a .c2pa sidecar when given a valid input."""
        src = tmp_path / "img.png"
        src.write_bytes(b"\x89PNG\r\n")
        out = tmp_path / "out.png"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "bin.c2pa_signer",
                "--input",
                str(src),
                "--output",
                str(out),
                "--model",
                "m",
                "--version",
                "1",
                "--synthetic",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "out.png.c2pa").exists()
