"""NitroTPM attestation document generation"""
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import logging
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Optional, Dict, Any, List

from src.models import AttestationDocument, OutputAttestationResult

try:
    import pynvml
except ImportError:  # pragma: no cover - exercised only when nvidia-ml-py is absent
    pynvml = None


logger = logging.getLogger(__name__)


# Envelope-format version: a plain breaking integer. Bumps on any change to the
# `user_data` envelope shape, its inline fields, or the claims_digest binding
# mechanism itself (D10). A verifier rejects an unknown `v` before attempting
# the binding check.
ENVELOPE_VERSION = 1

# Claims-document schema version: MAJOR.MINOR (D10).
#   MINOR bump -> a new OPTIONAL, safely-ignorable claim field was added.
#                 Old verifiers keep working: they read known fields and
#                 ignore the new one.
#   MAJOR bump -> a field was removed, renamed, re-typed, re-meaned, or a
#                 newly REQUIRED claim was added. Old verifiers must reject
#                 reads rather than silently misinterpret the document.
# The initial value reflects this change's breaking cutover from the old
# inline user_data layout (no dual-format emission).
CLAIMS_SCHEMA_VERSION = "1.0"


@dataclass
class AttestationError:
    """Detailed error information from attestation generation"""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    context: str


def _format_cuda_version(raw_version: int) -> str:
    """Format an NVML CUDA driver version int (e.g. 12040) as 'MAJOR.MINOR'."""
    major = raw_version // 1000
    minor = (raw_version % 1000) // 10
    return f"{major}.{minor}"


