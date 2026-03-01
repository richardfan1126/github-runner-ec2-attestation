"""Output collection for script execution"""
import threading
from typing import Dict, Optional
from dataclasses import dataclass, field
from src.models import OutputData


@dataclass
class OutputBuffer:
    """Thread-safe buffer for storing execution output"""
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    complete: bool = False
    exit_code: Optional[int] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class OutputCollector:
    """Collects and stores output from script executions"""
    
    def __init__(self):
        """Initialize the output collector"""
        self._outputs: Dict[str, OutputBuffer] = {}
        self._store_lock = threading.Lock()
    
    def create_buffer(self, execution_id: str) -> None:
        """Create a new output buffer for an execution
        
        Args:
            execution_id: Unique execution identifier
        """
        with self._store_lock:
            if execution_id not in self._outputs:
                self._outputs[execution_id] = OutputBuffer()
    
    def capture_output(self, execution_id: str, stream: str, data: bytes) -> None:
        """Capture output data from execution
        
        Args:
            execution_id: Unique execution identifier
            stream: Output stream name ('stdout' or 'stderr')
            data: Output data bytes to capture
        
        Raises:
            ValueError: If execution_id doesn't exist or stream is invalid
        """
        with self._store_lock:
            if execution_id not in self._outputs:
                raise ValueError(f"Execution ID not found: {execution_id}")
        
        buffer = self._outputs[execution_id]
        
        if stream not in ('stdout', 'stderr'):
            raise ValueError(f"Invalid stream: {stream}")
        
        with buffer.lock:
            if stream == 'stdout':
                buffer.stdout.extend(data)
            else:
                buffer.stderr.extend(data)
    
    def mark_complete(self, execution_id: str, exit_code: int) -> None:
        """Mark execution as complete with exit code
        
        Args:
            execution_id: Unique execution identifier
            exit_code: Script exit code
        
        Raises:
            ValueError: If execution_id doesn't exist
        """
        with self._store_lock:
            if execution_id not in self._outputs:
                raise ValueError(f"Execution ID not found: {execution_id}")
        
        buffer = self._outputs[execution_id]
        
        with buffer.lock:
            buffer.complete = True
            buffer.exit_code = exit_code
    
    def get_output(self, execution_id: str, offset: int = 0) -> OutputData:
        """Retrieve output from specified offset
        
        Args:
            execution_id: Unique execution identifier
            offset: Byte offset to start retrieving from (applies to both streams)
        
        Returns:
            OutputData containing output from offset, current offsets, and completion status
        
        Raises:
            ValueError: If execution_id doesn't exist or offset is negative
        """
        if offset < 0:
            raise ValueError(f"Offset must be non-negative: {offset}")
        
        with self._store_lock:
            if execution_id not in self._outputs:
                raise ValueError(f"Execution ID not found: {execution_id}")
        
        buffer = self._outputs[execution_id]
        
        with buffer.lock:
            # Get output from offset
            stdout_bytes = bytes(buffer.stdout[offset:])
            stderr_bytes = bytes(buffer.stderr[offset:])
            
            # Decode to strings
            stdout_str = stdout_bytes.decode('utf-8', errors='replace')
            stderr_str = stderr_bytes.decode('utf-8', errors='replace')
            
            # Current offsets are the total lengths
            stdout_offset = len(buffer.stdout)
            stderr_offset = len(buffer.stderr)
            
            return OutputData(
                stdout=stdout_str,
                stderr=stderr_str,
                stdout_offset=stdout_offset,
                stderr_offset=stderr_offset,
                complete=buffer.complete,
                exit_code=buffer.exit_code
            )
    
    def remove_output(self, execution_id: str) -> None:
        """Remove output buffer for an execution
        
        Args:
            execution_id: Unique execution identifier
        """
        with self._store_lock:
            if execution_id in self._outputs:
                del self._outputs[execution_id]
    
    def has_output(self, execution_id: str) -> bool:
        """Check if output buffer exists for execution
        
        Args:
            execution_id: Unique execution identifier
        
        Returns:
            True if output buffer exists, False otherwise
        """
        with self._store_lock:
            return execution_id in self._outputs
