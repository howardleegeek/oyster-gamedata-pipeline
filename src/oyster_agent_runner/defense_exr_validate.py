#!/usr/bin/env python3
"""
EXR Validator - Scan for NaN clusters and shape mismatch.
Blue team defense for G094 post-write validation.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Lazy imports
OpenEXR = Imath = None

def _imports() -> bool:
    """Lazy import OpenEXR and numpy."""
    global OpenEXR, Imath
    try:
        import OpenEXR
        import Imath
        import numpy
        globals()['np'] = numpy
        return True
    except ImportError:
        return False

def _nan_clusters(arr: 'numpy.ndarray', thresh: int = 3) -> List[Tuple[int, int, int, int]]:
    """Find NaN clusters using BFS."""
    import numpy as np
    from collections import deque
    
    mask = np.isnan(arr)
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    clusters = []
    
    for r in range(h):
        for c in range(w):
            if mask[r, c] and not visited[r, c]:
                q = deque([(r, c)])
                visited[r, c] = True
                pixels = [(r, c)]
                while q:
                    cr, cc = q.popleft()
                    for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
                        nr, nc = cr + dr, cc + dc
                        if 0 <= nr < h and 0 <= nc < w and mask[nr, nc] and not visited[nr, nc]:
                            visited[nr, nc] = True
                            q.append((nr, nc))
                            pixels.append((nr, nc))
                if len(pixels) >= thresh:
                    rows = [p[0] for p in pixels]
                    cols = [p[1] for p in pixels]
                    clusters.append((min(rows), min(cols), max(rows), max(cols)))
    return clusters

def validate(path: Path, thresh: int = 3) -> Dict[str, Any]:
    """Validate EXR file."""
    if not _imports():
        return {"file": str(path), "valid": False, "error": "OpenEXR/numpy required"}
    
    import OpenEXR
    import Imath
    import numpy as np
    
    res = {"file": str(path), "valid": True, "error": None, 
           "nan_clusters": [], "shape_mismatch": None, "channels": []}
    
    try:
        exr = OpenEXR.InputFile(str(path))
        hdr = exr.header()
        chans = list(hdr.get('channels', {}).keys())
        res["channels"] = chans
        
        if not chans:
            res["valid"] = False
            res["error"] = "No channels"
            return res
        
        dw = hdr['dataWindow']
        w, h = dw.max.x - dw.min.x + 1, dw.max.y - dw.min.y + 1
        
        if hdr.get('displayWindow', dw) != dw:
            res["valid"] = False
            res["shape_mismatch"] = "DataWindow != DisplayWindow"
        
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        data = {}
        for cn in chans:
            buf = exr.channel(cn, pt)
            data[cn] = np.frombuffer(buf, dtype=np.float32).reshape((h, w))
        
        shapes = {n: d.shape for n, d in data.items()}
        if len(shapes) > 1:
            ref = next(iter(shapes.values()))
            for n, s in shapes.items():
                if s != ref:
                    res["valid"] = False
                    res["shape_mismatch"] = f"{n}: {s} != {ref}"
                    break
        
        for cn, arr in data.items():
            clusters = _nan_clusters(arr, thresh)
            if clusters:
                res["nan_clusters"].extend([
                    {"channel": cn, "bbox": f"({r1},{c1})-({r2},{c2})", "size": (r2-r1+1)*(c2-c1+1)}
                    for r1, c1, r2, c2 in clusters
                ])
                res["valid"] = False
        
        if data:
            res["total_nans"] = int(sum(np.isnan(arr).sum() for arr in data.values()))
            
    except Exception as e:
        res["valid"] = False
        res["error"] = str(e)
    
    return res

def scan(path: Path, pattern: str = "*.exr", thresh: int = 3, recurse: bool = False) -> List[Dict[str, Any]]:
    """Scan directory for EXR files."""
    files = list(path.rglob(pattern) if recurse else path.glob(pattern))
    return [validate(f, thresh) for f in files if f.is_file()]

def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Validate EXR files for NaN clusters and shape mismatches")
    p.add_argument("path", nargs="?", help="EXR file or directory")
    p.add_argument("-d", "--directory", help="Directory to scan")
    p.add_argument("-o", "--output", help="Output JSON report")
    p.add_argument("-r", "--recursive", action="store_true", help="Scan recursively")
    p.add_argument("-t", "--threshold", type=int, default=3, help="NaN cluster threshold")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress output")
    
    args = p.parse_args(argv)
    
    input_path = args.path or args.directory
    if not input_path:
        p.error("path or --directory required")
    
    path = Path(input_path)
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        return 1
    
    if not _imports():
        print("Error: OpenEXR and numpy required", file=sys.stderr)
        return 1
    
    results = [validate(path, args.threshold)] if path.is_file() else scan(path, "*.exr", args.threshold, args.recursive)
    
    if args.output:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
            json.dump(results, tf, indent=2, default=str)
            tf_path = Path(tf.name)
        tf_path.rename(args.output)
    
    if not args.quiet:
        valid = sum(1 for r in results if r["valid"])
        invalid = len(results) - valid
        print(f"Validated {len(results)} EXR: {valid} valid, {invalid} invalid")
        for r in results:
            if not r["valid"]:
                print(f"  FAILED: {r['file']}")
                if r.get("error"):
                    print(f"    Error: {r['error']}")
                if r.get("shape_mismatch"):
                    print(f"    Shape: {r['shape_mismatch']}")
                if r.get("nan_clusters"):
                    print(f"    NaN clusters: {len(r['nan_clusters'])}")
    
    return 1 if any(not r["valid"] for r in results) else 0

if __name__ == "__main__":
    sys.exit(main())