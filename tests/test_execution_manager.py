"""Unit tests for ExecutionManager"""
import time
from datetime import datetime, timedelta, timezone
from threading import Thread

import pytest

from src.execution_manager import ExecutionManager
from src.models import ExecutionStatus


def test_create_execution():
    """Test creating a new execution record"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    assert record.execution_id is not None
    assert record.repository_url == "https://github.com/owner/repo"
    assert record.commit_hash == "abc123"
    assert record.script_path == "scripts/test.sh"
    assert record.status == ExecutionStatus.QUEUED
    assert record.timeout_seconds == 300
    assert record.created_at is not None
    assert record.started_at is None
    assert record.completed_at is None
    assert record.exit_code is None


def test_execution_id_uniqueness():
    """Test that execution IDs are unique"""
    manager = ExecutionManager(output_retention_hours=24)
    
    ids = set()
    for _ in range(100):
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="abc123",
            script_path="scripts/test.sh",
            timeout_seconds=300
        )
        ids.add(record.execution_id)
    
    # All IDs should be unique
    assert len(ids) == 100


def test_get_execution():
    """Test retrieving execution by ID"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    retrieved = manager.get_execution(record.execution_id)
    assert retrieved is not None
    assert retrieved.execution_id == record.execution_id
    assert retrieved.repository_url == record.repository_url


def test_get_nonexistent_execution():
    """Test retrieving non-existent execution returns None"""
    manager = ExecutionManager(output_retention_hours=24)
    
    retrieved = manager.get_execution("nonexistent-id")
    assert retrieved is None


def test_update_status_to_running():
    """Test updating status from queued to running"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    assert record.status == ExecutionStatus.QUEUED
    assert record.started_at is None
    
    success = manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    assert success is True
    
    updated = manager.get_execution(record.execution_id)
    assert updated.status == ExecutionStatus.RUNNING
    assert updated.started_at is not None


def test_update_status_to_completed():
    """Test updating status to completed with exit code"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    success = manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    assert success is True
    
    updated = manager.get_execution(record.execution_id)
    assert updated.status == ExecutionStatus.COMPLETED
    assert updated.completed_at is not None
    assert updated.exit_code == 0


def test_update_status_to_failed():
    """Test updating status to failed with exit code"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    success = manager.update_status(record.execution_id, ExecutionStatus.FAILED, exit_code=1)
    assert success is True
    
    updated = manager.get_execution(record.execution_id)
    assert updated.status == ExecutionStatus.FAILED
    assert updated.completed_at is not None
    assert updated.exit_code == 1


def test_update_status_to_timed_out():
    """Test updating status to timed out"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    success = manager.update_status(record.execution_id, ExecutionStatus.TIMED_OUT, exit_code=-1)
    assert success is True
    
    updated = manager.get_execution(record.execution_id)
    assert updated.status == ExecutionStatus.TIMED_OUT
    assert updated.completed_at is not None
    assert updated.exit_code == -1


def test_update_status_nonexistent_execution():
    """Test updating status of non-existent execution returns False"""
    manager = ExecutionManager(output_retention_hours=24)
    
    success = manager.update_status("nonexistent-id", ExecutionStatus.RUNNING)
    assert success is False


