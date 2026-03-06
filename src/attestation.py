"""AWS Nitro attestation document generation"""
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
    """Generates attestation documents using AWS Nitro Security Module"""
    
    def __init__(self, nsm_device_path: str = "/usr/bin/nitro-tpm-attest"):
        """
        Initialize the attestation generator.
        
        Args:
            nsm_device_path: Path to the nitro-tpm-attest command-line tool
        """
        self.nsm_device_path = nsm_device_path
    
    def verify_nsm_available(self) -> bool:
        """
        Check if NSM device is available.
        
        Returns:
            True if NSM device is available, False otherwise
        """
        return os.path.exists(self.nsm_device_path) and os.access(
            self.nsm_device_path, os.X_OK
        )
    
    def generate_attestation(
        self,
        repository_url: str,
        commit_hash: str,
        script_path: str,
        nonce: Optional[str] = None,
    ) -> tuple[Optional[AttestationDocument], Optional[AttestationError]]:
        """
        Generate an attestation document using AWS Nitro attestation.
        
        This method:
        1. Creates user_data containing execution metadata (repository URL, commit hash, script path, timestamp)
        2. Writes user_data and optional nonce to temporary files
        3. Invokes /usr/bin/nitro-tpm-attest with optional --user-data and --nonce flags
        4. Captures binary CBOR-encoded attestation document from stdout
        5. Implements 30-second timeout for attestation generation
        6. Returns attestation document as bytes or detailed error information
        7. Cleans up temporary files in finally block
        
        Args:
            repository_url: GitHub repository URL
            commit_hash: Git commit SHA
            script_path: Path to script file in repository
            nonce: Optional nonce for inclusion in attestation
        
        Returns:
            Tuple of (AttestationDocument, None) on success or (None, AttestationError) on failure
        """
        user_data_fd = None
        user_data_path = None
        nonce_fd = None
        nonce_path = None
        
        try:
            # Log attestation generation start
            logger.info(f"Generating attestation document for {repository_url}@{commit_hash}")
            
            # Create user_data with execution metadata
            timestamp = datetime.now(UTC)
            user_data = {
                "repository_url": repository_url,
                "commit_hash": commit_hash,
                "script_path": script_path,
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
            
            # Build command
            cmd = [self.nsm_device_path, "--user-data", user_data_path]
            
            # Write nonce to temporary file if provided
            if nonce is not None:
                nonce_fd, nonce_path = tempfile.mkstemp(
                    prefix="attestation_nonce_", suffix=".txt"
                )
                os.write(nonce_fd, nonce.encode("utf-8"))
                os.close(nonce_fd)
                nonce_fd = None  # Mark as closed
                cmd.extend(["--nonce", nonce_path])
            
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
                repository_url=repository_url,
                commit_hash=commit_hash,
                script_path=script_path,
                timestamp=timestamp,
                signature=signature,
            )
            
            return attestation_doc, None
            
        except Exception as e:
            # Handle unexpected errors
            logger.error(f"Unexpected error during attestation generation: {type(e).__name__}: {e}", exc_info=True)
            return None, AttestationError(
                command=self.nsm_device_path,
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
