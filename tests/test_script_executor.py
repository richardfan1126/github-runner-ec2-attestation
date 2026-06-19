"""Unit tests for ScriptExecutor

Tests script execution with known output, timeout scenarios, and cleanup behavior.
Requirements: 5.1-5.7, 8.4
"""
import os
import tempfile
import time
from pathlib import Path
from threading import Thread

import pytest

from src.script_executor import ScriptExecutor
from src.config import ServerConfig, CONTAINER_DEFAULT_CAP_ADD
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus
from tests.mock_docker import create_mock_docker_client


def _default_server_config(**overrides) -> ServerConfig:
    """Build a ServerConfig with all required fields set, security fields at defaults."""
    base = dict(
        port=8080,
        max_concurrent_executions=1,
        execution_timeout_seconds=1800,
        max_script_size_bytes=1024,
        rate_limit_per_ip=10,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp",
        output_retention_hours=1,
        tpm_attest_path="/usr/bin/nitro-tpm-attest",
        allowed_repositories=["owner/repo"],
        expected_audience="aud",
        container_image="image@sha256:" + "a" * 64,
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )
    base.update(overrides)
    return ServerConfig(**base)


def _executor_from_config(config: ServerConfig, docker_client, manager, collector, temp_dir):
    """Wire a ScriptExecutor from config exactly as server.py does (US1 seven seams)."""
    return ScriptExecutor(
        docker_client=docker_client,
        container_image=config.container_image,
        memory_limit=config.container_memory_limit,
        cpu_limit=config.container_cpu_limit,
        execution_manager=manager,
        output_collector=collector,
        temp_storage_path=temp_dir,
        user=config.container_user,
        cap_add=config.container_cap_add,
        no_new_privileges=config.no_new_privileges,
        read_only_rootfs=config.container_read_only_rootfs,
        tmpfs_size=config.container_tmpfs_size,
        tmpfs_exec=config.container_tmpfs_exec,
        workspace_mount_mode=config.workspace_mount_mode,
        network_mode=config.container_network_mode,
    )


def create_test_script(temp_dir: str, script_content: str, filename: str = "test_script.sh") -> str:
    """Helper to create a test script file"""
    script_path = os.path.join(temp_dir, filename)
    with open(script_path, 'w') as f:
        f.write("#!/bin/bash\n")
        f.write(script_content)
    os.chmod(script_path, 0o755)
    return script_path


def wait_for_completion(manager: ExecutionManager, execution_id: str, max_wait: float = 5.0) -> bool:
    """Helper to wait for execution to reach terminal state"""
    start_time = time.time()
    while time.time() - start_time < max_wait:
        record = manager.get_execution(execution_id)
        if record and record.status in [
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT
        ]:
            return True
        time.sleep(0.1)
    return False


def test_execute_script_with_stdout():
    """Test script execution captures stdout correctly"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script with known output
        expected_output = "Hello from script"
        script_content = f"echo '{expected_output}'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Verify output
        output_data = collector.get_output(record.execution_id)
        assert expected_output in output_data.stdout
        assert output_data.complete is True
        assert output_data.exit_code == 0


def test_execute_script_with_stderr():
    """Test script execution captures stderr correctly"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="b" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script that writes to stderr
        error_message = "Error message"
        script_content = f"echo '{error_message}' >&2\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Verify stderr output
        output_data = collector.get_output(record.execution_id)
        assert error_message in output_data.stderr
        assert output_data.complete is True


def test_execute_script_with_both_streams():
    """Test script execution captures both stdout and stderr"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="c" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script with both stdout and stderr
        stdout_msg = "Standard output"
        stderr_msg = "Error output"
        script_content = f"echo '{stdout_msg}'\necho '{stderr_msg}' >&2\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Verify both streams
        output_data = collector.get_output(record.execution_id)
        assert stdout_msg in output_data.stdout
        assert stderr_msg in output_data.stderr
        assert output_data.complete is True


def test_execute_script_with_exit_code_zero():
    """Test successful script execution with exit code 0"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="d" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script that exits with 0
        script_content = "exit 0\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Verify status and exit code
        final_record = manager.get_execution(record.execution_id)
        assert final_record.status == ExecutionStatus.COMPLETED
        assert final_record.exit_code == 0


