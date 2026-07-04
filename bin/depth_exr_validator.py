#!/usr/bin/env python3
"""Validate depth/*.exr files by checking magic bytes and structural integrity."""

import argparse
import logging
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)


def check_magic_byte(filepath):
    """Check if file has EXR magic byte 0x762f3101."""
    try:
        with open(filepath, 'rb') as f:
            return f.read(4) == b'\x76\x2f\x31\x01'
    except Exception as _exc:
        _LOG.debug("check_magic_byte: failed to read %r: %s", filepath, _exc, exc_info=True)
        return False


def check_structural(filepath):
    """Lazy-import OpenEXR and perform structural check."""
    try:
        import OpenEXR
        with OpenEXR.InputFile(str(filepath)) as exr_file:
            header = exr_file.header()
            if not header or not header.get('channels'):
                return False
        return True
    except ImportError:
        return True  # OpenEXR not available, skip structural check
    except Exception as _exc:
        _LOG.debug("check_structural: failed to inspect %r: %s", filepath, _exc, exc_info=True)
        return False


def validate_exr_files(depth_dir):
    """Walk depth directory and validate all .exr files."""
    depth_path = Path(depth_dir)
    if not depth_path.exists():
        print(f"Error: Directory '{depth_dir}' does not exist", file=sys.stderr)
        sys.exit(1)
    
    exr_files = sorted(depth_path.rglob('*.exr'))
    total = len(exr_files)
    valid = 0
    invalid_files = []
    
    if total == 0:
        return {'total': 0, 'valid': 0, 'invalid_files': []}
    
    # Files for structural check: first, middle, last
    structural_indices = {0, total - 1, total // 2}
    
    for idx, filepath in enumerate(exr_files):
        if not check_magic_byte(filepath):
            invalid_files.append(str(filepath))
            continue
        if idx in structural_indices and not check_structural(filepath):
            invalid_files.append(str(filepath))
            continue
        valid += 1
    
    return {'total': total, 'valid': valid, 'invalid_files': invalid_files}


def main():
    parser = argparse.ArgumentParser(description='Validate depth/*.exr files')
    parser.add_argument('--depth-dir', required=True, help='Directory containing depth EXR files')
    args = parser.parse_args()
    
    result = validate_exr_files(args.depth_dir)
    
    print(f"Total: {result['total']}")
    print(f"Valid: {result['valid']}")
    if result['invalid_files']:
        print(f"Invalid files ({len(result['invalid_files'])}):")
        for f in result['invalid_files']:
            print(f"  - {f}")
    else:
        print("Invalid files: []")
    
    sys.exit(0 if not result['invalid_files'] else 1)


if __name__ == '__main__':
    main()
