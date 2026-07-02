"""Parity module for the TypeScript ``web-tester/lib/upload-auth.ts``.

Production gap #6: vendors must include ``X-Upload-Token`` on
``POST /api/upload-tarball`` so the server can verify they own the
``tester_id`` they're claiming.

The contract is identical to the TypeScript module:

    token = HMAC_SHA256(secret=UPLOAD_HMAC_SECRET, msg=tester_id).hex()  # 64 chars
    prefix = token[:16]                                                  # filename-embed form

This Python implementation exists because:

1. The Rust recorder (vendor/recorder submodule) needs a reference
   implementation it can port directly.
2. The pipeline-side scripts under ``bin/`` that POST to ``/api/upload-tarball``
   (e.g. ``bin/upload_to_web_tester.py``) need to attach a valid token.
3. The pytest suite (``tests/test_upload_auth_hmac.py``) cross-checks
   parity with the TS module so a future contributor can't silently break
   the signature contract on one side.

Iron-law: ``constant_time_compare`` uses ``hmac.compare_digest`` so we
don't leak token bytes through timing oracles on hosts that re-implement
the verifier (rare, but possible — the Rust recorder we don't yet
control could be on any HTTP client).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from typing import Mapping

UPLOAD_TOKEN_HEADER = "X-Upload-Token"
TOKEN_PREFIX_LEN = 16
TOKEN_FULL_LEN = 64

_HEX_RE = re.compile(r"^[a-f0-9]+$")


class UploadAuthError(RuntimeError):
    """Raised when the upload-auth config is unusable."""


@dataclass(frozen=True)
class UploadAuthConfig:
    """Runtime config for the upload-auth gate.

    Mirrors the TS ``UploadAuthConfig`` shape. ``from_env`` reads the same
    ``UPLOAD_HMAC_SECRET`` and ``UPLOAD_REQUIRE_TOKEN`` env vars as the
    Next route handler.
    """

    secret: str
    require_token: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "UploadAuthConfig":
        e = env if env is not None else os.environ
        return cls(
            secret=e.get("UPLOAD_HMAC_SECRET", ""),
            require_token=(e.get("UPLOAD_REQUIRE_TOKEN", "").lower() == "true"),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.secret)


def compute_token(tester_id: str, secret: str) -> str:
    """Return the full 64-hex HMAC-SHA256 token for ``tester_id``.

    Raises ``UploadAuthError`` when ``secret`` is empty — callers must
    check ``UploadAuthConfig.is_configured`` first.
    """
    if not secret:
        raise UploadAuthError("UPLOAD_HMAC_SECRET is not configured")
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=tester_id.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def compute_token_prefix(tester_id: str, secret: str) -> str:
    """Return the 16-hex prefix suitable for embedding in the .exe filename."""
    return compute_token(tester_id, secret)[:TOKEN_PREFIX_LEN]


def verify_token(tester_id: str, presented: str, secret: str) -> bool:
    """Constant-time verify the presented token against the expected token.

    Accepts both 16-char prefix and 64-char full digest. Returns False
    early for any malformed input so a caller can short-circuit cleanly.
    """
    if not secret or not presented:
        return False
    p = presented.strip().lower()
    if len(p) not in (TOKEN_PREFIX_LEN, TOKEN_FULL_LEN):
        return False
    if not _HEX_RE.match(p):
        return False
    expected_full = compute_token(tester_id, secret)
    expected = expected_full[:TOKEN_PREFIX_LEN] if len(p) == TOKEN_PREFIX_LEN else expected_full
    # hmac.compare_digest is the canonical constant-time comparator.
    return hmac.compare_digest(p, expected)