def test_execute_script_with_nonzero_exit_code():
    """Test failed script execution with non-zero exit code"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="e" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script that exits with error code
        exit_code = 42
        script_content = f"exit {exit_code}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Verify status and exit code
        final_record = manager.get_execution(record.execution_id)
        assert final_record.status == ExecutionStatus.FAILED
        assert final_record.exit_code == exit_code


def test_execute_script_with_multiple_exit_codes():
    """Test various exit codes are captured correctly"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        test_exit_codes = [0, 1, 2, 127, 255]
        
        for exit_code in test_exit_codes:
            # Create execution
            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash=f"{exit_code:040d}",
                script_path="test.sh",
                timeout_seconds=5
            )
            
            # Create subdirectory for this script to avoid cleanup conflicts
            script_dir = os.path.join(temp_dir, f"exec_{exit_code}")
            os.makedirs(script_dir, exist_ok=True)
            
            # Create script with specific exit code
            script_content = f"exit {exit_code}\n"
            script_path = os.path.join(script_dir, "test.sh")
            with open(script_path, 'w') as f:
                f.write("#!/bin/bash\n")
                f.write(script_content)
            os.chmod(script_path, 0o755)
            
            # Execute
            executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
            
            # Wait for completion
            assert wait_for_completion(manager, record.execution_id)
            
            # Verify exit code
            final_record = manager.get_execution(record.execution_id)
            assert final_record.exit_code == exit_code
            
            # Verify status based on exit code
            if exit_code == 0:
                assert final_record.status == ExecutionStatus.COMPLETED
            else:
                assert final_record.status == ExecutionStatus.FAILED


def test_execute_script_timeout():
    """Test script execution timeout terminates process"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution with short timeout
        timeout = 1
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="f" * 40,
            script_path="test.sh",
            timeout_seconds=timeout
        )
        
        # Create script that sleeps longer than timeout
        script_content = f"sleep {timeout * 2}\necho 'Should not reach here'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        start_time = time.time()
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for timeout
        max_wait = timeout + 2
        while time.time() - start_time < max_wait:
            record = manager.get_execution(record.execution_id)
            if record and record.status == ExecutionStatus.TIMED_OUT:
                break
            time.sleep(0.1)
        
        execution_duration = time.time() - start_time
        
        # Verify timeout occurred
        final_record = manager.get_execution(record.execution_id)
        assert final_record.status == ExecutionStatus.TIMED_OUT
        assert final_record.exit_code == -1
        
        # Verify termination happened around timeout period
        assert execution_duration < timeout + 2


def test_execute_script_timeout_with_different_durations():
    """Test timeout works with various timeout durations"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        timeout_values = [1]
        
        for timeout in timeout_values:
            # Create execution
            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash=f"{timeout:040d}",
                script_path="test.sh",
                timeout_seconds=timeout
            )
            
            # Create subdirectory for this script to avoid cleanup conflicts
            script_dir = os.path.join(temp_dir, f"timeout_{timeout}")
            os.makedirs(script_dir, exist_ok=True)
            
            # Create script that sleeps longer than timeout
            script_content = f"sleep {timeout * 2}\n"
            script_path = os.path.join(script_dir, "test.sh")
            with open(script_path, 'w') as f:
                f.write("#!/bin/bash\n")
                f.write(script_content)
            os.chmod(script_path, 0o755)
            
            # Execute
            executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
            
            # Wait for timeout
            max_wait = timeout + 3
            start_time = time.time()
            while time.time() - start_time < max_wait:
                record = manager.get_execution(record.execution_id)
                if record and record.status == ExecutionStatus.TIMED_OUT:
                    break
                time.sleep(0.1)
            
            # Verify timeout
            final_record = manager.get_execution(record.execution_id)
            assert final_record.status == ExecutionStatus.TIMED_OUT


def test_cleanup_removes_script_file():
    """Test cleanup removes script file after execution"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="1" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script
        script_content = "echo 'test'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Verify script exists
        assert os.path.exists(script_path)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Wait for cleanup
        time.sleep(0.3)
        
        # Verify script was removed
        assert not os.path.exists(script_path)


def test_cleanup_on_success():
    """Test cleanup occurs after successful execution"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="2" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create successful script
        script_content = "echo 'success'\nexit 0\n"
        script_path = create_test_script(temp_dir, script_content)
        
        assert os.path.exists(script_path)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Verify success
        final_record = manager.get_execution(record.execution_id)
        assert final_record.status == ExecutionStatus.COMPLETED
        
        # Wait for cleanup
        time.sleep(0.3)
        
        # Verify cleanup
        assert not os.path.exists(script_path)


