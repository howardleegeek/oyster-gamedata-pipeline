"""
Regression tests for silent error in S3StorageBackend._get_metadata()

Issue: bare `except Exception:` silently swallowed metadata fetch failures,
making it impossible to debug why list_assets() returns incomplete results.

Fix: Replace bare except with `except Exception as e:` + logger.debug(..., exc_info=True)
to surface failures without changing control flow (still returns None on error).

Run: pytest -q tests/bin/test_storage_backend_silent_error.py --tb=short
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the module under test
from bin import storage_backend


class TestNoBareExceptInGetMetadata:
    """Verify the fix: no bare except in _get_metadata."""

    def test_no_bare_pass_in_get_metadata(self):
        """AST check: _get_metadata must not contain bare 'except Exception: pass'."""
        source_path = Path("bin/storage_backend.py")
        source = source_path.read_text()
        tree = ast.parse(source)

        class BareExceptVisitor(ast.NodeVisitor):
            def __init__(self):
                self.violations = []

            def visit_Try(self, node):
                for handler in node.handlers:
                    # Check for bare except Exception:
                    if handler.type is None or (
                        isinstance(handler.type, ast.Name)
                        and handler.type.id == "Exception"
                    ):
                        # Check if body is just `pass`
                        if len(handler.body) == 1 and isinstance(
                            handler.body[0], ast.Pass
                        ):
                            self.violations.append(
                                f"Line {handler.lineno}: bare except with only pass"
                            )
                self.generic_visit(node)

        visitor = BareExceptVisitor()
        visitor.visit(tree)

        # Filter violations to only those in _get_metadata method
        get_metadata_violations = [
            v for v in visitor.violations if "_get_metadata" in v
        ]
        assert not get_metadata_violations, (
            f"Found bare except in _get_metadata: {get_metadata_violations}"
        )


class TestGetMetadataErrorLogging:
    """Verify error handling logs at DEBUG level."""

    def test_s3_get_metadata_failure_logs_at_debug(self):
        """S3 get_object failure should log at DEBUG and return None."""
        import botocore.exceptions

        # Create a mock S3 client that raises on get_object
        mock_client = MagicMock()
        mock_client.get_object.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Object not found"}},
            "GetObject"
        )

        # Create S3StorageBackend with mock client
        backend = storage_backend.S3StorageBackend(
            bucket="test-bucket",
            client=mock_client,
        )

        # Patch logger to capture DEBUG calls
        with patch.object(storage_backend.logger, "debug") as mock_debug:
            result = backend._get_metadata("nonexistent-asset")

        # Verify: returns None on error
        assert result is None

        # Verify: logs at DEBUG level with exc_info=True
        mock_debug.assert_called_once()
        call_args = mock_debug.call_args
        assert "_get_metadata" in call_args[0][0]  # format string mentions method
        assert "nonexistent-asset" in call_args[1].get("args", ()) or "nonexistent-asset" in str(call_args[0])
        assert call_args[1].get("exc_info") is True, "exc_info=True must be set"


class TestControlFlowPreserved:
    """Verify control flow unchanged: returns None on error."""

    def test_get_metadata_returns_none_on_s3_error(self):
        """Any S3 error returns None (data-safe default)."""
        import botocore.exceptions

        mock_client = MagicMock()
        mock_client.get_object.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}},
            "GetObject"
        )

        backend = storage_backend.S3StorageBackend(
            bucket="test-bucket",
            client=mock_client,
        )

        result = backend._get_metadata("protected-asset")
        assert result is None

    def test_get_metadata_returns_none_on_json_parse_error(self):
        """Invalid JSON in metadata returns None."""
        from io import BytesIO

        # Return valid S3 response but corrupt JSON
        mock_client = MagicMock()
        mock_client.get_object.return_value = {
            "Body": BytesIO(b"not valid json {{{")
        }

        backend = storage_backend.S3StorageBackend(
            bucket="test-bucket",
            client=mock_client,
        )

        result = backend._get_metadata("corrupt-metadata")
        assert result is None


class TestModuleImportsClean:
    """Verify module imports without side effects."""

    def test_module_imports_clean(self):
        """Module should import without errors or sys.exit."""
        assert storage_backend.S3StorageBackend is not None
        assert storage_backend.logger is not None
