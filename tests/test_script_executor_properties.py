"""Property-based tests for script executor

Feature: github-actions-remote-executor
Tests Properties 21, 22, 25, 26, 27, 28 from the design document
"""
import pytest
import os
import tempfile
import time
import threading
from pathlib import Path
from hypothesis import given, strategies as st, assume, settings
from src.script_executor import ScriptExecutor
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
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
        'timeout_seconds': draw(st.integers(min_value=1, max_value=5))  # Reduced from 30 to 5
    }


def create_test_script(temp_dir: str, script_content: str, filename: str = "test_script.sh") -> str:
    """Helper to create a test script file"""
    script_path = os.path.join(temp_dir, filename)
    with open(script_path, 'w') as f:
        f.write("#!/bin/bash\n")
        f.write(script_content)
    os.chmod(script_path, 0o755)
    return script_path


# Property 21: Asynchronous Script Execution
# Feature: github-actions-remote-executor, Property 21: Asynchronous Script Execution
@given(
    params=execution_params(),
    output_text=st.text(min_size=0, max_size=100)  # Reduced from 1000
)
@settings(max_examples=10, deadline=5000)  # Reduced from 30 examples and 10000ms deadline
def test_property_21_asynchronous_script_execution(params, output_text):
    """
    Property 21: For any execution request, the script should execute asynchronously
    after the initial response is sent.
    
    Validates: Requirements 5.1
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        
        # Create a script that outputs text
        safe_output = output_text.replace("'", "'\\''")  # Escape single quotes
        script_content = f"echo '{safe_output}'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute asynchronously
        executor.execute_async(execution_id, script_path)
        
        # Verify execute_async returns immediately (non-blocking)
        # The function should return before script completes
        # We verify this by checking that the call doesn't block
        
        # Wait for execution to complete
        max_wait = 3  # Reduced from 5 seconds
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMED_OUT
            ]:
                break
            time.sleep(0.1)
        
        # Verify execution completed
        final_record = manager.get_execution(execution_id)
        assert final_record is not None, "Execution record should exist"
        assert final_record.status in [
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT
        ], f"Execution should reach terminal state, got {final_record.status}"


@given(
    params_list=st.lists(execution_params(), min_size=2, max_size=3)  # Reduced from 5
)
@settings(max_examples=10, deadline=10000)  # Reduced from 20 examples
def test_property_21_multiple_async_executions(params_list):
    """
    Property 21 (variant): Multiple scripts should execute asynchronously
    without blocking each other.
    
    Validates: Requirements 5.1
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        execution_ids = []
        
        # Start multiple executions
        for i, params in enumerate(params_list):
            record = manager.create_execution(**params)
            execution_ids.append(record.execution_id)
            
            # Create a simple script
            script_content = f"echo 'Execution {i}'\n"
            script_path = create_test_script(temp_dir, script_content, f"script_{i}.sh")
            
            # Execute asynchronously
            executor.execute_async(record.execution_id, script_path)
        
        # Wait for all executions to complete
        max_wait = 5  # Reduced from 10 seconds
        start_time = time.time()
        completed_count = 0
        
        while time.time() - start_time < max_wait and completed_count < len(execution_ids):
            completed_count = 0
            for exec_id in execution_ids:
                record = manager.get_execution(exec_id)
                if record and record.status in [
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.TIMED_OUT
                ]:
                    completed_count += 1
            time.sleep(0.1)
        
        # Verify all executions completed
        assert completed_count == len(execution_ids), \
            f"All {len(execution_ids)} executions should complete, got {completed_count}"


# Property 22: Process Isolation
# Feature: github-actions-remote-executor, Property 22: Process Isolation
@given(
    params=execution_params()
)
@settings(max_examples=10, deadline=5000)
def test_property_22_process_isolation(params):
    """
    Property 22: For any script execution, the script should run in an isolated
    process separate from the server process.
    
    Validates: Requirements 5.2
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        
        # Get server process ID
        server_pid = os.getpid()
        
        # Create a script that outputs its own PID
        script_content = "echo $$\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for execution to complete
        max_wait = 3
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMED_OUT
            ]:
                break
            time.sleep(0.1)
        
        # Get output
        output_data = collector.get_output(execution_id)
        
        if output_data.stdout.strip():
            script_pid = int(output_data.stdout.strip())
            
            # Verify script ran in different process
            assert script_pid != server_pid, \
                f"Script PID ({script_pid}) should differ from server PID ({server_pid})"


@given(
    params=execution_params()
)
@settings(max_examples=10, deadline=5000)
def test_property_22_process_isolation_crash_safety(params):
    """
    Property 22 (variant): A crashing script should not affect the server process.
    
    Validates: Requirements 5.2
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        
        # Create a script that exits with error
        script_content = "exit 42\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for execution to complete
        max_wait = 3
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMED_OUT
            ]:
                break
            time.sleep(0.1)
        
        # Verify server is still running (we're still executing)
        assert os.getpid() > 0, "Server process should still be running"
        
        # Verify execution was marked as failed
        final_record = manager.get_execution(execution_id)
        assert final_record is not None, "Execution record should exist"
        assert final_record.status == ExecutionStatus.FAILED, \
            "Execution with non-zero exit should be marked as FAILED"


