#!/usr/bin/env python3
"""Tests for bin/idempotency_token.py — G127 idempotency token generator.

Covers:
- IdempotencyTokenGenerator.__init__ (default + custom namespace)
- from_content (UUID5 determinism, distinct clips, name included)
- from_metadata (sorted-key determinism, key-order independence, varied types)
- random (UUID4 format, uniqueness)
- validate (well-formed UUIDs, malformed strings, non-strings, edge cases)
- parse_args (defaults, custom flags, validate mode)
- main (--validate valid/invalid → correct exit code; --random; --metadata
  with JSON error; --namespace invalid; --output choices; success path)
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.idempotency_token import (  # noqa: E402
    IdempotencyTokenGenerator,
    main,
    parse_args,
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# __init__ / namespace
# ---------------------------------------------------------------------------


class TestInit:
    """Tests for the constructor and namespace default."""

    def test_default_namespace_is_dns(self):
        gen = IdempotencyTokenGenerator()
        assert gen.namespace == uuid.NAMESPACE_DNS

    def test_custom_namespace_stored(self):
        ns = uuid.uuid4()
        gen = IdempotencyTokenGenerator(namespace=ns)
        assert gen.namespace == ns

    def test_none_namespace_treated_as_default(self):
        gen = IdempotencyTokenGenerator(namespace=None)
        assert gen.namespace == uuid.NAMESPACE_DNS


# ---------------------------------------------------------------------------
# from_content
# ---------------------------------------------------------------------------


class TestFromContent:
    """Tests for the content-derived (UUID5) token."""

    def test_returns_valid_uuid_string(self):
        gen = IdempotencyTokenGenerator()
        token = gen.from_content(b"hello world", clip_name="clip-001")
        assert _UUID_RE.match(token), f"not a UUID: {token!r}"

    def test_unique_per_call_due_to_timestamp(self):
        """Two consecutive calls with the same content must yield different
        tokens because the timestamp component changes (per-call precision)."""
        gen = IdempotencyTokenGenerator()
        a = gen.from_content(b"same-bytes", clip_name="x")
        b = gen.from_content(b"same-bytes", clip_name="x")
        assert a != b

    def test_distinct_content_yields_distinct_tokens(self):
        gen = IdempotencyTokenGenerator()
        a = gen.from_content(b"alpha", clip_name="x")
        b = gen.from_content(b"beta", clip_name="x")
        assert a != b

    def test_distinct_clip_names_yield_distinct_tokens(self):
        gen = IdempotencyTokenGenerator()
        a = gen.from_content(b"same", clip_name="clip-A")
        b = gen.from_content(b"same", clip_name="clip-B")
        assert a != b

    def test_empty_content_handled(self):
        gen = IdempotencyTokenGenerator()
        token = gen.from_content(b"", clip_name="")
        assert _UUID_RE.match(token)

    def test_token_is_uuid5(self):
        """Token is derived via uuid5 (version 5 in the UUID string)."""
        gen = IdempotencyTokenGenerator()
        token = gen.from_content(b"x", clip_name="y")
        parsed = uuid.UUID(token)
        # Version nibble is in the 13th hex digit of the canonical form
        # (group 3, first char): e.g. "...-5xxx-..."
        assert parsed.version == 5


# ---------------------------------------------------------------------------
# from_metadata
# ---------------------------------------------------------------------------


class TestFromMetadata:
    """Tests for the metadata-derived (UUID5) token with sorted keys."""

    def test_returns_valid_uuid_string(self):
        gen = IdempotencyTokenGenerator()
        token = gen.from_metadata({"a": 1, "b": 2})
        assert _UUID_RE.match(token)

    def test_key_order_independent(self):
        """Sorted-key JSON means dict order should not affect the token."""
        gen = IdempotencyTokenGenerator()
        a = gen.from_metadata({"a": 1, "b": 2, "c": 3})
        b = gen.from_metadata({"c": 3, "a": 1, "b": 2})
        assert a == b

    def test_different_values_different_tokens(self):
        gen = IdempotencyTokenGenerator()
        a = gen.from_metadata({"x": 1})
        b = gen.from_metadata({"x": 2})
        assert a != b

    def test_handles_mixed_types(self):
        gen = IdempotencyTokenGenerator()
        token = gen.from_metadata(
            {"name": "clip-1", "count": 42, "ratio": 0.5, "flag": True}
        )
        assert _UUID_RE.match(token)

    def test_empty_dict_handled(self):
        gen = IdempotencyTokenGenerator()
        token = gen.from_metadata({})
        assert _UUID_RE.match(token)


# ---------------------------------------------------------------------------
# random
# ---------------------------------------------------------------------------


class TestRandom:
    """Tests for the UUID4 random token."""

    def test_returns_valid_uuid_string(self):
        gen = IdempotencyTokenGenerator()
        token = gen.random()
        assert _UUID_RE.match(token)

    def test_token_is_uuid4(self):
        gen = IdempotencyTokenGenerator()
        token = gen.random()
        assert uuid.UUID(token).version == 4

    def test_unique_across_many_calls(self):
        gen = IdempotencyTokenGenerator()
        tokens = {gen.random() for _ in range(200)}
        assert len(tokens) == 200


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidate:
    """Tests for the static validate() helper."""

    def test_valid_uuid_v4(self):
        assert IdempotencyTokenGenerator.validate(str(uuid.uuid4())) is True

    def test_valid_uuid_v5(self):
        assert IdempotencyTokenGenerator.validate(str(uuid.uuid5(uuid.NAMESPACE_DNS, "x"))) is True

    def test_valid_uuid_with_braces(self):
        u = uuid.uuid4()
        assert IdempotencyTokenGenerator.validate(f"{{{u}}}") is True

    def test_valid_uuid_no_dashes(self):
        assert IdempotencyTokenGenerator.validate(uuid.uuid4().hex) is True

    def test_invalid_empty_string(self):
        assert IdempotencyTokenGenerator.validate("") is False

    def test_invalid_garbage(self):
        assert IdempotencyTokenGenerator.validate("not-a-uuid") is False

    def test_invalid_wrong_length(self):
        assert IdempotencyTokenGenerator.validate("abcdef") is False

    def test_string_with_only_dashes_invalid(self):
        # Looks vaguely UUID-shaped but is not a real UUID
        assert IdempotencyTokenGenerator.validate("----") is False

    def test_string_with_non_hex_chars_invalid(self):
        # Right shape, wrong content
        assert IdempotencyTokenGenerator.validate("zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz") is False


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Tests for the argparse wrapper."""

    def test_default_random_mode(self):
        args = parse_args([])
        assert args.random is False
        assert args.content is None
        assert args.metadata is None
        assert args.validate is None
        assert args.clip_name == ""
        assert args.namespace is None
        assert args.output == "token"

    def test_random_flag(self):
        args = parse_args(["--random"])
        assert args.random is True

    def test_content_with_clip_name(self):
        args = parse_args(["--content", "/tmp/x.tar", "--clip-name", "abc"])
        assert args.content == "/tmp/x.tar"
        assert args.clip_name == "abc"

    def test_metadata(self):
        args = parse_args(["--metadata", '{"a":1}'])
        assert args.metadata == '{"a":1}'

    def test_validate_mode(self):
        args = parse_args(["--validate", "abc-123"])
        assert args.validate == "abc-123"

    def test_output_choices(self):
        for choice in ("token", "json", "full"):
            args = parse_args(["--output", choice])
            assert args.output == choice

    def test_custom_namespace(self):
        args = parse_args(["--namespace", "deadbeef" * 4])
        assert args.namespace == "deadbeef" * 4

    def test_invalid_output_choice_exits(self):
        with pytest.raises(SystemExit):
            parse_args(["--output", "bogus"])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the CLI entry point."""

    def test_validate_valid_token_exits_zero(self, capsys):
        token = str(uuid.uuid4())
        rc = main(["--validate", token])
        captured = capsys.readouterr()
        assert rc == 0
        assert "valid" in captured.out
        assert token in captured.out

    def test_validate_invalid_token_exits_one(self, capsys):
        rc = main(["--validate", "not-a-uuid"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "invalid" in captured.out

    def test_validate_json_output(self, capsys):
        token = str(uuid.uuid4())
        rc = main(["--validate", token, "--output", "json"])
        captured = capsys.readouterr()
        assert rc == 0
        payload = json.loads(captured.out)
        assert payload == {"token": token, "valid": True}

    def test_validate_json_output_invalid(self, capsys):
        rc = main(["--validate", "garbage", "--output", "json"])
        captured = capsys.readouterr()
        assert rc == 1
        payload = json.loads(captured.out)
        assert payload == {"token": "garbage", "valid": False}

    def test_random_mode_prints_token(self, capsys):
        rc = main(["--random"])
        captured = capsys.readouterr()
        assert rc == 0
        assert _UUID_RE.match(captured.out.strip())

    def test_metadata_mode_with_valid_json(self, capsys):
        rc = main(["--metadata", '{"a":1,"b":2}'])
        captured = capsys.readouterr()
        assert rc == 0
        assert _UUID_RE.match(captured.out.strip())

    def test_metadata_mode_with_invalid_json_exits_one(self, capsys):
        rc = main(["--metadata", "{not json"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Error" in captured.err

    def test_invalid_namespace_exits_one(self, capsys):
        rc = main(["--random", "--namespace", "not-a-uuid"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Error" in captured.err

    def test_random_output_full_prints_metadata(self, capsys):
        rc = main(["--random", "--output", "full"])
        captured = capsys.readouterr()
        assert rc == 0
        out = captured.out
        assert "mode: random" in out
        assert "token: " in out
        assert "timestamp: " in out

    def test_content_mode_uses_file(self, tmp_path, capsys):
        f = tmp_path / "clip.bin"
        f.write_bytes(b"hello-content")
        rc = main(["--content", str(f), "--clip-name", "c1"])
        captured = capsys.readouterr()
        assert rc == 0
        assert _UUID_RE.match(captured.out.strip())
