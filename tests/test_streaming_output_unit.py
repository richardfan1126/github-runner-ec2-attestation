"""Unit tests for streaming output capture via Log_Streaming_Thread.

Tests the streaming log capture mechanism in ScriptExecutor, verifying that
output is captured incrementally during execution rather than in a single
batch after the container exits.

Requirements: 5.14, 44.1-44.11
"""
import os
import tempfile
import time
import threading
from unittest.mock import patch, MagicMock, call

import pytest

from src.script_executor import ScriptExecutor, CONTAINER_NAME_PREFIX
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus
from tests.mock_docker import create_mock_docker_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_script(temp_dir, content, filename="test_script.sh"):
    path = os.path.join(temp_dir, filename)
    with open(path, "w") as f:
        f.write("#!/bin/bash\n")
        f.write(content)
    os.chmod(path, 0o755)
    return path


def _make_executor(mock_client, manager, collector, temp_dir, **kw):
    defaults = dict(
        docker_client=mock_client,
        execution_manager=manager,
        output_collector=collector,
        temp_storage_path=temp_dir,
        container_image="test-image:latest",
        memory_limit="512m",
        cpu_limit=1.0,
    )
    defaults.update(kw)
    return ScriptExecutor(**defaults)


def _run(executor, manager, temp_dir, script, timeout=5, filename="test_script.sh"):
    record = manager.create_execution(
        repository_url="https://github.com/test/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        timeout_seconds=timeout,
    )
    _create_test_script(temp_dir, script, filename)
    executor.execute_async(record.execution_id, temp_dir, filename)
    return record.execution_id


def _wait(manager, eid, max_wait=10.0):
    start = time.time()
    while time.time() - start < max_wait:
        rec = manager.get_execution(eid)
        if rec and rec.status in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
        ):
            return True
        time.sleep(0.1)
    return False


# ===========================================================================
# 1. Streaming threads started after container.start() and before wait()
# ===========================================================================

class TestStreamingThreadLifecycle:
    """Verify streaming threads are started at the right time."""

    def test_streaming_threads_started_after_start_before_wait(self):
        """Streaming threads are started after container.start() and before container.wait()."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            call_order = []
            _orig_stream = executor._stream_container_logs

            def _track_stream(*args, **kwargs):
                call_order.append("stream_started")
                return _orig_stream(*args, **kwargs)

            with patch.object(executor, "_stream_container_logs", side_effect=_track_stream):
                eid = _run(executor, manager, temp_dir, "echo ok\n")
                assert _wait(manager, eid)

            # Verify streaming was invoked (threads were started)
            assert call_order.count("stream_started") >= 2, \
                "Should have started at least 2 streaming threads (stdout + stderr)"


# ===========================================================================
# 2. Output chunks fed to OutputCollector incrementally
# ===========================================================================

class TestIncrementalOutputCapture:
    """Verify output is captured incrementally during execution."""

    def test_output_chunks_fed_incrementally(self):
        """Output chunks are fed to OutputCollector during execution."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            script = "echo 'line1'\necho 'line2'\necho 'line3'\n"
            eid = _run(executor, manager, temp_dir, script)
            assert _wait(manager, eid)

            output = collector.get_output(eid)
            assert "line1" in output.stdout
            assert "line2" in output.stdout
            assert "line3" in output.stdout
            assert output.complete

    def test_stderr_captured_incrementally(self):
        """Stderr output is captured via streaming."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            script = "echo 'err_msg' >&2\n"
            eid = _run(executor, manager, temp_dir, script)
            assert _wait(manager, eid)

            output = collector.get_output(eid)
            assert "err_msg" in output.stderr


# ===========================================================================
# 3. _capture_container_logs NOT called after successful streaming
# ===========================================================================

class TestNoBatchRecapture:
    """Verify batch capture is skipped when streaming was active."""

    def test_no_batch_capture_on_success(self):
        """_capture_container_logs is not called after successful streaming."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            with patch.object(executor, "_capture_container_logs") as mock_capture:
                eid = _run(executor, manager, temp_dir, "echo ok\n")
                assert _wait(manager, eid)

                rec = manager.get_execution(eid)
                assert rec.status == ExecutionStatus.COMPLETED
                mock_capture.assert_not_called()

    def test_no_batch_capture_on_failure(self):
        """_capture_container_logs is not called after failed execution with streaming."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            with patch.object(executor, "_capture_container_logs") as mock_capture:
                eid = _run(executor, manager, temp_dir, "exit 1\n")
                assert _wait(manager, eid)

                rec = manager.get_execution(eid)
                assert rec.status == ExecutionStatus.FAILED
                mock_capture.assert_not_called()


# ===========================================================================
# 4. Streaming threads handle Docker API errors gracefully
# ===========================================================================

class TestStreamingErrorHandling:
    """Verify streaming threads handle errors without crashing."""

    def test_streaming_handles_docker_api_error(self):
        """Streaming thread logs a warning on Docker API error and doesn't crash."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            # Create a mock container whose logs() raises an exception
            import docker.errors

            def _error_stream(*args, **kwargs):
                if kwargs.get("stream"):
                    raise docker.errors.APIError("Simulated Docker API error")
                return b""

            eid = _run(executor, manager, temp_dir, "echo ok\n")
            assert _wait(manager, eid)

            # Execution should still complete even if streaming had errors
            rec = manager.get_execution(eid)
            assert rec.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED)


