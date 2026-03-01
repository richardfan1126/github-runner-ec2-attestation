"""Property-based tests for execution manager

Feature: github-actions-remote-executor
Tests Properties 18, 29, 36 from the design document
"""
import pytest
from datetime import datetime, timedelta, timezone
from hypothesis import given, strategies as st, assume, settings
from src.execution_manager import ExecutionManager
from src.models import ExecutionStatus


# Custom strategies for generating test data
@st.composite
def valid_github_url(draw):
    """Generate valid GitHub repository URLs"""
    owner = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-',
        min_size=1,
        max_size=39
    ))
    repo = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-',
        min_size=1,
        max_size=100
    ))
    return f"https://github.com/{owner}/{repo}"


@st.composite
def valid_commit_hash(draw):
    """Generate valid Git commit SHA (40 hex characters)"""
    return draw(st.text(alphabet='0123456789abcdef', min_size=40, max_size=40))


@st.composite
def valid_script_path(draw):
    """Generate valid script paths"""
    components = draw(st.lists(
        st.text(
            alphabet=st.characters(blacklist_characters='\\/:*?"<>|'),
            min_size=1,
            max_size=50
        ).filter(lambda x: '..' not in x and x.strip()),
        min_size=1,
        max_size=5
    ))
    return '/'.join(components)


@st.composite
def execution_params(draw):
    """Generate parameters for creating an execution"""
    return {
        'repository_url': draw(valid_github_url()),
        'commit_hash': draw(valid_commit_hash()),
        'script_path': draw(valid_script_path()),
        'timeout_seconds': draw(st.integers(min_value=1, max_value=3600))
    }


# Property 18: Execution ID Uniqueness
# Feature: github-actions-remote-executor, Property 18: Execution ID Uniqueness
@given(
    params_list=st.lists(execution_params(), min_size=2, max_size=100),
    retention_hours=st.integers(min_value=1, max_value=168)
)
@settings(max_examples=100)
def test_property_18_execution_id_uniqueness(params_list, retention_hours):
    """
    Property 18: For any set of execution requests, all generated execution IDs
    should be unique.
    
    Validates: Requirements 4.7
    """
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    execution_ids = set()
    
    # Create multiple executions
    for params in params_list:
        record = manager.create_execution(**params)
        
        # Check that execution ID is unique
        assert record.execution_id not in execution_ids, \
            f"Duplicate execution ID generated: {record.execution_id}"
        
        execution_ids.add(record.execution_id)
    
    # Verify all IDs are unique
    assert len(execution_ids) == len(params_list), \
        "Number of unique IDs should match number of executions"


@given(
    params=execution_params(),
    retention_hours=st.integers(min_value=1, max_value=168)
)
def test_property_18_execution_id_format(params, retention_hours):
    """
    Property 18 (variant): Execution IDs should be valid UUIDs.
    
    Validates: Requirements 4.7
    """
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    record = manager.create_execution(**params)
    
    # Check that execution ID is a non-empty string
    assert isinstance(record.execution_id, str), "Execution ID should be a string"
    assert len(record.execution_id) > 0, "Execution ID should not be empty"
    
    # Check UUID format (36 characters with hyphens in specific positions)
    # Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    assert len(record.execution_id) == 36, "Execution ID should be 36 characters (UUID format)"
    assert record.execution_id[8] == '-', "UUID should have hyphen at position 8"
    assert record.execution_id[13] == '-', "UUID should have hyphen at position 13"
    assert record.execution_id[18] == '-', "UUID should have hyphen at position 18"
    assert record.execution_id[23] == '-', "UUID should have hyphen at position 23"


