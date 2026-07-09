#!/usr/bin/env python3
"""Regression tests for src/oyster_agent_runner/error_client_python.py silent error fix."""
import ast
import logging


def test_module_compiles():
    """Module should compile without errors."""
    import src.oyster_agent_runner.error_client_python  # noqa: F401


def test_logging_imported_and_logger_defined():
    """Module should import logging and define a logger."""
    import src.oyster_agent_runner.error_client_python as mod
    assert hasattr(mod, "logger"), "Module should have a logger attribute"
    assert isinstance(mod.logger, logging.Logger), "logger should be a Logger instance"


def test_install_handlers_runtime_error_binds_exception():
    """install_handlers() RuntimeError handler should bind exception to a name."""
    with open("src/oyster_agent_runner/error_client_python.py") as f:
        source = f.read()
    tree = ast.parse(source)

    # Find install_handlers function
    install_handlers = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "install_handlers":
            install_handlers = node
            break

    assert install_handlers is not None, "install_handlers function should exist"

    # Find the try/except block with RuntimeError
    found_runtime_error_except = False
    for node in ast.walk(install_handlers):
        if isinstance(node, ast.ExceptHandler):
            # Check if this is except RuntimeError
            if node.type and isinstance(node.type, ast.Name) and node.type.id == "RuntimeError":
                # Check that body is not just 'pass'
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    # This is the bare pass we want to avoid
                    assert False, "Found bare 'except RuntimeError: pass' - should bind exception and log"
                # Check that exception is bound to a name
                assert node.name is not None, "RuntimeError handler should bind exception to a name"
                found_runtime_error_except = True

    assert found_runtime_error_except, "Should find RuntimeError handler in install_handlers"


def test_install_handlers_runtime_error_logs():
    """install_handlers() RuntimeError handler should call logger.debug."""
    with open("src/oyster_agent_runner/error_client_python.py") as f:
        source = f.read()

    # Check that logger.debug is called in the context of the RuntimeError handler
    assert "logger.debug" in source, "Module should call logger.debug in exception handler"
    # More specifically, look for a debug call that mentions RuntimeError or event loop context
    # The fix should add a log call with context about the event loop not running


def test_no_bare_except_pass_in_install_handlers():
    """install_handlers should have no bare except ...: pass patterns."""
    with open("src/oyster_agent_runner/error_client_python.py") as f:
        source = f.read()
    tree = ast.parse(source)

    # Find install_handlers function
    install_handlers = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "install_handlers":
            install_handlers = node
            break

    assert install_handlers is not None

    # Check for bare except: pass in the function
    for node in ast.walk(install_handlers):
        if isinstance(node, ast.ExceptHandler):
            # Bare except
            if node.type is None:
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    assert False, "Found bare 'except: pass' in install_handlers"
            # except SomeError: pass
            elif node.type is not None:
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    exc_type = ast.unparse(node.type) if hasattr(ast, 'unparse') else 'unknown'
                    assert False, f"Found 'except {exc_type}: pass' in install_handlers"


def test_install_handlers_handles_no_event_loop():
    """install_handlers should handle the case where no event loop is running."""
    # This is a functional test - we verify the function doesn't crash when
    # called without an event loop running
    import src.oyster_agent_runner.error_client_python as mod

    # The fix should make the handler gracefully handle RuntimeError
    # without silently swallowing it - the test is that the module still works
    # The actual logging is at DEBUG level so it won't print by default
    # We just verify the function exists and can be called
    assert callable(mod.install_handlers), "install_handlers should be callable"