def test_status_transition_lifecycle():
    """Test complete status transition lifecycle"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    # Initial state: QUEUED
    assert record.status == ExecutionStatus.QUEUED
    assert record.started_at is None
    assert record.completed_at is None
    
    # Transition to RUNNING
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    updated = manager.get_execution(record.execution_id)
    assert updated.status == ExecutionStatus.RUNNING
    assert updated.started_at is not None
    assert updated.completed_at is None
    
    # Transition to COMPLETED
    manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    updated = manager.get_execution(record.execution_id)
    assert updated.status == ExecutionStatus.COMPLETED
    assert updated.started_at is not None
    assert updated.completed_at is not None
    assert updated.exit_code == 0


def test_cleanup_expired_removes_old_executions():
    """Test cleanup removes executions past retention period"""
    manager = ExecutionManager(output_retention_hours=1)
    
    # Create and complete an execution
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Manually set completed_at to past retention period
    updated = manager.get_execution(record.execution_id)
    updated.completed_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    # Cleanup should remove it
    removed = manager.cleanup_expired()
    assert removed == 1
    
    # Execution should no longer exist
    assert manager.get_execution(record.execution_id) is None


def test_cleanup_expired_keeps_recent_executions():
    """Test cleanup keeps executions within retention period"""
    manager = ExecutionManager(output_retention_hours=24)
    
    # Create and complete an execution
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Cleanup should not remove it (within retention period)
    removed = manager.cleanup_expired()
    assert removed == 0
    
    # Execution should still exist
    assert manager.get_execution(record.execution_id) is not None


def test_cleanup_expired_keeps_active_executions():
    """Test cleanup does not remove active executions"""
    manager = ExecutionManager(output_retention_hours=1)
    
    # Create executions in various states
    queued = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    running = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    manager.update_status(running.execution_id, ExecutionStatus.RUNNING)
    
    # Manually set created_at to past retention period
    queued_record = manager.get_execution(queued.execution_id)
    queued_record.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    running_record = manager.get_execution(running.execution_id)
    running_record.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    # Cleanup should not remove active executions
    removed = manager.cleanup_expired()
    assert removed == 0
    
    assert manager.get_execution(queued.execution_id) is not None
    assert manager.get_execution(running.execution_id) is not None


def test_get_active_count():
    """Test getting count of active executions"""
    manager = ExecutionManager(output_retention_hours=24)
    
    assert manager.get_active_count() == 0
    
    # Create queued execution
    record1 = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    assert manager.get_active_count() == 1
    
    # Create running execution
    record2 = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    manager.update_status(record2.execution_id, ExecutionStatus.RUNNING)
    assert manager.get_active_count() == 2
    
    # Complete one execution
    manager.update_status(record1.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    assert manager.get_active_count() == 1
    
    # Complete second execution
    manager.update_status(record2.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    assert manager.get_active_count() == 0


def test_get_total_count():
    """Test getting total count of all executions"""
    manager = ExecutionManager(output_retention_hours=24)
    
    assert manager.get_total_count() == 0
    
    for i in range(5):
        manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="abc123",
            script_path="scripts/test.sh",
            timeout_seconds=300
        )
    
    assert manager.get_total_count() == 5


def test_concurrent_access():
    """Test thread-safe concurrent access to execution store"""
    manager = ExecutionManager(output_retention_hours=24)
    results = []
    
    def create_executions():
        for _ in range(10):
            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash="abc123",
                script_path="scripts/test.sh",
                timeout_seconds=300
            )
            results.append(record.execution_id)
    
    # Create executions from multiple threads
    threads = [Thread(target=create_executions) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    
    # All execution IDs should be unique
    assert len(results) == 50
    assert len(set(results)) == 50
    
    # All executions should be retrievable
    for execution_id in results:
        assert manager.get_execution(execution_id) is not None


def test_concurrent_status_updates():
    """Test thread-safe concurrent status updates"""
    manager = ExecutionManager(output_retention_hours=24)
    
    # Create multiple executions
    execution_ids = []
    for _ in range(10):
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="abc123",
            script_path="scripts/test.sh",
            timeout_seconds=300
        )
        execution_ids.append(record.execution_id)
    
    def update_statuses(ids):
        for execution_id in ids:
            manager.update_status(execution_id, ExecutionStatus.RUNNING)
            manager.update_status(execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Update statuses from multiple threads
    threads = [Thread(target=update_statuses, args=(execution_ids[i::2],)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    
    # All executions should be completed
    for execution_id in execution_ids:
        record = manager.get_execution(execution_id)
        assert record.status == ExecutionStatus.COMPLETED
        assert record.exit_code == 0


def test_concurrent_cleanup():
    """Test thread-safe concurrent cleanup operations"""
    manager = ExecutionManager(output_retention_hours=1)
    
    # Create and complete multiple executions
    for _ in range(20):
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="abc123",
            script_path="scripts/test.sh",
            timeout_seconds=300
        )
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
        
        # Set half to expired
        if _ % 2 == 0:
            updated = manager.get_execution(record.execution_id)
            updated.completed_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    cleanup_results = []
    
    def cleanup():
        removed = manager.cleanup_expired()
        cleanup_results.append(removed)
    
    # Run cleanup from multiple threads
    threads = [Thread(target=cleanup) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    
    # Total removed should equal expired executions (10)
    # Each thread may remove some, but total should be 10
    assert sum(cleanup_results) == 10
    assert manager.get_total_count() == 10


def test_status_transition_invalid_sequence():
    """Test that invalid status transitions are still applied (no validation)"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    # Jump directly from QUEUED to COMPLETED (skipping RUNNING)
    # The implementation allows this - no validation of transition sequence
    success = manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    assert success is True
    
    updated = manager.get_execution(record.execution_id)
    assert updated.status == ExecutionStatus.COMPLETED
    assert updated.started_at is None  # Never set because we skipped RUNNING
    assert updated.completed_at is not None
    assert updated.exit_code == 0


