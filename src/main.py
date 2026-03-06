"""Main entry point for GitHub Actions Remote Executor."""

import sys
import logging
from config import load_config, ConfigurationError
from src.logging_config import setup_logging


logger = logging.getLogger(__name__)


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
        
        # TODO: Initialize and start HTTP server
        logger.info("Server initialization complete")
        
        return 0
        
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error during startup: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
