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
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus


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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script with known output
        expected_output = "Hello from script"
        script_content = f"echo '{expected_output}'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="b" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script that writes to stderr
        error_message = "Error message"
        script_content = f"echo '{error_message}' >&2\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
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
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="d" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script that exits with 0
        script_content = "exit 0\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="e" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create script that exits with error code
        exit_code = 42
        script_content = f"exit {exit_code}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        test_exit_codes = [0, 1, 2, 127, 255]
        
        for exit_code in test_exit_codes:
            # Create execution
            record = manager.create_execution(
                repository_url="https://github.com/test/repo",
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
            executor.execute_async(record.execution_id, script_path)
            
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution with short timeout
        timeout = 1
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="f" * 40,
            script_path="test.sh",
            timeout_seconds=timeout
        )
        
        # Create script that sleeps longer than timeout
        script_content = f"sleep {timeout * 3}\necho 'Should not reach here'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        start_time = time.time()
        executor.execute_async(record.execution_id, script_path)
        
        # Wait for timeout
        max_wait = timeout + 3
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
        assert execution_duration < timeout + 3


def test_execute_script_timeout_with_different_durations():
    """Test timeout works with various timeout durations"""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        timeout_values = [1, 2]
        
        for timeout in timeout_values:
            # Create execution
            record = manager.create_execution(
                repository_url="https://github.com/test/repo",
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
            executor.execute_async(record.execution_id, script_path)
            
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
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
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="2" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create successful script
        script_content = "echo 'success'\nexit 0\n"
        script_path = create_test_script(temp_dir, script_content)
        
        assert os.path.exists(script_path)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="3" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create failing script
        script_content = "echo 'failure'\nexit 1\n"
        script_path = create_test_script(temp_dir, script_content)
        
        assert os.path.exists(script_path)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution with short timeout
        timeout = 1
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="4" * 40,
            script_path="test.sh",
            timeout_seconds=timeout
        )
        
        # Create script that times out
        script_content = f"sleep {timeout * 2}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        assert os.path.exists(script_path)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
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
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="6" * 40,
            script_path="test.sh",
            timeout_seconds=10
        )
        
        # Create script that generates large output
        lines = 1000
        script_content = f"for i in {{1..{lines}}}; do echo \"Line $i\"; done\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        num_executions = 5
        execution_ids = []
        
        # Create and start multiple executions
        for i in range(num_executions):
            record = manager.create_execution(
                repository_url="https://github.com/test/repo",
                commit_hash=f"{i:040d}",
                script_path=f"test_{i}.sh",
                timeout_seconds=5
            )
            execution_ids.append(record.execution_id)
            
            # Create script with unique output
            script_content = f"echo 'Execution {i}'\nsleep 0.5\n"
            script_path = create_test_script(temp_dir, script_content, f"script_{i}.sh")
            
            # Execute
            executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="7" * 40,
            script_path="test.sh",
            timeout_seconds=30
        )
        
        # Create long-running script
        script_content = "sleep 30\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
            commit_hash="8" * 40,
            script_path="test.sh",
            timeout_seconds=5
        )
        
        # Create fast script
        script_content = "echo 'done'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute
        executor.execute_async(record.execution_id, script_path)
        
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
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution
        record = manager.create_execution(
            repository_url="https://github.com/test/repo",
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
        executor.execute_async(record.execution_id, script_path)
        
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
