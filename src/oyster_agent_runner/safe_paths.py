"""Concurrency-safe path utilities for isolated workspaces.

This module provides tempfile-based path management to replace hardcoded
/tmp/paper_run / /tmp/integ_bot patterns. Future scripts should import
from here instead of hardcoding paths.
"""

import shutil
import socket
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, List


def make_isolated_workspace(prefix: str = 'oyster_') -> Path:
    """Create an isolated workspace directory using tempfile.mkdtemp.
    
    Args:
        prefix: Prefix for the temporary directory name.
        
    Returns:
        Path object pointing to the created directory.
        
    Note:
        The caller is responsible for cleanup. For automatic cleanup,
        use the IsolatedRun context manager instead.
    """
    return Path(tempfile.mkdtemp(prefix=prefix))


def pick_free_port() -> int:
    """Find a free port by binding to port 0.
    
    Creates a socket, binds to port 0 (OS assigns free port),
    reads the assigned port number, and closes the socket.
    
    Returns:
        An available port number as int.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


class IsolatedRun:
    """Context manager for isolated run environment.
    
    Creates a workspace directory and allocates 3 ports (paper/rcon/obs).
    Automatically cleans up on exit.
    
    Attributes:
        workspace: Path to the isolated workspace directory.
        paper_port: Port allocated for paper server.
        rcon_port: Port allocated for RCON.
        obs_port: Port allocated for observation/monitoring.
        
    Example:
        with IsolatedRun() as run:
            print(run.workspace)
            print(run.paper_port, run.rcon_port, run.obs_port)
        # Workspace is automatically cleaned up
    """
    
    def __init__(self, prefix: str = 'oyster_'):
        """Initialize IsolatedRun.
        
        Args:
            prefix: Prefix for the temporary workspace directory.
        """
        self._prefix = prefix
        self._workspace: Path | None = None
        self._paper_port: int | None = None
        self._rcon_port: int | None = None
        self._obs_port: int | None = None
        self._cleaned_up = False
    
    def __enter__(self) -> 'IsolatedRun':
        """Enter context: create workspace and allocate ports.
        
        Returns:
            Self for use in with statement.
        """
        self._workspace = make_isolated_workspace(self._prefix)
        self._paper_port = pick_free_port()
        self._rcon_port = pick_free_port()
        self._obs_port = pick_free_port()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context: clean up workspace.
        
        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception value if an exception was raised.
            exc_tb: Exception traceback if an exception was raised.
        """
        self.cleanup_now()
    
    @property
    def workspace(self) -> Path:
        """Get the workspace path.
        
        Returns:
            Path to the isolated workspace directory.
            
        Raises:
            RuntimeError: If accessed outside of context manager.
        """
        if self._workspace is None:
            raise RuntimeError("IsolatedRun not initialized - use within context manager")
        return self._workspace
    
    @property
    def paper_port(self) -> int:
        """Get the paper server port.
        
        Returns:
            Port number for paper server.
            
        Raises:
            RuntimeError: If accessed outside of context manager.
        """
        if self._paper_port is None:
            raise RuntimeError("IsolatedRun not initialized - use within context manager")
        return self._paper_port
    
    @property
    def rcon_port(self) -> int:
        """Get the RCON port.
        
        Returns:
            Port number for RCON.
            
        Raises:
            RuntimeError: If accessed outside of context manager.
        """
        if self._rcon_port is None:
            raise RuntimeError("IsolatedRun not initialized - use within context manager")
        return self._rcon_port
    
    @property
    def obs_port(self) -> int:
        """Get the observation port.
        
        Returns:
            Port number for observation/monitoring.
            
        Raises:
            RuntimeError: If accessed outside of context manager.
        """
        if self._obs_port is None:
            raise RuntimeError("IsolatedRun not initialized - use within context manager")
        return self._obs_port
    
    def cleanup_now(self) -> None:
        """Immediately clean up the workspace.
        
        Removes the workspace directory if it exists. Safe to call
        multiple times - subsequent calls are no-ops.
        """
        if self._cleaned_up:
            return
        
        if self._workspace is not None and self._workspace.exists():
            shutil.rmtree(self._workspace, ignore_errors=True)
        
        self._cleaned_up = True