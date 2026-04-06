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
import os
import sys
import time

import cbor2
import requests
from pycose.messages import Sign1Message
from pycose.keys import EC2Key
from pycose.headers import Algorithm, KID
from pycose.algorithms import Es384
from pycose.keys.keyparam import EC2KpCurve, EC2KpX, EC2KpY
from pycose.keys.curves import P384
from OpenSSL import crypto as ossl_crypto
from Crypto.Util.number import long_to_bytes
from cryptography.x509 import load_der_x509_certificate
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicNumbers
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

logger = logging.getLogger(__name__)

EXPECTED_ATTESTATION_FIELDS = [
    "module_id",
    "digest",
    "timestamp",
    "nitrotpm_pcrs",
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


class ClientEncryption:
    """HPKE encryption helper for the caller side.

    Generates a client X25519 keypair, derives a shared AES-256-GCM key
    from the server's public key via ECDH + HKDF-SHA256, and provides
    encrypt/decrypt methods for request/response payloads.
    """

    def __init__(self):
        """Generate a fresh X25519 keypair for this session."""
        self._private_key = X25519PrivateKey.generate()
        self._shared_key: bytes | None = None

    @property
    def client_public_key_bytes(self) -> bytes:
        """Return the raw 32-byte client public key for transmission."""
        return self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def derive_shared_key(self, server_public_key_bytes: bytes) -> None:
        """Derive the shared key from ECDH + HKDF-SHA256.

        Raises CallerError if server_public_key_bytes is not a valid 32-byte X25519 key.
        """
        try:
            server_public_key = X25519PublicKey.from_public_bytes(server_public_key_bytes)
        except Exception as exc:
            raise CallerError(
                message=f"Invalid server public key: {exc}",
                phase="encryption",
                details={"error": str(exc)},
            )
        shared_secret = self._private_key.exchange(server_public_key)
        self._shared_key = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=None,
            info=b"hpke-shared-key",
        ).derive(shared_secret)

    def encrypt_payload(self, payload_dict: dict) -> str:
        """Serialize payload_dict to JSON, encrypt with AES-256-GCM.

        Returns base64-encoded string of (12-byte nonce || ciphertext).
        Raises CallerError if shared key has not been derived yet.
        """
        if self._shared_key is None:
            raise CallerError(
                message="Cannot encrypt: shared key has not been derived yet",
                phase="encryption",
            )
        plaintext = json.dumps(payload_dict).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._shared_key).encrypt(nonce, plaintext, None)
        wire_bytes = nonce + ciphertext
        return base64.b64encode(wire_bytes).decode("ascii")

    def decrypt_response(self, encrypted_response_b64: str) -> dict:
        """Base64-decode, split nonce + ciphertext, decrypt with AES-256-GCM.

        Returns the deserialized JSON dict.
        Raises CallerError on decryption failure or invalid JSON.
        """
        if self._shared_key is None:
            raise CallerError(
                message="Cannot decrypt: shared key has not been derived yet",
                phase="encryption",
            )
        try:
            wire_bytes = base64.b64decode(encrypted_response_b64)
            nonce = wire_bytes[:12]
            ciphertext = wire_bytes[12:]
            plaintext = AESGCM(self._shared_key).decrypt(nonce, ciphertext, None)
        except CallerError:
            raise
        except Exception as exc:
            raise CallerError(
                message=f"Decryption failed: {exc}",
                phase="encryption",
                details={"error": str(exc)},
            )
        try:
            return json.loads(plaintext.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CallerError(
                message=f"Decrypted response is not valid JSON: {exc}",
                phase="encryption",
                details={"error": str(exc)},
            )


class RemoteExecutorCaller:
    """Client for the Remote Executor server."""

    def __init__(
        self,
        server_url: str,
        timeout: int = 30,
        poll_interval: int = 5,
        max_poll_duration: int = 600,
        max_retries: int = 3,
        root_cert_pem: str = "",
        expected_pcrs: dict[int, str] | None = None,
        audience: str = "",
    ):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_poll_duration = max_poll_duration
        self.max_retries = max_retries
        self.root_cert_pem = root_cert_pem
        self.expected_pcrs = expected_pcrs
        self.audience = audience
        self._oidc_token: str | None = None

    def request_oidc_token(self) -> str:
        """Request an OIDC token from GitHub's OIDC provider.

        Reads ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN
        from environment variables. Makes an HTTP GET to the request URL with
        the audience query parameter and Bearer authorization header.

        Returns the OIDC JWT token string.
        Raises CallerError(phase="oidc") if env vars are missing or request fails.
        """
        request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
        request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")

        if not request_url or not request_token:
            raise CallerError(
                message="OIDC token request requires id-token: write permission in the workflow",
                phase="oidc",
                details={
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "set" if request_url else "missing",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "set" if request_token else "missing",
                },
            )

        url = f"{request_url}&audience={self.audience}" if self.audience else request_url
        headers = {"Authorization": f"Bearer {request_token}"}

        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise CallerError(
                message=f"OIDC token request failed: {exc}",
                phase="oidc",
                details={"error": str(exc)},
            )

        if response.status_code != 200:
            raise CallerError(
                message=f"OIDC token request failed with HTTP {response.status_code}",
                phase="oidc",
                details={"status_code": response.status_code, "body": response.text},
            )

        try:
            token = response.json()["value"]
        except (KeyError, ValueError) as exc:
            raise CallerError(
                message=f"OIDC token response missing 'value' field: {exc}",
                phase="oidc",
                details={"error": str(exc)},
            )

        self._oidc_token = token
        logger.info("OIDC token acquired successfully")
        return token

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
        headers = {}
        if self._oidc_token:
            headers["Authorization"] = f"Bearer {self._oidc_token}"
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
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

        if response.status_code == 401:
            raise CallerError(
                message="Authentication failure: server returned HTTP 401 Unauthorized",
                phase="execute",
                details={"status_code": 401, "body": response.text},
            )
        if response.status_code == 403:
            raise CallerError(
                message="Repository is not authorized: server returned HTTP 403 Forbidden",
                phase="execute",
                details={"status_code": 403, "body": response.text},
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

    def _decode_cose_sign1(self, raw_bytes: bytes, phase: str) -> list:
        """Decode raw bytes into a COSE_Sign1 4-element array.

        Handles both CBOR-tagged (tag 18) and untagged representations.
        Returns the 4-element array [protected, unprotected, payload, signature].
        Raises CallerError on decode/structure failures.
        """
        try:
            decoded = cbor2.loads(raw_bytes)
        except Exception as exc:
            raise CallerError(
                message=f"Failed to CBOR-decode document: {exc}",
                phase=phase,
                details={"error": str(exc)},
            )

        # Unwrap CBOR tag 18 (COSE_Sign1) if present
        if isinstance(decoded, cbor2.CBORTag):
            if decoded.tag != 18:
                raise CallerError(
                    message=f"Unexpected CBOR tag {decoded.tag}, expected 18 (COSE_Sign1)",
                    phase=phase,
                    details={"tag": decoded.tag},
                )
            cose_array = decoded.value
        else:
            cose_array = decoded

        if not isinstance(cose_array, (list, tuple)) or len(cose_array) != 4:
            raise CallerError(
                message="CBOR result is not a valid COSE_Sign1 structure (expected 4-element array)",
                phase=phase,
                details={
                    "type": type(cose_array).__name__,
                    "length": len(cose_array) if isinstance(cose_array, (list, tuple)) else None,
                },
            )

        return list(cose_array)

    def validate_attestation(self, attestation_b64: str) -> dict:
        """Decode base64 -> CBOR -> COSE Sign1 array. Validate and verify.

        Returns parsed attestation payload dict.
        Raises CallerError on decode/parse/validation/verification failures.
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

        # CBOR-decode the binary — expect a COSE_Sign1 structure (tag 18)
        cose_array = self._decode_cose_sign1(raw_bytes, phase="attestation")

        # CBOR-decode the payload (index 2) to extract attestation fields
        try:
            payload_doc = cbor2.loads(cose_array[2])
        except Exception as exc:
            raise CallerError(
                message=f"Failed to CBOR-decode attestation payload: {exc}",
                phase="attestation",
                details={"error": str(exc)},
            )

        if not isinstance(payload_doc, dict):
            raise CallerError(
                message=f"Attestation payload is not a map, got {type(payload_doc).__name__}",
                phase="attestation",
                details={"type": type(payload_doc).__name__},
            )

        # Verify all expected structural fields are present
        missing = [f for f in EXPECTED_ATTESTATION_FIELDS if f not in payload_doc]
        if missing:
            raise CallerError(
                message=f"Attestation document missing fields: {missing}",
                phase="attestation",
                details={"missing_fields": missing},
            )

        # Validate certificate chain (PKI)
        self._verify_certificate_chain(payload_doc["certificate"], payload_doc["cabundle"])

        # Verify COSE Sign1 signature
        self._verify_cose_signature(cose_array)

        # Validate PCR values
        self._validate_pcrs(payload_doc["nitrotpm_pcrs"])

        # Log attestation document fields for audit
        for field in EXPECTED_ATTESTATION_FIELDS:
            logger.info("Attestation field %s: %s", field, payload_doc[field])

        return payload_doc

    def _verify_certificate_chain(self, cert_der: bytes, cabundle: list[bytes]) -> None:
        """Validate the signing certificate against the CA bundle and root certificate.

        Per AWS docs, cabundle is ordered [ROOT_CERT, INTERM_1, INTERM_2, ..., INTERM_N].
        The chain for validation is: TARGET_CERT <- INTERM_N <- ... <- INTERM_1 <- ROOT_CERT.
        Raises CallerError if certificate chain validation fails.
        """
        if not self.root_cert_pem:
            return

        try:
            store = ossl_crypto.X509Store()
            # Add the trusted root certificate from the provided PEM
            store.add_cert(ossl_crypto.load_certificate(ossl_crypto.FILETYPE_PEM, self.root_cert_pem))

            # Add all certificates from the CA bundle as intermediates.
            # cabundle[0] is the root from the document; the remaining are intermediates.
            for der_cert in cabundle:
                store.add_cert(ossl_crypto.load_certificate(ossl_crypto.FILETYPE_ASN1, der_cert))

            signing_cert = ossl_crypto.load_certificate(ossl_crypto.FILETYPE_ASN1, cert_der)
            store_ctx = ossl_crypto.X509StoreContext(store, signing_cert)
            store_ctx.verify_certificate()
        except Exception as exc:
            raise CallerError(
                message=f"Certificate chain validation failed: {exc}",
                phase="attestation",
                details={"error": str(exc)},
            )

    def _verify_cose_signature(self, cose_array: list) -> None:
        """Verify the COSE_Sign1 signature using the signing certificate's public key.

        The COSE_Sign1 structure per AWS docs:
          [protected_header, unprotected_header, payload, signature]
        where protected_header = {1: -35} (algorithm: ECDSA 384).
        Raises CallerError if signature verification fails.
        """
        if not self.root_cert_pem:
            return

        try:
            payload_doc = cbor2.loads(cose_array[2])
            cert_der = payload_doc["certificate"]

            cert = load_der_x509_certificate(cert_der)
            pub_numbers = cert.public_key().public_numbers()

            x_bytes = long_to_bytes(pub_numbers.x)
            y_bytes = long_to_bytes(pub_numbers.y)

            # Pad to 48 bytes (P-384 coordinate size)
            x_bytes = x_bytes.rjust(48, b'\x00')
            y_bytes = y_bytes.rjust(48, b'\x00')

            cose_key = EC2Key.from_dict({
                EC2KpCurve: P384,
                EC2KpX: x_bytes,
                EC2KpY: y_bytes,
            })

            # Decode protected header — it's CBOR-encoded bytes in the array
            phdr = cbor2.loads(cose_array[0]) if isinstance(cose_array[0], bytes) else cose_array[0]
            uhdr = cose_array[1] if cose_array[1] else {}

            msg = Sign1Message(
                phdr=phdr,
                uhdr=uhdr,
                payload=cose_array[2],
            )
            msg.signature = cose_array[3]
            msg.key = cose_key

            if not msg.verify_signature():
                raise CallerError(
                    message="COSE Sign1 signature verification failed",
                    phase="attestation",
                )
        except CallerError:
            raise
        except Exception as exc:
            raise CallerError(
                message=f"COSE signature verification error: {exc}",
                phase="attestation",
                details={"error": str(exc)},
            )

    def _validate_pcrs(self, document_pcrs: dict) -> None:
        """Compare expected PCR values against those in the attestation document.

        Raises CallerError if any expected PCR is missing or mismatched.
        """
        if not self.expected_pcrs:
            return

        for index, expected_hex in self.expected_pcrs.items():
            idx = int(index)
            if idx not in document_pcrs or document_pcrs[idx] is None:
                raise CallerError(
                    message=f"PCR index {idx} not found in attestation document",
                    phase="attestation",
                    details={"missing_pcr_index": idx},
                )
            actual_hex = document_pcrs[idx].hex()
            if actual_hex != expected_hex:
                raise CallerError(
                    message=f"PCR {idx} mismatch: expected {expected_hex}, got {actual_hex}",
                    phase="attestation",
                    details={"pcr_index": idx, "expected": expected_hex, "actual": actual_hex},
                )

    def poll_output(self, execution_id: str) -> dict:
        """Poll GET /execution/{id}/output until complete or timeout.

        Logs incremental output during polling.
        Returns final response with stdout, stderr, exit_code,
        output_attestation_document.
        Raises CallerError on timeout or repeated HTTP failures.
        """
        url = f"{self.server_url}/execution/{execution_id}/output"
        start_time = time.monotonic()
        consecutive_errors = 0
        prev_stdout_offset = 0
        prev_stderr_offset = 0
        headers = {}
        if self._oidc_token:
            headers["Authorization"] = f"Bearer {self._oidc_token}"

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= self.max_poll_duration:
                raise CallerError(
                    message=f"Polling timed out after {elapsed:.0f}s (max {self.max_poll_duration}s)",
                    phase="polling",
                    details={"elapsed": elapsed, "max_poll_duration": self.max_poll_duration},
                )

            try:
                response = requests.get(url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                consecutive_errors += 1
                if consecutive_errors >= self.max_retries:
                    raise CallerError(
                        message=f"Polling failed after {consecutive_errors} consecutive errors: {exc}",
                        phase="polling",
                        details={"error": str(exc), "consecutive_errors": consecutive_errors},
                    )
                logger.warning("Poll request error (%d/%d): %s", consecutive_errors, self.max_retries, exc)
                time.sleep(self.poll_interval)
                continue

            if response.status_code == 401:
                raise CallerError(
                    message="Authentication failure: server returned HTTP 401 Unauthorized",
                    phase="polling",
                    details={"status_code": 401, "body": response.text},
                )
            if response.status_code == 403:
                raise CallerError(
                    message="Repository is not authorized: server returned HTTP 403 Forbidden",
                    phase="polling",
                    details={"status_code": 403, "body": response.text},
                )

            if response.status_code != 200:
                consecutive_errors += 1
                if consecutive_errors >= self.max_retries:
                    raise CallerError(
                        message=f"Polling failed with HTTP {response.status_code} after {consecutive_errors} consecutive errors",
                        phase="polling",
                        details={"status_code": response.status_code, "consecutive_errors": consecutive_errors},
                    )
                logger.warning("Poll HTTP error %d (%d/%d)", response.status_code, consecutive_errors, self.max_retries)
                time.sleep(self.poll_interval)
                continue

            # Reset consecutive error counter on success
            consecutive_errors = 0
            data = response.json()

            # Log incremental output
            stdout = data.get("stdout", "")
            stderr = data.get("stderr", "")
            if len(stdout) > prev_stdout_offset:
                logger.info("stdout: %s", stdout[prev_stdout_offset:])
                prev_stdout_offset = len(stdout)
            if len(stderr) > prev_stderr_offset:
                logger.info("stderr: %s", stderr[prev_stderr_offset:])
                prev_stderr_offset = len(stderr)

            if data.get("complete"):
                return {
                    "stdout": data.get("stdout", ""),
                    "stderr": data.get("stderr", ""),
                    "exit_code": data.get("exit_code"),
                    "output_attestation_document": data.get("output_attestation_document"),
                }

            time.sleep(self.poll_interval)

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
        # Decode base64 → CBOR → COSE_Sign1 (tag 18) 4-element array
        try:
            raw_bytes = base64.b64decode(output_attestation_b64)
        except Exception as exc:
            raise CallerError(
                message=f"Failed to base64-decode output attestation document: {exc}",
                phase="output_attestation",
                details={"error": str(exc)},
            )

        cose_array = self._decode_cose_sign1(raw_bytes, phase="output_attestation")

        # CBOR-decode payload to extract attestation fields
        try:
            payload_doc = cbor2.loads(cose_array[2])
        except Exception as exc:
            raise CallerError(
                message=f"Failed to CBOR-decode output attestation payload: {exc}",
                phase="output_attestation",
                details={"error": str(exc)},
            )

        if not isinstance(payload_doc, dict):
            raise CallerError(
                message=f"Output attestation payload is not a map, got {type(payload_doc).__name__}",
                phase="output_attestation",
                details={"type": type(payload_doc).__name__},
            )

        # Validate structural fields
        missing = [f for f in EXPECTED_ATTESTATION_FIELDS if f not in payload_doc]
        if missing:
            raise CallerError(
                message=f"Output attestation document missing fields: {missing}",
                phase="output_attestation",
                details={"missing_fields": missing},
            )

        # Validate certificate chain (PKI) against root cert
        try:
            self._verify_certificate_chain(payload_doc["certificate"], payload_doc["cabundle"])
        except CallerError as exc:
            raise CallerError(
                message=exc.message,
                phase="output_attestation",
                details=exc.details,
            )

        # Verify COSE Sign1 signature
        try:
            self._verify_cose_signature(cose_array)
        except CallerError as exc:
            raise CallerError(
                message=exc.message,
                phase="output_attestation",
                details=exc.details,
            )

        # Validate PCR values
        try:
            self._validate_pcrs(payload_doc["nitrotpm_pcrs"])
        except CallerError as exc:
            raise CallerError(
                message=exc.message,
                phase="output_attestation",
                details=exc.details,
            )

        # Extract user_data from verified payload (SHA-256 hex digest)
        user_data_raw = payload_doc.get("user_data")
        if user_data_raw is None:
            raise CallerError(
                message="Output attestation document missing user_data field",
                phase="output_attestation",
            )

        if isinstance(user_data_raw, bytes):
            attestation_digest = user_data_raw.decode("utf-8")
        else:
            attestation_digest = str(user_data_raw)

        # Reconstruct canonical output and compute SHA-256 hex digest
        canonical_output = f"stdout:{stdout}\nstderr:{stderr}\nexit_code:{exit_code}"
        computed_digest = hashlib.sha256(canonical_output.encode("utf-8")).hexdigest()

        if computed_digest != attestation_digest:
            raise CallerError(
                message="Output integrity verification failed: digest mismatch",
                phase="output_attestation",
                details={"computed": computed_digest, "attestation": attestation_digest},
            )

        logger.info("Output integrity verification succeeded")
        return True

    def _generate_summary(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
        attestation_status: str,
        output_integrity_status: str,
    ) -> str:
        """Generate a GitHub Actions job summary string."""
        lines = [
            "## Remote Executor Results",
            "",
            f"**Exit Code:** {exit_code}",
            f"**Attestation Validation:** {attestation_status}",
            f"**Output Integrity:** {output_integrity_status}",
            "",
            "### stdout",
            "```",
            stdout,
            "```",
            "",
            "### stderr",
            "```",
            stderr,
            "```",
        ]
        return "\n".join(lines)

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
        # Health check
        logger.info("Checking server health...")
        self.health_check()
        logger.info("Server is healthy")

        # Acquire OIDC token
        logger.info("Requesting OIDC token...")
        self.request_oidc_token()
        logger.info("OIDC token acquired")

        # Execute
        logger.info("Submitting execution request...")
        exec_response = self.execute(repository_url, commit_hash, script_path, github_token)
        execution_id = exec_response["execution_id"]
        attestation_b64 = exec_response["attestation_document"]
        logger.info("Execution submitted: %s", execution_id)

        # Validate server identity attestation
        logger.info("Validating server attestation...")
        self.validate_attestation(attestation_b64)
        attestation_status = "pass"
        logger.info("Attestation validation: %s", attestation_status)

        # Poll for output
        logger.info("Polling for execution output...")
        output = self.poll_output(execution_id)
        stdout = output["stdout"]
        stderr = output["stderr"]
        exit_code = output["exit_code"]
        output_attestation_b64 = output.get("output_attestation_document")

        logger.info("stdout: %s", stdout)
        logger.info("stderr: %s", stderr)
        logger.info("exit_code: %s", exit_code)

        # Validate output attestation
        if output_attestation_b64:
            logger.info("Validating output attestation...")
            self.validate_output_attestation(output_attestation_b64, stdout, stderr, exit_code)
            output_integrity_status = "pass"
        else:
            logger.warning("No output attestation document received, skipping output integrity verification")
            output_integrity_status = "skipped"

        logger.info("Output integrity: %s", output_integrity_status)

        # Generate summary
        self.summary = self._generate_summary(
            stdout, stderr, exit_code, attestation_status, output_integrity_status,
        )

        return exit_code


def main():
    """CLI entry point for the Remote Executor Caller."""
    parser = argparse.ArgumentParser(description="GitHub Actions Remote Executor Caller")
    parser.add_argument("--server-url", required=True, help="Base URL of the Remote Executor server")
    parser.add_argument("--script-path", default=".github/scripts/sample-build.sh", help="Path to script in the repository")
    parser.add_argument("--commit-hash", default="", help="Git commit SHA to execute")
    parser.add_argument("--repository-url", default="", help="Git repository URL to execute against")
    parser.add_argument("--github-token", default="", help="GitHub token for authentication")
    parser.add_argument("--root-cert-pem", required=True, help="AWS NitroTPM attestation root CA certificate PEM string")
    parser.add_argument("--expected-pcrs", required=True, help="JSON string mapping PCR index to expected hex value")
    parser.add_argument("--audience", default="", help="Audience value for OIDC token request")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Environment variable overrides for timeout configuration
    timeout = int(os.environ.get("CALLER_HTTP_TIMEOUT", "30"))
    poll_interval = int(os.environ.get("CALLER_POLL_INTERVAL", "5"))
    max_poll_duration = int(os.environ.get("CALLER_MAX_POLL_DURATION", "600"))
    max_retries = int(os.environ.get("CALLER_MAX_RETRIES", "3"))

    # Parse expected PCRs from JSON string
    expected_pcrs = json.loads(args.expected_pcrs)
    # Convert string keys to int keys
    expected_pcrs = {int(k): v for k, v in expected_pcrs.items()}

    caller = RemoteExecutorCaller(
        server_url=args.server_url,
        timeout=timeout,
        poll_interval=poll_interval,
        max_poll_duration=max_poll_duration,
        max_retries=max_retries,
        root_cert_pem=args.root_cert_pem,
        expected_pcrs=expected_pcrs,
        audience=args.audience,
    )

    try:
        exit_code = caller.run(
            repository_url=args.repository_url,
            commit_hash=args.commit_hash,
            script_path=args.script_path,
            github_token=args.github_token,
        )
    except CallerError as exc:
        print(f"ERROR [{exc.phase}]: {exc.message}", file=sys.stderr)
        if exc.details:
            print(f"  Details: {json.dumps(exc.details, default=str)}", file=sys.stderr)
        exit_code = 1

    # Write job summary to $GITHUB_STEP_SUMMARY if set
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path and hasattr(caller, "summary"):
        with open(summary_path, "a") as f:
            f.write(caller.summary)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
