#!/usr/bin/env python3
"""
Example usage of OBSSpectatorCapture class.
This demonstrates how to use the OBS WebSocket v5 client.
"""

import asyncio
import logging
from obs_capture import OBSSpectatorCapture

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def record_with_obs():
    """
    Example: Connect to OBS, start recording, wait, then stop recording.
    """
    # Create OBS client (adjust password if needed)
    obs = OBSSpectatorCapture(
        host="localhost",
        port=4455,
        password=""  # Set your OBS WebSocket password here
    )
    
    try:
        # Connect to OBS
        logger.info("Connecting to OBS WebSocket...")
        if await obs.connect():
            logger.info("Connected to OBS WebSocket")
            
            # Start recording
            logger.info("Starting recording...")
            if await obs.start_recording():
                logger.info("Recording started successfully")
                
                # Record for 10 seconds
                logger.info("Recording for 10 seconds...")
                await asyncio.sleep(10)
                
                # Stop recording
                logger.info("Stopping recording...")
                output_path = await obs.stop_recording()
                
                if output_path:
                    logger.info(f"Recording saved to: {output_path}")
                else:
                    logger.warning("Recording stopped but no output path returned")
            else:
                logger.error("Failed to start recording")
        else:
            logger.error("Failed to connect to OBS WebSocket")
            
    except Exception as e:
        logger.error(f"Error during OBS recording: {e}")
        
    finally:
        # Always disconnect
        logger.info("Disconnecting from OBS...")
        await obs.disconnect()
        logger.info("Disconnected")


async def test_connection_only():
    """
    Example: Test connection to OBS without recording.
    """
    obs = OBSSpectatorCapture(password="")
    
    try:
        logger.info("Testing connection to OBS...")
        if await obs.connect():
            logger.info("Successfully connected to OBS WebSocket")
            
            # You could check OBS status here
            logger.info("Connection test successful")
        else:
            logger.error("Failed to connect to OBS WebSocket")
            
    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        
    finally:
        await obs.disconnect()


async def main():
    """Main function to run examples."""
    print("OBS WebSocket v5 Client Examples")
    print("=" * 40)
    
    # Choose which example to run
    choice = input("Choose example:\n1. Full recording test\n2. Connection test only\nChoice (1-2): ").strip()
    
    if choice == "1":
        await record_with_obs()
    elif choice == "2":
        await test_connection_only()
    else:
        print("Invalid choice. Running connection test...")
        await test_connection_only()


if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
