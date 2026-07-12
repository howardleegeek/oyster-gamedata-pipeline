#!/usr/bin/env python3
"""
macOS Notarization Automation Script

Automates Apple Developer ID notarization workflow for .pkg installers and .app bundles:
- Codesigning with Developer ID certificates
- Submission to Apple notarization service (notarytool)
- Stapling notarization tickets

Usage:
    python3 macos_notarization.py --app /path/to/App.app --bundle-id com.example.app
    python3 macos_notarization.py --pkg /path/to/Installer.pkg --bundle-id com.example.pkg
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple


class NotarizationError(Exception):
    """Custom exception for notarization failures."""
    pass


class NotarizationWorkflow:
    """Handles macOS notarization workflow for .pkg and .app files."""

    def __init__(
        self,
        bundle_id: str,
        apple_id: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_id: Optional[str] = None,
        issuer_id: Optional[str] = None,
        team_id: Optional[str] = None,
        sign_identity: Optional[str] = None,
        verbose: bool = False
    ) -> None:
        """Initialize notarization workflow with authentication credentials."""
        self.bundle_id = bundle_id
        self.apple_id = apple_id
        self.password = password
        self.api_key = api_key
        self.api_key_id = api_key_id
        self.issuer_id = issuer_id
        self.team_id = team_id
        self.sign_identity = sign_identity
        self.verbose = verbose
        self._temp_dir: Optional[str] = None

    def log(self, message: str, level: str = "INFO") -> None:
        """Log message with optional verbosity."""
        if self.verbose or level in ("ERROR", "WARNING"):
            stream = sys.stderr if level == "ERROR" else sys.stdout
            print(f"[{level}] {message}", file=stream)

    def run_cmd(self, cmd: List[str], check: bool = True) -> Tuple[int, str, str]:
        """Execute a shell command safely without shell=True."""
        self.log(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            raise NotarizationError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
        return result.returncode, result.stdout, result.stderr

    def find_signing_identity(self) -> Optional[str]:
        """Find Developer ID signing certificate in keychain."""
        _, stdout, _ = self.run_cmd(["security", "find-identity", "-v", "-p", "codesigning"], check=False)
        for line in stdout.splitlines():
            if "Developer ID Application" in line and '"' in line:
                return line.split('"')[1]
        return None

    def codesign_app(self, app_path: Path) -> None:
        """Codesign an .app bundle with runtime options."""
        identity = self.sign_identity or self.find_signing_identity()
        if not identity:
            raise NotarizationError("No signing identity found")
        self.log(f"Signing {app_path} with identity: {identity}")
        # Sign the app bundle with deep flag for embedded content
        self.run_cmd([
            "codesign", "--sign", identity, "--force", "--deep",
            "--options", "runtime", "--timestamp", str(app_path)
        ])

    def codesign_pkg(self, pkg_path: Path) -> None:
        """Codesign a .pkg installer."""
        identity = self.sign_identity or self.find_signing_identity()
        if not identity:
            raise NotarizationError("No signing identity found")
        self.log(f"Signing {pkg_path} with identity: {identity}")
        signed_path = Path(str(pkg_path) + ".signed")
        self.run_cmd(["productsign", "--sign", identity, str(pkg_path), str(signed_path)])
        shutil.move(str(signed_path), str(pkg_path))

    def verify_signature(self, target_path: Path) -> bool:
        """Verify code signature is valid."""
        ret, _, stderr = self.run_cmd([
            "codesign", "--verify", "--deep", "--strict", str(target_path)
        ], check=False)
        if ret == 0:
            self.log(f"Signature verified: {target_path}")
            return True
        self.log(f"Signature verification failed: {stderr}", level="WARNING")
        return False

    def build_auth_args(self) -> List[str]:
        """Build authentication arguments for notarytool."""
        if self.api_key and self.api_key_id and self.issuer_id:
            return ["--key", self.api_key, "--key-id", self.api_key_id, "--issuer", self.issuer_id]
        if self.apple_id and self.password and self.team_id:
            return ["--apple-id", self.apple_id, "--password", self.password, "--team-id", self.team_id]
        return ["--keychain-profile", "notarytool-profile"]

    def submit_notarization(self, target_path: Path) -> str:
        """Submit file for notarization and wait for completion."""
        self.log(f"Submitting {target_path} for notarization...")
        # Create zip for .app bundles
        submit_path = target_path
        if target_path.suffix == ".app":
            self._temp_dir = tempfile.mkdtemp(prefix="notarization_")
            zip_path = Path(self._temp_dir) / f"{target_path.stem}.zip"
            self.run_cmd(["ditto", "-c", "-k", "--keepParent", str(target_path), str(zip_path)])
            submit_path = zip_path

        cmd = ["xcrun", "notarytool", "submit", str(submit_path)] + self.build_auth_args() + ["--wait", "--output-format", "json"]
        _, stdout, _ = self.run_cmd(cmd, check=False)
        try:
            result = json.loads(stdout)
            status = result.get("status", "unknown")
            submission_id = result.get("id", "unknown")
            self.log(f"Submission ID: {submission_id}, Status: {status}")
            if status == "Accepted":
                return submission_id
            raise NotarizationError(f"Notarization failed with status: {status}")
        except json.JSONDecodeError as e:
            raise NotarizationError(f"Failed to parse notarytool output: {stdout}") from e

    def staple(self, target_path: Path) -> bool:
        """Staple notarization ticket to app/pkg."""
        self.log(f"Stapling notarization ticket to {target_path}")
        ret, _, stderr = self.run_cmd(["xcrun", "stapler", "staple", str(target_path)], check=False)
        if ret == 0:
            self.log("Stapling successful")
            return True
        self.log(f"Stapling failed: {stderr}", level="ERROR")
        return False

    def verify_notarization(self, target_path: Path) -> bool:
        """Verify notarization is valid."""
        ret, _, _ = self.run_cmd(["spctl", "--assess", "--verbose=4", "--type", "install", str(target_path)], check=False)
        if ret == 0:
            self.log(f"Notarization verified: {target_path}")
            return True
        # Alternative check for .app
        if target_path.suffix == ".app":
            ret, _, _ = self.run_cmd(["codesign", "--test-requirement=notarization", str(target_path)], check=False)
            return ret == 0
        return False

    def cleanup(self) -> None:
        """Clean up temporary files."""
        if self._temp_dir and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir)
            self._temp_dir = None

    def run(self, target_path: Path, is_pkg: bool = False) -> bool:
        """Execute full notarization workflow."""
        try:
            self.log("Step 1: Code signing...")
            if is_pkg:
                self.codesign_pkg(target_path)
            else:
                self.codesign_app(target_path)

            self.log("Step 2: Verifying signature...")
            if not self.verify_signature(target_path):
                raise NotarizationError("Signature verification failed")

            self.log("Step 3: Submitting for notarization...")
            self.submit_notarization(target_path)

            self.log("Step 4: Stapling notarization ticket...")
            if not self.staple(target_path):
                raise NotarizationError("Stapling failed")

            self.log("Step 5: Verifying notarization...")
            if not self.verify_notarization(target_path):
                raise NotarizationError("Notarization verification failed")

            self.log("Notarization workflow completed successfully!")
            return True
        except NotarizationError as e:
            self.log(str(e), level="ERROR")
            return False
        finally:
            self.cleanup()


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for macOS notarization script."""
    parser = argparse.ArgumentParser(
        description="Automate macOS notarization workflow for .pkg and .app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --app /path/to/App.app --bundle-id com.example.app
  %(prog)s --pkg /path/to/Installer.pkg --bundle-id com.example.pkg
  %(prog)s --app MyApp.app --apple-id user@example.com --password xxxx --team-id XXXXXX
        """
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--app", type=Path, help="Path to .app bundle to notarize")
    target.add_argument("--pkg", type=Path, help="Path to .pkg installer to notarize")

    parser.add_argument("--bundle-id", required=True, help="Bundle identifier for the app/pkg")

    auth = parser.add_argument_group("Authentication (Apple ID)")
    auth.add_argument("--apple-id", help="Apple ID email address")
    auth.add_argument("--password", help="App-specific password for Apple ID")
    auth.add_argument("--team-id", help="Developer team ID")

    api = parser.add_argument_group("Authentication (API Key)")
    api.add_argument("--api-key", help="Path to API key file (.p8)")
    api.add_argument("--api-key-id", help="API key identifier")
    api.add_argument("--issuer-id", help="API issuer identifier")

    parser.add_argument("--sign-identity", help="Developer ID signing certificate name")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")

    args = parser.parse_args(argv)

    # Validate authentication
    has_apple_id = all([args.apple_id, args.password, args.team_id])
    has_api_key = all([args.api_key, args.api_key_id, args.issuer_id])
    if not (has_apple_id or has_api_key):
        print("Warning: No complete authentication provided. Will use keychain profile.", file=sys.stderr)

    target_path = args.app or args.pkg
    is_pkg = args.pkg is not None

    if not target_path.exists():
        print(f"Error: Target not found: {target_path}", file=sys.stderr)
        return 1

    workflow = NotarizationWorkflow(
        bundle_id=args.bundle_id,
        apple_id=args.apple_id,
        password=args.password,
        api_key=args.api_key,
        api_key_id=args.api_key_id,
        issuer_id=args.issuer_id,
        team_id=args.team_id,
        sign_identity=args.sign_identity,
        verbose=args.verbose
    )

    return 0 if workflow.run(target_path, is_pkg=is_pkg) else 1


if __name__ == "__main__":
    sys.exit(main())
