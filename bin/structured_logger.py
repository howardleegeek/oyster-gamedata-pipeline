#!/usr/bin/env python3
"""
Structured JSON-line logger with correlation IDs (vendor + clip + step).

This module provides a structured logging system that outputs JSON lines with
correlation IDs for tracking operations across distributed systems. Each log
entry includes vendor, clip, and step identifiers for traceability.

Example:
    >>> logger = StructuredLogger("vendor1", "clip123", "processing")
    >>> logger.info("Processing started", duration_ms=150)
    {"level": "INFO", "vendor": "vendor1", "clip": "clip123", "step": "processing",
     "message": "Processing started", "timestamp": "2024-01-15T10:30:00Z", "duration_ms": 150}
"""

import json
import sys
import argparse
import logging
from datetime import datetime
from typing import Optional, Dict, Any, TextIO, Union
from enum import Enum
from contextlib import contextmanager
import time


class LogLevel(Enum):
    """Log level enumeration matching standard logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    
    @classmethod
    def from_string(cls, level_str: str) -> "LogLevel":
        """Convert string to LogLevel, defaulting to INFO for invalid values."""
        try:
            return cls[level_str.upper()]
        except KeyError:
            return cls.INFO


class StructuredLogger:
    """
    JSON-line logger with vendor/clip/step correlation IDs.
    
    Attributes:
        vendor: Vendor identifier
        clip: Clip identifier  
        step: Step identifier
        output: Output stream for log entries
        min_level: Minimum log level to output
        include_timestamp: Whether to include timestamps in logs
    """
    
    def __init__(self, vendor: str, clip: str, step: str, 
                 output: TextIO = sys.stdout, 
                 min_level: Union[str, LogLevel] = LogLevel.INFO,
                 include_timestamp: bool = True):
        """
        Initialize structured logger.
        
        Args:
            vendor: Vendor identifier
            clip: Clip identifier
            step: Step identifier
            output: Output stream (default: stdout)
            min_level: Minimum log level (string or LogLevel)
            include_timestamp: Include timestamp in logs (default: True)
        """
        self.vendor = vendor
        self.clip = clip
        self.step = step
        self.output = output
        self.include_timestamp = include_timestamp
        
        if isinstance(min_level, str):
            self.min_level = LogLevel.from_string(min_level)
        else:
            self.min_level = min_level
    
    def _should_log(self, level: LogLevel) -> bool:
        """Check if message should be logged based on level priority."""
        level_priority = {
            LogLevel.DEBUG: 0,
            LogLevel.INFO: 1,
            LogLevel.WARNING: 2,
            LogLevel.ERROR: 3,
            LogLevel.CRITICAL: 4
        }
        return level_priority[level] >= level_priority[self.min_level]
    
    def _log(self, level: LogLevel, message: str, **kwargs: Any) -> None:
        """
        Internal logging method.
        
        Args:
            level: Log level
            message: Log message
            **kwargs: Additional fields to include in log entry
        """
        if not self._should_log(level):
            return
            
        entry: Dict[str, Any] = {
            "level": level.value,
            "vendor": self.vendor,
            "clip": self.clip,
            "step": self.step,
            "message": message,
        }
        
        if self.include_timestamp:
            entry["timestamp"] = datetime.utcnow().isoformat() + "Z"
        
        # Add any additional fields
        if kwargs:
            entry.update(kwargs)
        
        # Write JSON line
        json.dump(entry, self.output)
        self.output.write("\n")
        self.output.flush()
    
    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        self._log(LogLevel.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        self._log(LogLevel.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        self._log(LogLevel.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message."""
        self._log(LogLevel.ERROR, message, **kwargs)
    
    def critical(self, message: str, **kwargs: Any) -> None:
        """Log critical message."""
        self._log(LogLevel.CRITICAL, message, **kwargs)
    
    @contextmanager
    def timed_operation(self, operation_name: str, **kwargs: Any):
        """
        Context manager for timing operations.
        
        Args:
            operation_name: Name of the operation being timed
            **kwargs: Additional fields to include in start/end logs
            
        Yields:
            None
            
        Example:
            with logger.timed_operation("image_processing", image_id="img123"):
                process_image()
        """
        start_time = time.time()
        self.info(f"Starting operation: {operation_name}", 
                  operation=operation_name, **kwargs)
        
        try:
            yield
        except Exception as e:
            end_time = time.time()
            duration_ms = int((end_time - start_time) * 1000)
            self.error(f"Operation failed: {operation_name}", 
                       operation=operation_name, 
                       duration_ms=duration_ms,
                       error=str(type(e).__name__),
                       error_message=str(e),
                       **kwargs)
            raise
        else:
            end_time = time.time()
            duration_ms = int((end_time - start_time) * 1000)
            self.info(f"Completed operation: {operation_name}", 
                      operation=operation_name, 
                      duration_ms=duration_ms,
                      **kwargs)