# Property 29: Execution Status Tracking
# Feature: github-actions-remote-executor, Property 29: Execution Status Tracking
@given(
    params=execution_params(),
    retention_hours=st.integers(min_value=1, max_value=168),
    final_status=st.sampled_from([
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT
    ]),
    exit_code=st.integers(min_value=-128, max_value=255)
)
def test_property_29_execution_status_tracking(params, retention_hours, final_status, exit_code):
    """
    Property 29: For any script execution, the status should transition correctly
    through the states: queued → running → (completed|failed|timed_out).
    
    Validates: Requirements 5.9
    """
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    # Create execution - should start in QUEUED state
    record = manager.create_execution(**params)
    assert record.status == ExecutionStatus.QUEUED, \
        "New execution should start in QUEUED state"
    assert record.started_at is None, "QUEUED execution should not have started_at"
    assert record.completed_at is None, "QUEUED execution should not have completed_at"
    assert record.exit_code is None, "QUEUED execution should not have exit_code"
    
    # Transition to RUNNING
    success = manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    assert success, "Status update should succeed"
    
    updated_record = manager.get_execution(record.execution_id)
    assert updated_record is not None, "Execution should still exist"
    assert updated_record.status == ExecutionStatus.RUNNING, \
        "Status should be RUNNING after update"
    assert updated_record.started_at is not None, \
        "RUNNING execution should have started_at timestamp"
    assert updated_record.completed_at is None, \
        "RUNNING execution should not have completed_at yet"
    
    # Transition to terminal state
    success = manager.update_status(record.execution_id, final_status, exit_code=exit_code)
    assert success, "Status update to terminal state should succeed"
    
    final_record = manager.get_execution(record.execution_id)
    assert final_record is not None, "Execution should still exist"
    assert final_record.status == final_status, \
        f"Status should be {final_status.value} after update"
    assert final_record.started_at is not None, \
        "Terminal state should preserve started_at timestamp"
    assert final_record.completed_at is not None, \
        "Terminal state should have completed_at timestamp"
    assert final_record.exit_code == exit_code, \
        f"Terminal state should have exit_code {exit_code}"


@given(
    params=execution_params(),
    retention_hours=st.integers(min_value=1, max_value=168)
)
def test_property_29_status_timestamps(params, retention_hours):
    """
    Property 29 (variant): Status transitions should update timestamps appropriately.
    
    Validates: Requirements 5.9
    """
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    # Create execution
    record = manager.create_execution(**params)
    created_at = record.created_at
    
    # Verify created_at is set
    assert created_at is not None, "Execution should have created_at timestamp"
    assert isinstance(created_at, datetime), "created_at should be a datetime"
    
    # Transition to RUNNING and check started_at
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    running_record = manager.get_execution(record.execution_id)
    
    assert running_record.started_at is not None, "RUNNING should set started_at"
    assert running_record.started_at >= created_at, \
        "started_at should be after or equal to created_at"
    
    # Transition to COMPLETED and check completed_at
    manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    completed_record = manager.get_execution(record.execution_id)
    
    assert completed_record.completed_at is not None, "COMPLETED should set completed_at"
    assert completed_record.completed_at >= running_record.started_at, \
        "completed_at should be after or equal to started_at"


@given(
    params=execution_params(),
    retention_hours=st.integers(min_value=1, max_value=168)
)
def test_property_29_nonexistent_execution_update(params, retention_hours):
    """
    Property 29 (variant): Updating status of non-existent execution should fail gracefully.
    
    Validates: Requirements 5.9
    """
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    # Try to update a non-existent execution
    fake_id = "00000000-0000-0000-0000-000000000000"
    success = manager.update_status(fake_id, ExecutionStatus.RUNNING)
    
    assert not success, "Updating non-existent execution should return False"


# Property 36: Output Retention Period
# Feature: github-actions-remote-executor, Property 36: Output Retention Period
@given(
    params_list=st.lists(execution_params(), min_size=1, max_size=20),
    retention_hours=st.integers(min_value=1, max_value=24)
)
@settings(max_examples=100)
def test_property_36_output_retention_period(params_list, retention_hours):
    """
    Property 36: For any completed execution, the output should be retained and
    accessible for the configured retention period, and removed after that period expires.
    
    Validates: Requirements 6.10
    """
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    # Create and complete multiple executions
    execution_ids = []
    for params in params_list:
        record = manager.create_execution(**params)
        execution_ids.append(record.execution_id)
        
        # Transition to terminal state
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Verify all executions exist
    for exec_id in execution_ids:
        record = manager.get_execution(exec_id)
        assert record is not None, f"Execution {exec_id} should exist before cleanup"
        assert record.status == ExecutionStatus.COMPLETED, "Execution should be completed"
    
    # Manually set completed_at to past retention period
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=retention_hours, minutes=1)
    for exec_id in execution_ids:
        record = manager.get_execution(exec_id)
        record.completed_at = cutoff_time
    
    # Run cleanup
    removed_count = manager.cleanup_expired()
    
    # Verify executions were removed
    assert removed_count == len(execution_ids), \
        f"Should remove {len(execution_ids)} expired executions, removed {removed_count}"
    
    for exec_id in execution_ids:
        record = manager.get_execution(exec_id)
        assert record is None, f"Expired execution {exec_id} should be removed"


