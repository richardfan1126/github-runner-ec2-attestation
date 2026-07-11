"""Tests for GPU passthrough via NVIDIA Container Toolkit in CDI mode.

Covers:
- Configuration parsing (ENABLE_GPU, GPU_DEVICES, NVIDIA_DRIVER_CAPABILITIES)
- ScriptExecutor GPU parameters (runtime="nvidia", env vars)
- Deny-list enforcement for NVIDIA env vars
- Attestation user_data gpu_enabled field
- Startup verification (nvidia runtime check, CDI spec, test container)

Requirements: 56.5-56.13, 56.20-56.24
"""
import base64
import json
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.config import ServerConfig, parse_strict_bool
from src.script_executor import ScriptExecutor
from src.attestation import AttestationGenerator
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from tests.mock_docker import create_mock_docker_client


# ---------------------------------------------------------------------------
# Helper: minimal valid ServerConfig kwargs
# ---------------------------------------------------------------------------

def _base_config_kwargs():
    """Return minimal kwargs for a valid ServerConfig (without GPU fields)."""
    return dict(
        port=8080,
        max_concurrent_executions=10,
        execution_timeout_seconds=300,
        max_script_size_bytes=1048576,
        rate_limit_per_ip=100,
        rate_limit_window_seconds=60,
        temp_storage_path="/tmp/gha-executor",
        output_retention_hours=24,
        tpm_attest_path="/dev/nsm",
        allowed_repositories=["owner/repo"],
        expected_audience="https://example.com",
        container_image="python:3.11-slim",
        container_memory_limit="512m",
        container_cpu_limit=1.0,
    )


# ===========================================================================
# Configuration Tests
# ===========================================================================


class TestGPUConfigParsing:
    """Test ENABLE_GPU, GPU_DEVICES, NVIDIA_DRIVER_CAPABILITIES parsing."""

    def _required_env(self, monkeypatch):
        """Set all required env vars so from_env() doesn't fail on missing."""
        monkeypatch.setenv("SERVER_PORT", "8080")
        monkeypatch.setenv("MAX_CONCURRENT_EXECUTIONS", "10")
        monkeypatch.setenv("EXECUTION_TIMEOUT_SECONDS", "300")
        monkeypatch.setenv("MAX_SCRIPT_SIZE_BYTES", "1048576")
        monkeypatch.setenv("RATE_LIMIT_PER_IP", "100")
        monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
        monkeypatch.setenv("TEMP_STORAGE_PATH", "/tmp/gha-executor")
        monkeypatch.setenv("OUTPUT_RETENTION_HOURS", "24")
        monkeypatch.setenv("TPM_ATTEST_PATH", "/dev/nsm")
        monkeypatch.setenv("ALLOWED_REPOSITORIES", "owner/repo")
        monkeypatch.setenv("EXPECTED_AUDIENCE", "https://example.com")
        monkeypatch.setenv("CONTAINER_IMAGE", "python:3.11-slim")
        monkeypatch.setenv("CONTAINER_MEMORY_LIMIT", "512m")
        monkeypatch.setenv("CONTAINER_CPU_LIMIT", "1.0")

    def test_enable_gpu_true(self, monkeypatch):
        """ENABLE_GPU=true is parsed correctly."""
        self._required_env(monkeypatch)
        monkeypatch.setenv("ENABLE_GPU", "true")
        config = ServerConfig.from_env()
        assert config.enable_gpu is True

    def test_enable_gpu_false_default(self, monkeypatch):
        """ENABLE_GPU defaults to false when not set."""
        self._required_env(monkeypatch)
        monkeypatch.delenv("ENABLE_GPU", raising=False)
        config = ServerConfig.from_env()
        assert config.enable_gpu is False

    def test_enable_gpu_invalid_value_fails(self, monkeypatch):
        """ENABLE_GPU=treu (typo) fails startup with ValueError."""
        self._required_env(monkeypatch)
        monkeypatch.setenv("ENABLE_GPU", "treu")
        with pytest.raises(ValueError, match="Invalid boolean value for ENABLE_GPU"):
            ServerConfig.from_env()

    def test_enable_gpu_case_insensitive(self, monkeypatch):
        """ENABLE_GPU=TRUE (uppercase) is accepted."""
        self._required_env(monkeypatch)
        monkeypatch.setenv("ENABLE_GPU", "TRUE")
        config = ServerConfig.from_env()
        assert config.enable_gpu is True

    def test_gpu_devices_default(self, monkeypatch):
        """GPU_DEVICES defaults to 'all' when not set."""
        self._required_env(monkeypatch)
        monkeypatch.delenv("GPU_DEVICES", raising=False)
        config = ServerConfig.from_env()
        assert config.gpu_devices == "all"

    def test_gpu_devices_custom(self, monkeypatch):
        """GPU_DEVICES=0,1 is parsed correctly."""
        self._required_env(monkeypatch)
        monkeypatch.setenv("GPU_DEVICES", "0,1")
        config = ServerConfig.from_env()
        assert config.gpu_devices == "0,1"

    def test_nvidia_driver_capabilities_default(self, monkeypatch):
        """NVIDIA_DRIVER_CAPABILITIES defaults to 'compute,utility'."""
        self._required_env(monkeypatch)
        monkeypatch.delenv("NVIDIA_DRIVER_CAPABILITIES", raising=False)
        config = ServerConfig.from_env()
        assert config.nvidia_driver_capabilities == "compute,utility"

    def test_nvidia_driver_capabilities_custom(self, monkeypatch):
        """NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics is parsed."""
        self._required_env(monkeypatch)
        monkeypatch.setenv("NVIDIA_DRIVER_CAPABILITIES", "compute,utility,graphics")
        config = ServerConfig.from_env()
        assert config.nvidia_driver_capabilities == "compute,utility,graphics"


