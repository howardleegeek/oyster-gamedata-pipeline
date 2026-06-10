#!/bin/bash
#
# Setup script for S3 Upload Daemon
# Registers the daemon to start automatically on boot
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_SCRIPT="$SCRIPT_DIR/upload_daemon.py"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/com.oyster.upload-daemon.plist"
LOG_DIR="$HOME/.oyster"
STATE_FILE="$LOG_DIR/upload_state.json"
LOG_FILE="$LOG_DIR/upload.log"

echo "=== Oyster Upload Daemon Setup ==="
echo ""

# Check if running as root (needed for some operations)
if [ "$EUID" -eq 0 ]; then
    echo "Warning: Running as root. This script should be run as the user."
    exit 1
fi

# Create necessary directories
echo "Creating directories..."
mkdir -p "$LOG_DIR"
mkdir -p "$PLIST_DIR"

# Check if daemon script exists
if [ ! -f "$DAEMON_SCRIPT" ]; then
    echo "Error: upload_daemon.py not found at $DAEMON_SCRIPT"
    exit 1
fi

# Make daemon script executable
chmod +x "$DAEMON_SCRIPT"

# Create LaunchAgent plist for macOS
echo "Creating LaunchAgent..."

cat > "$PLIST_FILE" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.oyster.upload-daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>__DAEMON_SCRIPT__</string>
        <string>--interval</string>
        <string>60</string>
        <string>--max-kbps</string>
        <string>5000</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>__LOG_FILE__</string>
    <key>StandardErrorPath</key>
    <string>__LOG_FILE__</string>
    <key>ProcessType</key>
    <string>Background</string>
    <key>Nice</key>
    <integer>10</integer>
    <key>ThrottleInterval</key>
    <integer>60</integer>
</dict>
</plist>
EOF

# Replace placeholders
sed -i '' "s|__DAEMON_SCRIPT__|$DAEMON_SCRIPT|g" "$PLIST_FILE"
sed -i '' "s|__LOG_FILE__|$LOG_FILE|g" "$PLIST_FILE"

echo "LaunchAgent created at: $PLIST_FILE"

# Check if already registered
if launchctl list | grep -q "com.oyster.upload-daemon"; then
    echo ""
    echo "Daemon is already registered. Unloading first..."
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
fi

# Register the daemon
echo "Registering daemon with launchctl..."
launchctl load "$PLIST_FILE"

# Start the daemon
echo "Starting daemon..."
launchctl start "com.oyster.upload-daemon"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Daemon Status:"
launchctl list | grep "com.oyster.upload-daemon" || echo "  (starting up...)"
echo ""
echo "Log file: $LOG_FILE"
echo "State file: $STATE_FILE"
echo ""
echo "To check status, run:"
echo "  launchctl list | grep oyster"
echo "  oyster-upload status"
echo ""
echo "To stop the daemon:"
echo "  launchctl stop com.oyster.upload-daemon"
echo ""
echo "To uninstall:"
echo "  launchctl unload $PLIST_FILE"
echo "  rm $PLIST_FILE"
echo ""

# Create a simple wrapper script for the CLI
WRAPPER_DIR="$HOME/.local/bin"
mkdir -p "$WRAPPER_DIR"

cat > "$WRAPPER_DIR/oyster-upload" << 'WRAPPER_EOF'
#!/bin/bash
# Oyster Upload CLI Wrapper

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
PYTHON_SCRIPT="$(dirname "$SCRIPT_DIR")/bin/upload_status.py"

if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "Error: upload_status.py not found"
    exit 1
fi

exec python3 "$PYTHON_SCRIPT" "$@"
WRAPPER_EOF

chmod +x "$WRAPPER_DIR/oyster-upload"

# Add to PATH if not already there
if [[ ":$PATH:" != *":$WRAPPER_DIR:"* ]]; then
    echo ""
    echo "NOTE: Add the following to your shell profile (.bashrc, .zshrc, etc.) to use 'oyster-upload':"
    echo "  export PATH=\"\$PATH:$WRAPPER_DIR\""
fi

echo "CLI wrapper installed at: $WRAPPER_DIR/oyster-upload"
echo ""
echo "You can now run: oyster-upload status"
