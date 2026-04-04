"""Unit tests for Docker-based ScriptExecutor

Tests Docker container creation, security constraints, execution lifecycle,
cleanup behavior, and daemon accessibility using the mock Docker client.

Requirements: 5.1-5.13, 8.1-8.10, 9.7, 9.11, 9.12
"""
import os
import tempfile
import time

import pytest
import docker.errors

from src.script_executor import ScriptExecutor, CONTAINER_NAME_PREFIX
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus
from tests.mock_docker import (
    create_mock_docker_client,
    MockDockerClient,
    MockContainer,
    MockContainersAPI,
)


# ---------------------------------------------------------------------------
# Helpers (following existing patterns from test_script_executor.py)
# ---------------------------------------------------------------------------

def create_test_script(temp_dir: str, script_content: str, filename: str = "test_script.sh") -> str:
    """Helper to create a test script file."""
    script_path = os.path.join(temp_dir, filename)
    with open(script_path, "w") as f:
        f.write("#!/bin/bash\n")
        f.write(script_content)
    os.chmod(script_path, 0o755)
    return script_path


def wait_for_completion(manager: ExecutionManager, execution_id: str, max_wait: float = 5.0) -> bool:
    """Helper to wait for execution to reach a terminal state."""
    start = time.time()
    while time.time() - start < max_wait:
        record = manager.get_execution(execution_id)
        if record and record.status in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
        ):
            return True
        time.sleep(0.1)
    return False


def _make_executor(mock_client, manager, collector, temp_dir, **kwargs):
    """Convenience factory for ScriptExecutor with sensible defaults."""
    defaults = dict(
        docker_client=mock_client,
        execution_manager=manager,
        output_collector=collector,
        temp_storage_path=temp_dir,
        container_image="test-image:latest",
        memory_limit="512m",
        cpu_limit=1.0,
    )
    defaults.update(kwargs)
    return ScriptExecutor(**defaults)


def _create_and_run(executor, manager, temp_dir, script_content, timeout=5):
    """Create an execution record, write a script, run it, and return the execution_id."""
    record = manager.create_execution(
        repository_url="https://github.com/test/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        timeout_seconds=timeout,
    )
    create_test_script(temp_dir, script_content)
    executor.execute_async(record.execution_id, temp_dir, "test_script.sh")
    return record.execution_id


# ===========================================================================
# 1. Container creation with correct image, name, and security constraints
# ===========================================================================

