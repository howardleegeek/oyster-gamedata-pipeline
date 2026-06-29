#!/usr/bin/env python3
"""
Continuous capture daemon for Oyster Minecraft recording.
State machine: IDLE → ARMED → RECORDING → FINALIZING → UPLOADING → COOLDOWN
"""

import json
import logging
import platform
import subprocess
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import psutil

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class DaemonState(Enum):
    IDLE = "IDLE"  # Waiting for MC to start
    ARMED = "ARMED"  # MC detected, recorder ARMed
    RECORDING = "RECORDING"  # Session in progress
    FINALIZING = "FINALIZING"  # Session ended, running canonical_pipeline
    UPLOADING = "UPLOADING"  # Session queued for upload daemon
    COOLDOWN = "COOLDOWN"  # 30s before re-arming


class ContinuousCaptureDaemon:
    def __init__(self):
        self.state = DaemonState.IDLE
        self.state_file = Path.home() / ".oyster" / "daemon_state.json"
        self.heartbeat_log = Path.home() / ".oyster" / "daemon_heartbeat.log"
        self.oyster_dir = Path.home() / ".oyster"
        self.active_session_dir = project_root / "active_session"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_log.parent.mkdir(parents=True, exist_ok=True)

        # State persistence
        self.persisted_state = self._load_state()
        if self.persisted_state.get("current_state"):
            try:
                self.state = DaemonState(self.persisted_state["current_state"])
            except ValueError:
                self.state = DaemonState.IDLE

        # Session tracking
        self.session_id = self.persisted_state.get("session_id")
        self.session_started = None
        if self.persisted_state.get("started_at"):
            try:
                self.session_started = datetime.fromisoformat(self.persisted_state["started_at"])
            except (ValueError, TypeError):
                self.session_started = None

        # Statistics
        self.total_sessions_today = self.persisted_state.get("total_sessions_today", 0)
        self.total_uptime_hours = self.persisted_state.get("total_uptime_hours", 0.0)
        self.sessions_completed_this_hour = 0
        self.uploads_completed_this_hour = 0
        self.errors_this_hour = []

        # Thread control
        self.running = True
        self.paused = False
        self.last_heartbeat = datetime.now()
        self.last_state_change = datetime.now()

        # Cooldown tracking
        self.cooldown_until = None

        # Setup logging
        self._setup_logging()

    def _setup_logging(self):
        """Setup logging to both file and console"""
        log_file = self.oyster_dir / "daemon.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
        )
        self.logger = logging.getLogger("oyster_daemon")

    def _load_state(self):
        """Load persisted state from file"""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def _save_state(self):
        """Save current state to file"""
        state_data = {
            "current_state": self.state.value,
            "session_id": self.session_id,
            "started_at": self.session_started.isoformat() if self.session_started else None,
            "total_sessions_today": self.total_sessions_today,
            "total_uptime_hours": self.total_uptime_hours,
            "last_updated": datetime.now().isoformat(),
        }
        try:
            with open(self.state_file, "w") as f:
                json.dump(state_data, f, indent=2)
        except IOError as e:
            self.logger.error(f"Failed to save state: {e}")

    def _log_heartbeat(self):
        """Log hourly heartbeat with statistics"""
        now = datetime.now()
        if (now - self.last_heartbeat).total_seconds() >= 3600:  # 1 hour
            # Check disk space
            disk_free_gb = self._get_free_disk_gb()

            heartbeat_data = {
                "timestamp": now.isoformat(),
                "state": self.state.value,
                "sessions_completed_last_hour": self.sessions_completed_this_hour,
                "uploads_completed_last_hour": self.uploads_completed_this_hour,
                "errors": self.errors_this_hour.copy(),
                "disk_free_gb": disk_free_gb,
                "total_sessions_today": self.total_sessions_today,
                "total_uptime_hours": self.total_uptime_hours,
            }

            try:
                with open(self.heartbeat_log, "a") as f:
                    f.write(json.dumps(heartbeat_data) + "\n")
            except IOError as e:
                self.logger.error(f"Failed to write heartbeat: {e}")

            # Reset hourly counters
            self.sessions_completed_this_hour = 0
            self.uploads_completed_this_hour = 0
            self.errors_this_hour = []
            self.last_heartbeat = now

            # Check disk space and pause if needed
            if disk_free_gb < 10:
                self.logger.warning(
                    f"Low disk space: {disk_free_gb:.1f} GB free. Pausing auto-arm."
                )
                self.paused = True

    def _get_free_disk_gb(self):
        """Get free disk space in GB"""
        try:
            usage = psutil.disk_usage(str(self.oyster_dir))
            return usage.free / (1024**3)  # Convert to GB
        except Exception as e:
            self.logger.error(f"Failed to get disk space: {e}")
            return 100.0  # Assume plenty of space

    def _is_minecraft_running(self):
        """Check if Minecraft is running"""
        system = platform.system()
        try:
            if system == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq javaw.exe"],
                    capture_output=True,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return "javaw.exe" in result.stdout
            elif system == "Darwin":  # macOS
                result = subprocess.run(
                    ["pgrep", "-f", "minecraft"], capture_output=True, text=True
                )
                return result.returncode == 0
            else:  # Linux
                result = subprocess.run(
                    ["pgrep", "-f", "java.*minecraft"], capture_output=True, text=True
                )
                return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Failed to check Minecraft: {e}")
            return False

    def _is_recorder_running(self):
        """Check if OysterRecorder is running"""
        system = platform.system()
        try:
            if system == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq OysterRecorder.exe"],
                    capture_output=True,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return "OysterRecorder.exe" in result.stdout
            elif system == "Darwin":  # macOS
                result = subprocess.run(
                    ["pgrep", "-f", "OysterRecorder"], capture_output=True, text=True
                )
                return result.returncode == 0
            else:  # Linux
                result = subprocess.run(
                    ["pgrep", "-f", "OysterRecorder"], capture_output=True, text=True
                )
                return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Failed to check recorder: {e}")
            return False

    def _start_recorder(self):
        """Start the Oyster recorder"""
        try:
            recorder_path = project_root / "bin" / "start_recorder.sh"
            if recorder_path.exists():
                subprocess.Popen([str(recorder_path)], start_new_session=True)
                self.logger.info("Started Oyster recorder")
                return True
            else:
                self.logger.error(f"Recorder script not found: {recorder_path}")
                return False
        except Exception as e:
            self.logger.error(f"Failed to start recorder: {e}")
            return False

    def _check_session_active(self):
        """Check if session is active by looking at game_state.jsonl mtime"""
        game_state_file = self.active_session_dir / "game_state.jsonl"
        if not game_state_file.exists():
            return False

        try:
            mtime = datetime.fromtimestamp(game_state_file.stat().st_mtime)
            # If modified within last 5 minutes, consider session active
            return (datetime.now() - mtime).total_seconds() < 300
        except Exception as e:
            self.logger.error(f"Failed to check session activity: {e}")
            return False

    def _check_finalize_complete(self):
        """Check if finalize completed by looking for clip-*.tar.gz files"""
        try:
            clip_files = list(project_root.glob("clip-*.tar.gz"))
            return len(clip_files) > 0
        except Exception as e:
            self.logger.error(f"Failed to check finalize: {e}")
            return False

    def _run_finalize(self):
        """Run the canonical pipeline finalization"""
        try:
            finalize_script = project_root / "bin" / "canonical_pipeline.py"
            if finalize_script.exists():
                result = subprocess.run(
                    [sys.executable, str(finalize_script)], capture_output=True, text=True
                )
                if result.returncode == 0:
                    self.logger.info("Finalize completed successfully")
                    return True
                else:
                    self.logger.error(f"Finalize failed: {result.stderr}")
                    return False
            else:
                self.logger.error(f"Finalize script not found: {finalize_script}")
                return False
        except Exception as e:
            self.logger.error(f"Failed to run finalize: {e}")
            return False

    def _queue_upload(self):
        """Queue the session for upload"""
        try:
            # Create upload marker file
            upload_queue_dir = self.oyster_dir / "upload_queue"
            upload_queue_dir.mkdir(exist_ok=True)

            # Find the latest clip file
            clip_files = list(project_root.glob("clip-*.tar.gz"))
            if not clip_files:
                self.logger.error("No clip files found to upload")
                return False

            latest_clip = max(clip_files, key=lambda p: p.stat().st_mtime)

            # Create upload job file
            upload_job = {
                "clip_file": str(latest_clip),
                "session_id": self.session_id,
                "created_at": datetime.now().isoformat(),
                "status": "pending",
            }

            job_file = upload_queue_dir / f"upload_{self.session_id}.json"
            with open(job_file, "w") as f:
                json.dump(upload_job, f, indent=2)

            self.logger.info(f"Queued upload for session {self.session_id}")
            self.uploads_completed_this_hour += 1
            return True
        except Exception as e:
            self.logger.error(f"Failed to queue upload: {e}")
            self.errors_this_hour.append(f"upload_failed: {str(e)}")
            return False

    def _cleanup_session(self):
        """Clean up session files"""
        try:
            # Remove clip files
            for clip_file in project_root.glob("clip-*.tar.gz"):
                clip_file.unlink()

            # Clear active session directory
            if self.active_session_dir.exists():
                for item in self.active_session_dir.iterdir():
                    if item.is_file():
                        item.unlink()

            self.logger.info("Cleaned up session files")
            return True
        except Exception as e:
            self.logger.error(f"Failed to cleanup session: {e}")
            return False

    def _transition_to(self, new_state):
        """Transition to a new state with logging"""
        old_state = self.state
        self.state = new_state
        self.last_state_change = datetime.now()

        self.logger.info(f"State transition: {old_state.value} → {new_state.value}")

        # Handle state-specific actions
        if new_state == DaemonState.ARMED:
            self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.session_started = datetime.now()
            self._start_recorder()

        elif new_state == DaemonState.RECORDING:
            self.logger.info(f"Recording session {self.session_id}")

        elif new_state == DaemonState.FINALIZING:
            self.logger.info(f"Finalizing session {self.session_id}")
            if self._run_finalize():
                self._transition_to(DaemonState.UPLOADING)
            else:
                self.logger.error("Finalize failed, going to COOLDOWN")
                self._transition_to(DaemonState.COOLDOWN)

        elif new_state == DaemonState.UPLOADING:
            if self._queue_upload():
                self.total_sessions_today += 1
                self.sessions_completed_this_hour += 1
                self._cleanup_session()
                self._transition_to(DaemonState.COOLDOWN)
            else:
                self.logger.error("Upload queue failed, going to COOLDOWN")
                self._transition_to(DaemonState.COOLDOWN)

        elif new_state == DaemonState.COOLDOWN:
            self.cooldown_until = datetime.now() + timedelta(seconds=30)
            self.session_id = None
            self.session_started = None

        elif new_state == DaemonState.IDLE:
            self.cooldown_until = None

        # Save state after transition
        self._save_state()

    def run(self):
        """Main daemon loop"""
        self.logger.info("Starting Oyster continuous capture daemon")

        # Resume from persisted state
        if self.state != DaemonState.IDLE:
            self.logger.info(f"Resuming from state: {self.state.value}")

        try:
            while self.running:
                # Log heartbeat if needed
                self._log_heartbeat()

                # Skip state machine if paused
                if self.paused:
                    time.sleep(5)
                    continue

                # State machine logic
                if self.state == DaemonState.IDLE:
                    # Check for Minecraft
                    if self._is_minecraft_running():
                        self._transition_to(DaemonState.ARMED)

                elif self.state == DaemonState.ARMED:
                    # Check if recorder is running
                    if self._is_recorder_running():
                        self._transition_to(DaemonState.RECORDING)
                    else:
                        # Recorder failed to start, go back to IDLE
                        time.sleep(2)
                        if not self._is_recorder_running():
                            self.logger.warning("Recorder failed to start, returning to IDLE")
                            self._transition_to(DaemonState.IDLE)

                elif self.state == DaemonState.RECORDING:
                    # Check if Minecraft is still running
                    if not self._is_minecraft_running():
                        self._transition_to(DaemonState.FINALIZING)
                    # Check if session is still active
                    elif not self._check_session_active():
                        self.logger.info("Session appears to have ended")
                        self._transition_to(DaemonState.FINALIZING)

                elif self.state in (DaemonState.FINALIZING, DaemonState.UPLOADING):
                    # Already handled in transition
                    pass

                elif self.state == DaemonState.COOLDOWN:
                    # Wait for cooldown period
                    if self.cooldown_until and datetime.now() >= self.cooldown_until:
                        self._transition_to(DaemonState.IDLE)

                # Update uptime
                self.total_uptime_hours += 5 / 3600  # 5 second sleep = 5/3600 hours

                # Sleep to prevent CPU spinning
                time.sleep(5)

        except KeyboardInterrupt:
            self.logger.info("Received shutdown signal")
        except Exception as e:
            self.logger.error(f"Daemon crashed: {e}", exc_info=True)
            self.errors_this_hour.append(f"daemon_crash: {str(e)}")
        finally:
            self.running = False
            self._save_state()
            self.logger.info("Daemon stopped")

    def stop(self):
        """Stop the daemon gracefully"""
        self.logger.info("Stopping daemon...")
        self.running = False

    def pause(self):
        """Pause auto-arming"""
        self.logger.info("Pausing daemon auto-arm")
        self.paused = True

    def resume(self):
        """Resume auto-arming"""
        self.logger.info("Resuming daemon auto-arm")
        self.paused = False


def main():
    """Main entry point"""
    daemon = ContinuousCaptureDaemon()

    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "stop":
            # Signal the daemon to stop (would need IPC in real implementation)
            print("Use daemon_control.py to control running daemon")
            return
        elif sys.argv[1] == "run":
            # Run in foreground
            daemon.run()
            return

    # Default: run as daemon
    import daemon.pidfile

    import daemon as python_daemon

    pid_file = Path.home() / ".oyster" / "daemon.pid"

    with python_daemon.DaemonContext(
        pidfile=daemon.pidfile.PIDLockFile(str(pid_file)),
        stdout=open(Path.home() / ".oyster" / "daemon.out", "w"),
        stderr=open(Path.home() / ".oyster" / "daemon.err", "w"),
    ):
        daemon.run()


if __name__ == "__main__":
    main()