def test_cleanup_on_failure():
    """Test cleanup occurs after failed execution"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="3" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create failing script
        script_content = "echo 'failure'\nexit 1\n"
        script_path = create_test_script(temp_dir, script_content)
        
        assert os.path.exists(script_path)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Verify failure
        final_record = manager.get_execution(record.execution_id)
        assert final_record.status == ExecutionStatus.FAILED
        
        # Wait for cleanup
        time.sleep(0.3)
        
        # Verify cleanup
        assert not os.path.exists(script_path)


def test_cleanup_on_timeout():
    """Test cleanup occurs after timeout"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution with short timeout
        timeout = 1
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="4" * 40,
            script_path="test.sh",
            timeout_seconds=timeout
        )
        
        # Create script that times out
        script_content = f"sleep {timeout * 2}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        assert os.path.exists(script_path)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for timeout
        max_wait = timeout + 3
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(record.execution_id)
            if record and record.status == ExecutionStatus.TIMED_OUT:
                break
            time.sleep(0.1)
        
        # Verify timeout
        final_record = manager.get_execution(record.execution_id)
        assert final_record.status == ExecutionStatus.TIMED_OUT
        
        # Wait for cleanup
        time.sleep(0.3)
        
        # Verify cleanup
        assert not os.path.exists(script_path)


def test_cleanup_removes_empty_directory():
    """Test cleanup removes empty execution directory"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="5" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create subdirectory for script
        script_dir = os.path.join(temp_dir, "execution_dir")
        os.makedirs(script_dir, exist_ok=True)
        script_path = os.path.join(script_dir, "test.sh")
        
        with open(script_path, 'w') as f:
            f.write("#!/bin/bash\necho 'test'\n")
        os.chmod(script_path, 0o755)
        
        assert os.path.exists(script_dir)
        assert os.path.exists(script_path)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Wait for cleanup
        time.sleep(0.3)
        
        # Verify both script and directory removed
        assert not os.path.exists(script_path)
        assert not os.path.exists(script_dir)


def test_large_output_capture():
    """Test script execution with large output"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="6" * 40,
            script_path="test.sh",
            timeout_seconds=10
        )
        
        # Create script that generates large output
        lines = 1000
        script_content = f"for i in {{1..{lines}}}; do echo \"Line $i\"; done\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id, max_wait=15)
        
        # Verify output captured
        output_data = collector.get_output(record.execution_id)
        assert output_data.complete is True
        
        # Verify output contains expected lines
        output_lines = output_data.stdout.strip().split('\n')
        assert len(output_lines) >= lines * 0.9  # Allow some tolerance


def test_concurrent_executions():
    """Test multiple scripts can execute concurrently"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        num_executions = 5
        execution_ids = []
        
        # Create and start multiple executions
        for i in range(num_executions):
            record = manager.create_execution(
                repository_url="https://github.com/owner/repo",
                commit_hash=f"{i:040d}",
                script_path=f"test_{i}.sh",
                timeout_seconds=5
            )
            execution_ids.append(record.execution_id)
            
            # Create script with unique output
            script_content = f"echo 'Execution {i}'\nsleep 0.5\n"
            script_path = create_test_script(temp_dir, script_content, f"script_{i}.sh")
            
            # Execute
            executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for all to complete
        max_wait = 10
        start_time = time.time()
        completed = set()
        
        while time.time() - start_time < max_wait and len(completed) < num_executions:
            for exec_id in execution_ids:
                if exec_id not in completed:
                    record = manager.get_execution(exec_id)
                    if record and record.status in [
                        ExecutionStatus.COMPLETED,
                        ExecutionStatus.FAILED,
                        ExecutionStatus.TIMED_OUT
                    ]:
                        completed.add(exec_id)
            time.sleep(0.1)
        
        # Verify all completed
        assert len(completed) == num_executions
        
        # Verify each has correct output
        for i, exec_id in enumerate(execution_ids):
            output_data = collector.get_output(exec_id)
            assert f"Execution {i}" in output_data.stdout


def test_terminate_running_execution():
    """Test terminating a running execution"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="7" * 40,
            script_path="test.sh",
            timeout_seconds=30
        )
        
        # Create long-running script
        script_content = "sleep 30\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for it to start
        time.sleep(0.5)
        
        # Terminate
        result = executor.terminate(record.execution_id)
        assert result is True
        
        # Wait a bit for termination to complete
        time.sleep(0.5)
        
        # Verify process is no longer active
        result = executor.terminate(record.execution_id)
        assert result is False  # Already terminated


