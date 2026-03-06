"""Main entry point for GitHub Actions Remote Executor."""

import sys
import logging
import signal
import os
from typing import Optional

from src.config import load_config, ConfigurationError
from src.logging_config import setup_logging
from src.attestation import AttestationGenerator
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
        logger.info(f"NSM device path: {config.nsm_device_path}")
        
        # Verify NSM device availability
        logger.info("Verifying NSM device availability...")
        attestation_generator = AttestationGenerator(config.nsm_device_path)
        if not attestation_generator.verify_nsm_available():
            logger.error(
                f"NSM device not available at {config.nsm_device_path}. "
                "Attestation functionality will not work."
            )
            logger.warning("Continuing startup, but attestation will fail at runtime.")
        else:
            logger.info("NSM device verified and available")
        
        # Ensure temp storage directory exists
        if not os.path.exists(config.temp_storage_path):
            logger.info(f"Creating temp storage directory: {config.temp_storage_path}")
            os.makedirs(config.temp_storage_path, mode=0o700, exist_ok=True)
        
        # Initialize all components via create_app
        logger.info("Initializing application components...")
        app = create_app(config)
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