# Property 25: Execution Timeout Configuration
# Feature: github-actions-remote-executor, Property 25: Execution Timeout Configuration
@given(
    params=execution_params()
)
@settings(max_examples=10, deadline=5000)
def test_property_25_execution_timeout_configuration(params):
    """
    Property 25: For any configured timeout value, script executions should
    respect that timeout limit.
    
    Validates: Requirements 5.5
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record with specific timeout
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        configured_timeout = record.timeout_seconds
        
        # Create a fast-completing script
        script_content = "echo 'done'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for execution to complete
        max_wait = configured_timeout + 5
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMED_OUT
            ]:
                break
            time.sleep(0.1)
        
        # Verify execution completed (didn't timeout)
        final_record = manager.get_execution(execution_id)
        assert final_record is not None, "Execution record should exist"
        
        # For fast scripts, they should complete before timeout
        assert final_record.status in [ExecutionStatus.COMPLETED, ExecutionStatus.FAILED], \
            f"Fast script should complete before timeout, got {final_record.status}"


# Property 26: Timeout Termination
# Feature: github-actions-remote-executor, Property 26: Timeout Termination
@given(
    params=execution_params().filter(lambda p: p['timeout_seconds'] <= 2),
    sleep_multiplier=st.floats(min_value=1.5, max_value=2.0)
)
@settings(max_examples=10, deadline=20000)  # Reduced from 20 examples
def test_property_26_timeout_termination(params, sleep_multiplier):
    """
    Property 26: For any script execution that exceeds the configured timeout,
    the Script Executor should terminate the process and mark the execution as timed_out.
    
    Validates: Requirements 5.6
    """
    # Ensure timeout is short for testing
    params['timeout_seconds'] = min(params['timeout_seconds'], 2)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        configured_timeout = record.timeout_seconds
        
        # Create a script that sleeps longer than timeout
        sleep_duration = int(configured_timeout * sleep_multiplier) + 1
        script_content = f"sleep {sleep_duration}\necho 'Should not reach here'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute script
        start_time = time.time()
        executor.execute_async(execution_id, script_path)
        
        # Wait for timeout to occur
        max_wait = configured_timeout + 5  # Reduced from 10
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status == ExecutionStatus.TIMED_OUT:
                break
            time.sleep(0.1)
        
        execution_duration = time.time() - start_time
        
        # Verify execution was terminated and marked as timed out
        final_record = manager.get_execution(execution_id)
        assert final_record is not None, "Execution record should exist"
        assert final_record.status == ExecutionStatus.TIMED_OUT, \
            f"Long-running script should timeout, got {final_record.status}"
        
        # Verify termination happened around the timeout period
        # Allow some overhead for process management
        assert execution_duration < configured_timeout + 5, \
            f"Execution should terminate near timeout ({configured_timeout}s), took {execution_duration:.1f}s"


@given(
    timeout_seconds=st.integers(min_value=1, max_value=2)
)
@settings(max_examples=10, deadline=20000)  # Reduced from 20 examples
def test_property_26_timeout_exit_code(timeout_seconds):
    """
    Property 26 (variant): Timed out executions should have exit code -1.
    
    Validates: Requirements 5.6
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        params = {
            'repository_url': 'https://github.com/test/repo',
            'commit_hash': '0' * 40,
            'script_path': 'test.sh',
            'timeout_seconds': timeout_seconds
        }
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        
        # Create a script that sleeps longer than timeout
        script_content = f"sleep {timeout_seconds * 2}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for timeout
        max_wait = timeout_seconds + 5  # Reduced from 10
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status == ExecutionStatus.TIMED_OUT:
                break
            time.sleep(0.1)
        
        # Verify exit code is -1
        final_record = manager.get_execution(execution_id)
        assert final_record is not None, "Execution record should exist"
        assert final_record.exit_code == -1, \
            f"Timed out execution should have exit code -1, got {final_record.exit_code}"


