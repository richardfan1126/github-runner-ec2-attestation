"""Script execution for GitHub Actions Remote Executor"""
import os
import subprocess
import threading
import logging
from pathlib import Path
from typing import Optional

from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus


logger = logging.getLogger(__name__)


class ScriptExecutor:
    """Executes scripts asynchronously with output capture and timeout handling"""
    
    def __init__(
        self,
        execution_manager: ExecutionManager,
        output_collector: OutputCollector,
        temp_storage_path: str
    ):
        """
        Initialize script executor
        
        Args:
            execution_manager: Manager for execution lifecycle and state
            output_collector: Collector for capturing stdout/stderr
            temp_storage_path: Base path for temporary file storage
        """
        self._execution_manager = execution_manager
        self._output_collector = output_collector
        self._temp_storage_path = temp_storage_path
        self._active_processes = {}
        self._process_lock = threading.Lock()
    
    def execute_async(self, execution_id: str, script_path: str) -> None:
        """
        Execute script asynchronously as root in background thread
        
        Creates a background thread that:
        1. Updates status to RUNNING
        2. Executes the script as root
        3. Captures stdout/stderr streams
        4. Enforces timeout with process termination
        5. Captures exit code
        6. Updates final status (COMPLETED/FAILED/TIMED_OUT)
        7. Cleans up temporary files
        
        Args:
            execution_id: Unique execution identifier
            script_path: Path to script file to execute
        """
        thread = threading.Thread(
            target=self._execute_script,
            args=(execution_id, script_path),
            daemon=True
        )
        thread.start()
    
    def _execute_script(self, execution_id: str, script_path: str) -> None:
        """
        Internal method to execute script (runs in background thread)
        
        Args:
            execution_id: Unique execution identifier
            script_path: Path to script file to execute
        """
        temp_dir = None
        
        try:
            # Get execution record for timeout
            record = self._execution_manager.get_execution(execution_id)
            if not record:
                logger.error(f"Execution record not found: {execution_id}")
                return
            
            timeout = record.timeout_seconds
            
            # Create output buffer
            self._output_collector.create_buffer(execution_id)
            
            # Update status to RUNNING
            self._execution_manager.update_status(execution_id, ExecutionStatus.RUNNING)
            logger.info(f"Starting execution {execution_id}: {script_path}")
            
            # Make script executable
            os.chmod(script_path, 0o755)
            
            # Execute script as root with timeout
            # Note: Running as root requires the server itself to run as root
            process = subprocess.Popen(
                [script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.path.dirname(script_path)
            )
            
            # Store process for potential termination
            with self._process_lock:
                self._active_processes[execution_id] = process
            
            try:
                # Wait for completion with timeout
                stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
                exit_code = process.returncode
                
                # Capture output
                if stdout_bytes:
                    self._output_collector.capture_output(execution_id, 'stdout', stdout_bytes)
                if stderr_bytes:
                    self._output_collector.capture_output(execution_id, 'stderr', stderr_bytes)
                
                # Mark as complete
                self._output_collector.mark_complete(execution_id, exit_code)
                
                # Update status based on exit code
                if exit_code == 0:
                    final_status = ExecutionStatus.COMPLETED
                    logger.info(f"Execution {execution_id} completed successfully")
                else:
                    final_status = ExecutionStatus.FAILED
                    logger.warning(f"Execution {execution_id} failed with exit code {exit_code}")
                
                self._execution_manager.update_status(
                    execution_id,
                    final_status,
                    exit_code=exit_code
                )
                
            except subprocess.TimeoutExpired:
                # Timeout occurred - terminate process
                logger.warning(f"Execution {execution_id} timed out after {timeout}s")
                process.kill()
                
                # Capture any output before termination
                try:
                    stdout_bytes, stderr_bytes = process.communicate(timeout=5)
                    if stdout_bytes:
                        self._output_collector.capture_output(execution_id, 'stdout', stdout_bytes)
                    if stderr_bytes:
                        self._output_collector.capture_output(execution_id, 'stderr', stderr_bytes)
                except subprocess.TimeoutExpired:
                    # Force kill if still running
                    process.kill()
                    process.wait()
                
                # Mark as timed out
                self._output_collector.mark_complete(execution_id, -1)
                self._execution_manager.update_status(
                    execution_id,
                    ExecutionStatus.TIMED_OUT,
                    exit_code=-1
                )
            
            finally:
                # Remove from active processes
                with self._process_lock:
                    self._active_processes.pop(execution_id, None)
        
        except Exception as e:
            logger.error(f"Execution {execution_id} failed with exception: {e}", exc_info=True)
            
            # Mark as failed
            try:
                self._output_collector.mark_complete(execution_id, -1)
            except ValueError:
                # Buffer might not exist if error occurred early
                pass
            
            self._execution_manager.update_status(
                execution_id,
                ExecutionStatus.FAILED,
                exit_code=-1
            )
        
        finally:
            # Clean up temporary files
            self._cleanup_temp_files(execution_id, script_path)
    
    def _cleanup_temp_files(self, execution_id: str, script_path: str) -> None:
        """
        Clean up temporary files after execution
        
        Args:
            execution_id: Unique execution identifier
            script_path: Path to script file to clean up
        """
        try:
            # Remove script file
            if os.path.exists(script_path):
                os.remove(script_path)
                logger.debug(f"Removed script file: {script_path}")
            
            # Remove execution directory if empty
            script_dir = os.path.dirname(script_path)
            if os.path.exists(script_dir) and not os.listdir(script_dir):
                os.rmdir(script_dir)
                logger.debug(f"Removed empty directory: {script_dir}")
        
        except Exception as e:
            logger.warning(f"Failed to cleanup temp files for {execution_id}: {e}")
    
    def terminate(self, execution_id: str) -> bool:
        """
        Terminate a running execution
        
        Args:
            execution_id: Unique execution identifier
        
        Returns:
            True if process was terminated, False if not found or already completed
        """
        with self._process_lock:
            process = self._active_processes.get(execution_id)
            if process and process.poll() is None:
                logger.info(f"Terminating execution {execution_id}")
                process.kill()
                return True
        
        return False
