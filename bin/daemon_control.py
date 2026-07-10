#!/usr/bin/env python3
"""CLI control for Oyster continuous capture daemon"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class DaemonControl:
    def __init__(self):
        self.oyster_dir = Path.home() / ".oyster"
        self.state_file = self.oyster_dir / "daemon_state.json"
        self.pid_file = self.oyster_dir / "daemon.pid"
        self.heartbeat_log = self.oyster_dir / "daemon_heartbeat.log"
        self.daemon_log = self.oyster_dir / "daemon.log"

        # Ensure directory exists
        self.oyster_dir.mkdir(exist_ok=True)

    def status(self):
        """Show current state and recent activity"""
        print("=== Oyster Continuous Capture Daemon Status ===")

        # Check if daemon is running
        if self.pid_file.exists():
            try:
                with open(self.pid_file, 'r') as f:
                    pid = int(f.read().strip())

                # Check if process exists
                try:
                    os.kill(pid, 0)
                    print(f"✓ Daemon running (PID: {pid})")
                except OSError:
                    print("✗ Daemon PID file exists but process is not running")
                    return 1
            except Exception as e:
                print(f"✗ Error reading PID file: {e}")
                return 1
        else:
            print("✗ Daemon is not running")
            return 1

        # Show state
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)

                print(f"\nCurrent State: {state.get('current_state', 'UNKNOWN')}")
                print(f"Session ID: {state.get('session_id', 'None')}")
                print(f"Started At: {state.get('started_at', 'None')}")
                print(f"Total Sessions Today: {state.get('total_sessions_today', 0)}")
                print(f"Total Uptime Hours: {state.get('total_uptime_hours', 0):.2f}")
                print(f"Last Updated: {state.get('last_updated', 'Never')}")
            except Exception as e:
                print(f"✗ Error reading state file: {e}")
        else:
            print("\nNo state file found")

        # Show recent heartbeats
        if self.heartbeat_log.exists():
            try:
                with open(self.heartbeat_log, 'r') as f:
                    lines = f.readlines()

                if lines:
                    print(f"\nRecent Heartbeats (last {min(5, len(lines))}):")
                    for line in lines[-5:]:
                        try:
                            data = json.loads(line.strip())
                            timestamp = data.get('timestamp', 'Unknown')
                            state = data.get('state', 'Unknown')
                            sessions = data.get('sessions_completed_last_hour', 0)
                            uploads = data.get('uploads_completed_last_hour', 0)
                            disk_free = data.get('disk_free_gb', 0)

                            print(f"  {timestamp}: {state} | Sessions: {sessions} | Uploads: {uploads} | Disk: {disk_free:.1f} GB")
                        except Exception as e:
                            logger.debug("Failed to parse heartbeat line: %s", e)
                            print(f"  {line.strip()}")
                else:
                    print("\nNo heartbeat data yet")
            except Exception as e:
                print(f"✗ Error reading heartbeat log: {e}")

        # Show recent errors
        if self.daemon_log.exists():
            try:
                with open(self.daemon_log, 'r') as f:
                    lines = f.readlines()

                error_lines = [ln for ln in lines[-20:] if 'ERROR' in ln or 'WARNING' in ln]
                if error_lines:
                    print(f"\nRecent Errors/Warnings (last {min(5, len(error_lines))}):")
                    for line in error_lines[-5:]:
                        print(f"  {line.strip()}")
            except Exception as e:
                print(f"✗ Error reading daemon log: {e}")

        print("\n=== End Status ===")
        return 0

    def pause(self):
        """Pause auto-arming"""
        print("Pausing daemon auto-arm...")

        # Send pause signal (would need IPC in real implementation)
        # For now, we'll create a pause file that the daemon checks
        pause_file = self.oyster_dir / "pause"
        pause_file.touch()

        print("✓ Daemon will pause auto-arming on next check")
        return 0

    def resume(self):
        """Resume auto-arming"""
        print("Resuming daemon auto-arm...")

        # Remove pause file
        pause_file = self.oyster_dir / "pause"
        if pause_file.exists():
            pause_file.unlink()

        print("✓ Daemon will resume auto-arming on next check")
        return 0

    def stop(self):
        """Stop the daemon gracefully"""
        print("Stopping daemon...")

        if not self.pid_file.exists():
            print("✗ Daemon is not running")
            return 1

        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())

            # Send SIGTERM
            os.kill(pid, signal.SIGTERM)
            print(f"✓ Sent stop signal to PID {pid}")

            # Wait a bit and check
            import time
            time.sleep(2)

            try:
                os.kill(pid, 0)
                print("✗ Daemon did not stop, sending SIGKILL")
                os.kill(pid, signal.SIGKILL)
            except OSError:
                print("✓ Daemon stopped successfully")

            # Clean up PID file
            if self.pid_file.exists():
                self.pid_file.unlink()

            return 0
        except Exception as e:
            print(f"✗ Error stopping daemon: {e}")
            return 1

    def logs(self):
        """Tail the daemon logs"""
        if not self.daemon_log.exists():
            print("No daemon log file found")
            return 1

        try:
            # Tail the last 50 lines
            with open(self.daemon_log, 'r') as f:
                lines = f.readlines()

            print(f"=== Last {min(50, len(lines))} lines of daemon log ===")
            for line in lines[-50:]:
                print(line.rstrip())
            print("=== End log ===")
            return 0
        except Exception as e:
            print(f"✗ Error reading log: {e}")
            return 1

    def start(self):
        """Start the daemon"""
        print("Starting daemon...")

        if self.pid_file.exists():
            print("✗ Daemon appears to already be running")
            print("   Use 'status' to check or 'stop' to stop it first")
            return 1

        daemon_script = Path(__file__).parent / "continuous_capture_daemon.py"

        if not daemon_script.exists():
            print(f"✗ Daemon script not found: {daemon_script}")
            return 1

        try:
            # Start daemon in background
            import sys
            python_exec = sys.executable

            # Create output files
            stdout_file = self.oyster_dir / "daemon.out"
            stderr_file = self.oyster_dir / "daemon.err"

            # Start the daemon
            proc = subprocess.Popen(
                [python_exec, str(daemon_script), "run"],
                stdout=open(stdout_file, 'w'),
                stderr=open(stderr_file, 'w'),
                start_new_session=True
            )

            # Write PID file
            with open(self.pid_file, 'w') as f:
                f.write(str(proc.pid))

            print(f"✓ Daemon started with PID {proc.pid}")
            print(f"   Logs: {self.daemon_log}")
            print(f"   Stdout: {stdout_file}")
            print(f"   Stderr: {stderr_file}")

            return 0
        except Exception as e:
            print(f"✗ Error starting daemon: {e}")
            return 1

def main():
    parser = argparse.ArgumentParser(description="Oyster Continuous Capture Daemon Control")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Status command
    subparsers.add_parser("status", help="Show current state and recent activity")

    # Pause command
    subparsers.add_parser("pause", help="Pause auto-arming of new sessions")

    # Resume command
    subparsers.add_parser("resume", help="Resume auto-arming of new sessions")

    # Stop command
    subparsers.add_parser("stop", help="Stop the daemon gracefully")

    # Logs command
    subparsers.add_parser("logs", help="Show daemon logs")

    # Start command
    subparsers.add_parser("start", help="Start the daemon")

    args = parser.parse_args()

    control = DaemonControl()

    if args.command == "status":
        return control.status()
    elif args.command == "pause":
        return control.pause()
    elif args.command == "resume":
        return control.resume()
    elif args.command == "stop":
        return control.stop()
    elif args.command == "logs":
        return control.logs()
    elif args.command == "start":
        return control.start()
    else:
        parser.print_help()
        return 1

if __name__ == "__main__":
    sys.exit(main())
