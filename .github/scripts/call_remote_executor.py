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
        raise NotImplementedError

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
        raise NotImplementedError

    def validate_attestation(self, attestation_b64: str) -> dict:
        """Decode base64 -> binary -> CBOR. Validate structural fields.

        Returns parsed attestation document as dict.
        Raises CallerError on decode/parse/validation failures.
        """
        raise NotImplementedError

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
