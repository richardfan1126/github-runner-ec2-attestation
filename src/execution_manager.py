"""Execution management for GitHub Actions Remote Executor"""
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Dict, Optional

from src.models import ExecutionRecord, ExecutionStatus


class ExecutionManager:
    """Manages execution lifecycle and state tracking"""
    
    def __init__(self, output_retention_hours: int):
        """
        Initialize execution manager

        Args:
            output_retention_hours: Hours to retain execution records after completion
        """
        self._executions: Dict[str, ExecutionRecord] = {}
        self._lock = Lock()
        self._output_retention_hours = output_retention_hours

        # Metrics tracking
        self._total_executions = 0
        self._successful_executions = 0
        self._failed_executions = 0
        self._execution_durations: list[float] = []  # Store durations in milliseconds
    
    def create_execution(
        self,
        repository_url: str,
        commit_hash: str,
        script_path: str,
        timeout_seconds: int
    ) -> ExecutionRecord:
        """
        Create a new execution record with unique ID

        Args:
            repository_url: GitHub repository URL
            commit_hash: Git commit SHA
            script_path: Path to script file in repository
            timeout_seconds: Execution timeout in seconds

        Returns:
            ExecutionRecord with unique execution_id and QUEUED status
        """
        execution_id = str(uuid.uuid4())

        record = ExecutionRecord(
            execution_id=execution_id,
            repository_url=repository_url,
            commit_hash=commit_hash,
            script_path=script_path,
            status=ExecutionStatus.QUEUED,
            created_at=datetime.now(timezone.utc),
            started_at=None,
            completed_at=None,
            exit_code=None,
            timeout_seconds=timeout_seconds
        )

        with self._lock:
            self._executions[execution_id] = record
            self._total_executions += 1

        return record
    
    def get_execution(self, execution_id: str) -> Optional[ExecutionRecord]:
        """
        Retrieve execution record by ID
        
        Args:
            execution_id: Unique execution identifier
        
        Returns:
            ExecutionRecord if found, None otherwise
        """
        with self._lock:
            return self._executions.get(execution_id)
    
    def update_status(
        self,
        execution_id: str,
        status: ExecutionStatus,
        exit_code: Optional[int] = None
    ) -> bool:
        """
        Update execution status with lifecycle tracking

        Tracks status transitions: queued → running → (completed|failed|timed_out)
        Updates timestamps appropriately for each transition.

        Args:
            execution_id: Unique execution identifier
            status: New execution status
            exit_code: Exit code (only for completed/failed/timed_out status)

        Returns:
            True if update succeeded, False if execution not found
        """
        with self._lock:
            record = self._executions.get(execution_id)
            if record is None:
                return False

            # Update status
            record.status = status

            # Update timestamps based on status transition
            if status == ExecutionStatus.RUNNING and record.started_at is None:
                record.started_at = datetime.now(timezone.utc)

            if status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT):
                if record.completed_at is None:
                    record.completed_at = datetime.now(timezone.utc)
                if exit_code is not None:
                    record.exit_code = exit_code

                # Update metrics
                if status == ExecutionStatus.COMPLETED:
                    self._successful_executions += 1
                elif status in (ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT):
                    self._failed_executions += 1

                # Track execution duration
                if record.started_at and record.completed_at:
                    duration_ms = (record.completed_at - record.started_at).total_seconds() * 1000
                    self._execution_durations.append(duration_ms)

            return True
    
    def cleanup_expired(self) -> int:
        """
        Remove executions past retention period
        
        Removes execution records that completed more than output_retention_hours ago.
        Only removes executions in terminal states (completed, failed, timed_out).
        
        Returns:
            Number of executions removed
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self._output_retention_hours)
        removed_count = 0
        
        with self._lock:
            expired_ids = []
            
            for execution_id, record in self._executions.items():
                # Only cleanup terminal states
                if record.status in (
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.TIMED_OUT
                ):
                    # Check if completed_at is past retention period
                    if record.completed_at and record.completed_at < cutoff_time:
                        expired_ids.append(execution_id)
            
            # Remove expired executions
            for execution_id in expired_ids:
                del self._executions[execution_id]
                removed_count += 1
        
        return removed_count
    
    def get_active_count(self) -> int:
        """
        Get count of active (queued or running) executions
        
        Returns:
            Number of executions in QUEUED or RUNNING state
        """
        with self._lock:
            return sum(
                1 for record in self._executions.values()
                if record.status in (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING)
            )
    
    def get_total_count(self) -> int:
        """
        Get total count of all executions
        
        Returns:
            Total number of execution records
        """
        with self._lock:
            return len(self._executions)

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get execution metrics for monitoring

        Returns:
            Dictionary containing:
            - total_executions: Total number of executions created
            - successful_executions: Number of completed executions
            - failed_executions: Number of failed/timed_out executions
            - average_duration_ms: Average execution duration in milliseconds
            - active_executions: Number of currently active executions
        """
        with self._lock:
            # Calculate average duration
            avg_duration = 0.0
            if self._execution_durations:
                avg_duration = sum(self._execution_durations) / len(self._execution_durations)

            # Count active executions
            active_count = sum(
                1 for record in self._executions.values()
                if record.status in (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING)
            )

            return {
                "total_executions": self._total_executions,
                "successful_executions": self._successful_executions,
                "failed_executions": self._failed_executions,
                "average_duration_ms": round(avg_duration, 2),
                "active_executions": active_count
            }

