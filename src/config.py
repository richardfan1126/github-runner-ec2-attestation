"""Configuration management for GitHub Actions Remote Executor"""
import os
import re
from dataclasses import dataclass, field
from typing import Optional


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing"""
    pass


def parse_strict_bool(value: str, key_name: str) -> bool:
    """
    Parse a boolean value from a string with strict validation.
    
    Only accepts: true, 1, yes (case-insensitive) → True
                  false, 0, no (case-insensitive) → False
    
    Args:
        value: The string value to parse
        key_name: The configuration key name (for error messages)
    
    Returns:
        The parsed boolean value
    
    Raises:
        ValueError: If the value is not in the recognized set
    """
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    raise ValueError(
        f"Invalid boolean value for {key_name}: '{value}'. "
        f"Accepted values: true, 1, yes, false, 0, no"
    )


# Closed allow-list of Linux capabilities an operator may grant via CONTAINER_CAP_ADD.
# This is the Docker default-bounding 14-cap set: case-sensitive, upper-case, no "CAP_" prefix.
CONTAINER_CAP_ALLOWLIST = frozenset({
    "CHOWN", "DAC_OVERRIDE", "FSETID", "FOWNER", "MKNOD", "NET_RAW",
    "SETGID", "SETUID", "SETFCAP", "SETPCAP", "NET_BIND_SERVICE",
    "SYS_CHROOT", "KILL", "AUDIT_WRITE",
})

# Default granted capability set when CONTAINER_CAP_ADD is unset (the existing 7-cap
# working set). Applied on top of cap_drop=["ALL"]; order matches the prior hard-coded list.
CONTAINER_DEFAULT_CAP_ADD = [
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "NET_BIND_SERVICE", "KILL",
]

# Accepted enum values and grammars for the container-security settings.
WORKSPACE_MOUNT_MODES = frozenset({"ro", "rw"})
CONTAINER_NETWORK_MODES = frozenset({"none", "bridge", "host"})
# uid:gid — exactly two non-negative integer parts, both present.
_CONTAINER_USER_RE = re.compile(r"^\d+:\d+$")
# Positive integer + required unit b/k/m/g (e.g. "256m"); zero is rejected below.
_TMPFS_SIZE_RE = re.compile(r"^(\d+)([bkmg])$")


@dataclass
class ServerConfig:
    """Server configuration loaded from environment variables"""
    
    # HTTP Server Configuration
    port: int
    
    # Execution Configuration
    max_concurrent_executions: int
    execution_timeout_seconds: int
    max_script_size_bytes: int
    
    # Rate Limiting Configuration
    rate_limit_per_ip: int
    rate_limit_window_seconds: int
    
    # Storage Configuration
    temp_storage_path: str
    output_retention_hours: int
    
    # NitroTPM Configuration
    tpm_attest_path: str
    
    # OIDC Authentication Configuration
    allowed_repositories: list[str]
    expected_audience: str
    
    # Container Execution Configuration
    container_image: str
    container_memory_limit: str
    container_cpu_limit: float
    
    # Output Buffer Size Configuration
    max_output_size_bytes: int = 10_485_760  # 10MB default
    
    # Anti-replay nonce cache TTL (matches OIDC token lifetime)
    nonce_cache_ttl_seconds: int = 300

    # Container Image Digest Pinning
    container_image_digest: Optional[str] = None

    # Container PID Limits (fork bomb protection)
    container_pids_limit: int = 256

    # Output Attestation Rate Limiting
    max_output_attestations_per_window: int = 10
    output_attestation_window_seconds: int = 60

    # NitroTPM Availability Enforcement
    allow_no_tpm: bool = False

    # Request Body Size Limits
    max_request_body_bytes: int = 1_048_576  # 1MB default
    max_encrypted_payload_bytes: int = 524_288  # 512KB default
    max_decrypted_payload_bytes: int = 262_144  # 256KB default

    # Script environment variable deny-list
    # Keys matching this list (exact or prefix for '*' entries) are rejected
    script_env_deny_list: list[str] = field(default_factory=lambda: [
        "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "BASH_FUNC_*",
        "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PROMPT_COMMAND",
        "PS1", "PS2", "PS4", "IFS", "CDPATH", "GLOBIGNORE", "BASH_XTRACEFD",
        "BASH_LOADABLES_PATH",
        # GPU access policy: prevent callers from overriding server-controlled GPU settings
        "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES",
    ])

    # GPU Passthrough Configuration
    enable_gpu: bool = False
    gpu_devices: str = "all"
    nvidia_driver_capabilities: str = "compute,utility"

    # Container Security Configuration (all optional; defaults are the hardened choice)
    container_user: str = "65534:65534"
    container_allow_root: bool = False
    container_cap_add: list[str] | None = None
    no_new_privileges: bool = True
    container_read_only_rootfs: bool = True
    container_tmpfs_size: str = "256m"
    workspace_mount_mode: str = "ro"
    container_network_mode: str = "none"

    # Optional OIDC Branch/Ref Restrictions
    allowed_branches: Optional[list[str]] = None
    require_protected_ref: bool = False
    
    @classmethod
    def from_env(cls) -> "ServerConfig":
        """Load configuration from environment variables"""
        missing_vars = []
        
        # Required environment variables
        port = os.getenv("SERVER_PORT")
        if port is None:
            missing_vars.append("SERVER_PORT")
        
        max_concurrent = os.getenv("MAX_CONCURRENT_EXECUTIONS")
        if max_concurrent is None:
            missing_vars.append("MAX_CONCURRENT_EXECUTIONS")
        
        timeout = os.getenv("EXECUTION_TIMEOUT_SECONDS")
        if timeout is None:
            missing_vars.append("EXECUTION_TIMEOUT_SECONDS")
        
        max_size = os.getenv("MAX_SCRIPT_SIZE_BYTES")
        if max_size is None:
            missing_vars.append("MAX_SCRIPT_SIZE_BYTES")
        
        rate_limit = os.getenv("RATE_LIMIT_PER_IP")
        if rate_limit is None:
            missing_vars.append("RATE_LIMIT_PER_IP")
        
        rate_window = os.getenv("RATE_LIMIT_WINDOW_SECONDS")
        if rate_window is None:
            missing_vars.append("RATE_LIMIT_WINDOW_SECONDS")
        
        temp_path = os.getenv("TEMP_STORAGE_PATH")
        if temp_path is None:
            missing_vars.append("TEMP_STORAGE_PATH")
        
        retention = os.getenv("OUTPUT_RETENTION_HOURS")
        if retention is None:
            missing_vars.append("OUTPUT_RETENTION_HOURS")
        
        tpm_path = os.getenv("TPM_ATTEST_PATH")
        if tpm_path is None:
            missing_vars.append("TPM_ATTEST_PATH")
        
        allowed_repos = os.getenv("ALLOWED_REPOSITORIES")
        if allowed_repos is None:
            missing_vars.append("ALLOWED_REPOSITORIES")
        
        expected_aud = os.getenv("EXPECTED_AUDIENCE")
        if expected_aud is None:
            missing_vars.append("EXPECTED_AUDIENCE")
        
        container_image = os.getenv("CONTAINER_IMAGE")
        if container_image is None:
            missing_vars.append("CONTAINER_IMAGE")
        
        container_memory_limit = os.getenv("CONTAINER_MEMORY_LIMIT")
        if container_memory_limit is None:
            missing_vars.append("CONTAINER_MEMORY_LIMIT")
        
        container_cpu_limit = os.getenv("CONTAINER_CPU_LIMIT")
        if container_cpu_limit is None:
            missing_vars.append("CONTAINER_CPU_LIMIT")
        
        if missing_vars:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )
        
        # Parse optional ALLOWED_BRANCHES (comma-separated, None if not set)
        allowed_branches_raw = os.getenv("ALLOWED_BRANCHES")

        # Parse optional CONTAINER_IMAGE_DIGEST (SHA-256 digest, None if not set)
        container_image_digest = os.getenv("CONTAINER_IMAGE_DIGEST") or None

        # Parse optional MAX_CONTAINER_PIDS (default 256, must be positive integer)
        container_pids_limit_raw = os.getenv("MAX_CONTAINER_PIDS")
        container_pids_limit = 256
        if container_pids_limit_raw is not None:
            try:
                container_pids_limit = int(container_pids_limit_raw)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Invalid MAX_CONTAINER_PIDS value: '{container_pids_limit_raw}' (must be a positive integer)"
                )
            if container_pids_limit <= 0:
                raise ValueError(
                    f"Invalid MAX_CONTAINER_PIDS value: {container_pids_limit} (must be a positive integer)"
                )
        # Parse optional MAX_OUTPUT_ATTESTATIONS_PER_WINDOW (default 10, must be positive integer)
        max_output_attestations_per_window_raw = os.getenv("MAX_OUTPUT_ATTESTATIONS_PER_WINDOW")
        max_output_attestations_per_window = 10
        if max_output_attestations_per_window_raw is not None:
            try:
                max_output_attestations_per_window = int(max_output_attestations_per_window_raw)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Invalid MAX_OUTPUT_ATTESTATIONS_PER_WINDOW value: '{max_output_attestations_per_window_raw}' (must be a positive integer)"
                )
            if max_output_attestations_per_window <= 0:
                raise ValueError(
                    f"Invalid MAX_OUTPUT_ATTESTATIONS_PER_WINDOW value: {max_output_attestations_per_window} (must be a positive integer)"
                )

        # Parse optional OUTPUT_ATTESTATION_WINDOW_SECONDS (default 60, must be positive integer)
        output_attestation_window_seconds_raw = os.getenv("OUTPUT_ATTESTATION_WINDOW_SECONDS")
        output_attestation_window_seconds = 60
        if output_attestation_window_seconds_raw is not None:
            try:
                output_attestation_window_seconds = int(output_attestation_window_seconds_raw)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Invalid OUTPUT_ATTESTATION_WINDOW_SECONDS value: '{output_attestation_window_seconds_raw}' (must be a positive integer)"
                )
            if output_attestation_window_seconds <= 0:
                raise ValueError(
                    f"Invalid OUTPUT_ATTESTATION_WINDOW_SECONDS value: {output_attestation_window_seconds} (must be a positive integer)"
                )

        allowed_branches = None
        if allowed_branches_raw is not None:
            allowed_branches = [b.strip() for b in allowed_branches_raw.split(",") if b.strip()]
            if not allowed_branches:
                allowed_branches = None
        
        # Parse optional REQUIRE_PROTECTED_REF (boolean, default False)
        require_protected_ref_raw = os.getenv("REQUIRE_PROTECTED_REF", "false")
        require_protected_ref = parse_strict_bool(require_protected_ref_raw, "REQUIRE_PROTECTED_REF")

        # Parse optional ALLOW_NO_TPM (boolean, default False)
        allow_no_tpm_raw = os.getenv("ALLOW_NO_TPM", "false")
        allow_no_tpm = parse_strict_bool(allow_no_tpm_raw, "ALLOW_NO_TPM")

        # Parse optional ENABLE_GPU (boolean, default False)
        enable_gpu_raw = os.getenv("ENABLE_GPU", "false")
        enable_gpu = parse_strict_bool(enable_gpu_raw, "ENABLE_GPU")

        # Parse optional GPU_DEVICES (default "all")
        gpu_devices = os.getenv("GPU_DEVICES", "all")

        # Parse optional NVIDIA_DRIVER_CAPABILITIES (default "compute,utility")
        nvidia_driver_capabilities = os.getenv("NVIDIA_DRIVER_CAPABILITIES", "compute,utility")

        # --- Container security configuration (eight optional vars, hardened defaults) ---
        # Raw strings for CONTAINER_USER / WORKSPACE_MOUNT_MODE / CONTAINER_NETWORK_MODE
        # are validated in validate(); the three booleans parse strictly here.
        container_user = os.getenv("CONTAINER_USER", "65534:65534")

        container_allow_root_raw = os.getenv("CONTAINER_ALLOW_ROOT", "false")
        container_allow_root = parse_strict_bool(container_allow_root_raw, "CONTAINER_ALLOW_ROOT")

        # CONTAINER_CAP_ADD: unset -> None (default 7-cap set applied later);
        # empty string -> [] (no caps added on top of drop ALL); else comma-separated names.
        container_cap_add_raw = os.getenv("CONTAINER_CAP_ADD")
        container_cap_add = None
        if container_cap_add_raw is not None:
            container_cap_add = [c.strip() for c in container_cap_add_raw.split(",") if c.strip()]

        no_new_privileges_raw = os.getenv("NO_NEW_PRIVILEGES", "true")
        no_new_privileges = parse_strict_bool(no_new_privileges_raw, "NO_NEW_PRIVILEGES")

        container_read_only_rootfs_raw = os.getenv("CONTAINER_READ_ONLY_ROOTFS", "true")
        container_read_only_rootfs = parse_strict_bool(
            container_read_only_rootfs_raw, "CONTAINER_READ_ONLY_ROOTFS"
        )

        # CONTAINER_TMPFS_SIZE: empty string preserved as "no tmpfs"; validated when non-empty.
        container_tmpfs_size = os.getenv("CONTAINER_TMPFS_SIZE", "256m")

        workspace_mount_mode = os.getenv("WORKSPACE_MOUNT_MODE", "ro")

        container_network_mode = os.getenv("CONTAINER_NETWORK_MODE", "none")

        # Parse optional MAX_OUTPUT_SIZE_BYTES (default 10MB)
        max_output_size_bytes_raw = os.getenv("MAX_OUTPUT_SIZE_BYTES")
        max_output_size_bytes = 10_485_760  # 10MB default
        if max_output_size_bytes_raw is not None:
            max_output_size_bytes = int(max_output_size_bytes_raw)
        
        # Parse optional NONCE_CACHE_TTL_SECONDS (default 300s = 5 min)
        nonce_cache_ttl_seconds_raw = os.getenv("NONCE_CACHE_TTL_SECONDS")
        nonce_cache_ttl_seconds = 300
        if nonce_cache_ttl_seconds_raw is not None:
            nonce_cache_ttl_seconds = int(nonce_cache_ttl_seconds_raw)
        
        # Parse optional request body size limits
        max_request_body_bytes_raw = os.getenv("MAX_REQUEST_BODY_BYTES")
        max_request_body_bytes = 1_048_576  # 1MB default
        if max_request_body_bytes_raw is not None:
            max_request_body_bytes = int(max_request_body_bytes_raw)
        
        max_encrypted_payload_bytes_raw = os.getenv("MAX_ENCRYPTED_PAYLOAD_BYTES")
        max_encrypted_payload_bytes = 524_288  # 512KB default
        if max_encrypted_payload_bytes_raw is not None:
            max_encrypted_payload_bytes = int(max_encrypted_payload_bytes_raw)
        
        max_decrypted_payload_bytes_raw = os.getenv("MAX_DECRYPTED_PAYLOAD_BYTES")
        max_decrypted_payload_bytes = 262_144  # 256KB default
        if max_decrypted_payload_bytes_raw is not None:
            max_decrypted_payload_bytes = int(max_decrypted_payload_bytes_raw)
        
        # Parse optional SCRIPT_ENV_DENY_LIST (comma-separated, uses default if not set)
        script_env_deny_list_raw = os.getenv("SCRIPT_ENV_DENY_LIST")
        script_env_deny_list = None  # Will use dataclass default
        if script_env_deny_list_raw is not None:
            script_env_deny_list = [e.strip() for e in script_env_deny_list_raw.split(",") if e.strip()]
        
        return cls(
            port=int(port),
            max_concurrent_executions=int(max_concurrent),
            execution_timeout_seconds=int(timeout),
            max_script_size_bytes=int(max_size),
            rate_limit_per_ip=int(rate_limit),
            rate_limit_window_seconds=int(rate_window),
            temp_storage_path=temp_path,
            output_retention_hours=int(retention),
            tpm_attest_path=tpm_path,
            allowed_repositories=[r.strip() for r in allowed_repos.split(",") if r.strip()],
            expected_audience=expected_aud,
            container_image=container_image,
            container_memory_limit=container_memory_limit,
            container_cpu_limit=float(container_cpu_limit),
            container_image_digest=container_image_digest,
            container_pids_limit=container_pids_limit,
            max_output_attestations_per_window=max_output_attestations_per_window,
            output_attestation_window_seconds=output_attestation_window_seconds,
            allow_no_tpm=allow_no_tpm,
            enable_gpu=enable_gpu,
            gpu_devices=gpu_devices,
            nvidia_driver_capabilities=nvidia_driver_capabilities,
            container_user=container_user,
            container_allow_root=container_allow_root,
            container_cap_add=container_cap_add,
            no_new_privileges=no_new_privileges,
            container_read_only_rootfs=container_read_only_rootfs,
            container_tmpfs_size=container_tmpfs_size,
            workspace_mount_mode=workspace_mount_mode,
            container_network_mode=container_network_mode,
            allowed_branches=allowed_branches,
            require_protected_ref=require_protected_ref,
            max_output_size_bytes=max_output_size_bytes,
            nonce_cache_ttl_seconds=nonce_cache_ttl_seconds,
            max_request_body_bytes=max_request_body_bytes,
            max_encrypted_payload_bytes=max_encrypted_payload_bytes,
            max_decrypted_payload_bytes=max_decrypted_payload_bytes,
            **({"script_env_deny_list": script_env_deny_list} if script_env_deny_list is not None else {}),
        )
    
    def validate(self) -> None:
        """Validate configuration values"""
        errors = []
        
        if self.port < 1 or self.port > 65535:
            errors.append(f"Invalid port: {self.port} (must be 1-65535)")
        
        if self.max_concurrent_executions < 1:
            errors.append(
                f"Invalid max_concurrent_executions: {self.max_concurrent_executions} (must be >= 1)"
            )
        
        if self.execution_timeout_seconds < 1:
            errors.append(
                f"Invalid execution_timeout_seconds: {self.execution_timeout_seconds} (must be >= 1)"
            )
        
        if self.max_script_size_bytes < 1:
            errors.append(
                f"Invalid max_script_size_bytes: {self.max_script_size_bytes} (must be >= 1)"
            )
        
        if self.rate_limit_per_ip < 1:
            errors.append(
                f"Invalid rate_limit_per_ip: {self.rate_limit_per_ip} (must be >= 1)"
            )
        
        if self.rate_limit_window_seconds < 1:
            errors.append(
                f"Invalid rate_limit_window_seconds: {self.rate_limit_window_seconds} (must be >= 1)"
            )
        
        if not self.temp_storage_path:
            errors.append("temp_storage_path cannot be empty")
        
        if self.output_retention_hours < 1:
            errors.append(
                f"Invalid output_retention_hours: {self.output_retention_hours} (must be >= 1)"
            )
        
        if not self.tpm_attest_path:
            errors.append("tpm_attest_path cannot be empty")
        
        if not self.allowed_repositories:
            errors.append("allowed_repositories must be a non-empty list")
        
        if not self.expected_audience:
            errors.append("expected_audience cannot be empty")
        
        if not self.container_image:
            errors.append("container_image cannot be empty")
        
        if not self.container_memory_limit:
            errors.append("container_memory_limit cannot be empty")
        
        if self.container_cpu_limit <= 0:
            errors.append(
                f"Invalid container_cpu_limit: {self.container_cpu_limit} (must be > 0)"
            )
        
        if self.container_pids_limit < 1:
            errors.append(
                f"Invalid container_pids_limit: {self.container_pids_limit} (must be >= 1)"
            )
        
        if self.max_output_size_bytes < 1:
            errors.append(
                f"Invalid max_output_size_bytes: {self.max_output_size_bytes} (must be >= 1)"
            )
        
        if self.nonce_cache_ttl_seconds < 1:
            errors.append(
                f"Invalid nonce_cache_ttl_seconds: {self.nonce_cache_ttl_seconds} (must be >= 1)"
            )
        
        # Container image digest validation (Requirements 34.7, 34.8)
        # If digest is not explicitly set, try to extract from image reference
        if self.container_image_digest is None and self.container_image and "@sha256:" in self.container_image:
            # Extract digest from image reference (e.g., "ubuntu:24.04@sha256:abc123..." -> "sha256:abc123...")
            digest_part = self.container_image.split("@sha256:", 1)[1]
            self.container_image_digest = f"sha256:{digest_part}"
        
        # After potential extraction, digest must be set
        if self.container_image_digest is None:
            errors.append(
                "container_image_digest is required: set CONTAINER_IMAGE_DIGEST or use a "
                "digest-pinned image reference (e.g., image@sha256:...)"
            )
        
        # GPU configuration validation
        if self.enable_gpu and not self.gpu_devices.strip():
            errors.append(
                "gpu_devices cannot be empty when enable_gpu is true"
            )

        # --- Container security configuration validation (FR-013 – FR-020) ---
        # Enum: workspace mount mode
        if self.workspace_mount_mode not in WORKSPACE_MOUNT_MODES:
            errors.append(
                f"Invalid WORKSPACE_MOUNT_MODE value: '{self.workspace_mount_mode}'. "
                f"Accepted values: ro, rw"
            )

        # Enum: container network mode
        if self.container_network_mode not in CONTAINER_NETWORK_MODES:
            errors.append(
                f"Invalid CONTAINER_NETWORK_MODE value: '{self.container_network_mode}'. "
                f"Accepted values: none, bridge, host"
            )

        # Format: container_user must be 'uid:gid', both non-negative integers, both present.
        resolved_uid = None
        if _CONTAINER_USER_RE.match(self.container_user):
            resolved_uid = int(self.container_user.split(":", 1)[0])
        else:
            errors.append(
                f"Invalid CONTAINER_USER value: '{self.container_user}'. "
                f"Expected format 'uid:gid' with both non-negative integers (e.g. '65534:65534')"
            )

        # Capability allow-list: every requested cap must be in the 14-cap set (case-sensitive).
        # None means "use the default set" and is not validated here.
        if self.container_cap_add is not None:
            invalid_caps = [c for c in self.container_cap_add if c not in CONTAINER_CAP_ALLOWLIST]
            if invalid_caps:
                allowed = ", ".join(sorted(CONTAINER_CAP_ALLOWLIST))
                offenders = ", ".join(repr(c) for c in invalid_caps)
                errors.append(
                    f"Invalid CONTAINER_CAP_ADD value(s): {offenders}. "
                    f"Allowed capabilities (case-sensitive, no CAP_ prefix): {allowed}"
                )

        # Size grammar: tmpfs size when non-empty (empty = no tmpfs, which is valid).
        if self.container_tmpfs_size:
            m = _TMPFS_SIZE_RE.match(self.container_tmpfs_size)
            if not m or int(m.group(1)) < 1:
                errors.append(
                    f"Invalid CONTAINER_TMPFS_SIZE value: '{self.container_tmpfs_size}'. "
                    f"Expected a positive integer with a unit b/k/m/g (e.g. '256m'), "
                    f"or empty for no tmpfs"
                )

        # Cross-field root-user gate: running as root requires an explicit opt-in.
        if resolved_uid == 0 and not self.container_allow_root:
            errors.append(
                "CONTAINER_USER resolves to root (uid 0) but CONTAINER_ALLOW_ROOT is false. "
                "Set CONTAINER_ALLOW_ROOT=true to permit running as root."
            )

        if errors:
            raise ValueError(f"Configuration validation failed: {'; '.join(errors)}")


def load_config() -> ServerConfig:
    """
    Load and validate server configuration from environment variables.
    
    Returns:
        Validated ServerConfig instance
    
    Raises:
        ConfigurationError: If required configuration is missing or invalid
    """
    try:
        config = ServerConfig.from_env()
        config.validate()
        return config
    except ValueError as e:
        raise ConfigurationError(str(e)) from e
