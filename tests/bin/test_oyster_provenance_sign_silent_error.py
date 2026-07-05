"""
Regression test: oyster_provenance/sign.py silent error surfacing.

This test verifies that the bare `except Exception:` block in
verify_signature() has been replaced with a bound exception and
debug logging.

Issue: verify_signature() had a bare `except Exception:` that silently
swallowed all verification errors (bad signature, malformed hex, missing
key, etc.) and returned False with no diagnostic. Fixed by binding to
`e` and logging at DEBUG so we can distinguish "signature did not match"
from "we could not even attempt verification."
"""

import ast
import logging
import os
import sys
import tempfile
from pathlib import Path

# Ensure oyster_provenance is in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from oyster_provenance import sign  # noqa: E402


SOURCE_FILE = Path(__file__).parent.parent.parent / "oyster_provenance" / "sign.py"


def _read_source() -> str:
    with open(SOURCE_FILE, "r") as f:
        return f.read()


class TestSignSilentErrorSurfacing:
    """Tests for silent error surfacing in sign.py verify_signature()."""

    def test_no_bare_except_in_verify_signature(self):
        """AST check: verify_signature must not have a bare except Exception:."""
        source = _read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "verify_signature":
                for stmt in ast.walk(node):
                    if isinstance(stmt, ast.Try):
                        for handler in stmt.handlers:
                            # Bare except: exc.type is None, or `Exception` with no name binding
                            if handler.type is None:
                                pytest_fail_unbound(handler, "bare except (no type)")
                            if (
                                isinstance(handler.type, ast.Name)
                                and handler.type.id == "Exception"
                                and handler.name is None
                            ):
                                pytest_fail_unbound(
                                    handler,
                                    "`except Exception:` without binding",
                                )
                return  # found function, stop walking

        raise AssertionError("verify_signature function not found in sign.py")

    def test_logger_imported(self):
        """Module must define a module-level logger via logging.getLogger(__name__)."""
        source = _read_source()
        assert "import logging" in source, "sign.py must import logging"
        assert "logger = logging.getLogger(__name__)" in source, (
            "sign.py must define module-level logger"
        )

    def test_verify_signature_logs_on_failure(self, caplog):
        """verify_signature must log at DEBUG when verification raises."""
        # Set up a real signing key pair so we don't have to mock cryptography internals.
        with tempfile.TemporaryDirectory() as tmpdir:
            private_path = os.path.join(tmpdir, "signing_key.pem")
            public_path = os.path.join(tmpdir, "signing_key.pub")

            keypair = sign.SigningKey.generate()
            keypair.save(
                private_key_path=Path(private_path),
                public_key_path=Path(public_path),
            )

            # Bogus hex signature — public_key.verify() will raise InvalidSignature.
            bogus_signature = "00" * 64

            with caplog.at_level(logging.DEBUG, logger="oyster_provenance.sign"):
                result = sign.verify_signature(
                    data=b"hello",
                    signature_hex=bogus_signature,
                    public_key_path=Path(public_path),
                )

            # Public contract preserved: bad signature returns False.
            assert result is False, "Bad signature must still return False"

            # New contract: we log the underlying reason at DEBUG.
            assert any(
                "Signature verification failed" in record.message
                for record in caplog.records
            ), "Expected DEBUG log naming the verification failure"

    def test_verify_signature_does_not_log_on_success(self, caplog):
        """verify_signature must not log on a successful verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            private_path = os.path.join(tmpdir, "signing_key.pem")
            public_path = os.path.join(tmpdir, "signing_key.pub")

            keypair = sign.SigningKey.generate()
            keypair.save(
                private_key_path=Path(private_path),
                public_key_path=Path(public_path),
            )

            data = b"some-payload"
            good_signature = keypair.sign(data)

            with caplog.at_level(logging.DEBUG, logger="oyster_provenance.sign"):
                result = sign.verify_signature(
                    data=data,
                    signature_hex=good_signature,
                    public_key_path=Path(public_path),
                )

            assert result is True
            assert not any(
                "Signature verification failed" in record.message
                for record in caplog.records
            ), "Successful verification should not emit DEBUG log"

    def test_module_compiles(self):
        """Sanity check: sign.py must compile without syntax errors."""
        source = _read_source()
        compile(source, str(SOURCE_FILE), "exec")


def pytest_fail_unbound(handler, label):
    """Helper: convert a detected bare except into a clear pytest failure."""
    import pytest

    pytest.fail(
        f"Line {handler.lineno}: {label} in verify_signature — must bind to `e` "
        f"and log via logger.debug()"
    )
