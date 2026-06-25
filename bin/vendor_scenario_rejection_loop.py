#!/usr/bin/env python3
"""
G068 · Vendor Scenario Rejection Loop

Walkthrough simulation: vendor uploads malformed clip, receives rejection email
with retry guide, and can attempt re-upload with corrected file.
"""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ClipMetadata:
    """Metadata for a vendor-submitted clip."""

    vendor_id: str
    clip_id: str
    filename: str
    file_size_bytes: int
    format: str
    duration_seconds: Optional[float] = None
    resolution: Optional[str] = None
    submitted_at: datetime = field(default_factory=datetime.now)


@dataclass
class ValidationResult:
    """Result of clip validation."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_clip(clip_path: Path, metadata: ClipMetadata) -> ValidationResult:
    """Validate a vendor-submitted clip for format and content compliance."""
    errors, warnings = [], []

    if not clip_path.exists():
        errors.append(f"Clip file not found: {clip_path}")
        return ValidationResult(is_valid=False, errors=errors, warnings=warnings)

    file_size = clip_path.stat().st_size
    if file_size == 0:
        errors.append("Clip file is empty (0 bytes)")
    elif file_size > 500 * 1024 * 1024:
        errors.append(f"Clip file exceeds 500MB limit: {file_size} bytes")

    valid_formats = {"mp4", "mov", "avi", "mkv", "webm"}
    if metadata.format.lower() not in valid_formats:
        errors.append(f"Invalid format '{metadata.format}'. Supported: {valid_formats}")

    if metadata.duration_seconds is not None:
        if metadata.duration_seconds <= 0:
            errors.append("Duration must be positive")
        elif metadata.duration_seconds > 600:
            warnings.append("Clip exceeds 10 minutes; may affect processing time")

    problematic_chars = {" ", "<", ">", ":", '"', "|", "?", "*"}
    found = [c for c in problematic_chars if c in metadata.filename]
    if found:
        errors.append(f"Filename contains problematic characters: {found}")

    return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)


def generate_retry_guide(errors: list[str]) -> str:
    """Generate a retry guide based on validation errors."""
    lines = ["=== RETRY GUIDE ===", "", "Your clip was rejected due to:", ""]
    lines.extend(f"  {i}. {e}" for i, e in enumerate(errors, 1))
    lines.extend(
        [
            "",
            "=== HOW TO FIX ===",
            "",
            "1. Empty files: Ensure clip exported correctly.",
            "2. File size: Compress or split into smaller segments.",
            "3. Format: Re-export using MP4 (H.264) or MOV (ProRes).",
            "4. Filename: Remove spaces/special characters. Use underscores.",
            "",
            "Re-upload at: https://vendor.example.com/upload",
            "Support: vendor-support@example.com",
        ]
    )
    return "\n".join(lines)


def format_rejection_email(vendor_email: str, metadata: ClipMetadata, errors: list[str]) -> str:
    """Format rejection email as plain text."""
    retry_guide = generate_retry_guide(errors)
    return f"""Subject: Clip Upload Rejected - Action Required
From: noreply@vendor-portal.example.com
To: {vendor_email}
Date: {datetime.now().isoformat()}

Dear Vendor ({metadata.vendor_id}),

Your clip upload (ID: {metadata.clip_id}) has been rejected.

REJECTION REASONS:
{chr(10).join(f"  - {r}" for r in errors)}

{retry_guide}

---
This is an automated message. Do not reply directly.
"""


def simulate_rejection_loop(
    clip_path: Path, metadata: ClipMetadata, vendor_email: str, output_dir: Optional[Path] = None
) -> dict:
    """Simulate the complete rejection loop workflow."""
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="vendor_rejection_"))

    logger.info(f"Validating clip {metadata.clip_id}")
    validation = validate_clip(clip_path, metadata)

    result = {
        "clip_id": metadata.clip_id,
        "vendor_id": metadata.vendor_id,
        "validation": {
            "is_valid": validation.is_valid,
            "errors": validation.errors,
            "warnings": validation.warnings,
        },
        "email_sent": False,
        "output_dir": str(output_dir),
    }

    if not validation.is_valid:
        email_content = format_rejection_email(vendor_email, metadata, validation.errors)
        email_path = output_dir / f"rejection_{metadata.clip_id}.eml"
        email_path.write_text(email_content)
        logger.info(f"Rejection email written to {email_path}")
        result["email_sent"] = True
        result["email_path"] = str(email_path)

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(result, indent=2, default=str))
    return result


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for vendor rejection loop simulation."""
    parser = argparse.ArgumentParser(description="Simulate vendor clip rejection loop")
    parser.add_argument("--clip-file", "-c", required=True, help="Path to clip file")
    parser.add_argument("--vendor-id", "-v", required=True, help="Vendor identifier")
    parser.add_argument("--clip-id", "-i", required=True, help="Clip identifier")
    parser.add_argument("--vendor-email", "-e", required=True, help="Vendor email")
    parser.add_argument("--format", "-f", default="mp4", help="Clip format")
    parser.add_argument("--duration", "-d", type=float, help="Duration in seconds")
    parser.add_argument("--resolution", "-r", help="Resolution (e.g., 1080p)")
    parser.add_argument("--output-dir", "-o", help="Output directory")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    clip_path = Path(args.clip_file)
    metadata = ClipMetadata(
        vendor_id=args.vendor_id,
        clip_id=args.clip_id,
        filename=clip_path.name,
        file_size_bytes=clip_path.stat().st_size if clip_path.exists() else 0,
        format=args.format,
        duration_seconds=args.duration,
        resolution=args.resolution,
    )

    output_dir = Path(args.output_dir) if args.output_dir else None
    try:
        result = simulate_rejection_loop(clip_path, metadata, args.vendor_email, output_dir)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["validation"]["is_valid"] else 1
    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
