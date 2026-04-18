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
            logger.error(
                f"NitroTPM device not available at {config.tpm_attest_path}. "
                "Attestation functionality will not work."
            )
            logger.warning("Continuing startup, but attestation will fail at runtime.")
        else:
            logger.info("NitroTPM device verified and available")
        
        # Initialize Docker client and verify daemon accessibility
        logger.info("Initializing Docker client...")
        try:
            docker_client = docker.from_env()
        except docker.errors.DockerException as e:
            logger.error(f"Failed to create Docker client: {e}")
            raise ConfigurationError(
                "Docker daemon is not accessible. Ensure Docker is installed and running."
            )
        
        # Verify Docker daemon is accessible
        logger.info("Verifying Docker daemon accessibility...")
        try:
            docker_client.ping()
            logger.info("Docker daemon verified and accessible")
        except docker.errors.APIError as e:
            logger.error(f"Docker daemon is not responding: {e}")
            raise ConfigurationError(
                "Docker daemon is not responding. Ensure Docker is running and accessible."
            )
        
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
