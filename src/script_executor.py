"""Script execution for GitHub Actions Remote Executor using Docker SDK"""
import contextvars
import hashlib
import json
import os
import shutil
import subprocess
import threading
import logging
from typing import Optional

import docker
import docker.errors

from src.config import CONTAINER_DEFAULT_CAP_ADD
from src.execution_manager import ExecutionManager
from src.output_collector import OutputCollector
from src.models import ExecutionStatus
from src.logging_config import set_log_context


logger = logging.getLogger(__name__)

CONTAINER_NAME_PREFIX = "gare-exec-"


_UNSET = object()  # Sentinel to distinguish "not provided" from explicit None


class ScriptExecutor:
    """Executes scripts asynchronously inside ephemeral Docker containers"""

    def __init__(
        self,
        docker_client: "docker.DockerClient | None" = _UNSET,
        container_image: str = "",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        timeout_seconds: int = 1800,
        execution_manager: "ExecutionManager | None" = None,
        output_collector: "OutputCollector | None" = None,
        temp_storage_path: str = "/tmp",
        container_image_digest: "str | None" = None,
        baked_image_archive_path: str = "/opt/github-actions-remote-executor/baked-image/image.tar",
        baked_image_manifest_path: str = "/opt/github-actions-remote-executor/baked-image/manifest.json",
        bound_image_id: "str | None" = None,
        container_pids_limit: int = 256,
        enable_gpu: bool = False,
        gpu_devices: str = "all",
        nvidia_driver_capabilities: str = "compute,utility",
        user: str = "65534:65534",
        cap_add: "list[str] | None" = None,
        no_new_privileges: bool = True,
        read_only_rootfs: bool = True,
        tmpfs_size: str = "256m",
        tmpfs_exec: bool = False,
        workspace_mount_mode: str = "ro",
        network_mode: str = "none",
    ):
        """
        Initialize script executor with Docker SDK.

        Args:
            docker_client: Docker SDK client instance. If not provided, creates a client
                connecting to the rootless Docker socket at /run/user/{uid}/docker.sock.
                Pass None explicitly to indicate Docker is unavailable.
            container_image: Docker image name for Execution_Containers
            memory_limit: Docker memory constraint (e.g. '512m')
            cpu_limit: Docker CPU constraint (e.g. 1.0 for one CPU)
            timeout_seconds: Maximum execution timeout in seconds
            execution_manager: Manager for execution lifecycle and state
            output_collector: Collector for capturing stdout/stderr
            temp_storage_path: Base path for temporary file storage
            container_image_digest: Expected OCI **manifest** digest (the canonical anchor).
                The baked OCI-manifest sidecar is verified offline against this value. It is
                NOT the image ID — execution binds to the config digest derived from the
                verified manifest, never to this manifest digest.
            baked_image_archive_path: Path to the baked docker-archive (docker save format)
                loaded into the daemon at startup
            baked_image_manifest_path: Path to the baked OCI-manifest sidecar whose bytes are
                re-hashed offline and compared to container_image_digest
            bound_image_id: Pre-derived image ID to bind execution to without re-loading.
                Used for the execution executor when a startup loader already verified
                and loaded the baked image into the shared daemon.
            container_pids_limit: Maximum number of PIDs allowed in the container (fork bomb protection)
            enable_gpu: Whether to enable GPU passthrough via NVIDIA Container Toolkit CDI mode
            gpu_devices: NVIDIA_VISIBLE_DEVICES value (e.g. "all", "0", "0,1")
            nvidia_driver_capabilities: NVIDIA_DRIVER_CAPABILITIES value (e.g. "compute,utility")
            user: Container user as "uid:gid" (default unprivileged 65534:65534)
            cap_add: Capabilities to add on top of cap_drop=ALL. None applies the default
                7-cap working set; [] adds no capabilities.
            no_new_privileges: Apply the no-new-privileges security option when True
            read_only_rootfs: Mount the container root filesystem read-only when True
            tmpfs_size: Size of the /tmp tmpfs scratch mount (e.g. "256m"); empty = no tmpfs
            tmpfs_exec: Mount /tmp tmpfs with exec permission when True (default noexec)
            workspace_mount_mode: Bind mode for the /workspace volume ("ro" or "rw")
            network_mode: Container network mode ("none", "bridge", or "host")
        """
        if docker_client is _UNSET:
            uid = os.getuid()
            docker_client = docker.DockerClient(
                base_url=f"unix:///run/user/{uid}/docker.sock"
            )
        self._docker_client = docker_client
        self._container_image = container_image
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._timeout_seconds = timeout_seconds
        self._execution_manager = execution_manager
        self._output_collector = output_collector
        self._temp_storage_path = temp_storage_path
        self._container_image_digest = container_image_digest
        self._baked_image_archive_path = baked_image_archive_path
        self._baked_image_manifest_path = baked_image_manifest_path
        # The trusted image ID (config digest) derived from the verified baked
        # manifest. Set by load_baked_image(); execution binds to it, never to the
        # manifest digest. May be pre-set via bound_image_id for the execution
        # executor, whose image was already verified+loaded into the shared daemon
        # by the startup loader. None until the baked image has been verified+loaded.
        self._derived_image_id: "str | None" = bound_image_id
        self._container_pids_limit = container_pids_limit
        self._enable_gpu = enable_gpu
        self._gpu_devices = gpu_devices
        self._nvidia_driver_capabilities = nvidia_driver_capabilities
        self._user = user
        # Resolve cap_add: unset (None) -> default 7-cap set; [] -> no caps added.
        self._cap_add = list(CONTAINER_DEFAULT_CAP_ADD) if cap_add is None else list(cap_add)
        self._no_new_privileges = no_new_privileges
        self._read_only_rootfs = read_only_rootfs
        self._tmpfs_size = tmpfs_size
        self._tmpfs_exec = tmpfs_exec
        self._workspace_mount_mode = workspace_mount_mode
        self._network_mode = network_mode
        self._immutable_image_ref = self._compute_immutable_image_ref(
            container_image, container_image_digest
        )
        self._active_containers = {}
        self._container_lock = threading.Lock()

    @property
    def cap_add(self) -> "list[str]":
        """The resolved capability set added on top of cap_drop=ALL (the attested list)."""
        return list(self._cap_add)

    @property
    def derived_image_id(self) -> "str | None":
        """The trusted image ID (config digest) derived from the verified baked manifest.

        None until load_baked_image() has verified the sidecar and loaded the archive.
        """
        return self._derived_image_id

    @property
    def execution_image_ref(self) -> str:
        """The reference every containers.create() binds to.

        Once the baked image is loaded this is the derived image ID (config digest),
        so the loss of RepoDigests and the absence of a repo tag on the loaded archive
        are irrelevant. Before load (and in unit tests that inject an image directly)
        it falls back to the configured immutable reference.
        """
        return self._derived_image_id or self._immutable_image_ref

    def _compute_immutable_image_ref(self, container_image: str, container_image_digest: "str | None") -> str:
        """
        Compute an immutable image reference from the container image and digest.

        Strips any mutable tag from the image reference and appends the digest
        to produce a reference of the form <repository>@sha256:<digest>.

        If no digest is configured but the image already contains @sha256:,
        uses it directly. If no digest is available and the image is a mutable
        tag, logs a warning and falls back to the tag for backward compatibility.

        Args:
            container_image: The configured container image reference (may include tag)
            container_image_digest: Optional SHA-256 digest string

        Returns:
            The immutable image reference string
        """
        if container_image_digest is not None:
            # Strip tag (everything after ':' but before '@') to get the repository
            repository = container_image
            # First strip any existing @sha256: suffix
            if "@sha256:" in repository:
                repository = repository.split("@sha256:", 1)[0]
            # Then strip any tag
            if ":" in repository:
                # Handle the case where ':' is part of a registry port (e.g. localhost:5000/image)
                # by only stripping after the last '/'
                last_slash = repository.rfind("/")
                if last_slash != -1:
                    after_slash = repository[last_slash + 1:]
                    if ":" in after_slash:
                        repository = repository[:last_slash + 1] + after_slash.split(":", 1)[0]
                else:
                    # No slash at all, simple image:tag
                    repository = repository.split(":", 1)[0]

            # Normalize digest: if it already has 'sha256:' prefix, extract just the hex
            digest_hex = container_image_digest
            if digest_hex.startswith("sha256:"):
                digest_hex = digest_hex[len("sha256:"):]

            immutable_ref = f"{repository}@sha256:{digest_hex}"
            logger.info(f"Using immutable image reference: {immutable_ref}")
            return immutable_ref

        # No explicit digest configured
        if "@sha256:" in container_image:
            # Image already pinned by digest — use it directly
            logger.info(f"Container image already pinned by digest: {container_image}")
            return container_image

        # Mutable tag with no digest — backward compatibility fallback
        logger.warning(
            f"No container_image_digest configured and image '{container_image}' "
            f"uses a mutable tag; falling back to tag-based reference (not immutable)"
        )
        return container_image

    def execute_async(self, execution_id: str, repo_path: str, script_path: str, script_env: "dict[str, str] | None" = None) -> None:
        """
        Execute script asynchronously inside an ephemeral Docker container.

        Creates a background thread that runs in a *fresh* contextvars.Context
        so that the parent request's log context is not inherited or leaked.

        The thread:
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
            script_env: Optional dictionary of environment variables to inject into the container
        """
        # Create a fresh context so the background thread does not inherit
        # (or pollute) the parent request's contextvars state.
        ctx = contextvars.copy_context()
        thread = threading.Thread(
            target=ctx.run,
            args=(self._execute_in_container, execution_id, repo_path, script_path, script_env),
            daemon=True,
        )
        thread.start()

    def _execute_in_container(self, execution_id: str, repo_path: str, script_path: str, script_env: "dict[str, str] | None" = None) -> None:
        """
        Internal method to execute script inside a Docker container (runs in background thread).

        Uses Log_Streaming_Threads to capture stdout/stderr incrementally during
        execution rather than batch-capturing after the container exits.

        Args:
            execution_id: Unique execution identifier
            repo_path: Path to the cloned repository directory on the host
            script_path: Relative path to the script within the repo
            script_env: Optional dictionary of environment variables to inject into the container
        """
        set_log_context(execution_id=execution_id)

        container = None
        container_name = f"{CONTAINER_NAME_PREFIX}{execution_id}"
        streaming_active = False

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
            nano_cpus = int(self._cpu_limit * 1e9)
            host_repo_path = os.path.abspath(repo_path)

            # Ensure cloned files are world-readable for defense-in-depth.
            subprocess.run(
                ["chmod", "-R", "a+rX", host_repo_path],
                timeout=30,
                check=True,
            )

            # Build container environment: start with script_env, then overlay
            # server-controlled GPU env vars so callers cannot override GPU policy.
            container_env = dict(script_env) if script_env else {}
            if self._enable_gpu:
                container_env["NVIDIA_VISIBLE_DEVICES"] = self._gpu_devices
                container_env["NVIDIA_DRIVER_CAPABILITIES"] = self._nvidia_driver_capabilities

            # Build optional kwargs for GPU passthrough (CDI mode)
            create_kwargs = {}
            if self._enable_gpu:
                create_kwargs["runtime"] = "nvidia"

            # no-new-privileges: include the security option only when enabled.
            if self._no_new_privileges:
                create_kwargs["security_opt"] = ["no-new-privileges"]

            # tmpfs scratch at the container's standard temp dir whenever a size is
            # configured — independent of the read-only rootfs setting.
            # mode=1777 is set explicitly: runc 1.1+/Docker 20.10.7+ bring an
            # option-less tmpfs up root-owned 0755 (despite the Docker docs'
            # claim of 1777), which would leave the non-root default user unable
            # to write /tmp (EACCES). See docker/docs#15594, runc#4971.
            if self._tmpfs_size:
                create_kwargs["tmpfs"] = {"/tmp": f"size={self._tmpfs_size},mode=1777" + (",exec" if self._tmpfs_exec else "")}

            container = self._docker_client.containers.create(
                image=self.execution_image_ref,
                name=container_name,
                command=["bash", f"/workspace/{script_path}"],
                mem_limit=self._memory_limit,
                nano_cpus=nano_cpus,
                pids_limit=self._container_pids_limit,
                user=self._user,
                volumes={
                    host_repo_path: {"bind": "/workspace", "mode": self._workspace_mount_mode},
                },
                working_dir="/workspace",
                read_only=self._read_only_rootfs,
                cap_drop=["ALL"],
                cap_add=self._cap_add,
                network_mode=self._network_mode,
                detach=True,
                environment=container_env,
                **create_kwargs,
            )

            # Track the container
            with self._container_lock:
                self._active_containers[execution_id] = container

            # Start the container
            container.start()
            logger.info(f"Container {container_name} started for execution {execution_id}")

            # Start Log_Streaming_Threads for stdout and stderr
            stdout_thread = threading.Thread(
                target=self._stream_container_logs,
                args=(execution_id, container, "stdout"),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._stream_container_logs,
                args=(execution_id, container, "stderr"),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()
            streaming_active = True
            logger.debug(f"Log streaming threads started for execution {execution_id}")

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

                # Join streaming threads to capture any partial output
                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=5)

                # Fallback batch capture only if streaming was not active
                if not streaming_active:
                    self._capture_container_logs(execution_id, container)

                self._output_collector.mark_complete(execution_id, -1)
                self._execution_manager.update_status(
                    execution_id, ExecutionStatus.TIMED_OUT, exit_code=-1
                )
                return

            # Join streaming threads with short timeout to ensure they finish
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

            # Streaming threads already captured all output — no batch re-capture needed

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

    def _stream_container_logs(self, execution_id: str, container, stream_name: str) -> None:
        """
        Stream logs from a container incrementally and feed chunks to the OutputCollector.

        This runs as a daemon thread (Log_Streaming_Thread) concurrently with
        container.wait(), reading from the Docker SDK streaming log API.

        Args:
            execution_id: Unique execution identifier
            container: Docker container object
            stream_name: 'stdout' or 'stderr'
        """
        try:
            is_stdout = stream_name == "stdout"
            log_stream = container.logs(
                stream=True,
                follow=True,
                stdout=is_stdout,
                stderr=not is_stdout,
            )
            for chunk in log_stream:
                if chunk:
                    self._output_collector.capture_output(execution_id, stream_name, chunk)
        except Exception as e:
            logger.warning(
                f"Error streaming {stream_name} for execution {execution_id}: {e}"
            )

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

    def load_baked_image(self) -> None:
        """Verify, derive, then load the image baked into the verity-sealed root.

        Replaces the former startup registry pull. The image is no longer a
        runtime-asserted input: its bytes are measured into PCR4 at build time,
        and at startup the executor proves — fully offline, with no daemon-
        reported value trusted — that the baked bytes are the expected image,
        in three separable steps:

        1. **Verify**: recompute a byte-exact SHA-256 over the stored OCI-manifest
           sidecar bytes and compare to the expected ``CONTAINER_IMAGE_DIGEST``
           (a *manifest* digest). Pure hashing — no daemon, no network, no
           index.json walk (the build emitted a single linux/amd64 manifest blob).
        2. **Derive**: read the config descriptor out of the *verified* manifest;
           its digest is the trusted **image ID** (a *config* digest), derived
           entirely from verity-measured, digest-verified bytes.
        3. **Load + bind**: ``docker load`` the baked docker-archive into the
           rootless daemon and bind execution to the derived image ID. If the
           loader was unfaithful (rewrote the config blob), no loaded image will
           carry the derived ID and binding fails closed.

        Raises:
            RuntimeError: with the same fail-closed semantics as the former
                startup pull, if the Docker client is unavailable, the expected
                digest is absent/empty, the baked archive or sidecar is missing
                or corrupt, the recomputed manifest digest mismatches, or no
                loaded image carries the derived image ID.
        """
        # ---- Verify -------------------------------------------------------
        manifest_bytes = self._read_baked_manifest()
        self._verify_manifest_digest(manifest_bytes)

        # ---- Derive -------------------------------------------------------
        image_id = self._derive_image_id(manifest_bytes)

        # ---- Load + bind --------------------------------------------------
        self._load_baked_archive()
        self._bind_derived_image_id(image_id)

    def _read_baked_manifest(self) -> bytes:
        """Read the baked OCI-manifest sidecar bytes exactly as stored on disk.

        The OCI digest is over the on-disk bytes, so they are returned verbatim
        and never re-parsed/re-serialized before hashing.
        """
        path = self._baked_image_manifest_path
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise RuntimeError(
                f"Cannot read baked OCI-manifest sidecar '{path}': {e}"
            )
        if not data:
            raise RuntimeError(
                f"Baked OCI-manifest sidecar '{path}' is empty or corrupt"
            )
        return data

    def _verify_manifest_digest(self, manifest_bytes: bytes) -> None:
        """Recompute the manifest digest over the sidecar bytes and compare.

        Pure offline hashing of the stored bytes (never a re-serialized form),
        compared to the expected manifest digest. Fail-closed on a
        missing/empty expected digest or any mismatch.
        """
        expected_digest = self._container_image_digest
        if not expected_digest:
            raise RuntimeError(
                "Cannot verify baked container image: no expected manifest digest "
                "configured (CONTAINER_IMAGE_DIGEST is absent or empty; this should "
                "have been caught during config validation)"
            )

        actual_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
        if actual_digest != expected_digest:
            raise RuntimeError(
                "Baked container image manifest digest mismatch: "
                f"expected {expected_digest}, recomputed {actual_digest} over the "
                f"sidecar '{self._baked_image_manifest_path}'"
            )
        logger.info(
            f"Baked OCI-manifest sidecar verified offline against expected digest {expected_digest}"
        )

    def _derive_image_id(self, manifest_bytes: bytes) -> str:
        """Read the config descriptor out of the verified manifest; its digest is the image ID.

        Only called after _verify_manifest_digest has confirmed the bytes, so the
        config digest is trusted by construction. Distinct from the manifest digest;
        the two are never conflated.
        """
        try:
            manifest = json.loads(manifest_bytes)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Verified baked manifest '{self._baked_image_manifest_path}' is not valid JSON: {e}"
            )
        config_digest = (manifest.get("config") or {}).get("digest")
        if not config_digest or not isinstance(config_digest, str):
            raise RuntimeError(
                "Verified baked manifest has no config descriptor digest; cannot derive image ID"
            )
        logger.info(f"Derived trusted image ID (config digest) from verified manifest: {config_digest}")
        return config_digest

    def _load_baked_archive(self) -> None:
        """`docker load` the baked docker-archive into the rootless daemon."""
        if self._docker_client is None:
            raise RuntimeError(
                "Cannot load baked container image: Docker client is not available"
            )
        path = self._baked_image_archive_path
        try:
            with open(path, "rb") as archive:
                self._docker_client.images.load(archive.read())
        except OSError as e:
            raise RuntimeError(f"Cannot read baked docker-archive '{path}': {e}")
        except docker.errors.APIError as e:
            raise RuntimeError(f"Failed to docker load baked archive '{path}': {e}")
        logger.info(f"Loaded baked docker-archive '{path}' into the rootless daemon")

    def _bind_derived_image_id(self, image_id: str) -> None:
        """Confirm the loaded image carries the derived image ID, then bind to it.

        Fail-closed if no loaded image matches the derived ID — that is the
        signature of an unfaithful loader (one that rewrote the config blob), and
        execution must never proceed against an image other than the one the
        verified manifest commits to.
        """
        if self._docker_client is None:
            raise RuntimeError(
                "Cannot bind baked container image: Docker client is not available"
            )
        try:
            self._docker_client.images.get(image_id)
        except docker.errors.ImageNotFound:
            raise RuntimeError(
                f"Baked image load did not yield the derived image ID {image_id}: the "
                "loaded image's config digest does not match the verified manifest "
                "(an unfaithful conversion rewrote the config blob); failing closed"
            )
        except docker.errors.APIError as e:
            raise RuntimeError(
                f"Failed to confirm derived image ID {image_id} after load: {e}"
            )
        self._derived_image_id = image_id
        logger.info(f"Bound execution to derived image ID {image_id}")

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
