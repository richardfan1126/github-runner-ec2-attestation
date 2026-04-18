"""Property-based tests for periodic cleanup scheduling

Feature: github-actions-remote-executor
Tests Property 151 from the design document
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from hypothesis import given, strategies as st, assume, settings
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus


# Custom strategies
@st.composite
def valid_github_url(draw):
    owner = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyz0123456789',
        min_size=1, max_size=20
    ))
    repo = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyz0123456789',
        min_size=1, max_size=20
    ))
    return f"https://github.com/{owner}/{repo}"


@st.composite
def valid_commit_hash(draw):
    return draw(st.text(alphabet='0123456789abcdef', min_size=40, max_size=40))


@st.composite
def execution_params(draw):
    return {
        'repository_url': draw(valid_github_url()),
        'commit_hash': draw(valid_commit_hash()),
        'script_path': draw(st.text(
            alphabet='abcdefghijklmnopqrstuvwxyz/',
            min_size=1, max_size=30
        ).filter(lambda x: x.strip() and not x.startswith('/'))),
        'timeout_seconds': draw(st.integers(min_value=1, max_value=3600))
    }


@given(
    params_list=st.lists(execution_params(), min_size=1, max_size=10),
    retention_hours=st.integers(min_value=1, max_value=24)
)
@settings(max_examples=100)
def test_property_151_cleanup_removes_output_and_encryption_context(params_list, retention_hours):
    """
    Property 151: Periodic Cleanup Scheduling

    For any set of expired execution records, cleanup_expired should call
    remove_output on the OutputCollector and remove_encryption_context on
    the EncryptionManager for each expired record.

    **Validates: Requirements 8.15, 8.16**
    """
    encryption_manager = MagicMock()
    output_collector = OutputCollector()
    manager = ExecutionManager(
        output_retention_hours=retention_hours,
        encryption_manager=encryption_manager,
        output_collector=output_collector,
    )

    execution_ids = []
    for params in params_list:
        record = manager.create_execution(**params)
        execution_ids.append(record.execution_id)

        # Create an output buffer for this execution
        output_collector.create_buffer(record.execution_id)
        output_collector.capture_output(record.execution_id, 'stdout', b'some output')

        # Move to terminal state
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

        # Expire the record
        rec = manager.get_execution(record.execution_id)
        rec.completed_at = datetime.now(timezone.utc) - timedelta(hours=retention_hours, minutes=1)

    # Run cleanup
    removed = manager.cleanup_expired()

    # All should be removed
    assert removed == len(execution_ids)

    # Verify remove_encryption_context was called for each expired ID
    called_enc_ids = {call.args[0] for call in encryption_manager.remove_encryption_context.call_args_list}
    assert called_enc_ids == set(execution_ids)

    # Verify output was removed for each expired ID
    for exec_id in execution_ids:
        assert not output_collector.has_output(exec_id), \
            f"Output for expired execution {exec_id} should have been removed"


@given(
    expired_params=st.lists(execution_params(), min_size=1, max_size=5),
    recent_params=st.lists(execution_params(), min_size=1, max_size=5),
    retention_hours=st.integers(min_value=1, max_value=24)
)
@settings(max_examples=100)
def test_property_151_cleanup_preserves_non_expired(expired_params, recent_params, retention_hours):
    """
    Property 151 (variant): Cleanup should only remove expired records,
    preserving non-expired records' output and encryption context.

    **Validates: Requirements 8.15, 8.16**
    """
    encryption_manager = MagicMock()
    output_collector = OutputCollector()
    manager = ExecutionManager(
        output_retention_hours=retention_hours,
        encryption_manager=encryption_manager,
        output_collector=output_collector,
    )

    expired_ids = []
    recent_ids = []

    # Create expired executions
    for params in expired_params:
        record = manager.create_execution(**params)
        expired_ids.append(record.execution_id)
        output_collector.create_buffer(record.execution_id)
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)
        rec = manager.get_execution(record.execution_id)
        rec.completed_at = datetime.now(timezone.utc) - timedelta(hours=retention_hours, minutes=1)

    # Create recent (non-expired) executions
    for params in recent_params:
        record = manager.create_execution(**params)
        recent_ids.append(record.execution_id)
        output_collector.create_buffer(record.execution_id)
        output_collector.capture_output(record.execution_id, 'stdout', b'recent output')
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

    removed = manager.cleanup_expired()

    assert removed == len(expired_ids)

    # Recent records should still have output
    for exec_id in recent_ids:
        assert output_collector.has_output(exec_id), \
            f"Output for recent execution {exec_id} should be preserved"

    # Encryption context removal should only be called for expired IDs
    called_enc_ids = {call.args[0] for call in encryption_manager.remove_encryption_context.call_args_list}
    for exec_id in recent_ids:
        assert exec_id not in called_enc_ids, \
            f"Encryption context for recent execution {exec_id} should not be removed"