@given(
    params_list=st.lists(execution_params(), min_size=1, max_size=20),
    retention_hours=st.integers(min_value=1, max_value=24)
)
def test_property_36_retention_preserves_recent(params_list, retention_hours):
    """
    Property 36 (variant): Cleanup should preserve executions within retention period.
    
    Validates: Requirements 6.10
    """
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    # Create and complete executions
    execution_ids = []
    for params in params_list:
        record = manager.create_execution(**params)
        execution_ids.append(record.execution_id)
        
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Run cleanup (executions are recent, should not be removed)
    removed_count = manager.cleanup_expired()
    
    # Verify no executions were removed
    assert removed_count == 0, "Recent executions should not be removed"
    
    for exec_id in execution_ids:
        record = manager.get_execution(exec_id)
        assert record is not None, f"Recent execution {exec_id} should still exist"


@given(
    params_list=st.lists(execution_params(), min_size=2, max_size=20),
    retention_hours=st.integers(min_value=1, max_value=24)
)
def test_property_36_retention_selective_cleanup(params_list, retention_hours):
    """
    Property 36 (variant): Cleanup should only remove expired terminal executions,
    preserving active and recent executions.
    
    Validates: Requirements 6.10
    """
    assume(len(params_list) >= 2)
    
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    # Create executions with different states
    expired_ids = []
    active_ids = []
    recent_ids = []
    
    # Create some expired completed executions
    for params in params_list[:len(params_list)//3]:
        record = manager.create_execution(**params)
        expired_ids.append(record.execution_id)
        
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
        
        # Set to expired
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=retention_hours, minutes=1)
        record = manager.get_execution(record.execution_id)
        record.completed_at = cutoff_time
    
    # Create some active executions (QUEUED or RUNNING)
    for params in params_list[len(params_list)//3:2*len(params_list)//3]:
        record = manager.create_execution(**params)
        active_ids.append(record.execution_id)
        # Leave in QUEUED or transition to RUNNING
        if len(active_ids) % 2 == 0:
            manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    
    # Create some recent completed executions
    for params in params_list[2*len(params_list)//3:]:
        record = manager.create_execution(**params)
        recent_ids.append(record.execution_id)
        
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
    
    # Run cleanup
    removed_count = manager.cleanup_expired()
    
    # Verify only expired executions were removed
    assert removed_count == len(expired_ids), \
        f"Should remove {len(expired_ids)} expired executions"
    
    # Expired should be gone
    for exec_id in expired_ids:
        record = manager.get_execution(exec_id)
        assert record is None, f"Expired execution {exec_id} should be removed"
    
    # Active should remain
    for exec_id in active_ids:
        record = manager.get_execution(exec_id)
        assert record is not None, f"Active execution {exec_id} should be preserved"
    
    # Recent should remain
    for exec_id in recent_ids:
        record = manager.get_execution(exec_id)
        assert record is not None, f"Recent execution {exec_id} should be preserved"


@given(
    params=execution_params(),
    retention_hours=st.integers(min_value=1, max_value=24),
    terminal_status=st.sampled_from([
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT
    ])
)
def test_property_36_retention_all_terminal_states(params, retention_hours, terminal_status):
    """
    Property 36 (variant): Retention policy should apply to all terminal states
    (completed, failed, timed_out).
    
    Validates: Requirements 6.10
    """
    manager = ExecutionManager(output_retention_hours=retention_hours)
    
    # Create execution in terminal state
    record = manager.create_execution(**params)
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    manager.update_status(record.execution_id, terminal_status, exit_code=1)
    
    # Set to expired
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=retention_hours, minutes=1)
    record = manager.get_execution(record.execution_id)
    record.completed_at = cutoff_time
    
    # Run cleanup
    removed_count = manager.cleanup_expired()
    
    # Verify execution was removed
    assert removed_count == 1, f"Should remove expired {terminal_status.value} execution"
    
    record = manager.get_execution(record.execution_id)
    assert record is None, f"Expired {terminal_status.value} execution should be removed"
