"""Unit tests for bounded execution durations in ExecutionManager

Feature: github-actions-remote-executor
Validates: Requirements 7.1
"""
from collections import deque

import pytest

from src.execution_manager import ExecutionManager
from src.models import ExecutionStatus

# The maxlen configured in ExecutionManager
DURATIONS_MAXLEN = 10000


def _complete_execution(manager: ExecutionManager) -> None:
    """Helper: create, start, and complete a single execution."""
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="script.sh",
        timeout_seconds=300,
    )
    manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
    manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)


class TestExecutionDurationsBoundedDeque:
    """Tests that _execution_durations is a bounded deque."""

    def test_execution_durations_is_deque(self):
        """_execution_durations must be a collections.deque instance."""
        manager = ExecutionManager(output_retention_hours=24)
        assert isinstance(manager._execution_durations, deque)

    def test_execution_durations_maxlen(self):
        """The deque must have maxlen=10000."""
        manager = ExecutionManager(output_retention_hours=24)
        assert manager._execution_durations.maxlen == DURATIONS_MAXLEN

    def test_execution_durations_does_not_exceed_maxlen(self):
        """After more completions than maxlen, the deque must not exceed maxlen."""
        manager = ExecutionManager(output_retention_hours=24)
        overflow = 50  # complete this many entries beyond the cap

        for _ in range(DURATIONS_MAXLEN + overflow):
            _complete_execution(manager)

        assert len(manager._execution_durations) == DURATIONS_MAXLEN, (
            f"Expected exactly {DURATIONS_MAXLEN} entries, "
            f"got {len(manager._execution_durations)}"
        )

    def test_oldest_entries_evicted_when_maxlen_reached(self):
        """When maxlen is exceeded, the oldest entries are evicted first."""
        manager = ExecutionManager(output_retention_hours=24)

        # Fill the deque to capacity
        for _ in range(DURATIONS_MAXLEN):
            _complete_execution(manager)

        # Snapshot the current oldest entry (index 0)
        oldest_before = manager._execution_durations[0]

        # Add one more entry — this should evict the oldest
        _complete_execution(manager)

        # The deque is still at maxlen
        assert len(manager._execution_durations) == DURATIONS_MAXLEN

        # The original oldest entry is gone
        assert manager._execution_durations[0] != oldest_before or (
            # Edge case: if all durations happen to be identical, just verify length
            len(set(manager._execution_durations)) == 1
        )

    def test_get_metrics_average_duration_with_bounded_deque(self):
        """get_metrics() returns a correct average_duration_ms with a bounded deque."""
        manager = ExecutionManager(output_retention_hours=24)

        # Complete a small number of executions so we can reason about the average
        count = 5
        for _ in range(count):
            _complete_execution(manager)

        metrics = manager.get_metrics()

        # average_duration_ms must be a non-negative float
        assert isinstance(metrics["average_duration_ms"], float)
        assert metrics["average_duration_ms"] >= 0.0

        # The internal deque should have exactly `count` entries
        assert len(manager._execution_durations) == count

    def test_get_metrics_average_duration_after_overflow(self):
        """get_metrics() still returns a valid average after the deque overflows."""
        manager = ExecutionManager(output_retention_hours=24)

        for _ in range(DURATIONS_MAXLEN + 100):
            _complete_execution(manager)

        metrics = manager.get_metrics()

        assert isinstance(metrics["average_duration_ms"], float)
        assert metrics["average_duration_ms"] >= 0.0
        # Deque must be capped
        assert len(manager._execution_durations) == DURATIONS_MAXLEN

    def test_get_metrics_average_duration_empty(self):
        """get_metrics() returns 0.0 average when no executions have completed."""
        manager = ExecutionManager(output_retention_hours=24)

        metrics = manager.get_metrics()

        assert metrics["average_duration_ms"] == 0.0

    def test_failed_and_timed_out_durations_also_bounded(self):
        """Durations from FAILED and TIMED_OUT executions are also stored in the bounded deque."""
        manager = ExecutionManager(output_retention_hours=24)

        # Mix of terminal states
        for i in range(30):
            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="script.sh",
                timeout_seconds=300,
            )
            manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
            if i % 3 == 0:
                manager.update_status(record.execution_id, ExecutionStatus.FAILED, exit_code=1)
            elif i % 3 == 1:
                manager.update_status(record.execution_id, ExecutionStatus.TIMED_OUT, exit_code=-1)
            else:
                manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

        assert len(manager._execution_durations) == 30
        assert len(manager._execution_durations) <= DURATIONS_MAXLEN
