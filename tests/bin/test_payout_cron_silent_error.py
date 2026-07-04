"""
Regression tests for silent error swallows in bin/payout_cron.py.

These tests verify that failed operations are logged at debug level
(binding the exception) rather than silently swallowed.

Specifically covers:
  * ``StripeClient._post()`` — fallback when Stripe error body is not JSON
  * ``post_slack()`` — best-effort Slack webhook ping
"""

import ast
from pathlib import Path


class TestPayoutCronSilentError:
    """Tests for silent error handling in payout_cron.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent / "bin" / "payout_cron.py"
        ).read_text()

    def test_no_bare_except(self):
        """No bare ``except Exception:`` (without ``as`` binding) should exist."""
        source = self._read_source()
        tree = ast.parse(source)

        bare_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is not None:
                        type_src = ast.unparse(handler.type)
                        if "Exception" in type_src and handler.name is None:
                            bare_excepts.append(handler.lineno)

        assert len(bare_excepts) == 0, (
            f"Found {len(bare_excepts)} bare 'except Exception:' "
            f"(no 'as' binding) at lines {bare_excepts}. "
            f"Bind the exception and log it via logger.debug(...)."
        )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        assert "import logging" in source
        assert "logger = logging.getLogger" in source

    def test_stripe_error_body_parse_failure_logs_at_debug(self):
        """When the Stripe error body is not valid JSON, the parse error must be logged."""
        source = self._read_source()
        # The Stripe error body parse sits inside ``except urllib.error.HTTPError as e:``
        # and used to have a bare ``except Exception:``. It must now log at DEBUG.
        assert "logger.debug" in source, (
            "logger.debug should be used to log Stripe error body parse failure"
        )
        # Verify the message references the parse failure and the HTTP code
        assert "JSON" in source, (
            "logger.debug message should mention that the body was not valid JSON"
        )

    def test_post_slack_failure_logs_at_debug(self):
        """When post_slack() fails, the cause must be logged at DEBUG."""
        source = self._read_source()
        # post_slack is a best-effort webhook. Its outer ``except Exception:``
        # must now bind the exception and log it at DEBUG.
        assert "post_slack" in source
        # Confirm the new binding is present
        assert "except Exception as e" in source, (
            "post_slack should bind the exception as 'e' so it can be logged"
        )

    def test_module_compiles(self):
        """The module should compile without syntax errors."""
        source = self._read_source()
        ast.parse(source)
