#!/usr/bin/env python3
"""
Edge case test: action_camera.json with empty records list.

The adapter must fail-closed (return error) rather than crash when
given an empty records list.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)


class AdapterError(Exception):
    """Raised when adapter fails to process records."""
    pass


class RecordAdapter:
    """Adapter for processing action_camera JSON records."""

    def load(self, filepath: str) -> Dict[str, Any]:
        """Load and parse JSON file."""
        if not os.path.exists(filepath):
            raise AdapterError(f"File not found: {filepath}")
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def validate(self, data: Dict[str, Any]) -> bool:
        """Validate input data. Fail-closed on empty records."""
        if not isinstance(data, dict):
            raise AdapterError("Invalid data: expected dict")
        if 'records' not in data:
            raise AdapterError("Missing required field: records")
        records = data['records']
        if not isinstance(records, list):
            raise AdapterError("Field 'records' must be a list")
        if len(records) == 0:
            raise AdapterError("Empty records list: must contain at least one record")
        return True

    def process(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process records and return results."""
        self.validate(data)
        return data.get('records', [])


def create_test_file(records: List[Dict[str, Any]]) -> str:
    """Create temporary JSON file with given records."""
    fd, path = tempfile.mkstemp(suffix='.json', prefix='action_camera_')
    try:
        data = {'source': 'action_camera', 'timestamp': '2024-01-01T00:00:00Z', 'records': records}
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        _LOG.debug("create_test_file: failed to write %s: %s", path, e)
        os.close(fd)
        raise
    return path


def run_test(verbose: bool = False) -> int:
    """Run edge case test for empty records. Returns 0 on success."""
    adapter = RecordAdapter()

    # Test 1: Empty records list should fail-closed
    if verbose:
        print("Test 1: Empty records list...")
    test_file = create_test_file([])
    try:
        data = adapter.load(test_file)
        adapter.validate(data)
        print("FAIL: Expected AdapterError for empty records", file=sys.stderr)
        return 1
    except AdapterError as e:
        if verbose:
            print(f"  Caught expected error: {e}")
    except Exception as e:
        print(f"FAIL: Unexpected exception: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        os.unlink(test_file)

    # Test 2: Non-empty records should succeed
    if verbose:
        print("Test 2: Non-empty records list...")
    test_file = create_test_file([{'id': 1, 'value': 'test'}])
    try:
        data = adapter.load(test_file)
        adapter.validate(data)
        result = adapter.process(data)
        if len(result) != 1:
            print(f"FAIL: Expected 1 record, got {len(result)}", file=sys.stderr)
            return 1
        if verbose:
            print(f"  Processed {len(result)} record(s)")
    except Exception as e:
        print(f"FAIL: Unexpected exception: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        os.unlink(test_file)

    if verbose:
        print("All tests passed!")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description='Edge case test: empty records handling')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    args = parser.parse_args(argv)
    return run_test(verbose=args.verbose)


if __name__ == '__main__':
    sys.exit(main())