def _collect_nvml_devices() -> List[Dict[str, Any]]:
    """
    Enumerate every GPU on the host via NVML.

    NVML is provided by the NVIDIA driver, which is installed at image-build
    time and baked into the sealed erofs root whose dm-verity roothash is
    embedded in the PCR4-measured UKI command line — so these fields are a
    measured-driver self-report (trustworthy to the extent the driver is
    trustworthy), not hardware/firmware attestation.
    """
    if pynvml is None:
        raise RuntimeError("pynvml (nvidia-ml-py) is not installed")

    pynvml.nvmlInit()
    try:
        driver_version = pynvml.nvmlSystemGetDriverVersion()
        cuda_version = _format_cuda_version(pynvml.nvmlSystemGetCudaDriverVersion())
        device_count = pynvml.nvmlDeviceGetCount()

        devices = []
        for index in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            devices.append({
                "uuid": pynvml.nvmlDeviceGetUUID(handle),
                "name": pynvml.nvmlDeviceGetName(handle),
                "driver_version": driver_version,
                "cuda_version": cuda_version,
                "vbios_version": pynvml.nvmlDeviceGetVbiosVersion(handle),
                "compute_capability": f"{major}.{minor}",
                "memory_total_mib": memory.total // (1024 * 1024),
            })
        return devices
    finally:
        pynvml.nvmlShutdown()


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

    @staticmethod
    def _build_security_user_data(
        container_user: Optional[str],
        container_allow_root: Optional[bool],
        container_cap_add: Optional[list],
        no_new_privileges: Optional[bool],
        container_read_only_rootfs: Optional[bool],
        container_tmpfs_size: Optional[str],
        workspace_mount_mode: Optional[str],
        container_network_mode: Optional[str],
        container_tmpfs_exec: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Build the container-security subset of the claims document from resolved config values.

        Each field is included only when provided (not None), so callers that do not
        supply security configuration (e.g. /attest) leave the claims document unchanged.
        Empty but meaningful values are preserved verbatim: an empty container_cap_add ([])
        is distinct from the default set, and an empty container_tmpfs_size ("") means
        no tmpfs — both are attested as-is so any relaxation stays visible (SC-004).
        """
        fields: Dict[str, Any] = {}
        if container_user is not None:
            fields["container_user"] = container_user
        if container_allow_root is not None:
            fields["container_allow_root"] = container_allow_root
        if container_cap_add is not None:
            fields["container_cap_add"] = list(container_cap_add)
        if no_new_privileges is not None:
            fields["no_new_privileges"] = no_new_privileges
        if container_read_only_rootfs is not None:
            fields["container_read_only_rootfs"] = container_read_only_rootfs
        if container_tmpfs_size is not None:
            fields["container_tmpfs_size"] = container_tmpfs_size
        if container_tmpfs_exec is not None:
            fields["container_tmpfs_exec"] = container_tmpfs_exec
        if workspace_mount_mode is not None:
            fields["workspace_mount_mode"] = workspace_mount_mode
        if container_network_mode is not None:
            fields["container_network_mode"] = container_network_mode
        return fields

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

    def _build_gpu_claims(
        self, gpu_devices: Optional[str]
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Build the `gpu` claims block for an enabled GPU posture (D6, D12).

        `gpu_devices` is the resolved `GPU_DEVICES` selection. Only the default
        `"all"` is resolvable to the workload-visible device set with the
        current collector (a real subset filter is unbuilt — dead code under
        `all`, per D12); any other value fails closed rather than emit the
        unfiltered host enumeration, which would over-claim devices the
        workload never saw.

        Returns (gpu_block, None) on success or (None, error_message) on
        failure (NVML unavailable, zero devices enumerated, or an
        unresolvable device selection) — callers must fail the whole
        attestation closed rather than emit `enabled: true` without a
        populated `devices` array.
        """
        visible_devices = gpu_devices if gpu_devices is not None else "all"
        if visible_devices.strip() != "all":
            return None, (
                f"GPU_DEVICES={visible_devices!r} cannot be resolved to the workload-visible "
                f"device set; failing closed rather than emitting the unfiltered host enumeration"
            )
        try:
            devices = _collect_nvml_devices()
        except Exception as e:
            return None, f"NVML GPU enumeration failed: {e}"
        if not devices:
            return None, "NVML enumerated zero GPU devices"
        return {"enabled": True, "visible_devices": visible_devices, "devices": devices}, None

    @staticmethod
    def build_claims_document(kind: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assemble the claims document for `kind` ("execution" or "output").

        `fields` supplies exactly the kind-specific claim body. Envelope-only
        fields (`v`, `claims_digest`, `timestamp`, `execution_id`) are never
        part of `fields` — they live only inline in `user_data`, per the
        strict envelope/claims partition (D3).
        """
        if kind not in ("execution", "output"):
            raise ValueError(f"Unknown claims kind: {kind!r}")
        return {"schema_version": CLAIMS_SCHEMA_VERSION, **fields}

    def _finalize_claims(
        self,
        kind: str,
        claim_fields: Dict[str, Any],
        execution_id: Optional[str],
        timestamp: datetime,
    ) -> tuple[Dict[str, Any], str]:
        """
        Build the claims document, bind it via claims_digest, and assemble the
        compact envelope that goes inline in `user_data` (D1, D2).

        The digest is computed over `claims_bytes` — the exact bytes that are
        base64-encoded and transmitted as `claims_raw` — never a
        re-canonicalized form (D4). This mirrors the shipped
        `server_public_key` fingerprint flow in encryption.py: hash the raw
        serialized bytes, transmit base64, decode-then-hash to verify (D9).
        """
        claims = self.build_claims_document(kind, claim_fields)
        claims_bytes = json.dumps(claims).encode("utf-8")
        claims_digest = "sha256:" + hashlib.sha256(claims_bytes).hexdigest()
        claims_raw = base64.b64encode(claims_bytes).decode("ascii")

        envelope: Dict[str, Any] = {
            "v": ENVELOPE_VERSION,
            "claims_digest": claims_digest,
            "timestamp": timestamp.isoformat(),
        }
        if execution_id is not None:
            envelope["execution_id"] = execution_id
        return envelope, claims_raw

    def generate_attestation(
        self,
        repository_url: Optional[str] = None,
        commit_hash: Optional[str] = None,
        script_path: Optional[str] = None,
        nonce: Optional[str] = None,
        public_key: Optional[bytes] = None,
        script_env: Optional[Dict[str, str]] = None,
        execution_id: Optional[str] = None,
        gpu_enabled: Optional[bool] = None,
        gpu_devices: Optional[str] = None,
        container_user: Optional[str] = None,
        container_allow_root: Optional[bool] = None,
        container_cap_add: Optional[list] = None,
        no_new_privileges: Optional[bool] = None,
        container_read_only_rootfs: Optional[bool] = None,
        container_tmpfs_size: Optional[str] = None,
        container_tmpfs_exec: Optional[bool] = None,
        workspace_mount_mode: Optional[str] = None,
        container_network_mode: Optional[str] = None,
    ) -> tuple[Optional[AttestationDocument], Optional[AttestationError]]:
        """
        Generate an attestation document using NitroTPM attestation.

        This method:
        1. If metadata is provided (repository_url, commit_hash, script_path), builds an
           execution claims document, binds it via `claims_digest`, and passes the compact
           signed envelope `{ v, claims_digest, timestamp, execution_id }` as user_data.
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
                        Used to compute script_env_hash for inclusion in the claims document.
            execution_id: Optional execution ID (UUID v4) to include inline in the envelope.
                          Provided when generating attestation for /execute responses.
            gpu_enabled: Optional boolean indicating whether GPU passthrough is enabled
                         on this server instance. When True, GPU device identity is
                         collected via NVML and included in the claims document's `gpu`
                         block; when False, the block is `{ enabled: false }`.
            gpu_devices: The resolved `GPU_DEVICES` selection (e.g. "all"), recorded as
                         `gpu.visible_devices` when gpu_enabled is True.

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

            claims_raw = None

            # Only create user_data when metadata is provided
            if has_metadata:
                claim_fields: Dict[str, Any] = {
                    "repository_url": repository_url,
                    "commit_hash": commit_hash,
                    "script_path": script_path,
                    "script_env_hash": self._compute_script_env_hash(script_env),
                }
                claim_fields.update(self._build_security_user_data(
                    container_user, container_allow_root, container_cap_add,
                    no_new_privileges, container_read_only_rootfs, container_tmpfs_size,
                    workspace_mount_mode, container_network_mode,
                    container_tmpfs_exec=container_tmpfs_exec,
                ))

                if gpu_enabled is not None:
                    if gpu_enabled:
                        gpu_block, gpu_error = self._build_gpu_claims(gpu_devices)
                        if gpu_error is not None:
                            logger.error(f"GPU claim collection failed: {gpu_error}")
                            return None, AttestationError(
                                command=" ".join(cmd),
                                exit_code=-1,
                                stdout="",
                                stderr="",
                                context=f"GPU claim collection failed: {gpu_error}",
                            )
                    else:
                        gpu_block = {"enabled": False}
                    claim_fields["gpu"] = gpu_block

                envelope, claims_raw = self._finalize_claims(
                    "execution", claim_fields, execution_id, timestamp
                )
                user_data_json = json.dumps(envelope)

                # NitroTPM caps user_data at 1024 bytes. The envelope is fixed-shape
                # (only claims_digest, a fixed-length hash, varies), so this should
                # never trigger — kept as a safety net.
                user_data_size = len(user_data_json.encode("utf-8"))
                if user_data_size > 1024:
                    logger.error(
                        f"Attestation user_data is {user_data_size} bytes, "
                        f"exceeding the NitroTPM 1024-byte limit"
                    )
                    return None, AttestationError(
                        command=" ".join(cmd),
                        exit_code=-1,
                        stdout="",
                        stderr="",
                        context=(
                            f"Attestation user_data is {user_data_size} bytes, "
                            f"exceeding the NitroTPM 1024-byte user_data limit"
                        ),
                    )

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
                claims_raw=claims_raw,
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
        stdout: str,
        stderr: str,
        exit_code: Optional[int],
        nonce: Optional[str] = None,
        execution_id: Optional[str] = None,
        gpu_enabled: Optional[bool] = None,
        gpu_devices: Optional[str] = None,
        container_user: Optional[str] = None,
        container_allow_root: Optional[bool] = None,
        container_cap_add: Optional[list] = None,
        no_new_privileges: Optional[bool] = None,
        container_read_only_rootfs: Optional[bool] = None,
        container_tmpfs_size: Optional[str] = None,
        container_tmpfs_exec: Optional[bool] = None,
        workspace_mount_mode: Optional[str] = None,
        container_network_mode: Optional[str] = None,
    ) -> tuple[Optional[OutputAttestationResult], Optional[str]]:
        """
        Generate an output attestation document for a completed script execution.

        Computes an `output_digest` over the canonical JSON object
        `{ stdout, stderr, exit_code }` (keys sorted, no whitespace, `exit_code`
        as a JSON number) — not a delimiter-glued string — so that two distinct
        `(stdout, stderr, exit_code)` triples can never collide on the same
        preimage (D11). The digest is carried inside the output claims
        document, bound via `claims_digest`, and passed as the same compact
        signed envelope used for execution attestations (D8).

        Args:
            stdout: The captured stdout of the script execution so far
            stderr: The captured stderr of the script execution so far
            exit_code: The exit code, or None if the execution has not finished
            nonce: Optional client-provided nonce for inclusion in attestation
            execution_id: Optional execution ID to include inline in the envelope
            gpu_enabled: Optional boolean indicating whether GPU passthrough is enabled
                         on this server instance. Included in the claims document's `gpu`
                         block when provided.
            gpu_devices: The resolved `GPU_DEVICES` selection (e.g. "all"), recorded as
                         `gpu.visible_devices` when gpu_enabled is True.

        Returns:
            Tuple of (OutputAttestationResult, None) on success or (None, error_message) on failure
        """
        user_data_fd = None
        user_data_path = None
        nonce_fd = None
        nonce_path = None

        try:
            # Canonical JSON preimage (D11): keys sorted, no whitespace, exit_code
            # as a JSON number. This is injective in (stdout, stderr, exit_code),
            # unlike the old delimiter-glued "stdout:...\nstderr:...\nexit_code:..."
            # string, which in-band delimiters embedded in stdout/stderr could forge.
            canonical_output = json.dumps(
                {"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
                sort_keys=True, separators=(',', ':'),
            )
            output_digest = "sha256:" + hashlib.sha256(
                canonical_output.encode("utf-8")
            ).hexdigest()

            logger.info(f"Generating output attestation document (output_digest={output_digest[:23]}...)")

            claim_fields: Dict[str, Any] = {"output_digest": output_digest}
            claim_fields.update(self._build_security_user_data(
                container_user, container_allow_root, container_cap_add,
                no_new_privileges, container_read_only_rootfs, container_tmpfs_size,
                workspace_mount_mode, container_network_mode,
                container_tmpfs_exec=container_tmpfs_exec,
            ))

            if gpu_enabled is not None:
                if gpu_enabled:
                    gpu_block, gpu_error = self._build_gpu_claims(gpu_devices)
                    if gpu_error is not None:
                        logger.error(f"GPU claim collection failed: {gpu_error}")
                        return None, f"GPU claim collection failed: {gpu_error}"
                else:
                    gpu_block = {"enabled": False}
                claim_fields["gpu"] = gpu_block

            timestamp = datetime.now(UTC)
            envelope, claims_raw = self._finalize_claims(
                "output", claim_fields, execution_id, timestamp
            )
            user_data_content = json.dumps(envelope)

            # NitroTPM caps user_data at 1024 bytes. The envelope is fixed-shape
            # (only claims_digest, a fixed-length hash, varies), so this should
            # never trigger — kept as a safety net.
            user_data_size = len(user_data_content.encode("utf-8"))
            if user_data_size > 1024:
                logger.error(
                    f"Output attestation user_data is {user_data_size} bytes, "
                    f"exceeding the NitroTPM 1024-byte limit"
                )
                return None, (
                    f"Output attestation user_data is {user_data_size} bytes, "
                    f"exceeding the NitroTPM 1024-byte user_data limit"
                )

            # Write user_data to temporary file
            user_data_fd, user_data_path = tempfile.mkstemp(
                prefix="output_attestation_user_data_", suffix=".json"
            )
            os.write(user_data_fd, user_data_content.encode("utf-8"))
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
            return OutputAttestationResult(signature=attestation_bytes, claims_raw=claims_raw), None

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
