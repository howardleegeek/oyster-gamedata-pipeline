#!/usr/bin/env python3
"""
Example integration with continuous_capture_daemon.py
This shows how to integrate the rate limiter with the daemon.
"""

import logging
import sys
import time
from pathlib import Path

# Add parent directory to path to import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from bin.recorder_rate_limiter import can_record_now, increment_daily_counter

# Setup logging
LOG_FILE = Path.home() / ".oyster" / "daemon_heartbeat.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


class ContinuousCaptureDaemon:
    """Example daemon class showing integration."""

    def __init__(self):
        self.state = "IDLE"
        self.heartbeat_interval = 60  # seconds

    def check_and_record(self):
        """Check if recording is allowed and start recording if possible."""
        allowed, reason = can_record_now()

        if not allowed:
            logger.warning(f"Recording blocked: {reason}")
            self.state = "IDLE"
            # Here you would also notify user via system tray if available
            self.notify_user(f"Recording paused: {reason}")
            return False

        # Recording is allowed
        logger.info("Starting recording session")
        self.state = "ARMED"

        # Start recording (simulated)
        self.start_recording_session()

        # Increment daily counter
        increment_daily_counter()

        return True

    def start_recording_session(self):
        """Start a recording session."""
        # This would contain the actual recording logic
        logger.info("Recording session started")
        # Simulate recording for 30 seconds
        time.sleep(30)
        logger.info("Recording session completed")

        # After recording, transition back to IDLE
        self.state = "IDLE"

    def notify_user(self, message):
        """Notify user via system tray (placeholder)."""
        # In a real implementation, this would use system tray notifications
        logger.info(f"User notification: {message}")
        # Example: could use notify-send on Linux
        # subprocess.run(["notify-send", "Oyster Recorder", message])

    def run(self):
        """Main daemon loop."""
        logger.info("Continuous capture daemon started")

        try:
            while True:
                logger.debug(f"Daemon state: {self.state}")

                if self.state == "IDLE":
                    # Check if we can start recording
                    self.check_and_record()

                # Wait for next heartbeat
                time.sleep(self.heartbeat_interval)

        except KeyboardInterrupt:
            logger.info("Daemon stopped by user")
        except Exception as e:
            logger.error(f"Daemon error: {e}", exc_info=True)


def integrate_with_existing_daemon():
    """
    Example of how to integrate with an existing daemon.
    This function would be called from the existing daemon's main loop.
    """
    # Before attempting to ARM/start recording:
    allowed, reason = can_record_now()

    if not allowed:
        # Log to daemon heartbeat log
        logger.warning(f"Rate limiter blocked recording: {reason}")

        # Stay in IDLE state (don't transition to ARMED)
        # In your daemon code, you would return early here

        # Notify user via system tray if available
        # notify_user_via_tray(f"Recording paused: {reason}")

        return False

    # Recording is allowed
    logger.info("Rate limiter allows recording")

    # Increment daily counter when recording starts
    # This should be called when the recording actually starts
    # increment_daily_counter()

    return True


if __name__ == "__main__":
    # Example usage
    print("=== Daemon Integration Example ===")
    print()

    # Check current status
    allowed, reason = can_record_now()
    print(f"Can record now: {allowed}")
    print(f"Reason: {reason}")
    print()

    # Show how to integrate
    print("To integrate with your daemon:")
    print("1. Import can_record_now and increment_daily_counter")
    print("2. Call can_record_now() before each ARM transition")
    print("3. If False, stay in IDLE state and log the reason")
    print("4. If True, proceed with recording and call increment_daily_counter()")
    print()

    # Run example daemon if requested
    if len(sys.argv) > 1 and sys.argv[1] == "--run-example":
        daemon = ContinuousCaptureDaemon()
        daemon.run()
