"""Property-based tests for Docker container execution

Feature: github-actions-remote-executor
Tests Properties 109, 110, 111, 112, 113, 114, 115 from the design document
"""
import os
import tempfile
import time

from hypothesis import given, strategies as st, settings
from src.script_executor import ScriptExecutor, CONTAINER_NAME_PREFIX
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus
from tests.mock_docker import create_mock_docker_client, MockDockerClient, MockContainer, MockContainersAPI
import docker.errors


# ---------------------------------------------------------------------------
# Strategies (reused from existing property tests)
# ---------------------------------------------------------------------------

@st.composite
def valid_github_url(draw):
    """Generate valid GitHub repository URLs"""
    owner = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-',
        min_size=1, max_size=39,
    ))
    repo = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-',
        min_size=1, max_size=100,
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
            min_size=1, max_size=50,
        ).filter(lambda x: '..' not in x and x.strip()),
        min_size=1, max_size=5,
    ))
    return '/'.join(components)


@st.composite
def execution_params(draw):
    """Generate parameters for creating an execution"""
    return {
        'repository_url': draw(valid_github_url()),
        'commit_hash': draw(valid_commit_hash()),
        'script_path': draw(valid_script_path()),
        'timeout_seconds': draw(st.integers(min_value=1, max_value=5)),
    }


@st.composite
def container_image_name(draw):
    """Generate valid Docker image names"""
    registry = draw(st.sampled_from(["", "docker.io/", "ghcr.io/", "registry.example.com/"]))
    namespace = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyz0123456789_-',
        min_size=1, max_size=30,
    ))
    image = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyz0123456789_-',
        min_size=1, max_size=30,
    ))
    tag = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyz0123456789._-',
        min_size=1, max_size=20,
    ))
    return f"{registry}{namespace}/{image}:{tag}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_test_script(temp_dir: str, script_content: str, filename: str = "test_script.sh") -> str:
    """Helper to create a test script file"""
    script_path = os.path.join(temp_dir, filename)
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


# ===========================================================================
# Property 109: Container Non-Reuse
# ===========================================================================

@given(
    params_list=st.lists(execution_params(), min_size=2, max_size=3),
)
@settings(max_examples=10, deadline=10000)
def test_property_109_container_non_reuse(params_list):
    """
    Property 109: For any two script executions, verify the Execution_Containers
    used are distinct and no container is reused.

    **Validates: Requirements 5.3**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
        )

        execution_ids = []
        for i, params in enumerate(params_list):
            record = manager.create_execution(**params)
            execution_ids.append(record.execution_id)
            script_path = create_test_script(temp_dir, "echo ok\n", f"script_{i}.sh")
            executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))

        # Wait for all to finish
        for eid in execution_ids:
            _wait_for_terminal(manager, eid)

        # Collect container names from creation calls
        creation_calls = mock_client.containers._creation_calls
        container_names = [c["name"] for c in creation_calls]

        # All names must be unique (no reuse)
        assert len(container_names) == len(set(container_names)), (
            f"Container names must be unique across executions, got: {container_names}"
        )

        # Number of containers created must equal number of executions
        assert len(container_names) == len(execution_ids), (
            f"Expected {len(execution_ids)} containers, got {len(container_names)}"
        )


# ===========================================================================
# Property 110: Container Unique Naming
# ===========================================================================

@given(params=execution_params())
@settings(max_examples=20, deadline=10000)
def test_property_110_container_unique_naming(params):
    """
    Property 110: For any script execution, verify the Execution_Container is
    assigned a unique name derived from the Execution_ID.

    **Validates: Requirements 5.13**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
        )

        record = manager.create_execution(**params)
        execution_id = record.execution_id
        script_path = create_test_script(temp_dir, "echo ok\n")
        executor.execute_async(execution_id, os.path.dirname(script_path), os.path.basename(script_path))

        _wait_for_terminal(manager, execution_id)

        # Verify exactly one container was created
        creation_calls = mock_client.containers._creation_calls
        assert len(creation_calls) == 1, f"Expected 1 container, got {len(creation_calls)}"

        container_name = creation_calls[0]["name"]

        # Name must start with the prefix and contain the execution_id
        expected_name = f"{CONTAINER_NAME_PREFIX}{execution_id}"
        assert container_name == expected_name, (
            f"Container name should be '{expected_name}', got '{container_name}'"
        )


# ===========================================================================
# Property 111: Docker Container Security Constraints
# ===========================================================================