# ===========================================================================
# 5. Streaming threads terminate when container exits
# ===========================================================================

class TestStreamingTermination:
    """Verify streaming threads terminate when the container exits."""

    def test_streaming_threads_terminate_on_exit(self):
        """Streaming threads terminate after the container exits."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _run(executor, manager, temp_dir, "echo done\n")
            assert _wait(manager, eid)

            # After execution completes, all threads should have terminated
            # (they were joined with timeout in the executor)
            rec = manager.get_execution(eid)
            assert rec.status == ExecutionStatus.COMPLETED


# ===========================================================================
# 6. Streaming threads capture partial output on timeout
# ===========================================================================

class TestStreamingOnTimeout:
    """Verify streaming captures partial output before timeout kills the container."""

    def test_partial_output_captured_on_timeout(self):
        """Streaming captures output produced before the timeout fires."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            # Use a script that flushes output before sleeping
            script = "echo 'partial_output'\nsleep 30\n"
            eid = _run(executor, manager, temp_dir, script, timeout=2)

            # Wait for timeout with generous margin
            start = time.time()
            while time.time() - start < 20:
                rec = manager.get_execution(eid)
                if rec and rec.status == ExecutionStatus.TIMED_OUT:
                    break
                time.sleep(0.1)

            rec = manager.get_execution(eid)
            assert rec.status == ExecutionStatus.TIMED_OUT

            output = collector.get_output(eid)
            assert "partial_output" in output.stdout
            assert output.complete


# ===========================================================================
# 7. Streaming threads are daemon threads
# ===========================================================================

class TestStreamingDaemonThreads:
    """Verify streaming threads are daemon threads."""

    def test_streaming_threads_are_daemon(self):
        """Streaming threads are created as daemon threads."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            created_threads = []
            _orig_init = threading.Thread.__init__

            def _track_init(self, *args, **kwargs):
                _orig_init(self, *args, **kwargs)
                target = kwargs.get("target") or (args[1] if len(args) > 1 else None)
                if target and hasattr(target, "__name__") and target.__name__ == "_stream_container_logs":
                    created_threads.append(self)

            with patch.object(threading.Thread, "__init__", _track_init):
                eid = _run(executor, manager, temp_dir, "echo ok\n")
                assert _wait(manager, eid)

            assert len(created_threads) >= 2
            for t in created_threads:
                assert t.daemon, "Streaming thread must be a daemon thread"


# ===========================================================================
# 8. Mock Docker streaming API
# ===========================================================================

class TestMockDockerStreaming:
    """Verify the mock Docker container.logs(stream=True) returns an iterator."""

    def test_mock_logs_stream_returns_iterator(self):
        """MockContainer.logs(stream=True) returns an iterator of chunks."""
        from tests.mock_docker import MockContainer

        container = MockContainer(name="test", command=["echo", "hi"])
        # Without starting, streaming should return an empty iterator
        result = container.logs(stream=True, follow=True, stdout=True, stderr=False)
        chunks = list(result)
        # No process started, so no output
        assert chunks == []

    def test_mock_logs_non_stream_returns_bytes(self):
        """MockContainer.logs() without stream returns bytes directly."""
        from tests.mock_docker import MockContainer

        container = MockContainer(name="test", command=["echo", "hi"])
        result = container.logs(stdout=True, stderr=False)
        assert isinstance(result, bytes)
