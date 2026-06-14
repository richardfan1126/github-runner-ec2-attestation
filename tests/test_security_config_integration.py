"""End-to-end integration tests for container security configuration.

Exercises the full config → executor → attestation flow together (not the
individual seams in isolation, which the per-seam suites already cover):

* US1 — a no-config (default) deployment produces a fully hardened container.
* US2 + US3 — a valid relaxation flows through ``ScriptExecutor`` into the
  ``docker.containers.create()`` kwargs AND is surfaced in attestation user_data.
* US2 — an invalid configuration fails fast in ``load_config()`` before the
  server could bind its port.

These mirror the real wiring in ``src/server.py`` (executor construction ~L312
and the ``generate_attestation`` / ``generate_output_attestation`` call sites).

See specs/001-container-security-config/quickstart.md ("Done When").
"""
import json
import os
import tempfile
import time
from unittest.mock import Mock, patch

import pytest

from src.attestation import AttestationGenerator
from src.config import ConfigurationError, load_config
from src.execution_manager import ExecutionManager
from src.models import ExecutionStatus
from src.output_collector import OutputCollector
from src.script_executor import ScriptExecutor
from tests.mock_docker import create_mock_docker_client


# Required environment variables (everything load_config() demands) with the
# eight security variables deliberately left to their hardened defaults.
BASE_ENV = {
    "SERVER_PORT": "8080",
    "MAX_CONCURRENT_EXECUTIONS": "1",
    "EXECUTION_TIMEOUT_SECONDS": "1800",
    "MAX_SCRIPT_SIZE_BYTES": "1048576",
    "RATE_LIMIT_PER_IP": "100",
    "RATE_LIMIT_WINDOW_SECONDS": "60",
    "TEMP_STORAGE_PATH": "/tmp",
    "OUTPUT_RETENTION_HOURS": "24",
    "TPM_ATTEST_PATH": "/usr/bin/nitro-tpm-attest",
    "ALLOWED_REPOSITORIES": "owner/repo",
    "EXPECTED_AUDIENCE": "https://executor.example.com",
    "CONTAINER_IMAGE": "ubuntu:24.04@sha256:" + "a" * 64,
    "CONTAINER_MEMORY_LIMIT": "512m",
    "CONTAINER_CPU_LIMIT": "1.0",
}

# All eight container-security env vars, so we can clear any leakage from the
# host environment before applying a scenario's overrides.
SECURITY_ENV_VARS = [
    "CONTAINER_USER",
    "CONTAINER_ALLOW_ROOT",
    "CONTAINER_CAP_ADD",
    "NO_NEW_PRIVILEGES",
    "CONTAINER_READ_ONLY_ROOTFS",
    "CONTAINER_TMPFS_SIZE",
    "WORKSPACE_MOUNT_MODE",
    "CONTAINER_NETWORK_MODE",
]

DEFAULT_CAP_ADD = [
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID",
    "NET_BIND_SERVICE", "KILL",
]


@pytest.fixture
def clean_security_env(monkeypatch):
    """Apply BASE_ENV and clear any inherited security vars to a known baseline."""
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    for key in SECURITY_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _executor_from_config(config, docker_client, manager, collector, temp_dir):
    """Wire a ScriptExecutor from config exactly as src/server.py does (~L312)."""
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
        workspace_mount_mode=config.workspace_mount_mode,
        network_mode=config.container_network_mode,
    )


def _run_one_script(executor, manager, temp_dir):
    """Drive one execution through the mock Docker client and return create kwargs."""
    record = manager.create_execution(
        repository_url="https://github.com/owner/repo",
        commit_hash="a" * 40,
        script_path="test.sh",
        timeout_seconds=5,
    )
    script_path = os.path.join(temp_dir, "test.sh")
    with open(script_path, "w") as f:
        f.write("#!/bin/bash\necho hello\n")
    os.chmod(script_path, 0o755)

    executor.execute_async(record.execution_id, temp_dir, "test.sh")

    deadline = time.time() + 5.0
    while time.time() < deadline:
        rec = manager.get_execution(record.execution_id)
        if rec and rec.status in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
        ):
            break
        time.sleep(0.05)

    calls = executor._docker_client.containers._creation_calls
    assert calls, "expected at least one containers.create() call"
    return calls[-1]


def _capture_attestation_user_data(config, executor):
    """Call generate_attestation mirroring server.py and capture the user_data."""
    captured = {}

    def capture_and_run(cmd, **kwargs):
        if "--user-data" in cmd:
            idx = cmd.index("--user-data")
            with open(cmd[idx + 1], "r") as f:
                captured["user_data"] = json.load(f)
        result = Mock()
        result.returncode = 0
        result.stdout = b"mock_cbor_attestation"
        result.stderr = b""
        return result

    generator = AttestationGenerator(tpm_attest_path=config.tpm_attest_path)
    with patch("subprocess.run", side_effect=capture_and_run):
        doc, error = generator.generate_attestation(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            # Mirror server.py: allow_root comes from config; cap_add is the
            # executor's resolved list.
            container_user=config.container_user,
            container_allow_root=config.container_allow_root,
            container_cap_add=executor.cap_add,
            no_new_privileges=config.no_new_privileges,
            container_read_only_rootfs=config.container_read_only_rootfs,
            container_tmpfs_size=config.container_tmpfs_size,
            workspace_mount_mode=config.workspace_mount_mode,
            container_network_mode=config.container_network_mode,
        )
    assert error is None
    assert doc is not None
    return captured["user_data"]


