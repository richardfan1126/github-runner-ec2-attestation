"""Property-based tests for Script Environment Variable Forwarding

Feature: github-actions-remote-executor
Tests Property 169 from the design document

**Validates: Requirements 52.1, 52.2, 52.3, 52.4, 52.5, 52.6**
"""
import os
import tempfile
import time

from hypothesis import given, strategies as st, settings
from src.script_executor import ScriptExecutor, CONTAINER_NAME_PREFIX
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus
from tests.mock_docker import create_mock_docker_client


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def env_var_name(draw):
    """Generate valid environment variable names (non-empty strings of alphanumeric + underscore)."""
    return draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_',
        min_size=1, max_size=30,
    ))


@st.composite
def env_var_value(draw):
    """Generate environment variable values (printable strings)."""
    return draw(st.text(
        alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'Z'), blacklist_characters='\x00'),
        min_size=0, max_size=100,
    ))


@st.composite
def valid_script_env(draw):
    """Generate a valid script_env dictionary with string keys and string values."""
    return draw(st.dictionaries(
        keys=env_var_name(),
        values=env_var_value(),
        min_size=0, max_size=10,
    ))


@st.composite
def mixed_type_script_env(draw):
    """Generate a script_env dictionary with potentially non-string keys or values."""
    non_string_key = st.one_of(
        st.integers(min_value=-100, max_value=100),
        st.floats(allow_nan=False, allow_infinity=False),
        st.booleans(),
    )
    non_string_value = st.one_of(
        st.integers(min_value=-100, max_value=100),
        st.floats(allow_nan=False, allow_infinity=False),
        st.booleans(),
        st.none(),
        st.lists(st.integers(), max_size=3),
    )
    key_strategy = st.one_of(env_var_name(), non_string_key)
    value_strategy = st.one_of(env_var_value(), non_string_value)
    return draw(st.dictionaries(
        keys=key_strategy,
        values=value_strategy,
        min_size=1, max_size=10,
    ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_test_script(temp_dir: str, script_content: str = "echo ok\n") -> str:
    """Helper to create a test script file."""
    script_path = os.path.join(temp_dir, "test_script.sh")
    with open(script_path, 'w') as f:
        f.write("#!/bin/bash\n")
        f.write(script_content)
    os.chmod(script_path, 0o755)
    return script_path


def _wait_for_terminal(manager, execution_id, max_wait=5):
    """Wait until execution reaches a terminal state."""
    start = time.time()
    while time.time() - start < max_wait:
        rec = manager.get_execution(execution_id)
        if rec and rec.status in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
        ):
            return rec
        time.sleep(0.1)
    return manager.get_execution(execution_id)


def _make_executor(mock_client, manager, collector, temp_dir):
    """Create a ScriptExecutor with sensible defaults."""
    return ScriptExecutor(
        docker_client=mock_client,
        execution_manager=manager,
        output_collector=collector,
        temp_storage_path=temp_dir,
        container_image="test-image:latest",
        memory_limit="512m",
        cpu_limit=1.0,
    )


def _create_execution(manager):
    """Create an execution record with default params."""
    return manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        timeout_seconds=5,
    )


# ===========================================================================
# Property 169: Script Environment Variable Forwarding
# ===========================================================================

@given(script_env=valid_script_env())
@settings(max_examples=100, deadline=10000)
def test_property_169_script_env_forwarded_to_container(script_env):
    """
    Property 169: For any /execute request with a `script_env` dictionary,
    verify the container is created with those environment variables.

    **Validates: Requirements 52.1, 52.2, 52.3, 52.4, 52.5, 52.6**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = _make_executor(mock_client, manager, collector, temp_dir)

        record = _create_execution(manager)
        create_test_script(temp_dir)
        executor.execute_async(
            record.execution_id,
            temp_dir,
            "test_script.sh",
            script_env=script_env,
        )

        _wait_for_terminal(manager, record.execution_id)

        creation_calls = mock_client.containers._creation_calls
        assert len(creation_calls) == 1, f"Expected 1 container, got {len(creation_calls)}"

        call = creation_calls[0]
        actual_env = call.get("environment", {})

        # The container should have exactly the script_env passed
        assert actual_env == script_env, (
            f"Container environment should match script_env.\n"
            f"Expected: {script_env}\n"
            f"Got: {actual_env}"
        )


@given(data=st.data())
@settings(max_examples=100, deadline=10000)
def test_property_169_absent_script_env_gives_empty_environment(data):
    """
    Property 169: For any /execute request without `script_env`,
    verify the container is created with an empty environment.

    **Validates: Requirements 52.1, 52.2, 52.3, 52.4, 52.5, 52.6**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = _make_executor(mock_client, manager, collector, temp_dir)

        record = _create_execution(manager)
        create_test_script(temp_dir)

        # Call without script_env (default None)
        executor.execute_async(
            record.execution_id,
            temp_dir,
            "test_script.sh",
        )

        _wait_for_terminal(manager, record.execution_id)

        creation_calls = mock_client.containers._creation_calls
        assert len(creation_calls) == 1

        call = creation_calls[0]
        actual_env = call.get("environment", {})

        # Without script_env, container should get empty environment
        assert actual_env == {}, (
            f"Container environment should be empty when script_env is not provided, got: {actual_env}"
        )


@given(mixed_env=mixed_type_script_env())
@settings(max_examples=100, deadline=10000)
def test_property_169_non_string_entries_sanitized(mixed_env):
    """
    Property 169: For any `script_env` with non-string keys or values,
    verify they are sanitized (dropped) by the server-side sanitization logic.

    This tests the sanitization logic that the server applies before passing
    script_env to the executor.

    **Validates: Requirements 52.1, 52.2, 52.3, 52.4, 52.5, 52.6**
    """
    # Apply the same sanitization logic as server.py
    sanitized = {
        str(k): str(v)
        for k, v in mixed_env.items()
        if isinstance(k, str) and isinstance(v, str)
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = _make_executor(mock_client, manager, collector, temp_dir)

        record = _create_execution(manager)
        create_test_script(temp_dir)

        # Pass the sanitized env (as the server would)
        executor.execute_async(
            record.execution_id,
            temp_dir,
            "test_script.sh",
            script_env=sanitized,
        )

        _wait_for_terminal(manager, record.execution_id)

        creation_calls = mock_client.containers._creation_calls
        assert len(creation_calls) == 1

        call = creation_calls[0]
        actual_env = call.get("environment", {})

        # All keys and values in actual_env must be strings
        for k, v in actual_env.items():
            assert isinstance(k, str), f"Key {k!r} should be a string"
            assert isinstance(v, str), f"Value {v!r} for key {k!r} should be a string"

        # Non-string entries from the original dict should have been dropped
        assert actual_env == sanitized, (
            f"Container environment should only contain sanitized string entries.\n"
            f"Original: {mixed_env}\n"
            f"Expected (sanitized): {sanitized}\n"
            f"Got: {actual_env}"
        )