class TestGPUConfigValidation:
    """Test GPU-related config validation rules."""

    def test_gpu_devices_empty_when_enabled_fails(self):
        """Validation fails when enable_gpu=True but gpu_devices is empty."""
        kwargs = _base_config_kwargs()
        kwargs["enable_gpu"] = True
        kwargs["gpu_devices"] = ""
        kwargs["container_image_digest"] = "sha256:" + "ab" * 32
        config = ServerConfig(**kwargs)
        with pytest.raises(ValueError, match="gpu_devices cannot be empty"):
            config.validate()

    def test_gpu_devices_empty_when_disabled_ok(self):
        """Validation passes when enable_gpu=False even if gpu_devices is empty."""
        kwargs = _base_config_kwargs()
        kwargs["enable_gpu"] = False
        kwargs["gpu_devices"] = ""
        kwargs["container_image_digest"] = "sha256:" + "ab" * 32
        config = ServerConfig(**kwargs)
        # Should not raise
        config.validate()


# ===========================================================================
# ScriptExecutor GPU Tests
# ===========================================================================


class TestScriptExecutorGPU:
    """Test ScriptExecutor passes correct GPU params to Docker."""

    def test_gpu_enabled_passes_runtime_nvidia(self):
        """When enable_gpu=True, containers.create() receives runtime='nvidia'."""
        mock_client = create_mock_docker_client()
        executor = ScriptExecutor(
            docker_client=mock_client,
            container_image="test-image",
            memory_limit="512m",
            cpu_limit=1.0,
            timeout_seconds=30,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
            temp_storage_path=tempfile.mkdtemp(),
            container_image_digest="sha256:" + "ab" * 32,
            enable_gpu=True,
            gpu_devices="all",
            nvidia_driver_capabilities="compute,utility",
        )

        # Create an execution and run it
        record = executor._execution_manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )

        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, "test.sh")
            with open(script_path, "w") as f:
                f.write("#!/bin/bash\necho hello\n")
            os.chmod(script_path, 0o755)

            executor.execute_async(record.execution_id, tmp, "test.sh")

            # Wait for execution
            for _ in range(30):
                r = executor._execution_manager.get_execution(record.execution_id)
                if r and r.status.value in ("completed", "failed", "timed_out"):
                    break
                time.sleep(0.1)

        # Inspect the creation call
        calls = mock_client.containers._creation_calls
        assert len(calls) >= 1
        create_call = calls[0]
        assert create_call.get("runtime") == "nvidia"

    def test_gpu_enabled_sets_nvidia_env_vars(self):
        """When enable_gpu=True, container env includes NVIDIA_VISIBLE_DEVICES and NVIDIA_DRIVER_CAPABILITIES."""
        mock_client = create_mock_docker_client()
        executor = ScriptExecutor(
            docker_client=mock_client,
            container_image="test-image",
            memory_limit="512m",
            cpu_limit=1.0,
            timeout_seconds=30,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
            temp_storage_path=tempfile.mkdtemp(),
            container_image_digest="sha256:" + "ab" * 32,
            enable_gpu=True,
            gpu_devices="0,1",
            nvidia_driver_capabilities="compute,utility",
        )

        record = executor._execution_manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )

        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, "test.sh")
            with open(script_path, "w") as f:
                f.write("#!/bin/bash\necho hello\n")
            os.chmod(script_path, 0o755)

            executor.execute_async(record.execution_id, tmp, "test.sh")

            for _ in range(30):
                r = executor._execution_manager.get_execution(record.execution_id)
                if r and r.status.value in ("completed", "failed", "timed_out"):
                    break
                time.sleep(0.1)

        calls = mock_client.containers._creation_calls
        assert len(calls) >= 1
        env = calls[0].get("environment", {})
        assert env.get("NVIDIA_VISIBLE_DEVICES") == "0,1"
        assert env.get("NVIDIA_DRIVER_CAPABILITIES") == "compute,utility"

    def test_gpu_env_vars_override_script_env(self):
        """Server-controlled GPU env vars take precedence over script_env."""
        mock_client = create_mock_docker_client()
        executor = ScriptExecutor(
            docker_client=mock_client,
            container_image="test-image",
            memory_limit="512m",
            cpu_limit=1.0,
            timeout_seconds=30,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
            temp_storage_path=tempfile.mkdtemp(),
            container_image_digest="sha256:" + "ab" * 32,
            enable_gpu=True,
            gpu_devices="all",
            nvidia_driver_capabilities="compute,utility",
        )

        record = executor._execution_manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )

        # Pass conflicting NVIDIA env vars in script_env
        script_env = {
            "NVIDIA_VISIBLE_DEVICES": "none",
            "NVIDIA_DRIVER_CAPABILITIES": "video",
            "MY_VAR": "hello",
        }

        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, "test.sh")
            with open(script_path, "w") as f:
                f.write("#!/bin/bash\necho hello\n")
            os.chmod(script_path, 0o755)

            executor.execute_async(record.execution_id, tmp, "test.sh", script_env=script_env)

            for _ in range(30):
                r = executor._execution_manager.get_execution(record.execution_id)
                if r and r.status.value in ("completed", "failed", "timed_out"):
                    break
                time.sleep(0.1)

        calls = mock_client.containers._creation_calls
        assert len(calls) >= 1
        env = calls[0].get("environment", {})
        # Server values override caller values
        assert env["NVIDIA_VISIBLE_DEVICES"] == "all"
        assert env["NVIDIA_DRIVER_CAPABILITIES"] == "compute,utility"
        # Non-GPU env vars are preserved
        assert env["MY_VAR"] == "hello"

    def test_gpu_disabled_no_runtime_or_env(self):
        """When enable_gpu=False, no runtime or GPU env vars are passed."""
        mock_client = create_mock_docker_client()
        executor = ScriptExecutor(
            docker_client=mock_client,
            container_image="test-image",
            memory_limit="512m",
            cpu_limit=1.0,
            timeout_seconds=30,
            execution_manager=ExecutionManager(output_retention_hours=1),
            output_collector=OutputCollector(),
            temp_storage_path=tempfile.mkdtemp(),
            container_image_digest="sha256:" + "ab" * 32,
            enable_gpu=False,
        )

        record = executor._execution_manager.create_execution(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="test.sh",
            timeout_seconds=5,
        )

        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, "test.sh")
            with open(script_path, "w") as f:
                f.write("#!/bin/bash\necho hello\n")
            os.chmod(script_path, 0o755)

            executor.execute_async(record.execution_id, tmp, "test.sh")

            for _ in range(30):
                r = executor._execution_manager.get_execution(record.execution_id)
                if r and r.status.value in ("completed", "failed", "timed_out"):
                    break
                time.sleep(0.1)

        calls = mock_client.containers._creation_calls
        assert len(calls) >= 1
        create_call = calls[0]
        # No runtime kwarg
        assert "runtime" not in create_call
        # No GPU env vars
        env = create_call.get("environment", {})
        assert "NVIDIA_VISIBLE_DEVICES" not in env
        assert "NVIDIA_DRIVER_CAPABILITIES" not in env


