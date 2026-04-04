"""Mock Docker client for testing ScriptExecutor.

Simulates the Docker SDK container lifecycle by running scripts locally
via subprocess, while exposing the same API surface that ScriptExecutor uses.
"""
import io
import os
import subprocess
import tarfile
import threading
import time
from unittest.mock import MagicMock

import docker.errors


class MockContainer:
    """Simulates a Docker container that runs scripts via local subprocess."""

    def __init__(self, name, command, **kwargs):
        self.name = name
        self._command = command
        self._creation_kwargs = kwargs
        self._process = None
        self._stdout = b""
        self._stderr = b""
        self._exit_code = None
        self._script_content = None
        self._removed = False
        self._started = False
        self._lock = threading.Lock()

    def put_archive(self, path, data):
        """Extract the script from the tar archive."""
        tar_stream = io.BytesIO(data.read() if hasattr(data, "read") else data)
        with tarfile.open(fileobj=tar_stream, mode="r") as tar:
            for member in tar.getmembers():
                f = tar.extractfile(member)
                if f:
                    self._script_content = f.read()

    def start(self):
        """Start executing the script via subprocess.

        Supports two modes:
        1. Bind-mount with /workspace: The repo directory is mounted at
           /workspace and the command references the script within it.
           The script host path is resolved from the volume mount and command.
        2. Bind-mount (legacy): A single .sh file is bind-mounted directly.
        3. put_archive: The script content is injected after creation via
           ``put_archive`` (legacy path).
        """
        self._started = True

        def _run():
            # --- Try to resolve the script from bind-mounted volumes first ---
            script_path = None
            volumes = self._creation_kwargs.get("volumes", {})

            # Mode 1: /workspace mount — resolve script from command + volume
            for host_path, mount_spec in volumes.items():
                if isinstance(mount_spec, dict) and mount_spec.get("bind") == "/workspace":
                    # Extract script relative path from command like ["sh", "/workspace/test.sh"]
                    if self._command and len(self._command) >= 2:
                        container_script = self._command[-1]
                        if container_script.startswith("/workspace/"):
                            rel_path = container_script[len("/workspace/"):]
                            candidate = os.path.join(host_path, rel_path)
                            if os.path.exists(candidate):
                                script_path = candidate
                    break

            # Mode 2 (legacy): direct .sh bind-mount
            if script_path is None:
                for host_path, mount_spec in volumes.items():
                    if isinstance(mount_spec, dict) and mount_spec.get("bind", "").endswith(".sh"):
                        script_path = host_path
                        break

            if script_path and os.path.exists(script_path):
                with open(script_path, "rb") as f:
                    self._script_content = f.read()
            else:
                # Fallback: wait for put_archive to supply the script (up to 5 s)
                for _ in range(50):
                    if self._script_content is not None:
                        break
                    time.sleep(0.1)

            if self._script_content is None:
                self._exit_code = -1
                return

            # Write script to a temp file and execute
            import tempfile

            self._tmp_script = tempfile.NamedTemporaryFile(
                mode="wb", suffix=".sh", delete=False
            )
            self._tmp_script.write(self._script_content)
            self._tmp_script.close()
            os.chmod(self._tmp_script.name, 0o755)

            self._process = subprocess.Popen(
                ["bash", self._tmp_script.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self._run_thread = threading.Thread(target=_run, daemon=True)
        self._run_thread.start()

    def wait(self, timeout=None):
        """Wait for the process to complete, with optional timeout.

        The background _run thread (started by ``start()``) may still be
        setting up the subprocess, so we first join that thread before
        inspecting ``_process``.
        """
        # Wait for the background start thread to finish launching the process
        if hasattr(self, "_run_thread"):
            self._run_thread.join(timeout=timeout)

        if self._process is None:
            return {"StatusCode": self._exit_code if self._exit_code is not None else -1}

        try:
            stdout, stderr = self._process.communicate(timeout=timeout)
            with self._lock:
                self._stdout = stdout
                self._stderr = stderr
                self._exit_code = self._process.returncode
            return {"StatusCode": self._exit_code}
        except subprocess.TimeoutExpired:
            self._process.kill()
            stdout, stderr = self._process.communicate()
            with self._lock:
                self._stdout = stdout
                self._stderr = stderr
                self._exit_code = -1
            raise Exception(f"Container wait timed out after {timeout}s")

    def logs(self, stdout=True, stderr=True):
        """Return captured logs."""
        with self._lock:
            if stdout and not stderr:
                return self._stdout
            if stderr and not stdout:
                return self._stderr
            return self._stdout + self._stderr

    def stop(self, timeout=None):
        """Stop the running process."""
        if self._process and self._process.poll() is None:
            self._process.kill()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def remove(self, force=False):
        """Mark container as removed and clean up."""
        self._removed = True
        self.stop()
        # Clean up temp script
        if hasattr(self, "_tmp_script") and os.path.exists(self._tmp_script.name):
            try:
                os.remove(self._tmp_script.name)
            except OSError:
                pass


class MockContainersAPI:
    """Simulates docker.DockerClient.containers."""

    def __init__(self):
        self._containers = {}
        self._lock = threading.Lock()
        self._creation_calls = []  # Track all create() calls with full kwargs

    def create(self, image, name, command, **kwargs):
        """Create a new mock container."""
        container = MockContainer(name=name, command=command, **kwargs)
        call_record = {"image": image, "name": name, "command": command, **kwargs}
        with self._lock:
            self._containers[name] = container
            self._creation_calls.append(call_record)
        return container

    def get(self, name):
        """Get a container by name. Raises NotFound if removed or missing."""
        with self._lock:
            container = self._containers.get(name)
        if container is None or container._removed:
            raise docker.errors.NotFound(f"Container {name} not found")
        return container

    def list(self, all=False, filters=None):
        """List containers, optionally filtering by name prefix."""
        with self._lock:
            containers = list(self._containers.values())
        if filters and "name" in filters:
            prefix = filters["name"]
            containers = [c for c in containers if c.name.startswith(prefix) and not c._removed]
        return containers


class MockDockerClient:
    """Simulates docker.DockerClient with containers API."""

    def __init__(self):
        self.containers = MockContainersAPI()

    def ping(self):
        return True


def create_mock_docker_client():
    """Factory function to create a mock Docker client."""
    return MockDockerClient()
