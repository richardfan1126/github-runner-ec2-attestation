"""NitroTPM attestation document generation"""
import hashlib
import json
import os
import subprocess
import tempfile
import logging
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Optional, Dict, Any

from src.models import AttestationDocument


logger = logging.getLogger(__name__)


@dataclass
class AttestationError:
    """Detailed error information from attestation generation"""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    context: str


class AttestationGenerator:
    """Generates attestation documents using NitroTPM on the Attestable EC2 instance"""
    
    def __init__(self, tpm_attest_path: str = "/usr/bin/nitro-tpm-attest"):
        """
        Initialize the attestation generator.
        
        Args:
            tpm_attest_path: Path to the nitro-tpm-attest command-line tool
        """
        self.tpm_attest_path = tpm_attest_path
    
    def verify_tpm_available(self) -> bool:
        """
        Check if NitroTPM device is available.
        
        Returns:
            True if NitroTPM device is available, False otherwise
        """
        return os.path.exists(self.tpm_attest_path) and os.access(
            self.tpm_attest_path, os.X_OK
        )
    
    def _compute_script_env_hash(self, script_env: Optional[Dict[str, str]]) -> str:
        """
        Compute SHA-256 hex digest of canonicalized script_env.
        
        Canonicalization: sort keys lexicographically, serialize as JSON with
        compact separators (',', ':') (no whitespace).
        When script_env is empty or None, computes SHA-256 of '{}' (empty JSON object).
        
        Args:
            script_env: Dictionary of environment variables, or None
            
        Returns:
            SHA-256 hex digest string
        """
        if not script_env:
            canonical = "{}"
        else:
            canonical = json.dumps(script_env, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def generate_attestation(
        self,
        repository_url: Optional[str] = None,
        commit_hash: Optional[str] = None,
        script_path: Optional[str] = None,
        nonce: Optional[str] = None,
        public_key: Optional[bytes] = None,
        script_env: Optional[Dict[str, str]] = None,
    ) -> tuple[Optional[AttestationDocument], Optional[AttestationError]]:
        """
        Generate an attestation document using NitroTPM attestation.
        
        This method:
        1. If metadata is provided (repository_url, commit_hash, script_path), creates user_data
           containing execution metadata and passes --user-data flag to nitro-tpm-attest
        2. If no metadata is provided (all three are None), skips user_data entirely (no --user-data flag)
        3. Writes optional nonce to temporary file
        4. Invokes /usr/bin/nitro-tpm-attest with optional --user-data, --nonce, and --public-key flags
        5. Captures binary CBOR-encoded attestation document from stdout
        6. Implements 30-second timeout for attestation generation
        7. Returns attestation document as bytes or detailed error information
        8. Cleans up temporary files in finally block
        
        Args:
            repository_url: GitHub repository URL (None when called from /attest)
            commit_hash: Git commit SHA (None when called from /attest)
            script_path: Path to script file in repository (None when called from /attest)
            nonce: Optional nonce for inclusion in attestation
            public_key: Optional public key bytes to include in attestation document.
                        Only provided when generating for the /attest endpoint.
            script_env: Optional dictionary of environment variables passed to the script.
                        Used to compute script_env_hash for inclusion in user_data.
        
        Returns:
            Tuple of (AttestationDocument, None) on success or (None, AttestationError) on failure
        """
        user_data_fd = None
        user_data_path = None
        nonce_fd = None
        nonce_path = None
        public_key_fd = None
        public_key_path = None
        
        try:
            # Determine if metadata is provided (i.e., called from /execute or /output)
            has_metadata = repository_url is not None or commit_hash is not None or script_path is not None
            
            # Log attestation generation start
            if has_metadata:
                logger.info(f"Generating attestation document for {repository_url}@{commit_hash}")
            else:
                logger.info("Generating attestation document for /attest (no user_data)")
            
            timestamp = datetime.now(UTC)
            
            # Build command
            cmd = [self.tpm_attest_path]
            
            # Only create user_data when metadata is provided
            if has_metadata:
                user_data = {
                    "repository_url": repository_url,
                    "commit_hash": commit_hash,
                    "script_path": script_path,
                    "script_env_hash": self._compute_script_env_hash(script_env),
                    "timestamp": timestamp.isoformat(),
                }
                user_data_json = json.dumps(user_data)
                
                # Write user_data to temporary file
                user_data_fd, user_data_path = tempfile.mkstemp(
                    prefix="attestation_user_data_", suffix=".json"
                )
                os.write(user_data_fd, user_data_json.encode("utf-8"))
                os.close(user_data_fd)
                user_data_fd = None  # Mark as closed
                
                cmd.extend(["--user-data", user_data_path])
            
            # Write nonce to temporary file if provided
            if nonce is not None:
                nonce_fd, nonce_path = tempfile.mkstemp(
                    prefix="attestation_nonce_", suffix=".txt"
                )
                os.write(nonce_fd, nonce.encode("utf-8"))
                os.close(nonce_fd)
                nonce_fd = None  # Mark as closed
                cmd.extend(["--nonce", nonce_path])
            
            # Write public_key to temporary file if provided
            if public_key is not None:
                public_key_fd, public_key_path = tempfile.mkstemp(
                    prefix="attestation_public_key_", suffix=".bin"
                )
                os.write(public_key_fd, public_key)
                os.close(public_key_fd)
                public_key_fd = None  # Mark as closed
                cmd.extend(["--public-key", public_key_path])
            
            # Invoke nitro-tpm-attest with timeout
            try:
                logger.debug(f"Invoking nitro-tpm-attest: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except subprocess.TimeoutExpired as e:
                logger.error("Attestation generation timed out after 30 seconds")
                return None, AttestationError(
                    command=" ".join(cmd),
                    exit_code=-1,
                    stdout=e.stdout.decode("utf-8", errors="replace") if e.stdout else "",
                    stderr=e.stderr.decode("utf-8", errors="replace") if e.stderr else "",
                    context="Attestation generation timed out after 30 seconds",
                )
            except OSError as e:
                logger.error(f"OS error while invoking nitro-tpm-attest: {e}")
                return None, AttestationError(
                    command=" ".join(cmd),
                    exit_code=-1,
                    stdout="",
                    stderr=str(e),
                    context=f"OS error while invoking nitro-tpm-attest: {e}",
                )
            
            # Check if command succeeded
            if result.returncode != 0:
                logger.error(f"nitro-tpm-attest failed with exit code {result.returncode}")
                return None, AttestationError(
                    command=" ".join(cmd),
                    exit_code=result.returncode,
                    stdout=result.stdout.decode("utf-8", errors="replace"),
                    stderr=result.stderr.decode("utf-8", errors="replace"),
                    context=f"nitro-tpm-attest failed with exit code {result.returncode}",
                )
            
            # Capture binary CBOR-encoded attestation document from stdout
            signature = result.stdout
            
            logger.info(f"Attestation document generated successfully ({len(signature)} bytes)")
            
            # Create and return attestation document
            attestation_doc = AttestationDocument(
                repository_url=repository_url or "",
                commit_hash=commit_hash or "",
                script_path=script_path or "",
                timestamp=timestamp,
                signature=signature,
            )
            
            return attestation_doc, None
            
        except Exception as e:
            # Handle unexpected errors
            logger.error(f"Unexpected error during attestation generation: {type(e).__name__}: {e}", exc_info=True)
            return None, AttestationError(
                command=self.tpm_attest_path,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                context=f"Unexpected error during attestation generation: {type(e).__name__}: {e}",
            )
        
        finally:
            # Clean up temporary files
            if user_data_fd is not None:
                try:
                    os.close(user_data_fd)
                except OSError:
                    pass
            
            if user_data_path is not None:
                try:
                    os.unlink(user_data_path)
                except OSError:
                    pass
            
            if nonce_fd is not None:
                try:
                    os.close(nonce_fd)
                except OSError:
                    pass
            
            if nonce_path is not None:
                try:
                    os.unlink(nonce_path)
                except OSError:
                    pass
            
            if public_key_fd is not None:
                try:
                    os.close(public_key_fd)
                except OSError:
                    pass
            
            if public_key_path is not None:
                try:
                    os.unlink(public_key_path)
                except OSError:
                    pass

    def generate_output_attestation(
        self,
        script_output: str,
        nonce: Optional[str] = None,
    ) -> tuple[Optional[bytes], Optional[str]]:
        """
        Generate an output attestation document for a completed script execution.

        Computes the SHA-256 digest of the Script_Output and passes the hex-encoded
        digest as user_data to nitro-tpm-attest.

        Args:
            script_output: The canonical Script_Output string (stdout + stderr + exit_code)
            nonce: Optional client-provided nonce for inclusion in attestation

        Returns:
            Tuple of (attestation_bytes, None) on success or (None, error_message) on failure
        """
        user_data_fd = None
        user_data_path = None
        nonce_fd = None
        nonce_path = None

        try:
            # Compute SHA-256 hex digest of the script output
            digest = hashlib.sha256(script_output.encode("utf-8")).hexdigest()

            logger.info(f"Generating output attestation document (digest={digest[:16]}...)")

            # Write hex digest as user_data to temporary file
            user_data_fd, user_data_path = tempfile.mkstemp(
                prefix="output_attestation_user_data_", suffix=".txt"
            )
            os.write(user_data_fd, digest.encode("utf-8"))
            os.close(user_data_fd)
            user_data_fd = None  # Mark as closed

            # Build command
            cmd = [self.tpm_attest_path, "--user-data", user_data_path]

            # Write nonce to temporary file if provided
            if nonce is not None:
                nonce_fd, nonce_path = tempfile.mkstemp(
                    prefix="output_attestation_nonce_", suffix=".txt"
                )
                os.write(nonce_fd, nonce.encode("utf-8"))
                os.close(nonce_fd)
                nonce_fd = None  # Mark as closed
                cmd.extend(["--nonce", nonce_path])

            # Invoke nitro-tpm-attest with timeout
            try:
                logger.debug(f"Invoking nitro-tpm-attest for output attestation: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                logger.error("Output attestation generation timed out after 30 seconds")
                return None, "Output attestation generation timed out after 30 seconds"
            except OSError as e:
                logger.error(f"OS error during output attestation generation: {e}")
                return None, f"OS error during output attestation generation: {e}"

            if result.returncode != 0:
                stderr_text = result.stderr.decode("utf-8", errors="replace")
                logger.error(f"Output attestation failed with exit code {result.returncode}: {stderr_text}")
                return None, f"Output attestation failed with exit code {result.returncode}: {stderr_text}"

            attestation_bytes = result.stdout
            logger.info(f"Output attestation document generated successfully ({len(attestation_bytes)} bytes)")
            return attestation_bytes, None

        except Exception as e:
            logger.error(f"Unexpected error during output attestation generation: {e}", exc_info=True)
            return None, f"Unexpected error during output attestation generation: {e}"

        finally:
            if user_data_fd is not None:
                try:
                    os.close(user_data_fd)
                except OSError:
                    pass
            if user_data_path is not None:
                try:
                    os.unlink(user_data_path)
                except OSError:
                    pass
            if nonce_fd is not None:
                try:
                    os.close(nonce_fd)
                except OSError:
                    pass
            if nonce_path is not None:
                try:
                    os.unlink(nonce_path)
                except OSError:
                    pass