def test_terminate_nonexistent_execution():
    """Test terminating non-existent execution returns False"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Try to terminate non-existent execution
        result = executor.terminate("nonexistent-id")
        assert result is False


def test_terminate_completed_execution():
    """Test terminating already completed execution returns False"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="8" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create fast script
        script_content = "echo 'done'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Try to terminate completed execution
        result = executor.terminate(record.execution_id)
        assert result is False


def test_status_transitions():
    """Test execution status transitions through lifecycle"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=create_mock_docker_client(),
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="9" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Initial status should be QUEUED
        assert record.status == ExecutionStatus.QUEUED
        
        # Create script
        script_content = "echo 'test'\nsleep 0.5\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        
        # Wait for RUNNING status
        max_wait = 2
        start_time = time.time()
        running_seen = False
        while time.time() - start_time < max_wait:
            record = manager.get_execution(record.execution_id)
            if record.status == ExecutionStatus.RUNNING:
                running_seen = True
                break
            time.sleep(0.05)
        
        assert running_seen, "Execution should transition to RUNNING"
        
        # Wait for completion
        assert wait_for_completion(manager, record.execution_id)
        
        # Final status should be COMPLETED
        final_record = manager.get_execution(record.execution_id)
        assert final_record.status == ExecutionStatus.COMPLETED


def test_container_created_with_cap_drop_all():
    """Test that container is created with cap_drop=["ALL"]"""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=docker_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )

        script_content = "echo 'test'\n"
        script_path = create_test_script(temp_dir, script_content)

        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        creation_calls = docker_client.containers._creation_calls
        assert len(creation_calls) >= 1
        assert creation_calls[0].get("cap_drop") == ["ALL"]


def test_container_created_with_cap_add_build_capabilities():
    """Test that cap_add contains exactly the 7 documented capabilities"""
    expected_cap_add = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "NET_BIND_SERVICE", "KILL"]

    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=docker_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="b" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )

        script_content = "echo 'test'\n"
        script_path = create_test_script(temp_dir, script_content)

        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        creation_calls = docker_client.containers._creation_calls
        assert len(creation_calls) >= 1
        actual_cap_add = creation_calls[0].get("cap_add")
        assert actual_cap_add is not None, "cap_add should be present in container creation"
        assert actual_cap_add == expected_cap_add


def test_container_no_additional_capabilities():
    """Test that no additional capabilities are present in cap_add"""
    expected_cap_add = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "NET_BIND_SERVICE", "KILL"}

    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=docker_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="c" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )

        script_content = "echo 'test'\n"
        script_path = create_test_script(temp_dir, script_content)

        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        creation_calls = docker_client.containers._creation_calls
        assert len(creation_calls) >= 1
        actual_cap_add = set(creation_calls[0].get("cap_add", []))
        assert actual_cap_add == expected_cap_add, \
            f"cap_add should contain exactly {expected_cap_add}, got {actual_cap_add}"
        assert len(creation_calls[0].get("cap_add", [])) == 7, \
            f"cap_add should have exactly 7 capabilities, got {len(creation_calls[0].get('cap_add', []))}"


# ===========================================================================
# User Story 1: Hardened sandbox by default (T006)
# ===========================================================================

def test_default_config_produces_hardened_container_kwargs():
    """A ScriptExecutor built from a default ServerConfig hardens every container."""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        config = _default_server_config()
        executor = _executor_from_config(config, docker_client, manager, collector, temp_dir)

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = create_test_script(temp_dir, "echo 'test'\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        creation_calls = docker_client.containers._creation_calls
        assert len(creation_calls) == 1
        call = creation_calls[0]

        # Non-root user
        assert call.get("user") == "65534:65534"
        # Read-only rootfs + bounded /tmp tmpfs scratch
        assert call.get("read_only") is True
        assert call.get("tmpfs") == {"/tmp": "size=256m,mode=1777"}
        # Read-only workspace bind
        repo_mount = next(iter(call.get("volumes", {}).values()))
        assert repo_mount["bind"] == "/workspace"
        assert repo_mount["mode"] == "ro"
        # no-new-privileges on
        assert call.get("security_opt") == ["no-new-privileges"]
        # cap_drop ALL + default 7-cap set on top
        assert call.get("cap_drop") == ["ALL"]
        assert call.get("cap_add") == list(CONTAINER_DEFAULT_CAP_ADD)
        # Network isolated
        assert call.get("network_mode") == "none"


def test_default_cap_add_applied_on_top_of_cap_drop_all():
    """With cap_add unset (None), the default 7-cap working set is granted over drop-ALL."""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        config = _default_server_config()
        assert config.container_cap_add is None  # unset -> resolves to default set
        executor = _executor_from_config(config, docker_client, manager, collector, temp_dir)

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="b" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = create_test_script(temp_dir, "echo 'test'\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        call = docker_client.containers._creation_calls[0]
        assert call.get("cap_drop") == ["ALL"]
        assert call.get("cap_add") == [
            "CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "NET_BIND_SERVICE", "KILL",
        ]


def test_empty_cap_add_adds_no_capabilities():
    """An explicitly empty cap_add ([]) grants no capabilities over drop-ALL."""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        config = _default_server_config(container_cap_add=[])
        executor = _executor_from_config(config, docker_client, manager, collector, temp_dir)

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="c" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = create_test_script(temp_dir, "echo 'test'\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        call = docker_client.containers._creation_calls[0]
        assert call.get("cap_drop") == ["ALL"]
        assert call.get("cap_add") == []


def test_no_new_privileges_disabled_omits_security_opt():
    """When no_new_privileges is False, the security_opt is omitted entirely."""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        config = _default_server_config(no_new_privileges=False)
        executor = _executor_from_config(config, docker_client, manager, collector, temp_dir)

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="d" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = create_test_script(temp_dir, "echo 'test'\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        call = docker_client.containers._creation_calls[0]
        assert "security_opt" not in call


def test_empty_tmpfs_size_omits_tmpfs_mount():
    """An empty CONTAINER_TMPFS_SIZE means no tmpfs mount is configured."""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        config = _default_server_config(container_tmpfs_size="")
        executor = _executor_from_config(config, docker_client, manager, collector, temp_dir)

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="e" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = create_test_script(temp_dir, "echo 'test'\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        call = docker_client.containers._creation_calls[0]
        assert "tmpfs" not in call


def test_relaxed_values_flow_through_to_container():
    """Valid non-default values (rw workspace, bridge network) reach containers.create()."""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        config = _default_server_config(
            workspace_mount_mode="rw",
            container_network_mode="bridge",
            container_user="0:0",
        )
        executor = _executor_from_config(config, docker_client, manager, collector, temp_dir)

        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="f" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = create_test_script(temp_dir, "echo 'test'\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        call = docker_client.containers._creation_calls[0]
        assert call.get("user") == "0:0"
        assert call.get("network_mode") == "bridge"
        repo_mount = next(iter(call.get("volumes", {}).values()))
        assert repo_mount["mode"] == "rw"


# --- T008: tmpfs_exec=False produces byte-identical options string (INV-1) ---

def test_tmpfs_exec_false_options_byte_identical(monkeypatch):
    """tmpfs_exec=False with a size produces exactly 'size=<size>,mode=1777' — no exec token (INV-1)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=docker_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
            tmpfs_size="256m",
            tmpfs_exec=False,
        )
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = create_test_script(temp_dir, "echo ok\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        call = docker_client.containers._creation_calls[0]
        assert call.get("tmpfs") == {"/tmp": "size=256m,mode=1777"}, (
            f"Disabled exec MUST produce byte-identical pre-feature string, got {call.get('tmpfs')!r}"
        )


def test_tmpfs_exec_false_default_is_noexec():
    """ScriptExecutor with no tmpfs_exec argument (default) has noexec mount (SC-001)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        docker_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=docker_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
            tmpfs_size="128m",
        )
        record = manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="b" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )
        script_path = create_test_script(temp_dir, "echo ok\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))
        assert wait_for_completion(manager, record.execution_id)

        call = docker_client.containers._creation_calls[0]
        opts = call.get("tmpfs", {}).get("/tmp", "")
        assert "exec" not in opts, f"Default executor must not have exec in tmpfs opts, got {opts!r}"
        assert "size=128m,mode=1777" == opts