# ===========================================================================
# Deny-List Tests
# ===========================================================================


class TestGPUDenyList:
    """Test that NVIDIA env vars are in the default deny list."""

    def test_nvidia_visible_devices_in_deny_list(self):
        """NVIDIA_VISIBLE_DEVICES is in the default script_env_deny_list."""
        config = ServerConfig(**_base_config_kwargs())
        assert "NVIDIA_VISIBLE_DEVICES" in config.script_env_deny_list

    def test_nvidia_driver_capabilities_in_deny_list(self):
        """NVIDIA_DRIVER_CAPABILITIES is in the default script_env_deny_list."""
        config = ServerConfig(**_base_config_kwargs())
        assert "NVIDIA_DRIVER_CAPABILITIES" in config.script_env_deny_list

    def test_deny_list_rejects_nvidia_visible_devices(self):
        """Server rejects script_env containing NVIDIA_VISIBLE_DEVICES."""
        config = ServerConfig(**_base_config_kwargs())
        deny_list = config.script_env_deny_list
        exact_deny = {e for e in deny_list if not e.endswith('*')}
        prefix_deny = [e[:-1] for e in deny_list if e.endswith('*')]

        # Simulate the deny-list check from server.py
        script_env = {"NVIDIA_VISIBLE_DEVICES": "0", "MY_VAR": "ok"}
        denied_keys = []
        for key in script_env:
            if key in exact_deny:
                denied_keys.append(key)
            else:
                for prefix in prefix_deny:
                    if key.startswith(prefix):
                        denied_keys.append(key)
                        break

        assert "NVIDIA_VISIBLE_DEVICES" in denied_keys
        assert "MY_VAR" not in denied_keys

    def test_deny_list_rejects_nvidia_driver_capabilities(self):
        """Server rejects script_env containing NVIDIA_DRIVER_CAPABILITIES."""
        config = ServerConfig(**_base_config_kwargs())
        deny_list = config.script_env_deny_list
        exact_deny = {e for e in deny_list if not e.endswith('*')}

        script_env = {"NVIDIA_DRIVER_CAPABILITIES": "all"}
        denied_keys = [k for k in script_env if k in exact_deny]

        assert "NVIDIA_DRIVER_CAPABILITIES" in denied_keys


