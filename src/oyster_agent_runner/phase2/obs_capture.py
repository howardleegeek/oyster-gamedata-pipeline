"""
OBS Spectator Capture Module

This module provides a Python interface to capture OBS recordings via the obs-websocket protocol.
It replaces the ffmpeg testsrc placeholder with real OBS recording functionality.

The module implements the obs-websocket v5 protocol for controlling OBS Studio remotely.
"""

import json
import base64
import hashlib
import struct
import threading
import time
from typing import Optional, Dict, Any, Union
from enum import Enum


class Opcode(Enum):
    """OBS WebSocket protocol opcodes."""
    HELLO = 0
    IDENTIFY = 1
    EVENT = 5
    REQUEST = 6
    REQUEST_RESPONSE = 7


class OBSSpectatorCapture:
    """
    A client for capturing OBS recordings via obs-websocket protocol.
    
    This class connects to OBS Studio via WebSocket and controls recording
    functionality, providing a simple interface to start and stop recordings.
    
    Args:
        host: OBS WebSocket server hostname (default: "localhost")
        port: OBS WebSocket server port (default: 4455)
        password: OBS WebSocket server password (default: "")
    """
    
    def __init__(self, host: str = "localhost", port: int = 4455, password: str = ""):
        self.host = host
        self.port = port
        self.password = password
        self.ws = None
        self.connected = False
        self.recording = False
        self.output_path = ""
        self.request_id = 0
        self.pending_requests = {}
        self._lock = threading.Lock()
    
    def connect(self) -> bool:
        """
        Connect to OBS WebSocket server.
        
        Returns:
            True if connection successful, False otherwise.
            
        Raises:
            ImportError: If websocket-client module is not installed.
            ConnectionError: If connection to OBS fails.
        """
        try:
            import websocket
        except ImportError:
            raise ImportError(
                "websocket-client module is required. "
                "Install with: pip install websocket-client"
            )
        
        try:
            ws_url = f"ws://{self.host}:{self.port}"
            self.ws = websocket.WebSocket()
            self.ws.connect(ws_url)
            
            # Receive HELLO message
            hello_data = self._receive_message()
            if hello_data.get("op") != Opcode.HELLO.value:
                self.ws.close()
                return False
            
            # Handle authentication if required
            auth_required = hello_data.get("d", {}).get("authentication")
            if auth_required:
                if not self._authenticate(hello_data["d"]):
                    self.ws.close()
                    return False
            
            # Send IDENTIFY message
            identify_data = {
                "rpcVersion": 1,
                "eventSubscriptions": 0
            }
            self._send_message(Opcode.IDENTIFY.value, identify_data)
            
            # Wait for ready
            response = self._receive_message()
            if response.get("op") == Opcode.IDENTIFY.value:
                self.connected = True
                return True
            
            return False
            
        except Exception as e:
            if self.ws:
                try:
                    self.ws.close()
                except:
                    pass
            self.connected = False
            print(f"Failed to connect to OBS: {e}")
            return False
    
    def _authenticate(self, hello_data: Dict[str, Any]) -> bool:
        """Authenticate with OBS WebSocket server."""
        if not self.password:
            print("OBS requires authentication but no password provided")
            return False
        
        try:
            # Get authentication challenge
            challenge = hello_data.get("authentication", {}).get("challenge")
            salt = hello_data.get("authentication", {}).get("salt")
            
            if not challenge or not salt:
                print("Invalid authentication data from OBS")
                return False
            
            # Calculate authentication response
            secret_string = self.password + salt
            secret_hash = hashlib.sha256(secret_string.encode()).hexdigest()
            auth_response = hashlib.sha256((secret_hash + challenge).encode()).hexdigest()
            
            # Send authentication
            auth_data = {
                "rpcVersion": 1,
                "authentication": auth_response,
                "eventSubscriptions": 0
            }
            self._send_message(Opcode.IDENTIFY.value, auth_data)
            
            return True
            
        except Exception as e:
            print(f"Authentication failed: {e}")
            return False
    
    def _send_message(self, op: int, data: Dict[str, Any]) -> None:
        """Send a WebSocket message to OBS."""
        if not self.ws:
            raise ConnectionError("Not connected to OBS")
        
        message = {
            "op": op,
            "d": data
        }
        
        if op == Opcode.REQUEST.value:
            self.request_id += 1
            message["d"]["requestId"] = str(self.request_id)
        
        self.ws.send(json.dumps(message))
    
    def _receive_message(self) -> Dict[str, Any]:
        """Receive a WebSocket message from OBS."""
        if not self.ws:
            raise ConnectionError("Not connected to OBS")
        
        try:
            data = self.ws.recv()
            if isinstance(data, bytes):
                data = data.decode('utf-8')
            return json.loads(data)
        except Exception as e:
            raise ConnectionError(f"Failed to receive message: {e}")
    
    def _send_request(self, request_type: str, request_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a request to OBS and wait for response."""
        if not self.connected:
            raise ConnectionError("Not connected to OBS")
        
        with self._lock:
            request_id = self.request_id + 1
            self.request_id = request_id
            
            request = {
                "requestType": request_type,
                "requestId": str(request_id)
            }
            
            if request_data:
                request["requestData"] = request_data
            
            self._send_message(Opcode.REQUEST.value, request)
            
            # Wait for response
            start_time = time.time()
            while time.time() - start_time < 5:  # 5 second timeout
                try:
                    response = self._receive_message()
                    if response.get("op") == Opcode.REQUEST_RESPONSE.value:
                        response_data = response.get("d", {})
                        if response_data.get("requestId") == str(request_id):
                            return response_data
                except Exception:
                    time.sleep(0.1)
            
            raise TimeoutError(f"Timeout waiting for response to {request_type}")
    
    def start_recording(self, output_path: str) -> bool:
        """
        Start recording in OBS.
        
        Args:
            output_path: Path where the recording should be saved.
            
        Returns:
            True if recording started successfully, False otherwise.
            
        Raises:
            ConnectionError: If not connected to OBS.
        """
        if not self.connected:
            raise ConnectionError("Not connected to OBS")
        
        try:
            # First check if recording is already active
            status_response = self._send_request("GetRecordStatus")
            if status_response.get("responseData", {}).get("outputActive", False):
                print("Recording is already active")
                return False
            
            # Start recording
            response = self._send_request("StartRecord")
            if response.get("requestStatus", {}).get("code") == 100:
                self.recording = True
                self.output_path = output_path
                print(f"Recording started, will save to: {output_path}")
                return True
            
            return False
            
        except Exception as e:
            print(f"Failed to start recording: {e}")
            return False
    
    def stop_recording(self) -> str:
        """
        Stop recording in OBS and return the output file path.
        
        Returns:
            Path to the recorded file, or empty string if recording failed.
            
        Raises:
            ConnectionError: If not connected to OBS.
        """
        if not self.connected:
            raise ConnectionError("Not connected to OBS")
        
        if not self.recording:
            print("No recording in progress")
            return ""
        
        try:
            # Stop recording
            response = self._send_request("StopRecord")
            
            if response.get("requestStatus", {}).get("code") == 100:
                self.recording = False
                
                # Try to get the output path from response
                output_path = response.get("responseData", {}).get("outputPath", "")
                if output_path:
                    return output_path
                
                # Fall back to the path we stored
                return self.output_path
            
            return ""
            
        except Exception as e:
            print(f"Failed to stop recording: {e}")
            self.recording = False
            return ""
    
    def disconnect(self) -> None:
        """Disconnect from OBS WebSocket server."""
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        
        self.ws = None
        self.connected = False
        self.recording = False
        self.output_path = ""
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self.recording:
            self.stop_recording()
        self.disconnect()


# Example usage
if __name__ == "__main__":
    # Example demonstrating the usage
    print("OBS Spectator Capture Module")
    print("=" * 40)
    
    # Create capture instance
    capture = OBSSpectatorCapture(
        host="localhost",
        port=4455,
        password=""  # Set your OBS WebSocket password here
    )
    
    try:
        # Connect to OBS
        print("Connecting to OBS...")
        if capture.connect():
            print("Connected successfully!")
            
            # Start recording
            print("Starting recording...")
            if capture.start_recording("/tmp/obs_recording.mp4"):
                print("Recording started!")
                
                # Simulate some recording time
                import time
                print("Recording for 5 seconds...")
                time.sleep(5)
                
                # Stop recording
                print("Stopping recording...")
                output_path = capture.stop_recording()
                if output_path:
                    print(f"Recording saved to: {output_path}")
                else:
                    print("Failed to get recording path")
            else:
                print("Failed to start recording")
        else:
            print("Failed to connect to OBS")
            
    except ImportError as e:
        print(f"Import error: {e}")
        print("Please install websocket-client: pip install websocket-client")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Clean up
        capture.disconnect()
        print("Disconnected from OBS")