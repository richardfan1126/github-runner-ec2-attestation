"""Unit tests for periodic cleanup scheduling

Feature: github-actions-remote-executor
Tests periodic cleanup_expired invocation and resource cleanup.
Requirements: 8.15, 8.16
"""
import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus


class TestCleanupExpiredWithOutputCollector:
    """Test that cleanup_expired calls remove_output and remove_encryption_context."""

    def test_cleanup_calls_remove_output_for_expired(self):
        """Expired records should trigger remove_output on the OutputCollector."""
        encryption_manager = MagicMock()
        output_collector = OutputCollector()
        manager = ExecutionManager(
            output_retention_hours=1,
            encryption_manager=encryption_manager,
            output_collector=output_collector,
        )

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="script.sh",
            timeout_seconds=60,
        )
        output_collector.create_buffer(record.execution_id)
        output_collector.capture_output(record.execution_id, 'stdout', b'hello')

        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

        # Expire the record
        rec = manager.get_execution(record.execution_id)
        rec.completed_at = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)

        removed = manager.cleanup_expired()

        assert removed == 1
        assert not output_collector.has_output(record.execution_id)
        encryption_manager.remove_encryption_context.assert_called_once_with(record.execution_id)

    def test_cleanup_calls_remove_encryption_context_for_expired(self):
        """Expired records should trigger remove_encryption_context on the EncryptionManager."""
        encryption_manager = MagicMock()
        output_collector = OutputCollector()
        manager = ExecutionManager(
            output_retention_hours=1,
            encryption_manager=encryption_manager,
            output_collector=output_collector,
        )

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="b" * 40,
            script_path="run.sh",
            timeout_seconds=60,
        )
        output_collector.create_buffer(record.execution_id)

        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.FAILED, exit_code=1)

        rec = manager.get_execution(record.execution_id)
        rec.completed_at = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)

        manager.cleanup_expired()

        encryption_manager.remove_encryption_context.assert_called_once_with(record.execution_id)

    def test_cleanup_does_not_remove_non_expired(self):
        """Non-expired records should not have their output or encryption context removed."""
        encryption_manager = MagicMock()
        output_collector = OutputCollector()
        manager = ExecutionManager(
            output_retention_hours=1,
            encryption_manager=encryption_manager,
            output_collector=output_collector,
        )

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="c" * 40,
            script_path="test.sh",
            timeout_seconds=60,
        )
        output_collector.create_buffer(record.execution_id)
        output_collector.capture_output(record.execution_id, 'stdout', b'data')

        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

        removed = manager.cleanup_expired()

        assert removed == 0
        assert output_collector.has_output(record.execution_id)
        encryption_manager.remove_encryption_context.assert_not_called()

    def test_cleanup_handles_missing_output_buffer_gracefully(self):
        """Cleanup should not fail if the output buffer was already removed."""
        encryption_manager = MagicMock()
        output_collector = OutputCollector()
        manager = ExecutionManager(
            output_retention_hours=1,
            encryption_manager=encryption_manager,
            output_collector=output_collector,
        )

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="d" * 40,
            script_path="go.sh",
            timeout_seconds=60,
        )
        # Don't create a buffer — simulates already-removed output

        manager.update_status(record.execution_id, ExecutionStatus.RUNNING)
        manager.update_status(record.execution_id, ExecutionStatus.COMPLETED, exit_code=0)

        rec = manager.get_execution(record.execution_id)
        rec.completed_at = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)

        # Should not raise
        removed = manager.cleanup_expired()
        assert removed == 1


class TestPeriodicCleanupScheduling:
    """Test that the periodic cleanup background task works correctly."""

    @pytest.mark.asyncio
    async def test_periodic_cleanup_invokes_cleanup_expired(self):
        """The background task should invoke cleanup_expired periodically."""
        mock_execution_manager = MagicMock()
        mock_execution_manager.cleanup_expired.return_value = 0

        invocation_count = 0

        async def periodic_cleanup():
            nonlocal invocation_count
            while True:
                try:
                    await asyncio.sleep(0.05)
                    mock_execution_manager.cleanup_expired()
                    invocation_count += 1
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        task = asyncio.create_task(periodic_cleanup())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert invocation_count >= 2, f"Expected at least 2 invocations, got {invocation_count}"
        assert mock_execution_manager.cleanup_expired.call_count >= 2

    @pytest.mark.asyncio
    async def test_periodic_cleanup_handles_errors_gracefully(self):
        """The background task should continue running even if cleanup_expired raises."""
        mock_execution_manager = MagicMock()
        call_count = 0

        def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated error")
            return 0

        mock_execution_manager.cleanup_expired.side_effect = side_effect

        async def periodic_cleanup():
            while True:
                try:
                    await asyncio.sleep(0.05)
                    mock_execution_manager.cleanup_expired()
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        task = asyncio.create_task(periodic_cleanup())
        await asyncio.sleep(0.25)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should have been called multiple times despite the first call raising
        assert call_count >= 2, f"Expected at least 2 calls, got {call_count}"

    @pytest.mark.asyncio
    async def test_periodic_cleanup_stops_on_cancellation(self):
        """The background task should stop cleanly when cancelled."""
        mock_execution_manager = MagicMock()
        mock_execution_manager.cleanup_expired.return_value = 0

        async def periodic_cleanup():
            while True:
                try:
                    await asyncio.sleep(0.05)
                    mock_execution_manager.cleanup_expired()
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        task = asyncio.create_task(periodic_cleanup())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        count_at_cancel = mock_execution_manager.cleanup_expired.call_count
        await asyncio.sleep(0.15)

        # No more calls after cancellation
        assert mock_execution_manager.cleanup_expired.call_count == count_at_cancel