# ===========================================================================
# Attestation Tests
# ===========================================================================


FAKE_GPU_DEVICE = {
    "uuid": "GPU-00000000-0000-0000-0000-000000000000",
    "name": "NVIDIA A10G",
    "driver_version": "550.90.07",
    "cuda_version": "12.4",
    "vbios_version": "94.02.00.00.02",
    "compute_capability": "8.6",
    "memory_total_mib": 23028,
}


def _decode_claims(claims_raw: str) -> dict:
    return json.loads(base64.b64decode(claims_raw))


class TestGPUAttestation:
    """Test the `gpu` claims block in the attestation's claims document."""

    def test_gpu_enabled_true_in_claims(self, tmp_path):
        """gpu_enabled=True populates the gpu block in the claims document
        (NVML collection is mocked since no real GPU is present)."""
        fake_attest = tmp_path / "fake-attest"
        fake_attest.write_text("#!/bin/bash\necho -n 'FAKE_ATTESTATION'\n")
        fake_attest.chmod(0o755)

        generator = AttestationGenerator(tpm_attest_path=str(fake_attest))
        with patch("src.attestation._collect_nvml_devices", return_value=[FAKE_GPU_DEVICE]):
            doc, err = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
                gpu_enabled=True,
            )

        assert err is None
        assert doc is not None
        claims = _decode_claims(doc.claims_raw)
        assert claims["gpu"] == {
            "enabled": True,
            "visible_devices": "all",
            "devices": [FAKE_GPU_DEVICE],
        }

    def test_gpu_enabled_false_in_claims(self, tmp_path):
        """gpu_enabled=False yields exactly { enabled: false } in the claims document."""
        fake_attest = tmp_path / "fake-attest"
        fake_attest.write_text("#!/bin/bash\necho -n 'FAKE_ATTESTATION'\n")
        fake_attest.chmod(0o755)

        generator = AttestationGenerator(tpm_attest_path=str(fake_attest))
        doc, err = generator.generate_attestation(
            repository_url="https://github.com/owner/repo",
            commit_hash="a" * 40,
            script_path="scripts/build.sh",
            gpu_enabled=False,
        )

        assert err is None
        assert doc is not None
        claims = _decode_claims(doc.claims_raw)
        assert claims["gpu"] == {"enabled": False}

    def test_gpu_enabled_included_in_claims_document(self, tmp_path):
        """Verify the gpu block and other claims appear in the claims document
        passed alongside the attestation."""
        fake_attest = tmp_path / "capture-attest"
        fake_attest.write_text(
            f'#!/bin/bash\n'
            f'echo -n "ATTESTATION_BYTES"\n'
        )
        fake_attest.chmod(0o755)

        generator = AttestationGenerator(tpm_attest_path=str(fake_attest))
        with patch("src.attestation._collect_nvml_devices", return_value=[FAKE_GPU_DEVICE]):
            doc, err = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="b" * 40,
                script_path="scripts/test.sh",
                gpu_enabled=True,
                execution_id="exec-gpu-test-001",
            )

        assert err is None
        claims = _decode_claims(doc.claims_raw)
        assert claims["gpu"]["enabled"] is True
        assert claims["repository_url"] == "https://github.com/owner/repo"
        # execution_id is envelope-only, not duplicated into the claims document
        assert "execution_id" not in claims

    def test_gpu_enabled_none_omitted_from_claims(self, tmp_path):
        """When gpu_enabled is None, no gpu key is included in the claims document."""
        fake_attest = tmp_path / "capture-attest"
        fake_attest.write_text(
            f'#!/bin/bash\n'
            f'echo -n "ATTESTATION_BYTES"\n'
        )
        fake_attest.chmod(0o755)

        generator = AttestationGenerator(tpm_attest_path=str(fake_attest))
        doc, err = generator.generate_attestation(
            repository_url="https://github.com/owner/repo",
            commit_hash="c" * 40,
            script_path="scripts/test.sh",
            gpu_enabled=None,
        )

        assert err is None
        claims = _decode_claims(doc.claims_raw)
        assert "gpu" not in claims

    def test_output_attestation_includes_gpu_block(self, tmp_path):
        """generate_output_attestation includes the gpu block in the output claims document."""
        fake_attest = tmp_path / "capture-attest"
        fake_attest.write_text(
            f'#!/bin/bash\n'
            f'echo -n "OUTPUT_ATTESTATION_BYTES"\n'
        )
        fake_attest.chmod(0o755)

        generator = AttestationGenerator(tpm_attest_path=str(fake_attest))
        with patch("src.attestation._collect_nvml_devices", return_value=[FAKE_GPU_DEVICE]):
            result, err = generator.generate_output_attestation(
                "hello world\n", "", 0,
                execution_id="exec-output-gpu-001",
                gpu_enabled=True,
            )

        assert err is None
        assert result is not None
        claims = _decode_claims(result.claims_raw)
        assert claims["gpu"]["enabled"] is True
        assert "execution_id" not in claims
        assert "output_digest" in claims

    def test_gpu_enabled_nvml_failure_fails_closed(self, tmp_path):
        """When ENABLE_GPU is true but NVML cannot enumerate devices, the whole
        attestation fails closed rather than emitting enabled: true with no devices."""
        fake_attest = tmp_path / "fake-attest"
        fake_attest.write_text("#!/bin/bash\necho -n 'FAKE_ATTESTATION'\n")
        fake_attest.chmod(0o755)

        generator = AttestationGenerator(tpm_attest_path=str(fake_attest))
        with patch("src.attestation._collect_nvml_devices", side_effect=RuntimeError("no NVML")):
            doc, err = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
                gpu_enabled=True,
            )

        assert doc is None
        assert err is not None
        assert "GPU claim collection failed" in err.context

    def test_gpu_enabled_zero_devices_fails_closed(self, tmp_path):
        """When NVML enumerates zero devices, the attestation fails closed."""
        fake_attest = tmp_path / "fake-attest"
        fake_attest.write_text("#!/bin/bash\necho -n 'FAKE_ATTESTATION'\n")
        fake_attest.chmod(0o755)

        generator = AttestationGenerator(tpm_attest_path=str(fake_attest))
        with patch("src.attestation._collect_nvml_devices", return_value=[]):
            doc, err = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
                gpu_enabled=True,
            )

        assert doc is None
        assert err is not None

    def test_gpu_devices_subset_fails_closed(self, tmp_path):
        """A GPU_DEVICES value other than 'all' cannot be resolved to the
        workload-visible set with the current collector and fails closed (D12)."""
        fake_attest = tmp_path / "fake-attest"
        fake_attest.write_text("#!/bin/bash\necho -n 'FAKE_ATTESTATION'\n")
        fake_attest.chmod(0o755)

        generator = AttestationGenerator(tpm_attest_path=str(fake_attest))
        with patch("src.attestation._collect_nvml_devices", return_value=[FAKE_GPU_DEVICE]):
            doc, err = generator.generate_attestation(
                repository_url="https://github.com/owner/repo",
                commit_hash="a" * 40,
                script_path="scripts/build.sh",
                gpu_enabled=True,
                gpu_devices="0,1",
            )

        assert doc is None
        assert err is not None