def create_logger(vendor: str, clip: str, step: str, 
                  output: TextIO = sys.stdout,
                  level: Union[str, LogLevel] = "INFO",
                  **kwargs: Any) -> StructuredLogger:
    """
    Factory function to create a structured logger.
    
    Args:
        vendor: Vendor identifier
        clip: Clip identifier
        step: Step identifier
        output: Output stream (default: stdout)
        level: Minimum log level (string or LogLevel)
        **kwargs: Additional arguments passed to StructuredLogger
        
    Returns:
        StructuredLogger instance
    """
    return StructuredLogger(vendor, clip, step, output, level, **kwargs)


def main(argv=None) -> int:
    """
    Command-line interface for structured logger.
    
    Args:
        argv: Command line arguments (default: sys.argv[1:])
        
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description="Structured JSON-line logger with correlation IDs"
    )
    parser.add_argument(
        "--vendor", "-v", 
        required=True,
        help="Vendor identifier"
    )
    parser.add_argument(
        "--clip", "-c", 
        required=True,
        help="Clip identifier"
    )
    parser.add_argument(
        "--step", "-s", 
        required=True,
        help="Step identifier"
    )
    parser.add_argument(
        "--level", "-l",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Minimum log level (default: INFO)"
    )
    parser.add_argument(
        "--no-timestamp",
        action="store_true",
        help="Disable timestamp in log entries"
    )
    parser.add_argument(
        "--output", "-o",
        type=argparse.FileType("w"),
        default=sys.stdout,
        help="Output file (default: stdout)"
    )
    parser.add_argument(
        "message",
        nargs="?",
        help="Log message (if not provided, reads from stdin)"
    )
    parser.add_argument(
        "--extra",
        "-e",
        action="append",
        nargs=2,
        metavar=("KEY", "VALUE"),
        help="Extra key-value pairs to include in log"
    )
    
    args = parser.parse_args(argv)
    
    # Create logger
    logger = create_logger(
        vendor=args.vendor,
        clip=args.clip,
        step=args.step,
        output=args.output,
        level=args.level,
        include_timestamp=not args.no_timestamp
    )
    
    # Parse extra fields
    extra_fields = {}
    if args.extra:
        for key, value in args.extra:
            extra_fields[key] = value
    
    # Get message
    if args.message:
        message = args.message
    else:
        # Read from stdin
        message = sys.stdin.read().strip()
        if not message:
            print("Error: No message provided", file=sys.stderr)
            return 1
    
    # Determine log level from message prefix if present
    level = args.level
    message_lower = message.lower()
    
    if message_lower.startswith("debug:"):
        level = "DEBUG"
        message = message[6:].strip()
    elif message_lower.startswith("info:"):
        level = "INFO"
        message = message[5:].strip()
    elif message_lower.startswith("warning:"):
        level = "WARNING"
        message = message[8:].strip()
    elif message_lower.startswith("error:"):
        level = "ERROR"
        message = message[6:].strip()
    elif message_lower.startswith("critical:"):
        level = "CRITICAL"
        message = message[9:].strip()
    
    # Log the message
    log_method = {
        "DEBUG": logger.debug,
        "INFO": logger.info,
        "WARNING": logger.warning,
        "ERROR": logger.error,
        "CRITICAL": logger.critical
    }[level]
    
    log_method(message, **extra_fields)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())