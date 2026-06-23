#!/usr/bin/env python3
"""
G282 · bin/recorder_mp4_faststart.py

Purpose: Add ffmpeg -movflags=+faststart to recording cmd; ensures mp4 moov atom
at front so mid-record crashes leave a playable container.

This script wraps ffmpeg commands to add the faststart flag for MP4 recordings,
ensuring the moov atom is placed at the beginning of the file for immediate
playback and resilience against recording interruptions.
"""

import argparse
import os
import shlex
import subprocess
import sys
from typing import List


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Wrapper for ffmpeg that adds -movflags=+faststart for MP4 recordings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -i input.mp4 output.mp4
  %(prog)s -i input.mp4 -c:v libx264 -crf 23 output.mp4
  %(prog)s --dry-run -i input.mp4 output.mp4
  %(prog)s --verbose -i input.mp4 output.mp4
  
Note: When in doubt, use '--' to separate script options from ffmpeg options:
  %(prog)s --dry-run -- -i input.mp4 output.mp4
        """
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print the command that would be executed without running it'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Print the full command being executed'
    )
    
    # Parse known args first, leaving unknown args as ffmpeg_args
    args, ffmpeg_args = parser.parse_known_args(argv)
    
    # Handle the case where '--' is used as separator
    if '--' in ffmpeg_args:
        idx = ffmpeg_args.index('--')
        ffmpeg_args = ffmpeg_args[idx + 1:]
    
    # Store ffmpeg args in the namespace
    args.ffmpeg_args = ffmpeg_args
    
    return args


def is_mp4_output(args: List[str]) -> bool:
    """
    Check if the output file is an MP4 file based on the arguments.
    
    Args:
        args: List of ffmpeg arguments
        
    Returns:
        True if output appears to be an MP4 file, False otherwise
    """
    # Look for output file (typically the last argument that doesn't start with -)
    output_files = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith('-'):
            # Skip the next argument if this is a flag with a value
            if arg in ['-i', '-c', '-codec', '-f', '-format', '-preset', '-crf', '-b', '-bitrate']:
                i += 2
            else:
                i += 1
        else:
            # This could be an output file
            output_files.append(arg)
            i += 1
    
    # Check if any output file ends with .mp4
    for output_file in output_files:
        if output_file.lower().endswith('.mp4'):
            return True
    
    return False


def add_faststart_flag(args: List[str]) -> List[str]:
    """
    Add -movflags=+faststart to ffmpeg arguments if not already present.
    
    Args:
        args: Original ffmpeg arguments
        
    Returns:
        Modified arguments with faststart flag added
    """
    # Check if -movflags is already present
    for i, arg in enumerate(args):
        if arg == '-movflags':
            # Check if faststart is in the next argument
            if i + 1 < len(args):
                movflags_value = args[i + 1]
                if '+faststart' not in movflags_value:
                    # Add faststart to existing movflags
                    args[i + 1] = f'{movflags_value}+faststart'
                return args
        elif arg.startswith('-movflags='):
            # Handle -movflags=value format
            if '+faststart' not in arg:
                args[i] = f'{arg}+faststart'
            return args
    
    # If we get here, -movflags wasn't found, so add it
    # Insert before output file (typically at the end before the last argument)
    if args:
        # Insert before the last argument (output file)
        args.insert(-1, '-movflags')
        args.insert(-1, '+faststart')
    
    return args


def find_ffmpeg() -> str:
    """
    Find ffmpeg executable in PATH.
    
    Returns:
        Path to ffmpeg executable
        
    Raises:
        FileNotFoundError: If ffmpeg is not found in PATH
    """
    # Check common locations first
    common_paths = [
        '/usr/bin/ffmpeg',
        '/usr/local/bin/ffmpeg',
        '/opt/homebrew/bin/ffmpeg',
    ]
    
    for path in common_paths:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    
    # Try to find in PATH
    import shutil
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return ffmpeg_path
    
    raise FileNotFoundError(
        "ffmpeg not found. Please install ffmpeg and ensure it's in your PATH."
    )


def main(argv: List[str]) -> int:
    """
    Main entry point for the script.
    
    Args:
        argv: Command line arguments
        
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    try:
        args = parse_args(argv)
        
        if not args.ffmpeg_args:
            print("Error: No ffmpeg arguments provided", file=sys.stderr)
            print("Examples:", file=sys.stderr)
            print("  recorder_mp4_faststart.py -i input.mp4 output.mp4", file=sys.stderr)
            print("  recorder_mp4_faststart.py --dry-run -i input.mp4 output.mp4", file=sys.stderr)
            return 1
        
        # Check if this appears to be an MP4 output
        if not is_mp4_output(args.ffmpeg_args):
            print("Warning: Output does not appear to be an MP4 file.", file=sys.stderr)
            print("The -movflags=+faststart flag is only useful for MP4 files.", file=sys.stderr)
            print("Continuing without modification...", file=sys.stderr)
            modified_args = args.ffmpeg_args
        else:
            modified_args = add_faststart_flag(args.ffmpeg_args.copy())
        
        # Find ffmpeg executable
        try:
            ffmpeg_path = find_ffmpeg()
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        
        # Build the full command
        full_cmd = [ffmpeg_path] + modified_args
        
        if args.verbose or args.dry_run:
            # Print the command in a shell-escaped format
            cmd_str = ' '.join(shlex.quote(arg) for arg in full_cmd)
            print(f"Command: {cmd_str}")
        
        if args.dry_run:
            print("Dry run: Command not executed")
            return 0
        
        # Execute the command
        try:
            result = subprocess.run(full_cmd, check=False)
            return result.returncode
        except KeyboardInterrupt:
            print("\nInterrupted by user", file=sys.stderr)
            return 130
        except Exception as e:
            print(f"Error executing ffmpeg: {e}", file=sys.stderr)
            return 1
            
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
