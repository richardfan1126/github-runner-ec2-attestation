# GitHub Actions Remote Executor

HTTP server for executing GitHub Actions scripts with AWS Nitro attestation.

## Overview

The GitHub Actions Remote Executor runs on AWS Nitro-based EC2 instances, providing a secure and attestable environment for executing scripts from GitHub repositories. The system generates cryptographic attestation documents proving the execution environment and executes scripts asynchronously while allowing clients to poll for output and status.

## Requirements

- Python 3.11+
- AWS Nitro-based EC2 instance (for attestation capabilities)
- GitHub personal access token

## Installation

1. Install uv (Python package manager):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Clone the repository and install dependencies:
```bash
git clone <repository-url>
cd github-actions-remote-executor
uv sync
```

3. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

## Configuration

All configuration is done through environment variables. See `.env.example` for available options:

- `SERVER_PORT`: HTTP server listening port (default: 8080)
- `MAX_CONCURRENT_EXECUTIONS`: Maximum concurrent script executions (default: 10)
- `EXECUTION_TIMEOUT_SECONDS`: Script execution timeout (default: 300)
- `MAX_SCRIPT_SIZE_BYTES`: Maximum script file size (default: 1048576)
- `RATE_LIMIT_PER_IP`: Rate limit per IP address (default: 100)
- `RATE_LIMIT_WINDOW_SECONDS`: Rate limit window (default: 60)
- `TEMP_STORAGE_PATH`: Temporary file storage location (default: /tmp/gha-executor)
- `OUTPUT_RETENTION_HOURS`: Output retention period (default: 24)
- `NSM_DEVICE_PATH`: AWS Nitro Security Module device path (default: /dev/nsm)

## Usage

Start the server:
```bash
uv run python -m src.main
```

## API Endpoints

### POST /execute

Initiates script execution and returns attestation document.

**Request:**
```json
{
  "repository_url": "https://github.com/owner/repo",
  "commit_hash": "abc123def456...",
  "script_path": "scripts/build.sh",
  "github_token": "ghp_..."
}
```

**Response:**
```json
{
  "execution_id": "uuid-v4",
  "attestation_document": "base64-encoded-cbor",
  "status": "queued"
}
```

### GET /execution/{execution_id}/output

Retrieves execution status and output.

**Response:**
```json
{
  "execution_id": "uuid-v4",
  "status": "running|completed|failed|timed_out",
  "stdout": "output text...",
  "stderr": "error text...",
  "stdout_offset": 1024,
  "stderr_offset": 256,
  "complete": false,
  "exit_code": null
}
```

### GET /health

Health check endpoint.

### GET /metrics

Metrics endpoint for monitoring.

## Development

Run tests:
```bash
uv run pytest
```

Run with hot reload:
```bash
uv run uvicorn src.main:app --reload --port 8080
```

## AWS Nitro EC2 Deployment

This application requires an AWS Nitro-based EC2 instance for attestation capabilities. Supported instance types include:
- C5, C5a, C5n, C6i, C6a, C7g
- M5, M5a, M5n, M6i, M6a, M7g
- R5, R5a, R5n, R6i, R6a, R7g
- And other Nitro-based instances

## License

See LICENSE file for details.
