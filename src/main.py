"""Main entry point for GitHub Actions Remote Executor."""

import sys
import logging
import signal
import os
from typing import Optional

import docker

from src.config import load_config, ConfigurationError
from src.logging_config import setup_logging
from src.attestation import AttestationGenerator
from src.encryption import EncryptionManager
from src.script_executor import ScriptExecutor
from src.server import create_app


logger = logging.getLogger(__name__)

# Global reference to server for graceful shutdown
_server_process: Optional[any] = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    # FastAPI/uvicorn will handle the actual shutdown
    sys.exit(0)


def main() -> int:
    """Main entry point for the server.
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        # Set up logging infrastructure first
        setup_logging(
            log_level="INFO",
            log_dir="/var/log/github-actions-executor",
            enable_rotation=True
        )
        
        logger.info("Starting GitHub Actions Remote Executor...")
        
        # Load and validate configuration
        logger.info("Loading configuration...")
        config = load_config()
        logger.info("Configuration loaded successfully")
        logger.info(f"Server will listen on port {config.port}")
        logger.info(f"Max concurrent executions: {config.max_concurrent_executions}")
        logger.info(f"Execution timeout: {config.execution_timeout_seconds}s")
        logger.info(f"Max script size: {config.max_script_size_bytes} bytes")
        logger.info(f"Rate limit: {config.rate_limit_per_ip} requests per {config.rate_limit_window_seconds}s")
        logger.info(f"Temp storage path: {config.temp_storage_path}")
        logger.info(f"Output retention: {config.output_retention_hours} hours")
        logger.info(f"NitroTPM device path: {config.tpm_attest_path}")
        logger.info(f"Container image: {config.container_image}")
        logger.info(f"Container memory limit: {config.container_memory_limit}")
        logger.info(f"Container CPU limit: {config.container_cpu_limit}")
        
        # Verify NitroTPM device availability
        logger.info("Verifying NitroTPM device availability...")
        attestation_generator = AttestationGenerator(config.tpm_attest_path)
        if not attestation_generator.verify_tpm_available():
            if not config.allow_no_tpm:
                logger.error(
                    f"NitroTPM device not available at {config.tpm_attest_path}. "
                    "Attestation cannot be produced. Set ALLOW_NO_TPM=true to bypass "
                    "this check in development/test environments."
                )
                return 1
            else:
                logger.warning(
                    "*** NitroTPM device NOT available — attestation will fail at runtime. ***"
                )
                logger.warning(
                    "ALLOW_NO_TPM is set to true. This is acceptable for dev/test only. "
                    "Do NOT run in production without a functioning NitroTPM device."
                )
        else:
            logger.info("NitroTPM device verified and available")
        
        # Initialize Docker client using rootless Docker socket
        logger.info("Initializing Docker client...")
        uid = os.getuid()
        rootless_socket = f"unix:///run/user/{uid}/docker.sock"
        logger.info(f"Using rootless Docker socket: {rootless_socket}")
        try:
            docker_client = docker.DockerClient(base_url=rootless_socket)
        except docker.errors.DockerException as e:
            logger.error(f"Failed to create Docker client: {e}")
            raise ConfigurationError(
                f"Docker daemon is not accessible at {rootless_socket}. "
                "Ensure rootless Docker is installed and running for the service user."
            )
        
        # Verify Docker daemon is accessible via rootless socket
        logger.info("Verifying Docker daemon accessibility...")
        try:
            docker_client.ping()
            logger.info("Docker daemon verified and accessible")
        except docker.errors.APIError as e:
            logger.error(f"Docker daemon is not responding at {rootless_socket}: {e}")
            raise ConfigurationError(
                f"Docker daemon is not responding at {rootless_socket}. "
                "Ensure rootless Docker is running for the service user."
            )
        
        # GPU startup verification (when enabled)
        if config.enable_gpu:
            logger.info("GPU passthrough enabled — verifying NVIDIA runtime...")

            # 1. Verify the 'nvidia' runtime is registered with the Docker daemon
            try:
                docker_info = docker_client.info()
                runtimes = docker_info.get("Runtimes", {})
                if "nvidia" not in runtimes:
                    logger.error(
                        "NVIDIA runtime not registered with Docker daemon. "
                        f"Available runtimes: {list(runtimes.keys())}. "
                        "Ensure NVIDIA Container Toolkit is installed and configured."
                    )
                    return 1
                logger.info("NVIDIA runtime is registered with Docker daemon")
            except docker.errors.APIError as e:
                logger.error(f"Failed to query Docker daemon info for GPU verification: {e}")
                return 1

            # 2. Check for CDI specification existence (non-fatal warning)
            cdi_spec_paths = [
                "/var/run/cdi/nvidia.yaml",
                "/etc/cdi/nvidia.yaml",
            ]
            cdi_found = any(os.path.exists(p) for p in cdi_spec_paths)
            if not cdi_found:
                logger.warning(
                    "CDI specification not found at any of: "
                    f"{', '.join(cdi_spec_paths)}. "
                    "GPU access may still work via legacy mode, but CDI mode is recommended."
                )
            else:
                logger.info("CDI specification found")

            # 3. Create and immediately remove a test container to verify GPU access
            logger.info("Running GPU access verification container...")
            try:
                test_container = docker_client.containers.create(
                    image=config.container_image,
                    command=["true"],
                    runtime="nvidia",
                    environment={"NVIDIA_VISIBLE_DEVICES": "all"},
                    detach=True,
                )
                # Remove the test container immediately
                test_container.remove(force=True)
                logger.info("GPU access verification succeeded — test container created and removed")
            except docker.errors.APIError as e:
                logger.error(
                    f"GPU access verification failed: could not create test container "
                    f"with runtime='nvidia': {e}. "
                    "Ensure NVIDIA drivers are installed and GPUs are accessible."
                )
                return 1
            except docker.errors.ImageNotFound as e:
                logger.error(
                    f"GPU access verification failed: container image not found: {e}. "
                    f"Ensure '{config.container_image}' is available locally."
                )
                return 1
        else:
            logger.info("GPU passthrough disabled (ENABLE_GPU=false)")

        # Clean up any dangling execution containers from previous runs
        logger.info("Cleaning up dangling execution containers...")
        from src.execution_manager import ExecutionManager
        from src.output_collector import OutputCollector
        temp_executor = ScriptExecutor(
            docker_client=docker_client,
            container_image=config.container_image,
            memory_limit=config.container_memory_limit,
            cpu_limit=config.container_cpu_limit,
            timeout_seconds=config.execution_timeout_seconds,
            execution_manager=ExecutionManager(config.output_retention_hours),
            output_collector=OutputCollector(),
            temp_storage_path=config.temp_storage_path,
            container_image_digest=config.container_image_digest,
        )
        temp_executor.cleanup_dangling_containers()
        logger.info("Dangling container cleanup complete")
        
        # Pull container image if not already present
        logger.info(f"Ensuring container image '{config.container_image}' is available...")
        try:
            temp_executor.pull_container_image()
        except Exception as e:
            raise ConfigurationError(
                f"Failed to pull container image '{config.container_image}': {e}"
            )
        
        # Ensure temp storage directory exists
        if not os.path.exists(config.temp_storage_path):
            logger.info(f"Creating temp storage directory: {config.temp_storage_path}")
            os.makedirs(config.temp_storage_path, mode=0o700, exist_ok=True)
        
        # Initialize encryption manager
        logger.info("Initializing encryption manager...")
        encryption_manager = EncryptionManager()
        
        # Initialize all components via create_app
        logger.info("Initializing application components...")
        app = create_app(config, docker_client=docker_client, encryption_manager=encryption_manager)
        logger.info("All components initialized successfully")
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start HTTP server
        logger.info(f"Starting HTTP server on 0.0.0.0:{config.port}...")
        
        # Import uvicorn here to start the server
        import uvicorn
        
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=config.port,
            log_level="info",
            access_log=True
        )
        
        logger.info("Server shutdown complete")
        return 0
        
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error during startup: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
