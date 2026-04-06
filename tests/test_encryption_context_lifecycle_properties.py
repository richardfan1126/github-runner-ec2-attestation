"""Property-based tests for Encryption Context Lifecycle

Feature: github-actions-remote-executor
Tests Property 131 from the design document

Property 131: Encryption Context Lifecycle
For any successful /execute request, the server should store the Shared_Key in an
Encryption_Context associated with the Execution_ID, and the context should persist
until the execution record is cleaned up, at which point it is removed from memory.

**Validates: Requirements 41.1, 41.2, 41.6**
"""
import os
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st

from src.encryption import EncryptionManager
from src.execution_manager import ExecutionManager
from src.models import ExecutionStatus


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def retention_and_expiry(draw):
    """Generate a retention_hours value and an expiry offset that exceeds it."""
    retention_hours = draw(st.integers(min_value=1, max_value=48))
    extra_minutes = draw(st.integers(min_value=1, max_value=120))
    return retention_hours, extra_minutes


@st.composite
def terminal_status(draw):
    """Pick a random terminal execution status."""
    return draw(st.sampled_from([
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
    ]))


# ---------------------------------------------------------------------------
# Property 131: Encryption Context Lifecycle
# ---------------------------------------------------------------------------

@given(
    data=retention_and_expiry(),
    status=terminal_status(),
    exit_code=st.integers(min_value=-128, max_value=255),
)
@settings(max_examples=50)
def test_property_131_encryption_context_lifecycle(data, status, exit_code):
    """Property 131: Encryption Context Lifecycle

    Shared_Key is stored after /execute, persists during execution, and is
    removed when the execution record is cleaned up.

    **Validates: Requirements 41.1, 41.2, 41.6**
    """
    retention_hours, extra_minutes = data

    encryption_manager = EncryptionManager()
    execution_manager = ExecutionManager(
        output_retention_hours=retention_hours,
        encryption_manager=encryption_manager,
    )

    # --- Phase 1: simulate /execute storing the shared key (Req 41.1) ---
    record = execution_manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="scripts/run.sh",
        timeout_seconds=300,
    )
    shared_key = os.urandom(32)
    encryption_manager.store_encryption_context(record.execution_id, shared_key)

    # Key must be retrievable immediately after storing
    assert encryption_manager.get_shared_key(record.execution_id) == shared_key

    # --- Phase 2: key persists while execution is active (Req 41.2) ---
    execution_manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    assert encryption_manager.get_shared_key(record.execution_id) == shared_key

    # Cleanup should NOT remove active executions or their contexts
    removed = execution_manager.cleanup_expired()
    assert removed == 0
    assert encryption_manager.get_shared_key(record.execution_id) == shared_key

    # --- Phase 3: key persists after terminal state but within retention ---
    execution_manager.update_status(record.execution_id, status, exit_code=exit_code)
    assert encryption_manager.get_shared_key(record.execution_id) == shared_key

    # Cleanup within retention window should not remove anything
    removed = execution_manager.cleanup_expired()
    assert removed == 0
    assert encryption_manager.get_shared_key(record.execution_id) == shared_key

    # --- Phase 4: cleanup removes both record and context (Req 41.6) ---
    rec = execution_manager.get_execution(record.execution_id)
    rec.completed_at = datetime.now(timezone.utc) - timedelta(
        hours=retention_hours, minutes=extra_minutes
    )

    removed = execution_manager.cleanup_expired()
    assert removed == 1

    # Execution record gone
    assert execution_manager.get_execution(record.execution_id) is None
    # Encryption context gone
    assert encryption_manager.get_shared_key(record.execution_id) is None