class TestDefaultsAreHardenedEndToEnd:
    """US1 — no security vars set yields a fully hardened container create-spec."""

    def test_default_config_produces_hardened_container(self, clean_security_env):
        config = load_config()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _executor_from_config(
                config, create_mock_docker_client(), manager, collector, temp_dir
            )
            kwargs = _run_one_script(executor, manager, temp_dir)

        assert kwargs["user"] == "65534:65534"
        assert kwargs["read_only"] is True
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["cap_add"] == DEFAULT_CAP_ADD
        assert kwargs["network_mode"] == "none"
        assert kwargs["security_opt"] == ["no-new-privileges"]
        assert kwargs["tmpfs"] == {"/tmp": "size=256m,mode=1777"}
        # Workspace bind is read-only.
        bind_modes = [spec["mode"] for spec in kwargs["volumes"].values()]
        assert bind_modes == ["ro"]

    def test_default_posture_is_attested(self, clean_security_env):
        config = load_config()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _executor_from_config(
                config, create_mock_docker_client(), manager, collector, temp_dir
            )
            user_data = _capture_attestation_user_data(config, executor)

        assert user_data["container_user"] == "65534:65534"
        assert user_data["container_allow_root"] is False
        assert user_data["container_cap_add"] == DEFAULT_CAP_ADD
        assert user_data["no_new_privileges"] is True
        assert user_data["container_read_only_rootfs"] is True
        assert user_data["container_tmpfs_size"] == "256m"
        assert user_data["workspace_mount_mode"] == "ro"
        assert user_data["container_network_mode"] == "none"


class TestValidRelaxationFlowsThroughAndIsAttested:
    """US2 + US3 — a relaxed value takes effect in the container and is attested."""

    def test_network_and_workspace_relaxation_end_to_end(self, clean_security_env):
        clean_security_env.setenv("CONTAINER_NETWORK_MODE", "bridge")
        clean_security_env.setenv("WORKSPACE_MOUNT_MODE", "rw")
        config = load_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _executor_from_config(
                config, create_mock_docker_client(), manager, collector, temp_dir
            )
            kwargs = _run_one_script(executor, manager, temp_dir)
            user_data = _capture_attestation_user_data(config, executor)

        # Relaxation took effect in the container create-spec.
        assert kwargs["network_mode"] == "bridge"
        assert [s["mode"] for s in kwargs["volumes"].values()] == ["rw"]

        # ...and is visible in attestation user_data (distinguishable from default).
        assert user_data["container_network_mode"] == "bridge"
        assert user_data["workspace_mount_mode"] == "rw"
        # Unrelaxed settings remain at their hardened defaults.
        assert user_data["container_read_only_rootfs"] is True
        assert user_data["container_user"] == "65534:65534"

    def test_narrowed_cap_add_flows_through_and_is_attested(self, clean_security_env):
        clean_security_env.setenv("CONTAINER_CAP_ADD", "CHOWN,NET_RAW")
        config = load_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _executor_from_config(
                config, create_mock_docker_client(), manager, collector, temp_dir
            )
            kwargs = _run_one_script(executor, manager, temp_dir)
            user_data = _capture_attestation_user_data(config, executor)

        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["cap_add"] == ["CHOWN", "NET_RAW"]
        assert user_data["container_cap_add"] == ["CHOWN", "NET_RAW"]

    def test_root_user_opt_in_flows_through_and_is_attested(self, clean_security_env):
        clean_security_env.setenv("CONTAINER_USER", "0:0")
        clean_security_env.setenv("CONTAINER_ALLOW_ROOT", "true")
        config = load_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ExecutionManager(output_retention_hours=1)
            collector = OutputCollector()
            executor = _executor_from_config(
                config, create_mock_docker_client(), manager, collector, temp_dir
            )
            kwargs = _run_one_script(executor, manager, temp_dir)
            user_data = _capture_attestation_user_data(config, executor)

        assert kwargs["user"] == "0:0"
        assert user_data["container_user"] == "0:0"
        assert user_data["container_allow_root"] is True


class TestInvalidConfigFailsFastBeforeServing:
    """US2 — invalid configs raise ConfigurationError in load_config() (pre-bind)."""

    @pytest.mark.parametrize(
        "var,value,expected_token",
        [
            ("CONTAINER_NETWORK_MODE", "nat", "CONTAINER_NETWORK_MODE"),
            ("WORKSPACE_MOUNT_MODE", "readwrite", "WORKSPACE_MOUNT_MODE"),
            ("CONTAINER_ALLOW_ROOT", "maybe", "CONTAINER_ALLOW_ROOT"),
            ("CONTAINER_USER", "1000", "CONTAINER_USER"),
            ("CONTAINER_CAP_ADD", "SYS_ADMIN", "CONTAINER_CAP_ADD"),
            ("CONTAINER_TMPFS_SIZE", "256", "CONTAINER_TMPFS_SIZE"),
        ],
    )
    def test_invalid_value_fails_fast(self, clean_security_env, var, value, expected_token):
        clean_security_env.setenv(var, value)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config()
        assert expected_token in str(exc_info.value)

    def test_root_without_optin_names_both_variables(self, clean_security_env):
        clean_security_env.setenv("CONTAINER_USER", "0:0")
        clean_security_env.setenv("CONTAINER_ALLOW_ROOT", "false")
        with pytest.raises(ConfigurationError) as exc_info:
            load_config()
        message = str(exc_info.value)
        assert "CONTAINER_USER" in message
        assert "CONTAINER_ALLOW_ROOT" in message
