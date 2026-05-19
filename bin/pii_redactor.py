#!/usr/bin/env python3
"""
PII Redactor - Automatically redact PII from session data.
Replaces player username with player_<hash8>, masks chat, masks IPs.
"""

import json
import re
import hashlib
import os
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional


def sha8(text: str, salt: str = "oyster_salt") -> str:
    """Generate 8-character hash of text."""
    combined = f"{text}{salt}"
    return hashlib.sha256(combined.encode()).hexdigest()[:8]


def pseudonymize_username(username: str) -> str:
    """Replace username with pseudonymized version."""
    if not username:
        return "player_unknown"
    # If already pseudonymized, return as-is
    if username.startswith("player_") and len(username) > 8:
        return username
    return f"player_{sha8(username)}"


def is_already_pseudonymized(username: str) -> bool:
    """Check if username is already pseudonymized."""
    return username and username.startswith("player_") and len(username) > 8


def mask_ip(ip: str) -> str:
    """Mask IP address to xxx.xxx.xxx.0"""
    parts = ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
    return "xxx.xxx.xxx.0"


def redact_chat_message(message: str) -> str:
    """Replace chat message with redaction marker."""
    return "[redacted]"


def redact_file_content(content: str, player_username: str, pseudonymized_name: str) -> str:
    """Redact PII from file content."""
    
    # Replace player username
    if player_username and player_username in content:
        content = content.replace(player_username, pseudonymized_name)
    
    # Replace email addresses
    email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    content = email_pattern.sub('[email_redacted]', content)
    
    # Replace phone numbers
    phone_pattern = re.compile(r'\b(?:\+1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b')
    content = phone_pattern.sub('[phone_redacted]', content)
    
    # Replace SSNs
    ssn_pattern = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
    content = ssn_pattern.sub('[ssn_redacted]', content)
    
    # Replace credit card numbers
    cc_pattern = re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b')
    content = cc_pattern.sub('[cc_redacted]', content)
    
    # Replace public IP addresses (not private)
    private_ip_pattern = re.compile(r'\b(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.|127\.|169\.254\.)')
    
    def replace_ip(match):
        ip = match.group()
        if private_ip_pattern.match(ip):
            return ip  # Keep private IPs
        return mask_ip(ip)
    
    ip_pattern = re.compile(r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b')
    content = ip_pattern.sub(replace_ip, content)
    
    # Replace real names (simple pattern)
    name_pattern = re.compile(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b')
    content = name_pattern.sub('[name_redacted]', content)
    
    return content


def redact_jsonl_file(filepath: Path, player_username: str, pseudonymized_name: str) -> int:
    """Redact PII from a JSONL file. Returns count of redacted entries."""
    if not filepath.exists():
        return 0
    
    redacted_count = 0
    lines = []
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                original_line = line
                line = redact_file_content(line, player_username, pseudonymized_name)
                
                # Check if chat messages should be redacted
                try:
                    data = json.loads(line)
                    # Look for chat fields
                    if 'chat' in data and isinstance(data['chat'], str):
                        data['chat'] = redact_chat_message(data['chat'])
                        line = json.dumps(data) + '\n'
                    if 'message' in data and isinstance(data['message'], str):
                        data['message'] = redact_chat_message(data['message'])
                        line = json.dumps(data) + '\n'
                    if 'messages' in data and isinstance(data['messages'], list):
                        data['messages'] = ['[redacted]' for _ in data['messages']]
                        line = json.dumps(data) + '\n'
                except json.JSONDecodeError:
                    pass
                
                if line != original_line:
                    redacted_count += 1
                lines.append(line)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return 0
    
    # Write back
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    return redacted_count


def redact_json_file(filepath: Path, player_username: str, pseudonymized_name: str) -> int:
    """Redact PII from a JSON file. Returns count of redacted fields."""
    if not filepath.exists():
        return 0
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return 0
    
    original_data = json.dumps(data)
    
    # Redact player username in data
    data = redact_json_data(data, player_username, pseudonymized_name)
    
    new_data = json.dumps(data)
    
    if new_data != original_data:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return 1
    
    return 0


def redact_json_data(data: Any, player_username: str, pseudonymized_name: str) -> Any:
    """Recursively redact PII from JSON data."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            # Redact chat-related fields
            if key in ['chat', 'message', 'messages', 'chat_message']:
                if isinstance(value, str):
                    result[key] = redact_chat_message(value)
                elif isinstance(value, list):
                    result[key] = ['[redacted]' for _ in value]
                else:
                    result[key] = redact_json_data(value, player_username, pseudonymized_name)
            # Redact player username
            elif key in ['player', 'username', 'player_username', 'user']:
                if isinstance(value, str) and value == player_username:
                    result[key] = pseudonymized_name
                else:
                    result[key] = redact_json_data(value, player_username, pseudonymized_name)
            else:
                result[key] = redact_json_data(value, player_username, pseudonymized_name)
        return result
    elif isinstance(data, list):
        return [redact_json_data(item, player_username, pseudonymized_name) for item in data]
    elif isinstance(data, str):
        # Redact content
        return redact_file_content(data, player_username, pseudonymized_name)
    else:
        return data


def redact_session(session_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """Redact all PII from a session directory."""
    
    # Find player username
    player_username = None
    game_state_file = session_dir / 'game_state.jsonl'
    if game_state_file.exists():
        with open(game_state_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if 'player' in data:
                        player = data['player']
                        if isinstance(player, str):
                            player_username = player
                        elif isinstance(player, dict):
                            player_username = player.get('username') or player.get('name')
                        if player_username:
                            break
                except json.JSONDecodeError:
                    continue
    
    if not player_username:
        player_username = "unknown_player"
    
    # Check if already pseudonymized - if so, use existing pseudonym
    if is_already_pseudonymized(player_username):
        pseudonymized_name = player_username
    else:
        pseudonymized_name = pseudonymize_username(player_username)
    
    stats = {
        'player_username': player_username,
        'pseudonymized_to': pseudonymized_name,
        'files_redacted': 0,
        'dry_run': dry_run
    }
    
    if dry_run:
        print(f"DRY RUN: Would pseudonymize '{player_username}' -> '{pseudonymized_name}'")
        return stats
    
    # Process JSONL files
    for jsonl_file in session_dir.glob('*.jsonl'):
        count = redact_jsonl_file(jsonl_file, player_username, pseudonymized_name)
        if count > 0:
            print(f"Redacted {count} entries in {jsonl_file.name}")
            stats['files_redacted'] += 1
    
    # Process JSON files
    for json_file in session_dir.glob('*.json'):
        count = redact_json_file(json_file, player_username, pseudonymized_name)
        if count > 0:
            print(f"Redacted {json_file.name}")
            stats['files_redacted'] += 1
    
    # Create redaction log entry
    log_entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'session_dir': str(session_dir),
        'original_username': player_username,
        'pseudonymized_to': pseudonymized_name,
        'action': 'redact_pii'
    }
    
    log_file = session_dir / 'redaction_log.jsonl'
    with open(log_file, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')
    
    print(f"Redaction complete: {pseudonymized_name}")
    return stats


from datetime import datetime, timezone


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='PII Redactor - Redact PII from session data')
    parser.add_argument('session_dir', type=Path, help='Session directory to redact')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be redacted without making changes')
    
    args = parser.parse_args()
    
    if not args.session_dir.exists():
        print(f"Error: Session directory {args.session_dir} does not exist")
        return 1
    
    result = redact_session(args.session_dir, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    exit(main())