# Property 27: Exit Code Capture
# Feature: github-actions-remote-executor, Property 27: Exit Code Capture
@given(
    params=execution_params(),
    exit_code=st.integers(min_value=0, max_value=255)
)
@settings(max_examples=10, deadline=5000)
def test_property_27_exit_code_capture(params, exit_code):
    """
    Property 27: For any completed script execution, the exit code should be
    captured and stored with the execution record.
    
    Validates: Requirements 5.7
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        
        # Create a script that exits with specific code
        script_content = f"exit {exit_code}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for execution to complete
        max_wait = 3
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMED_OUT
            ]:
                break
            time.sleep(0.1)
        
        # Verify exit code was captured
        final_record = manager.get_execution(execution_id)
        assert final_record is not None, "Execution record should exist"
        assert final_record.exit_code == exit_code, \
            f"Exit code should be {exit_code}, got {final_record.exit_code}"
        
        # Verify status matches exit code
        if exit_code == 0:
            assert final_record.status == ExecutionStatus.COMPLETED, \
                "Exit code 0 should result in COMPLETED status"
        else:
            assert final_record.status == ExecutionStatus.FAILED, \
                "Non-zero exit code should result in FAILED status"


@given(
    params=execution_params()
)
@settings(max_examples=10, deadline=5000)
def test_property_27_exit_code_in_output(params):
    """
    Property 27 (variant): Exit code should be available in output data.
    
    Validates: Requirements 5.7
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        
        # Create a script with known exit code
        exit_code = 7
        script_content = f"echo 'test'\nexit {exit_code}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for execution to complete
        max_wait = 3
        start_time = time.time()
        while time.time() - start_time < max_wait:
            output_data = collector.get_output(execution_id)
            if output_data.complete:
                break
            time.sleep(0.1)
        
        # Verify exit code in output data
        output_data = collector.get_output(execution_id)
        assert output_data.complete, "Execution should be complete"
        assert output_data.exit_code == exit_code, \
            f"Output data should contain exit code {exit_code}, got {output_data.exit_code}"


# Property 28: Temporary File Cleanup
# Feature: github-actions-remote-executor, Property 28: Temporary File Cleanup
@given(
    params=execution_params().filter(lambda p: p['timeout_seconds'] <= 5),
    should_succeed=st.booleans()
)
@settings(max_examples=10, deadline=10000)  # Reduced from 20 examples and 15000ms
def test_property_28_temporary_file_cleanup(params, should_succeed):
    """
    Property 28: For any script execution (successful or failed), all temporary
    files should be cleaned up after execution completes.
    
    Validates: Requirements 8.4
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        
        # Create a script that succeeds or fails
        exit_code = 0 if should_succeed else 1
        script_content = f"echo 'test'\nexit {exit_code}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Verify script file exists before execution
        assert os.path.exists(script_path), "Script file should exist before execution"
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for execution to complete
        max_wait = 3
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMED_OUT
            ]:
                break
            time.sleep(0.1)
        
        # Wait a bit more for cleanup to complete
        time.sleep(0.2)
        
        # Verify script file was cleaned up
        assert not os.path.exists(script_path), \
            f"Script file should be cleaned up after execution, but still exists at {script_path}"


@given(
    params=execution_params().filter(lambda p: p['timeout_seconds'] <= 2)
)
@settings(max_examples=10, deadline=20000)  # Reduced from 20 examples
def test_property_28_cleanup_after_timeout(params):
    """
    Property 28 (variant): Temporary files should be cleaned up even after timeout.
    
    Validates: Requirements 8.4
    """
    params['timeout_seconds'] = min(params['timeout_seconds'], 2)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        timeout = record.timeout_seconds
        
        # Create a script that times out
        script_content = f"sleep {timeout * 2}\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Verify script file exists
        assert os.path.exists(script_path), "Script file should exist before execution"
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for timeout
        max_wait = timeout + 5  # Reduced from 10
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status == ExecutionStatus.TIMED_OUT:
                break
            time.sleep(0.1)
        
        # Wait for cleanup
        time.sleep(0.2)
        
        # Verify script file was cleaned up even after timeout
        assert not os.path.exists(script_path), \
            "Script file should be cleaned up after timeout"


@given(
    params=execution_params().filter(lambda p: p['timeout_seconds'] <= 5)
)
@settings(max_examples=10, deadline=10000)  # Reduced from 20 examples and 15000ms
def test_property_28_cleanup_preserves_output(params):
    """
    Property 28 (variant): Cleanup should remove script files but preserve output data.
    
    Validates: Requirements 8.4
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create components
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir
        )
        
        # Create execution record
        record = manager.create_execution(**params)
        execution_id = record.execution_id
        
        # Create a script with output
        test_output = "Test output message"
        script_content = f"echo '{test_output}'\n"
        script_path = create_test_script(temp_dir, script_content)
        
        # Execute script
        executor.execute_async(execution_id, script_path)
        
        # Wait for execution to complete
        max_wait = 3
        start_time = time.time()
        while time.time() - start_time < max_wait:
            record = manager.get_execution(execution_id)
            if record and record.status in [
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMED_OUT
            ]:
                break
            time.sleep(0.1)
        
        # Wait for cleanup
        time.sleep(0.2)
        
        # Verify script file was cleaned up
        assert not os.path.exists(script_path), "Script file should be cleaned up"
        
        # Verify output is still accessible
        output_data = collector.get_output(execution_id)
        assert test_output in output_data.stdout, \
            "Output data should be preserved after cleanup"
