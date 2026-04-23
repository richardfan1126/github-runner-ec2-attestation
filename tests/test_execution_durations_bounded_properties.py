"""Property-based tests for bounded execution durations in ExecutionManager

Feature: github-actions-remote-executor
Tests Property 172: Execution Durations Bounded Memory
"""
from collections import deque

import pytest
from hypothesis import given, settings, strategies as st

from src.execution_manager import ExecutionManager
from src.models import ExecutionStatus

# The maxlen configured in ExecutionManager
DURATIONS_MAXLEN = 10000


# Property 172: Execution Durations Bounded Memory
# Feature: github-actions-remote-executor, Property 172: Execution Durations Bounded Memory
@given(
    num_completions=st.integers(min_value=DURATIONS_MAXLEN + 1, max_value=DURATIONS_MAXLEN + 500)
)
@settings(max_examples=10)
def test_property_172_execution_durations_bounded_memory(num_completions):
    """
    Property 172: For any number of completed executions exceeding the deque maxlen,
    the _execution_durations collection should never grow beyond maxlen entries.

    Validates: Requirements 7.1
    """
    manager = ExecutionManager(output_retention_hours=24)

    for _ in range(num_completions):
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="script.sh",
            timeout_seconds=300,
        )
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

    # The internal deque must never exceed its maxlen regardless of how many
    # executions have completed.
    assert len(manager._execution_durations) <= DURATIONS_MAXLEN, (
        f"_execution_durations grew to {len(manager._execution_durations)}, "
        f"exceeding maxlen={DURATIONS_MAXLEN}"
    )


@given(
    num_completions=st.integers(min_value=1, max_value=DURATIONS_MAXLEN)
)
@settings(max_examples=10)
def test_property_172_execution_durations_within_maxlen(num_completions):
    """
    Property 172 (variant): When completions are fewer than maxlen, all durations
    are retained (no premature eviction).

    Validates: Requirements 7.1
    """
    manager = ExecutionManager(output_retention_hours=24)

    for _ in range(num_completions):
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="b" * 40,
            script_path="script.sh",
            timeout_seconds=300,
        )
        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

    # All durations should be present when under the cap.
    assert len(manager._execution_durations) == num_completions, (
        f"Expected {num_completions} duration entries, "
        f"got {len(manager._execution_durations)}"
    )


@given(
    extra=st.integers(min_value=1, max_value=200)
)
@settings(max_examples=10)
def test_property_172_execution_durations_type_is_deque(extra):
    """
    Property 172 (variant): _execution_durations must be a bounded deque instance,
    not a plain list, so that automatic eviction is guaranteed.

    Validates: Requirements 7.1
    """
    manager = ExecutionManager(output_retention_hours=24)

    assert isinstance(manager._execution_durations, deque), (
        "_execution_durations should be a collections.deque"
    )
    assert manager._execution_durations.maxlen == DURATIONS_MAXLEN, (
        f"deque maxlen should be {DURATIONS_MAXLEN}, "
        f"got {manager._execution_durations.maxlen}"
    )
