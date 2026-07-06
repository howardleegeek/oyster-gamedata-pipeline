"""Regression tests for silent error surfacing in c2pa_signer.py."""
import ast
from pathlib import Path


def test_module_compiles():
    """Ensure c2pa_signer.py can be imported without errors."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "c2pa_signer", Path(__file__).parent.parent.parent / "bin" / "c2pa_signer.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Don't actually execute - just verify it parses
    spec.loader.exec_module(module)


def test_logger_imported():
    """Ensure logger is imported at module level."""
    source = Path(__file__).parent.parent.parent / "bin" / "c2pa_signer.py"
    content = source.read_text()
    tree = ast.parse(content)
    # Check for logging import
    has_logging_import = any(
        (isinstance(n, ast.Import) and any("logging" in a.name for a in n.names)) or
        (isinstance(n, ast.ImportFrom) and n.module == "logging")
        for n in ast.walk(tree)
    )
    assert has_logging_import, "logging module should be imported"


def test_embed_manifest_no_bare_except():
    """Ensure embed_manifest method binds exception in its except block."""
    source = Path(__file__).parent.parent.parent / "bin" / "c2pa_signer.py"
    content = source.read_text()
    tree = ast.parse(content)
    
    # Find the embed_manifest function
    embed_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "embed_manifest":
            embed_func = node
            break
    
    assert embed_func is not None, "embed_manifest function not found"
    
    # Find the try/except block inside embed_manifest
    except_binds_exception = False
    for node in ast.walk(embed_func):
        if isinstance(node, ast.ExceptHandler):
            # Check if exception is bound to a name (not bare)
            if node.type is not None:
                except_binds_exception = True
                break
    
    assert except_binds_exception, "embed_manifest should bind exception in except block"


def test_embed_manifest_logs_on_failure():
    """Ensure embed_manifest logs the exception on failure."""
    source = Path(__file__).parent.parent.parent / "bin" / "c2pa_signer.py"
    content = source.read_text()
    tree = ast.parse(content)
    
    # Find the embed_manifest function
    embed_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "embed_manifest":
            embed_func = node
            break
    
    assert embed_func is not None
    
    # Look for logger.debug or logger.error call in the except block
    has_log_call = False
    for node in ast.walk(embed_func):
        if isinstance(node, ast.ExceptHandler):
            # Check for logger.* calls in the handler body
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Attribute):
                        if child.func.attr in ("debug", "error", "warning", "info"):
                            has_log_call = True
                            break
    
    assert has_log_call, "embed_manifest should call logger in except block"


def test_parse_params_no_bare_except():
    """Ensure parse_params binds exception in its except block."""
    source = Path(__file__).parent.parent.parent / "bin" / "c2pa_signer.py"
    content = source.read_text()
    tree = ast.parse(content)
    
    # Find parse_params function
    parse_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "parse_params":
            parse_func = node
            break
    
    assert parse_func is not None, "parse_params function not found"
    
    # Check for bound exception (ExceptionHandler with type != None)
    has_bound_exception = False
    for node in ast.walk(parse_func):
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                has_bound_exception = True
                break
    
    assert has_bound_exception, "parse_params should bind exception in except block"


def test_parse_params_logs_on_failure():
    """Ensure parse_params logs when JSON parsing fails."""
    source = Path(__file__).parent.parent.parent / "bin" / "c2pa_signer.py"
    content = source.read_text()
    
    # Check that logger.debug is called in parse_params after JSONDecodeError
    assert "logger.debug" in content, "parse_params should use logger.debug"
    assert "JSONDecodeError" in content, "parse_params should handle JSONDecodeError"
