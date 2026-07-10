#!/usr/bin/env python3
"""
GPG manifest signer and verifier.

This module provides GPG signing and verification for manifest.yaml files
to ensure tamper-evidence in build/deployment pipelines.

Usage:
    Sign a manifest:
        python manifest_signer.py sign -i manifest.yaml -o manifest.yaml.sig

    Verify a signature:
        python manifest_signer.py verify -i manifest.yaml -s manifest.yaml.sig
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple


def run_gpg(args: list[str], input_data: Optional[bytes] = None) -> Tuple[int, bytes, bytes]:
    """
    Run GPG command and return exit code, stdout, stderr.

    Args:
        args: List of GPG command-line arguments.
        input_data: Optional bytes to pass to stdin.

    Returns:
        Tuple of (returncode, stdout, stderr).
    """
    result = subprocess.run(
        ["gpg"] + args,
        input=input_data,
        capture_output=True,
    )
    return result.returncode, result.stdout, result.stderr


def sign_manifest(manifest_path: Path, output_path: Path, keyid: Optional[str] = None) -> int:
    """
    Sign a manifest.yaml file with GPG.

    Args:
        manifest_path: Path to the manifest.yaml file to sign.
        output_path: Path where the signature will be written.
        keyid: Optional GPG key ID to use for signing. If None, uses default key.

    Returns:
        0 on success, non-zero on failure.
    """
    if not manifest_path.exists():
        print(f"Error: Manifest file not found: {manifest_path}", file=sys.stderr)
        return 1

    # Build GPG arguments for signing
    gpg_args = [
        "--batch",
        "--yes",
        "--armor",
        "--detach-sign",
        "--output", str(output_path),
    ]

    if keyid:
        gpg_args.extend(["--local-user", keyid])

    gpg_args.append(str(manifest_path))

    returncode, stdout, stderr = run_gpg(gpg_args)

    if returncode != 0:
        print(f"GPG signing failed: {stderr.decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1

    print(f"Successfully signed manifest: {output_path}")
    return 0


def verify_manifest(manifest_path: Path, signature_path: Path) -> int:
    """
    Verify a GPG signature on a manifest.yaml file.

    Args:
        manifest_path: Path to the manifest.yaml file.
        signature_path: Path to the GPG signature file.

    Returns:
        0 if signature is valid, non-zero otherwise.
    """
    if not manifest_path.exists():
        print(f"Error: Manifest file not found: {manifest_path}", file=sys.stderr)
        return 1

    if not signature_path.exists():
        print(f"Error: Signature file not found: {signature_path}", file=sys.stderr)
        return 1

    # Verify the signature
    gpg_args = [
        "--batch",
        "--verify",
        str(signature_path),
        str(manifest_path),
    ]

    returncode, stdout, stderr = run_gpg(gpg_args)

    if returncode != 0:
        decoded = stderr.decode("utf-8", errors="replace")
        print(f"Signature verification failed: {decoded}", file=sys.stderr)
        return 1

    print(f"Signature verified successfully for: {manifest_path}")
    return 0


def main(argv: list[str]) -> int:
    """
    Main entry point for the manifest signer CLI.

    Args:
        argv: Command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        description="GPG sign and verify manifest.yaml files for tamper-evidence"
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Command to execute")

    # Sign subcommand
    sign_parser = subparsers.add_parser("sign", help="Sign a manifest file")
    sign_parser.add_argument(
        "--input", "-i",
        type=Path,
        required=True,
        help="Path to manifest.yaml file to sign"
    )
    sign_parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Path for output signature file"
    )
    sign_parser.add_argument(
        "--keyid", "-k",
        type=str,
        default=None,
        help="GPG key ID to use for signing (default: uses default key)"
    )

    # Verify subcommand
    verify_parser = subparsers.add_parser("verify", help="Verify a manifest signature")
    verify_parser.add_argument(
        "--input", "-i",
        type=Path,
        required=True,
        help="Path to manifest.yaml file"
    )
    verify_parser.add_argument(
        "--signature", "-s",
        type=Path,
        required=True,
        help="Path to signature file"
    )

    args = parser.parse_args(argv)

    if args.command == "sign":
        return sign_manifest(args.input, args.output, args.keyid)
    elif args.command == "verify":
        return verify_manifest(args.input, args.signature)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