@given(params=execution_params())
@settings(max_examples=20, deadline=10000)
def test_property_111_docker_container_security_constraints(params):
    """
    Property 111: For any Execution_Container, verify it is configured with:
    non-root user, network disabled, read-only root filesystem (except execution
    directory), privilege escalation disabled, memory limits, and CPU limits.

    **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
            memory_limit="512m",
            cpu_limit=1.0,
        )

        record = manager.create_execution(**params)
        script_path = create_test_script(temp_dir, "echo ok\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))

        _wait_for_terminal(manager, record.execution_id)

        creation_calls = mock_client.containers._creation_calls
        assert len(creation_calls) == 1
        call = creation_calls[0]

        # Req 8.3: non-root user
        assert call.get("user") == "nobody", (
            f"Container must run as non-root user 'nobody', got '{call.get('user')}'"
        )

        # Req 8.4: network disabled
        assert call.get("network_mode") == "none", (
            f"Container must have network disabled, got '{call.get('network_mode')}'"
        )

        # Req 8.5: read-only root filesystem with writable execution dir
        assert call.get("read_only") is True, "Container root filesystem must be read-only"
        assert "tmpfs" in call, "Container must have a tmpfs for the execution directory"

        # Req 8.6: privilege escalation disabled
        security_opt = call.get("security_opt", [])
        assert "no-new-privileges" in security_opt, (
            f"Container must disable privilege escalation, got security_opt={security_opt}"
        )

        # Req 8.1: memory limits
        assert call.get("mem_limit") == "512m", (
            f"Container must have memory limit '512m', got '{call.get('mem_limit')}'"
        )

        # Req 8.2: CPU limits
        expected_nano_cpus = int(1.0 * 1e9)
        assert call.get("nano_cpus") == expected_nano_cpus, (
            f"Container must have CPU limit {expected_nano_cpus} nano_cpus, got {call.get('nano_cpus')}"
        )


# ===========================================================================
# Property 112: Container Removal Verification
# ===========================================================================

@given(params=execution_params())
@settings(max_examples=10, deadline=10000)
def test_property_112_container_removal_verification(params):
    """
    Property 112: For any Execution_Container that is removed, verify the
    container no longer exists on the Docker host.

    **Validates: Requirements 8.9**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
        )

        record = manager.create_execution(**params)
        execution_id = record.execution_id
        script_path = create_test_script(temp_dir, "echo ok\n")
        executor.execute_async(execution_id, os.path.dirname(script_path), os.path.basename(script_path))

        _wait_for_terminal(manager, execution_id)
        # Allow cleanup to finish
        time.sleep(0.3)

        # After execution completes, the container should have been removed
        container_name = f"{CONTAINER_NAME_PREFIX}{execution_id}"
        try:
            mock_client.containers.get(container_name)
            # If we get here, the container still exists — that's a failure
            assert False, f"Container {container_name} should have been removed after execution"
        except docker.errors.NotFound:
            pass  # Expected: container no longer exists

        # Also verify via the executor's own method
        assert executor.verify_container_removed(execution_id), (
            f"verify_container_removed should return True for {execution_id}"
        )


# ===========================================================================
# Property 113: Dangling Container Cleanup on Startup
# ===========================================================================

@given(
    num_dangling=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20, deadline=10000)
def test_property_113_dangling_container_cleanup_on_startup(num_dangling):
    """
    Property 113: For any server startup, verify the Script_Executor removes
    dangling Execution_Containers matching the naming convention.

    **Validates: Requirements 8.10**
    """
    mock_client = create_mock_docker_client()

    # Pre-populate dangling containers that match the naming convention
    dangling_names = []
    for i in range(num_dangling):
        name = f"{CONTAINER_NAME_PREFIX}dangling-{i}"
        dangling_names.append(name)
        mock_client.containers.create(
            image="test-image",
            name=name,
            command=["echo", "stale"],
        )

    # Verify they exist before cleanup
    listed = mock_client.containers.list(all=True, filters={"name": CONTAINER_NAME_PREFIX})
    assert len(listed) == num_dangling

    # Create executor and run cleanup
    executor = ScriptExecutor(
        docker_client=mock_client,
        execution_manager=ExecutionManager(output_retention_hours=1),
        output_collector=OutputCollector(),
    )
    executor.cleanup_dangling_containers()

    # After cleanup, no dangling containers should remain
    remaining = mock_client.containers.list(all=True, filters={"name": CONTAINER_NAME_PREFIX})
    assert len(remaining) == 0, (
        f"Expected 0 dangling containers after cleanup, found {len(remaining)}"
    )


