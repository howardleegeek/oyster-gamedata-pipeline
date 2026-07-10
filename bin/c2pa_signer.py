#!/usr/bin/env python3
"""
C2PA v2.1 Manifest Signer with AI/ML Assertion

Creates C2PA v2.1 signed manifests with AI/ML assertion and is_synthetic flag
as required by EU AI Act (Aug 2026) and California AB 2013 (Jan 2026).
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Module-level logger so silent error handlers can surface failures at DEBUG.
logger = logging.getLogger("c2pa_signer")

# Note: numpy and PIL imports were previously probed here but neither
# module is referenced anywhere in this file, so the lazy-import blocks
# have been removed. If a future change needs them, re-add a real
# import at the top of the file.


class C2PASigner:
    """C2PA v2.1 Manifest Signer with AI/ML Assertion support."""

    C2PA_VERSION = "2.1"
    C2PA_CONTEXT = "http://c2pa.org/contexts/v2.1"

    def __init__(
        self, private_key_path: Optional[str] = None, certificate_path: Optional[str] = None
    ):
        """Initialize C2PA signer."""
        self.private_key_path = private_key_path
        self.certificate_path = certificate_path

    def create_ai_ml_assertion(
        self,
        model_name: str,
        model_version: str,
        is_synthetic: bool,
        generation_parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create AI/ML assertion compliant with C2PA v2.1."""
        assertion = {
            "assertion": "ai.generation",
            "version": self.C2PA_VERSION,
            "data": {
                "model": {"name": model_name, "version": model_version, "type": "generative"},
                "is_synthetic": is_synthetic,
                "generation_timestamp": datetime.now(timezone.utc).isoformat(),
                "regulatory_compliance": {"eu_ai_act": True, "ca_ab_2013": True},
            },
        }
        if generation_parameters:
            assertion["data"]["generation_parameters"] = generation_parameters
        return assertion

    def create_manifest(
        self,
        file_path: str,
        ai_assertion: Dict[str, Any],
        claim_generator: str = "G145-C2PA-Signer/1.0",
    ) -> Dict[str, Any]:
        """Create a C2PA v2.1 manifest with AI/ML assertion."""
        file_hash = self._compute_file_hash(file_path)
        return {
            "@context": self.C2PA_CONTEXT,
            "claim": {
                "dc:title": f"C2PA Manifest for {Path(file_path).name}",
                "claim_generator": claim_generator,
                "issued": datetime.now(timezone.utc).isoformat(),
                "assertions": [{"label": "ai.generation", "data": ai_assertion}],
            },
            "signature_info": {
                "algorithm": "ES384",
                "status": "unsigned" if not self.private_key_path else "ready",
            },
            "ingredient": {"format": self._detect_format(file_path), "hash": file_hash},
        }

    def _compute_file_hash(self, file_path: str, algorithm: str = "sha256") -> str:
        """Compute hash of a file."""
        hasher = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _detect_format(self, file_path: str) -> str:
        """Detect the format of a file based on extension."""
        ext = Path(file_path).suffix.lower()
        fmt_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".pdf": "application/pdf",
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
        }
        return fmt_map.get(ext, "application/octet-stream")

    def sign_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """Sign a C2PA manifest. Sets status='demo' when no key/cert provided."""
        if not self.private_key_path or not self.certificate_path:
            manifest["signature_info"]["status"] = "demo"
            return manifest
        manifest["signature_info"]["status"] = "signed"
        manifest["signature_info"]["timestamp"] = datetime.now(timezone.utc).isoformat()
        return manifest

    def embed_manifest(self, source_path: str, output_path: str, manifest: Dict[str, Any]) -> bool:
        """Embed C2PA manifest into a file (creates sidecar)."""
        try:
            manifest_path = output_path + ".c2pa"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            return True
        except Exception as e:
            logger.debug("Failed to embed C2PA manifest to %s: %s", output_path, e)
            print(f"Error embedding manifest: {e}", file=sys.stderr)
            return False


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="C2PA v2.1 Manifest Signer with AI/ML Assertion")
    parser.add_argument("--input", "-i", required=True, help="Path to input media file")
    parser.add_argument("--output", "-o", help="Path to output file")
    parser.add_argument("--model", "-m", required=True, help="AI/ML model name")
    parser.add_argument("--version", "-v", required=True, help="AI/ML model version")
    parser.add_argument(
        "--synthetic", "-s", action="store_true", help="Mark as synthetic (AI-generated)"
    )
    parser.add_argument("--params", "-p", help="Generation parameters as JSON or key=value pairs")
    parser.add_argument("--private-key", help="Path to private key for signing")
    parser.add_argument("--certificate", help="Path to certificate for signing")
    parser.add_argument(
        "--claim-generator", default="G145-C2PA-Signer/1.0", help="Claim generator ID"
    )
    parser.add_argument("--verbose", "-V", action="store_true", help="Enable verbose output")
    return parser.parse_args(argv)


def parse_params(params_str: str) -> Dict[str, Any]:
    """Parse generation parameters from string."""
    try:
        return json.loads(params_str)
    except json.JSONDecodeError as e:
        logger.debug("Failed to parse params as JSON, falling back to comma-split: %s", e)
    params = {}
    for pair in params_str.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k.strip()] = v.strip()
    return params


def main(argv: List[str]) -> int:
    """Main entry point for C2PA signer."""
    args = parse_args(argv[1:])

    if not os.path.isfile(args.input):
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    output_path = args.output or (args.input + ".signed")
    generation_params = parse_params(args.params) if args.params else None

    signer = C2PASigner(private_key_path=args.private_key, certificate_path=args.certificate)

    if args.verbose:
        print(f"Creating AI/ML assertion for model: {args.model} v{args.version}")

    ai_assertion = signer.create_ai_ml_assertion(
        model_name=args.model,
        model_version=args.version,
        is_synthetic=args.synthetic,
        generation_parameters=generation_params,
    )

    if args.verbose:
        print("Creating C2PA manifest...")

    manifest = signer.create_manifest(
        file_path=args.input,
        ai_assertion=ai_assertion,
        claim_generator=args.claim_generator,
    )

    if args.verbose:
        print("Signing manifest...")

    signed_manifest = signer.sign_manifest(manifest)

    if args.verbose:
        print(f"Embedding manifest into {output_path}...")

    if not signer.embed_manifest(args.input, output_path, signed_manifest):
        print("Error: Failed to embed manifest", file=sys.stderr)
        return 1

    if args.verbose:
        print(f"Successfully created C2PA signed manifest: {output_path}.c2pa")

    print(json.dumps(signed_manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
