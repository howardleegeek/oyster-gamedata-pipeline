#!/usr/bin/env python3
"""
PII Auditor - Scans session data for potential PII leaks.
Flags: player username, real names in chat, emails, credit cards, IPs, SSNs, phones.
Outputs pii_audit.json with verdict and recommendations.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Regex patterns for PII detection
PATTERNS = {
    "emails": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "ip_addresses": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
    ),
    "ssns": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phones": re.compile(r"\b(?:\+1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b"),
    "credit_cards": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    "real_names": re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b"),  # Simple name pattern
}

# Private IP ranges (not considered leaks)
PRIVATE_IP_RANGES = [
    re.compile(r"^10\."),
    re.compile(r"^172\.(?:1[6-9]|2\d|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^127\."),
    re.compile(r"^169\.254\."),
]


def is_private_ip(ip: str) -> bool:
    """Check if IP is in private range."""
    return any(pattern.match(ip) for pattern in PRIVATE_IP_RANGES)


def luhn_check(card_number: str) -> bool:
    """Validate credit card number using Luhn algorithm."""
    digits = re.sub(r"\D", "", card_number)
    if len(digits) < 13 or len(digits) > 19:
        return False

    total = 0
    reverse_digits = digits[::-1]

    for i, digit in enumerate(reverse_digits):
        n = int(digit)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n

    return total % 10 == 0


def scan_file_for_pii(filepath: Path) -> Dict[str, List[str]]:
    """Scan a single file for PII patterns."""
    flags = {
        "real_names_in_chat": [],
        "emails": [],
        "credit_cards": [],
        "ip_addresses": [],
        "ssns": [],
        "phones": [],
    }

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return flags

    # Check for emails
    for match in PATTERNS["emails"].finditer(content):
        email = match.group()
        if email not in flags["emails"]:
            flags["emails"].append(email)

    # Check for IPs (separate private from public)
    for match in PATTERNS["ip_addresses"].finditer(content):
        ip = match.group()
        if not is_private_ip(ip) and ip not in flags["ip_addresses"]:
            flags["ip_addresses"].append(ip)

    # Check for SSNs
    for match in PATTERNS["ssns"].finditer(content):
        ssn = match.group()
        if ssn not in flags["ssns"]:
            flags["ssns"].append(ssn)

    # Check for phones
    for match in PATTERNS["phones"].finditer(content):
        phone = match.group()
        if phone not in flags["phones"]:
            flags["phones"].append(phone)

    # Check for credit cards with Luhn validation
    for match in PATTERNS["credit_cards"].finditer(content):
        card = match.group()
        if luhn_check(card) and card not in flags["credit_cards"]:
            flags["credit_cards"].append(card)

    # Check for real names (in chat context)
    for match in PATTERNS["real_names"].finditer(content):
        name = match.group()
        # Filter out common non-names
        if name not in ["Hello World", "Test User", "Default User"] and name not in flags["real_names_in_chat"]:
            flags["real_names_in_chat"].append(name)

    return flags


def scan_session(session_dir: Path) -> Dict[str, Any]:
    """Scan a session directory for PII."""
    all_flags = {
        "player_username": None,
        "real_names_in_chat": [],
        "emails": [],
        "credit_cards": [],
        "ip_addresses": [],
        "ssns": [],
        "phones": [],
    }

    # Check game_state.jsonl for player username
    game_state_file = session_dir / "game_state.jsonl"
    if game_state_file.exists():
        try:
            with open(game_state_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        # Look for player username fields
                        if "player" in data:
                            player = data["player"]
                            if isinstance(player, str):
                                all_flags["player_username"] = player
                            elif isinstance(player, dict):
                                all_flags["player_username"] = player.get("username") or player.get(
                                    "name"
                                )
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

    # Scan inputs.jsonl for chat messages
    inputs_file = session_dir / "inputs.jsonl"
    if inputs_file.exists():
        file_flags = scan_file_for_pii(inputs_file)
        for key in file_flags:
            all_flags[key].extend(file_flags[key])

    # Scan any other JSON/JSONL files
    for json_file in session_dir.glob("*.jsonl"):
        if json_file.name not in ["game_state.jsonl", "inputs.jsonl"]:
            file_flags = scan_file_for_pii(json_file)
            for key in file_flags:
                all_flags[key].extend(file_flags[key])

    for json_file in session_dir.glob("*.json"):
        file_flags = scan_file_for_pii(json_file)
        for key in file_flags:
            all_flags[key].extend(file_flags[key])

    # Deduplicate
    for key in all_flags:
        if key != "player_username":
            all_flags[key] = list(set(all_flags[key]))

    return all_flags


def determine_verdict(flags: Dict[str, Any]) -> tuple[str, List[str]]:
    """Determine if session passes PII audit.

    PASS if no high-risk flags (credit_cards, ssns).
    Player username is medium-risk (warn but don't fail).
    """
    recommendations = []
    high_risk = []

    # Always flag player username (medium risk - warning only)
    if flags["player_username"]:
        recommendations.append(
            f"Player username '{flags['player_username']}' should be pseudonymized if shipped to buyer"
        )
        # NOT adding to high_risk - it's a warning, not a failure

    # High risk flags (these cause FAIL)
    if flags["credit_cards"]:
        high_risk.append("credit_cards")
        recommendations.append(
            f"Credit card numbers found: {len(flags['credit_cards'])} - IMMEDIATE ACTION REQUIRED"
        )

    if flags["ssns"]:
        high_risk.append("ssns")
        recommendations.append(f"SSNs found: {len(flags['ssns'])} - IMMEDIATE ACTION REQUIRED")

    # Medium risk flags (warnings but don't fail)
    if flags["emails"]:
        recommendations.append(f"Email addresses found: {len(flags['emails'])} - requires consent")

    if flags["real_names_in_chat"]:
        recommendations.append(f"Real names in chat: {flags['real_names_in_chat']}")

    if flags["phones"]:
        recommendations.append(f"Phone numbers found: {len(flags['phones'])}")

    if flags["ip_addresses"]:
        recommendations.append(f"Public IP addresses found: {flags['ip_addresses']}")

    # Verdict
    if high_risk:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    return verdict, recommendations


def audit_session(session_dir: Path, output_file: Optional[Path] = None) -> Dict[str, Any]:
    """Run full PII audit on a session."""
    flags = scan_session(session_dir)
    verdict, recommendations = determine_verdict(flags)

    result = {
        "scan_ran_at": datetime.now(timezone.utc).isoformat(),
        "session_dir": str(session_dir),
        "flags": flags,
        "verdict": verdict,
        "recommendations": recommendations,
    }

    if output_file:
        with open(output_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Audit complete: {output_file}")
        print(f"Verdict: {verdict}")

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="PII Auditor - Scan session for PII")
    parser.add_argument("session_dir", type=Path, help="Session directory to scan")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("pii_audit.json"),
        help="Output file for audit results",
    )

    args = parser.parse_args()

    if not args.session_dir.exists():
        print(f"Error: Session directory {args.session_dir} does not exist")
        return 1

    result = audit_session(args.session_dir, args.output)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    exit(main())