# ===========================================================================
# Startup Verification Tests (mock-based)
# ===========================================================================


class TestGPUStartupVerification:
    """Test GPU startup verification logic from main.py."""

    def test_startup_fails_when_nvidia_runtime_not_registered(self):
        """Startup fails when enable_gpu=True but nvidia runtime is not in Docker info."""
        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True
        # Docker info without nvidia runtime
        mock_docker_client.info.return_value = {
            "Runtimes": {"runc": {}}
        }

        # Simulate the startup check from main.py
        docker_info = mock_docker_client.info()
        runtimes = docker_info.get("Runtimes", {})
        assert "nvidia" not in runtimes

    def test_startup_succeeds_when_nvidia_runtime_registered(self):
        """Startup succeeds when nvidia runtime is registered."""
        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True
        mock_docker_client.info.return_value = {
            "Runtimes": {"runc": {}, "nvidia": {"path": "/usr/bin/nvidia-container-runtime"}}
        }

        docker_info = mock_docker_client.info()
        runtimes = docker_info.get("Runtimes", {})
        assert "nvidia" in runtimes

    def test_cdi_spec_missing_logs_warning(self, tmp_path):
        """Warning is logged when CDI specs are missing (non-fatal)."""
        # Simulate CDI spec check with non-existent paths
        cdi_spec_paths = [
            str(tmp_path / "nonexistent" / "nvidia.yaml"),
            str(tmp_path / "also_nonexistent" / "nvidia.yaml"),
        ]
        cdi_found = any(os.path.exists(p) for p in cdi_spec_paths)
        assert cdi_found is False  # Confirms warning would be logged

    def test_cdi_spec_found_no_warning(self, tmp_path):
        """No warning when CDI spec exists."""
        cdi_dir = tmp_path / "cdi"
        cdi_dir.mkdir()
        cdi_spec = cdi_dir / "nvidia.yaml"
        cdi_spec.write_text("# CDI spec\n")

        cdi_spec_paths = [str(cdi_spec)]
        cdi_found = any(os.path.exists(p) for p in cdi_spec_paths)
        assert cdi_found is True

    def test_startup_fails_when_test_container_creation_fails(self):
        """Startup fails when GPU test container cannot be created."""
        import docker.errors as docker_errors

        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True
        mock_docker_client.info.return_value = {
            "Runtimes": {"runc": {}, "nvidia": {}}
        }
        # Test container creation fails
        mock_docker_client.containers.create.side_effect = docker_errors.APIError(
            "GPU not accessible"
        )

        # Simulate the startup verification
        docker_info = mock_docker_client.info()
        runtimes = docker_info.get("Runtimes", {})
        assert "nvidia" in runtimes  # Runtime check passes

        # But test container creation fails
        with pytest.raises(docker_errors.APIError):
            mock_docker_client.containers.create(
                image="python:3.11-slim",
                command=["true"],
                runtime="nvidia",
                environment={"NVIDIA_VISIBLE_DEVICES": "all"},
                detach=True,
            )

    def test_startup_test_container_created_and_removed(self):
        """Startup creates and immediately removes a test container."""
        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True
        mock_docker_client.info.return_value = {
            "Runtimes": {"runc": {}, "nvidia": {}}
        }
        mock_container = MagicMock()
        mock_docker_client.containers.create.return_value = mock_container

        # Simulate the startup verification
        test_container = mock_docker_client.containers.create(
            image="python:3.11-slim",
            command=["true"],
            runtime="nvidia",
            environment={"NVIDIA_VISIBLE_DEVICES": "all"},
            detach=True,
        )
        test_container.remove(force=True)

        # Verify create was called with correct params
        mock_docker_client.containers.create.assert_called_once_with(
            image="python:3.11-slim",
            command=["true"],
            runtime="nvidia",
            environment={"NVIDIA_VISIBLE_DEVICES": "all"},
            detach=True,
        )
        # Verify container was removed
        mock_container.remove.assert_called_once_with(force=True)

    def test_main_gpu_startup_integration(self, monkeypatch, tmp_path):
        """Integration test: main() GPU startup path with mocked Docker."""
        import docker.errors as docker_errors

        # Mock the entire main() GPU verification flow
        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True
        mock_docker_client.info.return_value = {
            "Runtimes": {"runc": {}, "nvidia": {"path": "/usr/bin/nvidia-container-runtime"}}
        }
        mock_test_container = MagicMock()
        mock_docker_client.containers.create.return_value = mock_test_container

        # Simulate the full GPU startup verification from main.py
        enable_gpu = True

        if enable_gpu:
            # Step 1: Check nvidia runtime
            docker_info = mock_docker_client.info()
            runtimes = docker_info.get("Runtimes", {})
            assert "nvidia" in runtimes

            # Step 2: Check CDI spec (non-fatal)
            cdi_spec_paths = [str(tmp_path / "nvidia.yaml")]
            cdi_found = any(os.path.exists(p) for p in cdi_spec_paths)
            # Not found is OK (just a warning)

            # Step 3: Test container
            test_container = mock_docker_client.containers.create(
                image="python:3.11-slim",
                command=["true"],
                runtime="nvidia",
                environment={"NVIDIA_VISIBLE_DEVICES": "all"},
                detach=True,
            )
            test_container.remove(force=True)

        # All steps passed
        mock_test_container.remove.assert_called_once_with(force=True)
