"""GitHub Actions Remote Executor Caller.

Client-side caller for the Remote Executor system. Orchestrates the full
lifecycle of a remote script execution: health check, submission, attestation
validation, output polling, output integrity verification, and result reporting.
"""

import argparse
import base64
import hashlib
import json
import logging
import sys
import time

import cbor2
import requests

logger = logging.getLogger(__name__)

EXPECTED_ATTESTATION_FIELDS = [
    "module_id",
    "digest",
    "timestamp",
    "pcrs",
    "certificate",
    "cabundle",
]


class CallerError(Exception):
    """Raised when the caller encounters a fatal error."""

    def __init__(self, message: str, phase: str, details: dict | None = None):
        self.message = message
        self.phase = phase
        self.details = details or {}
        super().__init__(self.message)


class RemoteExecutorCaller:
    """Client for the Remote Executor server."""

    def __init__(
        self,
        server_url: str,
        timeout: int = 30,
        poll_interval: int = 5,
        max_poll_duration: int = 600,
        max_retries: int = 3,
    ):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_poll_duration = max_poll_duration
        self.max_retries = max_retries

    def health_check(self) -> dict:
        """GET /health - verify server is healthy.

        Returns parsed JSON response.
        Raises CallerError if unhealthy or unreachable.
        """
        url = f"{self.server_url}/health"
        try:
            response = requests.get(url, timeout=self.timeout)
        except requests.ConnectionError as exc:
            raise CallerError(
                message=f"Failed to connect to server health endpoint: {exc}",
                phase="health_check",
                details={"url": url, "error": str(exc)},
            )
        except requests.RequestException as exc:
            raise CallerError(
                message=f"Health check request failed: {exc}",
                phase="health_check",
                details={"url": url, "error": str(exc)},
            )

        if response.status_code != 200:
            raise CallerError(
                message=f"Health check failed with HTTP {response.status_code}",
                phase="health_check",
                details={
                    "status_code": response.status_code,
                    "body": response.text,
                },
            )

        data = response.json()
        if data.get("status") != "healthy":
            raise CallerError(
                message=f"Server is not healthy: status={data.get('status')}",
                phase="health_check",
                details={"response": data},
            )

        return data

    def execute(
        self,
        repository_url: str,
        commit_hash: str,
        script_path: str,
        github_token: str,
    ) -> dict:
        """POST /execute - submit execution request.

        Returns parsed JSON response with execution_id and attestation_document.
        Raises CallerError on HTTP errors or connection failures.
        """
        url = f"{self.server_url}/execute"
        payload = {
            "repository_url": repository_url,
            "commit_hash": commit_hash,
            "script_path": script_path,
            "github_token": github_token,
        }
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.ConnectionError as exc:
            raise CallerError(
                message=f"Failed to connect to server execute endpoint: {exc}",
                phase="execute",
                details={"url": url, "error": str(exc)},
            )
        except requests.RequestException as exc:
            raise CallerError(
                message=f"Execute request failed: {exc}",
                phase="execute",
                details={"url": url, "error": str(exc)},
            )

        if response.status_code != 200:
            raise CallerError(
                message=f"Execute failed with HTTP {response.status_code}",
                phase="execute",
                details={
                    "status_code": response.status_code,
                    "body": response.text,
                },
            )

        return response.json()

    def validate_attestation(self, attestation_b64: str) -> dict:
        """Decode base64 -> binary -> CBOR. Validate structural fields.

        Returns parsed attestation document as dict.
        Raises CallerError on decode/parse/validation failures.
        """
        # Base64-decode the attestation string to binary
        try:
            raw_bytes = base64.b64decode(attestation_b64)
        except Exception as exc:
            raise CallerError(
                message=f"Failed to base64-decode attestation document: {exc}",
                phase="attestation",
                details={"error": str(exc)},
            )

        # CBOR-decode the binary to a Python dict
        try:
            doc = cbor2.loads(raw_bytes)
        except Exception as exc:
            raise CallerError(
                message=f"Failed to CBOR-decode attestation document: {exc}",
                phase="attestation",
                details={"error": str(exc)},
            )

        if not isinstance(doc, dict):
            raise CallerError(
                message=f"Attestation document is not a map, got {type(doc).__name__}",
                phase="attestation",
                details={"type": type(doc).__name__},
            )

        # Verify all expected structural fields are present
        missing = [f for f in EXPECTED_ATTESTATION_FIELDS if f not in doc]
        if missing:
            raise CallerError(
                message=f"Attestation document missing fields: {missing}",
                phase="attestation",
                details={"missing_fields": missing},
            )

        # Log attestation document fields for audit
        for field in EXPECTED_ATTESTATION_FIELDS:
            logger.info("Attestation field %s: %s", field, doc[field])

        return doc

    def poll_output(self, execution_id: str) -> dict:
        """Poll GET /execution/{id}/output until complete or timeout.

        Logs incremental output during polling.
        Returns final response with stdout, stderr, exit_code,
        output_attestation_document.
        Raises CallerError on timeout or repeated HTTP failures.
        """
        raise NotImplementedError

    def validate_output_attestation(
        self,
        output_attestation_b64: str,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> bool:
        """Decode output attestation CBOR, extract user_data digest.

        Compute SHA-256 of canonical output format. Compare digests.
        Returns True if match.
        Raises CallerError on decode/parse failures or digest mismatch.
        """
        raise NotImplementedError

    def run(
        self,
        repository_url: str,
        commit_hash: str,
        script_path: str,
        github_token: str,
    ) -> int:
        """Orchestrate full flow.

        health_check -> execute -> validate_attestation -> poll_output
        -> validate_output_attestation -> report results.
        Returns remote script exit code.
        """
        raise NotImplementedError
