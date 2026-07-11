#!/usr/bin/env python3
"""G127 · Idempotency Token Generator — per-clip UUID with at-least-once dedup."""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Union


class IdempotencyTokenGenerator:
    """Generate idempotency tokens for clip deduplication on ingest path."""

    def __init__(self, namespace: Optional[uuid.UUID] = None) -> None:
        self.namespace = namespace or uuid.NAMESPACE_DNS

    def from_content(self, content: bytes, clip_name: str = "") -> str:
        """Deterministic token from raw clip bytes + name + UTC timestamp (UUID v5)."""
        h = hashlib.sha256(content).hexdigest()
        ts = datetime.now(timezone.utc).isoformat()
        return str(uuid.uuid5(self.namespace, f"{h}:{clip_name}:{ts}"))

    def from_metadata(self, metadata: Dict[str, Union[str, int, float]]) -> str:
        """Deterministic token from metadata dict (sorted-key JSON, UUID v5)."""
        return str(uuid.uuid5(self.namespace, json.dumps(metadata, sort_keys=True, default=str)))

    def random(self) -> str:
        """Random UUID v4 token."""
        return str(uuid.uuid4())

    @staticmethod
    def validate(token: str) -> bool:
        """Return True if *token* is a well-formed UUID string."""
        try:
            uuid.UUID(token)
            return True
        except (ValueError, AttributeError):
            return False


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(description="Generate idempotency tokens for clip deduplication")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--random", action="store_true", help="Random UUID v4")
    g.add_argument("--content", metavar="FILE", help="Token from file content")
    g.add_argument("--metadata", metavar="JSON", help="Token from JSON metadata")
    p.add_argument("--validate", metavar="TOKEN", help="Validate a UUID token")
    p.add_argument("--clip-name", default="", help="Clip name for content tokens")
    p.add_argument("--namespace", help="Custom UUID namespace (hex)")
    p.add_argument("--output", choices=["token", "json", "full"], default="token")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    """Main entry point. Returns 0 on success, 1 on error."""
    args = parse_args(argv)

    # Validation-only mode
    if args.validate is not None:
        ok = IdempotencyTokenGenerator.validate(args.validate)
        if args.output == "json":
            print(json.dumps({"token": args.validate, "valid": ok}))
        else:
            print(f"{'valid' if ok else 'invalid'}: {args.validate}")
        return 0 if ok else 1

    # Parse namespace
    ns: Optional[uuid.UUID] = None
    if args.namespace:
        try:
            ns = uuid.UUID(args.namespace)
        except ValueError:
            print(f"Error: invalid namespace '{args.namespace}'", file=sys.stderr)
            return 1

    gen = IdempotencyTokenGenerator(ns)
    token: Optional[str] = None
    meta: Dict[str, str] = {}

    if args.random:
        token = gen.random()
        meta["mode"] = "random"
    elif args.content:
        p = Path(args.content)
        if not p.is_file():
            print(f"Error: file not found: {args.content}", file=sys.stderr)
            return 1
        token = gen.from_content(p.read_bytes(), clip_name=args.clip_name)
        meta["mode"] = "content"
        meta["source"] = args.content
    elif args.metadata:
        try:
            md = json.loads(args.metadata)
        except json.JSONDecodeError as exc:
            print(f"Error: invalid JSON: {exc}", file=sys.stderr)
            return 1
        token = gen.from_metadata(md)
        meta["mode"] = "metadata"
    else:
        token = gen.random()
        meta["mode"] = "random"

    meta["token"] = token
    meta["timestamp"] = datetime.now(timezone.utc).isoformat()

    if args.output == "json":
        print(json.dumps(meta))
    elif args.output == "full":
        for k, v in meta.items():
            print(f"{k}: {v}")
    else:
        print(token)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
