"""Script execution for GitHub Actions Remote Executor using Docker SDK"""
import io
import os
import shutil
import tarfile
import time
import threading
import logging
from typing import Optional

import docker
import docker.errors

from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus
from src.logging_config import set_log_context


logger = logging.getLogger(__name__)

CONTAINER_NAME_PREFIX = "gare-exec-"


class ScriptExecutor:
    """Executes scripts asynchronously inside ephemeral Docker containers"""

    def __init__(
        self,
        docker_client: "docker.DockerClient | None" = None,
        container_image: str = "",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        timeout_seconds: int = 1800,
        execution_manager: "ExecutionManager | None" = None,
        output_collector: "OutputCollector | None" = None,
        temp_storage_path: str = "/tmp",
    ):
        """
        Initialize script executor with Docker SDK.

        Args:
            docker_client: Docker SDK client instance (None if Docker unavailable)
            container_image: Docker image name for Execution_Containers
            memory_limit: Docker memory constraint (e.g. '512m')
            cpu_limit: Docker CPU constraint (e.g. 1.0 for one CPU)
            timeout_seconds: Maximum execution timeout in seconds
            execution_manager: Manager for execution lifecycle and state
            output_collector: Collector for capturing stdout/stderr
            temp_storage_path: Base path for temporary file storage
        """
        self._docker_client = docker_client
        self._container_image = container_image
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._timeout_seconds = timeout_seconds
        self._execution_manager = execution_manager
        self._output_collector = output_collector
        self._temp_storage_path = temp_storage_path
        self._active_containers = {}
        self._container_lock = threading.Lock()

    def execute_async(self, execution_id: str, repo_path: str, script_path: str) -> None:
        """
        Execute script asynchronously inside an ephemeral Docker container.

        Creates a background thread that:
        1. Creates a new container from the configured Container_Image
        2. Mounts the cloned repository directory read-only at /workspace
        3. Starts the container and waits for completion with timeout
        4. Captures stdout/stderr and exit code
        5. Removes the container and verifies removal
        6. Cleans up the cloned repository directory

        Args:
            execution_id: Unique execution identifier
            repo_path: Path to the cloned repository directory on the host
            script_path: Relative path to the script within the repo
        """
        thread = threading.Thread(
            target=self._execute_in_container,
            args=(execution_id, repo_path, script_path),
            daemon=True,
        )
        thread.start()

    def _execute_in_container(self, execution_id: str, repo_path: str, script_path: str) -> None:
        """
        Internal method to execute script inside a Docker container (runs in background thread).

        Args:
            execution_id: Unique execution identifier
            repo_path: Path to the cloned repository directory on the host
            script_path: Relative path to the script within the repo
        """
        set_log_context(execution_id=execution_id)

        container = None
        container_name = f"{CONTAINER_NAME_PREFIX}{execution_id}"

        try:
            # Get execution record for timeout
            record = self._execution_manager.get_execution(execution_id)
            if not record:
                logger.error(f"Execution record not found: {execution_id}")
                return

            timeout = record.timeout_seconds

            # Create output buffer
            self._output_collector.create_buffer(execution_id)

            # Update status to RUNNING
            self._execution_manager.update_status(execution_id, ExecutionStatus.RUNNING)
            logger.info(f"Starting execution {execution_id}: {script_path}")

            # Create container with security constraints
            # The cloned repo directory is bind-mounted read-only into the
            # container at /workspace so the script can reference sibling files.
            # A tmpfs at /tmp/execution gives the script a writable area.
            nano_cpus = int(self._cpu_limit * 1e9)
            host_repo_path = os.path.abspath(repo_path)
            container = self._docker_client.containers.create(
                image=self._container_image,
                name=container_name,
                command=["sh", f"/workspace/{script_path}"],
                mem_limit=self._memory_limit,
                nano_cpus=nano_cpus,
                read_only=True,
                tmpfs={"/tmp/execution": "size=64m,uid=65534"},
                volumes={
                    host_repo_path: {"bind": "/workspace", "mode": "ro"},
                },
                working_dir="/workspace",
                network_mode="none",
                security_opt=["no-new-privileges"],
                user="nobody",
                detach=True,
            )

            # Track the container
            with self._container_lock:
                self._active_containers[execution_id] = container

            # Start the container
            container.start()
            logger.info(f"Container {container_name} started for execution {execution_id}")

            # Wait for completion with timeout
            try:
                result = container.wait(timeout=timeout)
                exit_code = result.get("StatusCode", -1)
            except Exception as wait_err:
                # Timeout or other error — stop the container
                logger.warning(
                    f"Execution {execution_id} timed out or wait failed after {timeout}s: {wait_err}"
                )
                try:
                    container.stop(timeout=5)
                except docker.errors.APIError:
                    pass

                # Capture any partial output before marking timed out
                self._capture_container_logs(execution_id, container)
                self._output_collector.mark_complete(execution_id, -1)
                self._execution_manager.update_status(
                    execution_id, ExecutionStatus.TIMED_OUT, exit_code=-1
                )
                return

            # Capture stdout/stderr from container logs
            self._capture_container_logs(execution_id, container)

            # Mark output as complete
            self._output_collector.mark_complete(execution_id, exit_code)

            # Update status based on exit code
            if exit_code == 0:
                final_status = ExecutionStatus.COMPLETED
                logger.info(f"Execution {execution_id} completed successfully")
            else:
                final_status = ExecutionStatus.FAILED
                logger.warning(f"Execution {execution_id} failed with exit code {exit_code}")

            self._execution_manager.update_status(
                execution_id, final_status, exit_code=exit_code
            )

        except Exception as e:
            logger.error(
                f"Execution {execution_id} failed with exception: {e}", exc_info=True
            )
            try:
                self._output_collector.mark_complete(execution_id, -1)
            except ValueError:
                pass
            self._execution_manager.update_status(
                execution_id, ExecutionStatus.FAILED, exit_code=-1
            )

        finally:
            # Remove from active containers
            with self._container_lock:
                self._active_containers.pop(execution_id, None)

            # Remove the container and verify
            if container is not None:
                self._remove_container(container, execution_id)

            # Clean up temporary files
            self._cleanup_temp_files(execution_id, repo_path)

    def _copy_script_to_container(self, container, script_path: str) -> None:
        """
        Copy a script file into the container using Docker SDK put_archive.

        Retries briefly to handle the race between container.start() returning
        and the tmpfs mount at /tmp/execution actually being available.

        Args:
            container: Docker container object
            script_path: Local path to the script file
        """
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(script_path, arcname="script.sh")
        tar_stream.seek(0)

        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                container.put_archive("/tmp/execution", tar_stream)
                return
            except (docker.errors.NotFound, docker.errors.APIError) as exc:
                if attempt < max_attempts - 1:
                    logger.debug(
                        f"Container tmpfs not ready yet, retrying ({attempt + 1}/{max_attempts}): {exc}"
                    )
                    time.sleep(0.5 * (attempt + 1))
                    tar_stream.seek(0)
                else:
                    raise

    def _capture_container_logs(self, execution_id: str, container) -> None:
        """
        Capture stdout and stderr from a container's logs.

        Args:
            execution_id: Unique execution identifier
            container: Docker container object
        """
        try:
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            if stdout_bytes:
                self._output_collector.capture_output(execution_id, "stdout", stdout_bytes)
            if stderr_bytes:
                self._output_collector.capture_output(execution_id, "stderr", stderr_bytes)
        except docker.errors.APIError as e:
            logger.warning(f"Failed to capture logs for {execution_id}: {e}")

    def _remove_container(self, container, execution_id: str) -> None:
        """
        Remove a container and verify it no longer exists.

        Args:
            container: Docker container object
            execution_id: Unique execution identifier
        """
        container_name = f"{CONTAINER_NAME_PREFIX}{execution_id}"
        try:
            container.remove(force=True)
            logger.info(f"Removed container {container_name}")
        except docker.errors.APIError as e:
            logger.warning(f"Failed to remove container {container_name}: {e}")

        # Verify removal
        removed = self.verify_container_removed(execution_id)
        if removed:
            logger.info(f"Verified container {container_name} no longer exists")
        else:
            logger.warning(f"Container {container_name} still exists after removal attempt")

    def verify_container_removed(self, execution_id: str) -> bool:
        """
        Verify the container no longer exists on the Docker host.

        Args:
            execution_id: Unique execution identifier

        Returns:
            True if the container does not exist, False if it still exists
        """
        container_name = f"{CONTAINER_NAME_PREFIX}{execution_id}"
        try:
            self._docker_client.containers.get(container_name)
            return False
        except docker.errors.NotFound:
            return True
        except docker.errors.APIError as e:
            logger.warning(f"Error verifying container removal for {container_name}: {e}")
            return False

    def terminate(self, execution_id: str) -> bool:
        """
        Terminate a running execution by stopping and removing its container.

        Args:
            execution_id: Unique execution identifier

        Returns:
            True if container was terminated, False if not found or already completed
        """
        with self._container_lock:
            container = self._active_containers.pop(execution_id, None)

        if container is not None:
            container_name = f"{CONTAINER_NAME_PREFIX}{execution_id}"
            logger.info(f"Terminating execution {execution_id}")
            try:
                container.stop(timeout=5)
                container.remove(force=True)
                return True
            except docker.errors.APIError as e:
                logger.warning(f"Failed to terminate container {container_name}: {e}")
                return False

        return False

    def cleanup_dangling_containers(self) -> None:
        """
        Remove any dangling Execution_Containers on startup that match
        the container naming convention (prefix 'gare-exec-').
        """
        if self._docker_client is None:
            logger.warning("Docker client not available; skipping dangling container cleanup")
            return
        try:
            all_containers = self._docker_client.containers.list(
                all=True,
                filters={"name": CONTAINER_NAME_PREFIX},
            )
            for container in all_containers:
                name = container.name
                logger.info(f"Found dangling container: {name}, stopping and removing")
                try:
                    container.stop(timeout=5)
                except docker.errors.APIError:
                    pass
                try:
                    container.remove(force=True)
                    logger.info(f"Cleaned up dangling container: {name}")
                except docker.errors.APIError as e:
                    logger.warning(f"Failed to remove dangling container {name}: {e}")
        except docker.errors.APIError as e:
            logger.warning(f"Failed to list dangling containers: {e}")

    def verify_docker_daemon(self) -> bool:
        """
        Verify the Docker daemon is accessible.

        Returns:
            True if the Docker daemon responds to ping, False otherwise
        """
        if self._docker_client is None:
            return False
        try:
            self._docker_client.ping()
            return True
        except Exception:
            return False

    def pull_container_image(self) -> None:
        """Pull the configured Container_Image if not already present locally.

        Checks the local Docker image store first. If the image exists, skips
        the pull. Otherwise pulls from the registry and verifies availability.

        Raises:
            RuntimeError: If the Docker client is unavailable, the pull fails,
                or the image cannot be verified after pulling.
        """
        if self._docker_client is None:
            raise RuntimeError(
                f"Cannot pull container image '{self._container_image}': Docker client is not available"
            )

        image_name = self._container_image

        # Check if image already exists locally
        try:
            self._docker_client.images.get(image_name)
            logger.info(f"Container image '{image_name}' already present locally, skipping pull")
            return
        except docker.errors.ImageNotFound:
            logger.info(f"Container image '{image_name}' not found locally, pulling from registry...")
        except docker.errors.APIError as e:
            logger.warning(f"Error checking local image '{image_name}': {e}. Attempting pull...")

        # Pull the image
        start = time.monotonic()
        try:
            image = self._docker_client.images.pull(image_name)
            duration = time.monotonic() - start
            size_bytes = image.attrs.get("Size", 0) if image.attrs else 0
            size_mb = size_bytes / (1024 * 1024)
            logger.info(
                f"Pulled container image '{image_name}' in {duration:.1f}s "
                f"(size: {size_mb:.1f} MB)"
            )
        except docker.errors.ImageNotFound:
            raise RuntimeError(
                f"Container image '{image_name}' not found in registry"
            )
        except docker.errors.APIError as e:
            raise RuntimeError(
                f"Failed to pull container image '{image_name}': {e}"
            )

        # Verify the image is available after pull
        try:
            self._docker_client.images.get(image_name)
        except docker.errors.ImageNotFound:
            raise RuntimeError(
                f"Container image '{image_name}' not available after pull"
            )

    def _cleanup_temp_files(self, execution_id: str, repo_path: str) -> None:
        """
        Clean up the cloned repository directory after execution.

        Args:
            execution_id: Unique execution identifier
            repo_path: Path to the cloned repository directory to clean up
        """
        try:
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
                logger.debug(f"Removed cloned repo directory: {repo_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp files for {execution_id}: {e}")