class TestContainerCreationAndSecurity:
    """Validates: Requirements 5.1, 5.2, 5.13, 8.1-8.6, 9.7"""

    def test_container_created_with_correct_image(self):
        """Container uses the configured Container_Image."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            image = "my-custom-image:v2"
            executor = _make_executor(mock_client, manager, collector, temp_dir, container_image=image)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            calls = mock_client.containers._creation_calls
            assert len(calls) == 1
            assert calls[0]["image"] == image

    def test_container_name_derived_from_execution_id(self):
        """Container name follows gare-exec-{execution_id} convention."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            calls = mock_client.containers._creation_calls
            assert len(calls) == 1
            assert calls[0]["name"] == f"{CONTAINER_NAME_PREFIX}{eid}"

    def test_container_memory_limit(self):
        """Container is created with the configured memory limit (Req 8.1)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir, memory_limit="256m")

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            assert call["mem_limit"] == "256m"

    def test_container_cpu_limit(self):
        """Container is created with the configured CPU limit (Req 8.2)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir, cpu_limit=2.0)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            assert call["nano_cpus"] == int(2.0 * 1e9)

    def test_container_non_root_user(self):
        """Container runs as non-root user 'nobody' (Req 8.3)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            assert call["user"] == "nobody"

    def test_container_network_disabled(self):
        """Container has network access disabled (Req 8.4)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            assert call["network_mode"] == "none"

    def test_container_read_only_root_fs(self):
        """Container has read-only root filesystem with writable tmpfs (Req 8.5)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            assert call["read_only"] is True
            assert "/tmp/execution" in call["tmpfs"]

    def test_container_no_privilege_escalation(self):
        """Container disables privilege escalation (Req 8.6)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            assert "no-new-privileges" in call["security_opt"]

    def test_all_security_constraints_together(self):
        """All security constraints are applied in a single container creation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            assert call["user"] == "nobody"
            assert call["network_mode"] == "none"
            assert call["read_only"] is True
            assert "/tmp/execution" in call["tmpfs"]
            assert "no-new-privileges" in call["security_opt"]
            assert call["mem_limit"] == "512m"
            assert call["nano_cpus"] == int(1.0 * 1e9)


# ===========================================================================
# 2. Container execution captures stdout, stderr, and exit code
# ===========================================================================

class TestContainerOutputCapture:
    """Validates: Requirements 5.6, 5.7, 5.8"""

    def test_captures_stdout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo 'hello stdout'\n")
            assert wait_for_completion(manager, eid)

            output = collector.get_output(eid)
            assert "hello stdout" in output.stdout
            assert output.complete is True

    def test_captures_stderr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo 'hello stderr' >&2\n")
            assert wait_for_completion(manager, eid)

            output = collector.get_output(eid)
            assert "hello stderr" in output.stderr

    def test_captures_exit_code_zero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "exit 0\n")
            assert wait_for_completion(manager, eid)

            record = manager.get_execution(eid)
            assert record.status == ExecutionStatus.COMPLETED
            assert record.exit_code == 0

    def test_captures_nonzero_exit_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "exit 42\n")
            assert wait_for_completion(manager, eid)

            record = manager.get_execution(eid)
            assert record.status == ExecutionStatus.FAILED
            assert record.exit_code == 42

    def test_captures_both_streams(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            script = "echo 'out'\necho 'err' >&2\n"
            eid = _create_and_run(executor, manager, temp_dir, script)
            assert wait_for_completion(manager, eid)

            output = collector.get_output(eid)
            assert "out" in output.stdout
            assert "err" in output.stderr


# ===========================================================================
# 3. Container is removed after successful execution
# ===========================================================================

class TestContainerRemovalOnSuccess:
    """Validates: Requirements 5.4, 8.9"""

    def test_container_removed_after_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo 'done'\nexit 0\n")
            assert wait_for_completion(manager, eid)
            time.sleep(0.3)  # allow cleanup thread to finish

            container_name = f"{CONTAINER_NAME_PREFIX}{eid}"
            with pytest.raises(docker.errors.NotFound):
                mock_client.containers.get(container_name)


# ===========================================================================
# 4. Container is removed after failed execution
# ===========================================================================

class TestContainerRemovalOnFailure:
    """Validates: Requirements 5.5"""

    def test_container_removed_after_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "exit 1\n")
            assert wait_for_completion(manager, eid)
            time.sleep(0.3)

            container_name = f"{CONTAINER_NAME_PREFIX}{eid}"
            with pytest.raises(docker.errors.NotFound):
                mock_client.containers.get(container_name)


# ===========================================================================
# 5. Container is removed after timeout
# ===========================================================================

class TestContainerRemovalOnTimeout:
    """Validates: Requirements 5.10"""

    def test_container_removed_after_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            timeout = 1
            eid = _create_and_run(executor, manager, temp_dir, f"sleep {timeout * 2}\n", timeout=timeout)

            # Wait for timeout — use same pattern as existing test_script_executor.py
            start = time.time()
            max_wait = timeout + 5
            while time.time() - start < max_wait:
                rec = manager.get_execution(eid)
                if rec and rec.status == ExecutionStatus.TIMED_OUT:
                    break
                time.sleep(0.1)

            record = manager.get_execution(eid)
            assert record.status == ExecutionStatus.TIMED_OUT
            time.sleep(0.5)

            container_name = f"{CONTAINER_NAME_PREFIX}{eid}"
            with pytest.raises(docker.errors.NotFound):
                mock_client.containers.get(container_name)


# ===========================================================================
# 6. Container removal verification
# ===========================================================================

class TestContainerRemovalVerification:
    """Validates: Requirements 8.9"""

    def test_verify_container_removed_returns_true_after_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)
            time.sleep(0.3)

            assert executor.verify_container_removed(eid) is True

    def test_verify_container_removed_returns_false_when_exists(self):
        """If a container still exists, verify_container_removed returns False."""
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=manager,
            output_collector=collector,
        )

        # Manually create a container that won't be removed
        container_name = f"{CONTAINER_NAME_PREFIX}manual-test-id"
        mock_client.containers.create(
            image="test-image",
            name=container_name,
            command=["echo", "hi"],
        )

        assert executor.verify_container_removed("manual-test-id") is False


# ===========================================================================
# 7. Dangling container cleanup on startup
# ===========================================================================

class TestDanglingContainerCleanup:
    """Validates: Requirements 8.10"""

    def test_cleanup_removes_dangling_containers(self):
        mock_client = create_mock_docker_client()

        # Pre-populate dangling containers
        for i in range(3):
            mock_client.containers.create(
                image="test-image",
                name=f"{CONTAINER_NAME_PREFIX}stale-{i}",
                command=["echo", "stale"],
            )

        listed = mock_client.containers.list(all=True, filters={"name": CONTAINER_NAME_PREFIX})
        assert len(listed) == 3

        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
        )
        executor.cleanup_dangling_containers()

        remaining = mock_client.containers.list(all=True, filters={"name": CONTAINER_NAME_PREFIX})
        assert len(remaining) == 0

    def test_cleanup_with_no_dangling_containers(self):
        mock_client = create_mock_docker_client()
        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
        )
        # Should not raise
        executor.cleanup_dangling_containers()

        remaining = mock_client.containers.list(all=True, filters={"name": CONTAINER_NAME_PREFIX})
        assert len(remaining) == 0

    def test_cleanup_skipped_when_docker_client_is_none(self):
        executor = ScriptExecutor(
            docker_client=None,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
        )
        # Should not raise even with None client
        executor.cleanup_dangling_containers()


# ===========================================================================
# 8. Docker daemon accessibility check
# ===========================================================================

class TestDockerDaemonAccessibility:
    """Validates: Requirements 9.11, 9.12"""

    def test_verify_docker_daemon_success(self):
        mock_client = create_mock_docker_client()
        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
        )
        assert executor.verify_docker_daemon() is True

    def test_verify_docker_daemon_failure(self):
        mock_client = MockDockerClient()
        mock_client.ping = lambda: (_ for _ in ()).throw(Exception("Docker daemon not accessible"))

        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
        )
        assert executor.verify_docker_daemon() is False

    def test_verify_docker_daemon_none_client(self):
        executor = ScriptExecutor(
            docker_client=None,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
        )
        assert executor.verify_docker_daemon() is False


# ===========================================================================
# 9. Container name derivation from Execution_ID
# ===========================================================================

class TestContainerNameDerivation:
    """Validates: Requirements 5.13"""

    def test_container_name_uses_prefix_and_execution_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            calls = mock_client.containers._creation_calls
            assert len(calls) == 1
            assert calls[0]["name"].startswith(CONTAINER_NAME_PREFIX)
            assert eid in calls[0]["name"]

    def test_multiple_executions_have_distinct_container_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eids = []
            for i in range(3):
                record = manager.create_execution(
                    repository_url="https://github.com/test/repo",
                    commit_hash=f"{i:040d}",
                    script_path="test.sh",
                    timeout_seconds=5,
                )
                eids.append(record.execution_id)
                sp = create_test_script(temp_dir, "echo ok\n", f"script_{i}.sh")
                executor.execute_async(record.execution_id, temp_dir, f"script_{i}.sh")

            for eid in eids:
                wait_for_completion(manager, eid)

            calls = mock_client.containers._creation_calls
            names = [c["name"] for c in calls]
            assert len(names) == len(set(names)), "Container names must be unique"


# ===========================================================================
# 10. Repository directory mounting at /workspace
# ===========================================================================

class TestRepoDirectoryMounting:
    """Validates: Requirements 5.1, 5.2, 5.4, 5.5 — repo directory mounting"""

    def test_container_mounts_repo_dir_at_workspace(self):
        """Verify container is created with repo directory mounted read-only at /workspace."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            volumes = call.get("volumes", {})
            # There should be a volume mapping with bind=/workspace and mode=ro
            workspace_found = False
            for host_path, mount_spec in volumes.items():
                if isinstance(mount_spec, dict) and mount_spec.get("bind") == "/workspace":
                    assert mount_spec.get("mode") == "ro", "Repo mount should be read-only"
                    workspace_found = True
                    break
            assert workspace_found, "Repo directory should be mounted at /workspace"

    def test_container_working_dir_is_workspace(self):
        """Verify working_dir is set to /workspace."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            eid = _create_and_run(executor, manager, temp_dir, "echo ok\n")
            assert wait_for_completion(manager, eid)

            call = mock_client.containers._creation_calls[0]
            assert call.get("working_dir") == "/workspace"

    def test_container_command_uses_workspace_script_path(self):
        """Verify command uses /workspace/{script_path}."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            # Use a specific script filename
            record = manager.create_execution(
                repository_url="https://github.com/test/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
                timeout_seconds=5,
            )
            create_test_script(temp_dir, "echo ok\n", "build.sh")
            executor.execute_async(record.execution_id, temp_dir, "build.sh")
            assert wait_for_completion(manager, record.execution_id)

            call = mock_client.containers._creation_calls[0]
            assert call["command"] == ["sh", "/workspace/build.sh"]

    def test_repo_directory_cleaned_up_after_execution(self):
        """Verify repo directory is cleaned up after execution completes."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_client = create_mock_docker_client()
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _make_executor(mock_client, manager, collector, temp_dir)

            # Create a subdirectory to simulate a cloned repo
            repo_dir = os.path.join(temp_dir, "repo_clone")
            os.makedirs(repo_dir, exist_ok=True)
            script_path = os.path.join(repo_dir, "test.sh")
            with open(script_path, "w") as f:
                f.write("#!/bin/bash\necho ok\n")
            os.chmod(script_path, 0o755)

            record = manager.create_execution(
                repository_url="https://github.com/test/repo",
                commit_hash="a" * 40,
                script_path="test.sh",
                timeout_seconds=5,
            )
            executor.execute_async(record.execution_id, repo_dir, "test.sh")
            assert wait_for_completion(manager, record.execution_id)

            # Wait for cleanup
            time.sleep(0.5)
            assert not os.path.exists(repo_dir), "Repo directory should be cleaned up"
