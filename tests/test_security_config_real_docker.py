"""Real-Docker end-to-end tests for relaxed container security settings.

Unlike the rest of the suite (which drives the in-repo Docker fakes), these
tests run an actual job against a live Docker daemon to prove that a relaxed
security setting has its real-world effect:

* ``WORKSPACE_MOUNT_MODE=rw`` → the container can write an artifact that
  persists on the host workspace.
* ``CONTAINER_NETWORK_MODE=bridge`` → the container gets a real network
  interface (``eth0``) beyond loopback, which the default ``none`` would not.

They are gated to skip unless a Docker daemon is reachable, so they do NOT run
in standard CI. To run them explicitly:

    RUN_REAL_DOCKER_TESTS=1 .venv/bin/pytest -q tests/test_security_config_real_docker.py

See specs/001-container-security-config/spec.md (Clarifications 2026-06-13) and
SC-006.
"""
import os
import tempfile
import time

import pytest

from src.execution_manager import ExecutionManager
from src.models import ExecutionStatus
from src.output_collector import OutputCollector
from src.script_executor import ScriptExecutor

# Image used for the live job. A common, small base image; pulled on demand.
REAL_DOCKER_IMAGE = os.getenv("REAL_DOCKER_TEST_IMAGE", "ubuntu:24.04")


def _docker_daemon_available() -> bool:
    """Return True if a real Docker daemon is reachable from this process."""
    if os.getenv("RUN_REAL_DOCKER_TESTS") not in ("1", "true", "yes"):
        return False
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_daemon_available(),
    reason="real Docker daemon not available (set RUN_REAL_DOCKER_TESTS=1 to opt in)",
)


@pytest.fixture(scope="module")
def real_docker_client():
    """A real Docker SDK client with the test image pulled and available."""
    import docker

    client = docker.from_env()
    client.images.pull(REAL_DOCKER_IMAGE)
    return client


def _wait_for_completion(manager, execution_id, max_wait=60.0):
    start = time.time()
    while time.time() - start < max_wait:
        record = manager.get_execution(execution_id)
        if record and record.status in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
        ):
            return record
        time.sleep(0.2)
    raise AssertionError(f"execution {execution_id} did not finish within {max_wait}s")


def _run_job(real_docker_client, repo_dir, script_body, **executor_overrides):
    """Run a single script job under a real container and return (record, output)."""
    manager = ExecutionManager(output_retention_hours=1)
    collector = OutputCollector()
    executor = ScriptExecutor(
        docker_client=real_docker_client,
        container_image=REAL_DOCKER_IMAGE,
        memory_limit="512m",
        cpu_limit=1.0,
        execution_manager=manager,
        output_collector=collector,
        temp_storage_path=repo_dir,
        **executor_overrides,
    )

    script_path = os.path.join(repo_dir, "job.sh")
    with open(script_path, "w") as f:
        f.write("#!/bin/bash\n")
        f.write(script_body)
    os.chmod(script_path, 0o755)

    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="job.sh",
        timeout_seconds=60,
    )
    executor.execute_async(record.execution_id, repo_dir, "job.sh")
    record = _wait_for_completion(manager, record.execution_id)
    output = collector.get_output(record.execution_id)
    return record, output


def test_workspace_rw_persists_artifact(real_docker_client):
    """With WORKSPACE_MOUNT_MODE=rw the container can persist a file to the host."""
    with tempfile.TemporaryDirectory() as repo_dir:
        record, _ = _run_job(
            real_docker_client,
            repo_dir,
            "echo persisted-by-container > /workspace/artifact.txt\n",
            workspace_mount_mode="rw",
        )
        assert record.status == ExecutionStatus.COMPLETED

        artifact = os.path.join(repo_dir, "artifact.txt")
        assert os.path.exists(artifact), "rw workspace should persist the artifact to the host"
        with open(artifact) as f:
            assert f.read().strip() == "persisted-by-container"


def test_bridge_network_provides_real_interface(real_docker_client):
    """With CONTAINER_NETWORK_MODE=bridge the container gets an eth0 interface."""
    with tempfile.TemporaryDirectory() as repo_dir:
        # /sys/class/net lists interfaces without needing extra tooling.
        record, output = _run_job(
            real_docker_client,
            repo_dir,
            "ls /sys/class/net/\n",
            network_mode="bridge",
        )
        assert record.status == ExecutionStatus.COMPLETED
        interfaces = output.stdout.split()
        assert "eth0" in interfaces, (
            f"bridge mode should expose a non-loopback interface; got {interfaces}"
        )


def test_default_none_network_has_no_external_interface(real_docker_client):
    """The hardened default (network=none) exposes only loopback — the contrast case."""
    with tempfile.TemporaryDirectory() as repo_dir:
        record, output = _run_job(
            real_docker_client,
            repo_dir,
            "ls /sys/class/net/\n",
            # network_mode defaults to "none"
        )
        assert record.status == ExecutionStatus.COMPLETED
        interfaces = output.stdout.split()
        assert "eth0" not in interfaces, (
            f"default 'none' network should not expose eth0; got {interfaces}"
        )