# ===========================================================================
# Property 114: Docker Daemon Accessibility Check
# ===========================================================================

@given(daemon_accessible=st.booleans())
@settings(max_examples=20, deadline=10000)
def test_property_114_docker_daemon_accessibility_check(daemon_accessible):
    """
    Property 114: For any server startup, verify the Script_Executor checks
    Docker daemon accessibility; if not accessible, the server fails to start.

    **Validates: Requirements 9.11, 9.12**
    """
    if daemon_accessible:
        mock_client = create_mock_docker_client()
    else:
        # Create a client whose ping() raises an exception
        mock_client = MockDockerClient()
        mock_client.ping = lambda: (_ for _ in ()).throw(Exception("Docker daemon not accessible"))

    executor = ScriptExecutor(
        docker_client=mock_client,
        execution_manager=ExecutionManager(output_retention_hours=1),
        output_collector=OutputCollector(),
    )

    result = executor.verify_docker_daemon()

    if daemon_accessible:
        assert result is True, "verify_docker_daemon should return True when daemon is accessible"
    else:
        assert result is False, "verify_docker_daemon should return False when daemon is not accessible"


@settings(max_examples=20, deadline=10000)
@given(data=st.data())
def test_property_114_none_docker_client(data):
    """
    Property 114 (variant): When Docker client is None, verify_docker_daemon
    returns False.

    **Validates: Requirements 9.11, 9.12**
    """
    executor = ScriptExecutor(
        docker_client=None,
        execution_manager=ExecutionManager(output_retention_hours=1),
        output_collector=OutputCollector(),
    )

    assert executor.verify_docker_daemon() is False, (
        "verify_docker_daemon should return False when docker_client is None"
    )


# ===========================================================================
# Property 115: Container Image Configuration
# ===========================================================================

@given(image_name=container_image_name())
@settings(max_examples=20, deadline=10000)
def test_property_115_container_image_configuration(image_name):
    """
    Property 115: For any configured Container_Image name, verify the
    Script_Executor uses that image when creating Execution_Containers.

    **Validates: Requirements 9.7**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=mock_client,
            container_image=image_name,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
        )

        params = {
            'repository_url': 'https://github.com/owner/repo',
            'commit_hash': '0' * 40,
            'script_path': 'test.sh',
            'timeout_seconds': 3,
        }
        record = manager.create_execution(**params)
        script_path = create_test_script(temp_dir, "echo ok\n")
        executor.execute_async(record.execution_id, os.path.dirname(script_path), os.path.basename(script_path))

        _wait_for_terminal(manager, record.execution_id)

        creation_calls = mock_client.containers._creation_calls
        assert len(creation_calls) == 1
        assert creation_calls[0]["image"] == image_name, (
            f"Container should use image '{image_name}', got '{creation_calls[0]['image']}'"
        )


# ===========================================================================
# Property 152: Capability Dropping
# ===========================================================================

@given(params=execution_params())
@settings(max_examples=10, deadline=10000)
def test_property_152_capability_dropping(params):
    """
    Property 152: For any Execution_Container, verify it is created with
    cap_drop=["ALL"] and no capabilities are added back via cap_add.

    **Validates: Requirements 8.17, 8.18**
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        mock_client = create_mock_docker_client()
        manager = ExecutionManager(output_retention_hours=1)
        collector = OutputCollector()
        executor = ScriptExecutor(
            docker_client=mock_client,
            execution_manager=manager,
            output_collector=collector,
            temp_storage_path=temp_dir,
            memory_limit="512m",
            cpu_limit=1.0,
        )

        record = manager.create_execution(**params)
        script_path = create_test_script(temp_dir, "echo ok\n")
        executor.execute_async(
            record.execution_id,
            os.path.dirname(script_path),
            os.path.basename(script_path),
        )

        _wait_for_terminal(manager, record.execution_id)

        creation_calls = mock_client.containers._creation_calls
        assert len(creation_calls) == 1
        call = creation_calls[0]

        # Req 8.17: cap_drop must be ["ALL"]
        assert call.get("cap_drop") == ["ALL"], (
            f"Container must have cap_drop=['ALL'], got {call.get('cap_drop')!r}"
        )

        # Req 8.18: no capabilities added back
        assert "cap_add" not in call or call["cap_add"] is None or call["cap_add"] == [], (
            f"Container must not add back any capabilities, got cap_add={call.get('cap_add')!r}"
        )