def test_cleanup_multiple_terminal_states():
    """Test cleanup handles all terminal states (completed, failed, timed_out)"""
    manager = ExecutionManager(output_retention_hours=1)
    
    # Create executions in different terminal states
    completed = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    manager.update_status(completed.execution_id, ExecutionStatus.RUNNING)
    manager.update_status(completed.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    failed = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    manager.update_status(failed.execution_id, ExecutionStatus.RUNNING)
    manager.update_status(failed.execution_id, ExecutionStatus.FAILED, exit_code=1)
    
    timed_out = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    manager.update_status(timed_out.execution_id, ExecutionStatus.RUNNING)
    manager.update_status(timed_out.execution_id, ExecutionStatus.TIMED_OUT, exit_code=-1)
    
    # Set all to expired
    for execution_id in [completed.execution_id, failed.execution_id, timed_out.execution_id]:
        record = manager.get_execution(execution_id)
        record.completed_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    # Cleanup should remove all three
    removed = manager.cleanup_expired()
    assert removed == 3
    assert manager.get_total_count() == 0


def test_concurrent_get_and_update():
    """Test concurrent reads and writes don't cause data corruption"""
    manager = ExecutionManager(output_retention_hours=24)
    
    # Create initial execution
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    execution_id = record.execution_id
    
    read_results = []
    
    def reader():
        for _ in range(50):
            result = manager.get_execution(execution_id)
            if result:
                read_results.append(result.status)
            time.sleep(0.001)
    
    def writer():
        time.sleep(0.01)
        manager.update_status(execution_id, ExecutionStatus.RUNNING)
        time.sleep(0.02)
        manager.update_status(execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Run concurrent reads and writes
    reader_threads = [Thread(target=reader) for _ in range(3)]
    writer_thread = Thread(target=writer)
    
    for thread in reader_threads:
        thread.start()
    writer_thread.start()
    
    for thread in reader_threads:
        thread.join()
    writer_thread.join()
    
    # Final state should be COMPLETED
    final = manager.get_execution(execution_id)
    assert final.status == ExecutionStatus.COMPLETED
    assert final.exit_code == 0
    
    # All reads should have returned valid statuses
    assert len(read_results) > 0
    for status in read_results:
        assert status in [ExecutionStatus.QUEUED, ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED]


def test_update_status_preserves_timestamps():
    """Test that updating status doesn't overwrite existing timestamps"""
    manager = ExecutionManager(output_retention_hours=24)
    
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="abc123",
        script_path="scripts/test.sh",
        timeout_seconds=300
    )
    
    # Transition to RUNNING
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    first_update = manager.get_execution(record.execution_id)
    first_started_at = first_update.started_at
    
    # Update to RUNNING again (shouldn't change started_at)
    time.sleep(0.01)
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    second_update = manager.get_execution(record.execution_id)
    
    assert second_update.started_at == first_started_at
    
    # Transition to COMPLETED
    manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    third_update = manager.get_execution(record.execution_id)
    first_completed_at = third_update.completed_at
    
    # Update to COMPLETED again (shouldn't change completed_at)
    time.sleep(0.01)
    manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    fourth_update = manager.get_execution(record.execution_id)
    
    assert fourth_update.completed_at == first_completed_at
