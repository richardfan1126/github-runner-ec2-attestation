#!/usr/bin/env python3
"""
AMI Build Script

Use an EC2 instance to pull pre-built KIWI image from GitHub Container Registry,
verify its signature, and transform it into an AMI.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import logging
import time
from typing import Any, Optional, Tuple

import boto3
import paramiko
from botocore.exceptions import ClientError, WaiterError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('build_ami.log')
    ]
)
logger = logging.getLogger(__name__)

# Generous wall-clock backstop for a single remote command (D6). Sized well above
# the longest legitimate command — the ~5 min mostly-silent coldsnap compile and a
# 10–15 min 8 GB `coldsnap upload` — so it never false-aborts slow-but-progressing
# work. It is only the last-resort catch for a truly wedged channel; transport
# liveness (below) is the *primary*, fast death detector.
DEFAULT_COMMAND_TIMEOUT = 2700  # 45 minutes

# Bounded SSH reconnect budget on a transport/timeout error (D12). The reconnect
# outcome is the host-alive-vs-dead discriminator.
RECONNECT_MAX_ATTEMPTS = 5
RECONNECT_DELAY = 30

# Per-step toolchain-install retry budget for *transient* failures only (D10).
INSTALL_MAX_ATTEMPTS = 3
INSTALL_BASE_DELAY = 10


class RemoteCommandTimeout(Exception):
    """Raised when a remote command exceeds the wall-clock backstop (D6).

    Treated by the driver as a transport/timeout signal (host suspect), triggering
    the bounded reconnect path (D12) — never as a flavor-local application error.
    """


class TransportError(Exception):
    """Raised when the SSH transport dies mid-command (D6).

    Distinct from a non-zero command exit (which means the host is healthy but the
    command failed). Treated by the driver as host-suspect (D7/D12).
    """


class TransientInstallError(RuntimeError):
    """A toolchain-install failure that is worth retrying (D10).

    Raised for network-dominated steps (download/clone/dnf) where a blip is
    plausibly transient. Deterministic failures (GPG/checksum mismatch, compile
    error, upstream 404) raise plain ``RuntimeError`` and fail fast without retry.
    """


def validate_artifact_reference(artifact_ref: str) -> None:
    """
    Validate artifact reference format against a strict allowlist pattern.

    Expected format: ghcr.io/owner/repo/package:tag@sha256:<hex64> or
    ghcr.io/owner/repo/package@sha256:<hex64>. A digest-pinned reference
    (@sha256:<64 hex chars>) is REQUIRED. Each path segment and optional tag
    contain only alphanumeric characters, dots, hyphens, and underscores.
    At least two path segments (owner/repo) are required; an optional third
    segment (package name) is also supported.

    If both a tag and digest are present, the digest is authoritative for
    verification and pull operations.

    Args:
        artifact_ref: GitHub Container Registry artifact reference

    Raises:
        ValueError: If artifact reference format is invalid, contains
                    characters outside the allowlist, or is missing a
                    digest-pinned reference

    Requirements: 15.17, 15.18, 15.19, 15.20, 15.21
    """
    # Require @sha256:<hex64> digest pin
    if '@sha256:' not in artifact_ref:
        raise ValueError(
            f"Artifact reference must be digest-pinned: {artifact_ref}. "
            "Expected @sha256:<64 hex chars> in the reference. "
            "Tag-only references are not accepted — use a digest-pinned reference "
            "(e.g., ghcr.io/owner/repo/package:tag@sha256:abc123...)."
        )

    # Pattern: ghcr.io/owner/repo[/package...][:tag]@sha256:<64 hex chars>
    pattern = (
        r'^ghcr\.io/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+)*'
        r'(?::[a-zA-Z0-9._-]+)?'
        r'@sha256:[0-9a-fA-F]{64}$'
    )
    if not re.match(pattern, artifact_ref):
        raise ValueError(
            f"Invalid artifact reference format: {artifact_ref}. "
            "Expected format: ghcr.io/owner/repo/package[:tag]@sha256:<64 hex chars> "
            "(only alphanumeric, dots, hyphens, and underscores allowed in path/tag)"
        )

    logger.info(f"Artifact reference validated: {artifact_ref}")


def extract_digest_from_artifact_ref(artifact_ref: str) -> str:
    """
    Extract the sha256 digest from a digest-pinned artifact reference.

    Args:
        artifact_ref: Validated artifact reference containing @sha256:<hex64>

    Returns:
        The full digest string including prefix, e.g. "sha256:abcdef..."

    Raises:
        ValueError: If no digest found in the reference
    """
    if '@sha256:' not in artifact_ref:
        raise ValueError(f"No digest found in artifact reference: {artifact_ref}")
    digest = artifact_ref.split('@', 1)[1]  # "sha256:<hex64>"
    return digest


def get_digest_pinned_ref(artifact_ref: str) -> str:
    """
    Get the digest-only reference (without tag) for pull/verify operations.

    Given ghcr.io/owner/repo/pkg:tag@sha256:abc..., returns
    ghcr.io/owner/repo/pkg@sha256:abc... (tag stripped, digest retained).

    Args:
        artifact_ref: Validated artifact reference

    Returns:
        Reference using only the digest (no tag)
    """
    # Split at @sha256: to get the base and digest
    base, digest = artifact_ref.split('@', 1)
    # Strip the tag from the base if present
    if ':' in base.split('/')[-1]:
        # There's a tag — remove it
        base = base.rsplit(':', 1)[0]
    return f"{base}@{digest}"


def validate_aws_region(region: str) -> None:
    """
    Validate AWS region format.

    Args:
        region: AWS region name

    Raises:
        ValueError: If region format is invalid
    """
    # Basic validation - AWS regions follow pattern: us-east-1, eu-west-2, etc.
    import re
    region_pattern = r'^[a-z]{2}-[a-z]+-\d+$'

    if not re.match(region_pattern, region):
        raise ValueError(
            f"Invalid AWS region format: {region}. "
            "Expected format: us-east-1, eu-west-2, etc."
        )

    logger.info(f"AWS region validated: {region}")


def validate_run_id(run_id: str) -> None:
    """
    Validate the run identifier format.

    Expected value is ``${github.run_id}-${github.run_attempt}`` — two integers
    joined by a hyphen. It is interpolated into a remote shell command (the
    ``coldsnap upload --tag Key=run_id,Value=<run_id>`` invocation, D13) and used as an
    EBS-snapshot tag value, so it is restricted to a strict, shell-safe pattern.

    Args:
        run_id: Run identifier

    Raises:
        ValueError: If the run id format is invalid
    """
    if not re.match(r'^[0-9]+-[0-9]+$', run_id):
        raise ValueError(
            f"Invalid run id format: {run_id}. "
            "Expected ${github.run_id}-${github.run_attempt} (digits-digits)."
        )
    logger.info(f"Run id validated: {run_id}")


def validate_output_file_path(output_file: str) -> None:
    """
    Validate output file path.

    Args:
        output_file: Path to output file

    Raises:
        ValueError: If output file path is invalid
    """
    # Check if parent directory exists or can be created
    output_path = Path(output_file)
    parent_dir = output_path.parent

    if parent_dir != Path('.') and not parent_dir.exists():
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created output directory: {parent_dir}")
        except Exception as e:
            raise ValueError(
                f"Cannot create output directory {parent_dir}: {e}"
            )

    # Check if we can write to the location
    if output_path.exists() and not os.access(output_path, os.W_OK):
        raise ValueError(
            f"Output file is not writable: {output_file}"
        )

    logger.info(f"Output file path validated: {output_file}")

def verify_ssh_connectivity(
    host: str,
    username: str,
    key_filename: str,
    max_attempts: int = 10,
    delay: int = 30
) -> paramiko.SSHClient:
    """
    Verify SSH connectivity to the instance
    
    Args:
        host: Instance public IP address
        username: SSH username (ec2-user for AL2023)
        key_filename: Path to SSH private key file
        max_attempts: Maximum number of connection attempts
        delay: Delay between attempts in seconds
    
    Returns:
        Connected paramiko SSHClient
    """
    logger.info(f"Verifying SSH connectivity to {host}...")
    
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"SSH connection attempt {attempt}/{max_attempts}")
            ssh_client.connect(
                hostname=host,
                username=username,
                key_filename=key_filename,
                timeout=10,
                banner_timeout=10
            )
            # Enable keepalive to prevent connection timeouts during long operations
            ssh_client.get_transport().set_keepalive(30)
            logger.info("SSH connection established successfully")
            return ssh_client
        except (paramiko.SSHException, OSError) as e:
            if attempt < max_attempts:
                logger.warning(f"SSH connection failed: {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"Failed to establish SSH connection after {max_attempts} attempts")
                raise
    
    raise RuntimeError("Failed to establish SSH connection")

def execute_remote_command(
    ssh_client: paramiko.SSHClient,
    command: str,
    stream_output: bool = True,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
) -> tuple[int, str, str]:
    """
    Execute a command on the remote instance via SSH.

    Hardened against a build instance that dies mid-command (D6). The old loop
    spun `while not exit_status_ready(): time.sleep(0.1)` with no timeout and no
    channel-liveness check — paramiko does not raise when the host dies, so the
    channel simply never reports ready and the loop spins forever. Under the old
    per-flavor matrix that only wedged one leg (killed by the job timeout); on the
    shared single instance a hang during flavor 1's upload would mean flavors 2 and
    3 never start and the per-flavor `try/except` never runs. Two guards fix that:

    - **Transport liveness (primary, fast).** Each idle poll checks
      `transport.is_active()`. Keepalive (`set_keepalive(30)`, set at connect time)
      carries the connection through the mostly-silent compile stretch AND causes
      paramiko to mark the transport inactive within a couple of missed keepalive
      acks (~60–90 s) when the host genuinely dies — so a dead host raises promptly
      via this check, not via the slow wall-clock. Keepalive is a *dependency* of
      this check, not a substitute (paramiko does not raise out of this read-loop on
      keepalive failure by itself — surfacing it is exactly this function's job).
    - **Wall-clock backstop (generous).** A comfortably-large upper bound
      (`DEFAULT_COMMAND_TIMEOUT`) catches a truly wedged-but-"active" channel
      without false-aborting a legitimately long, slow-but-progressing command.

    Args:
        ssh_client: Connected paramiko SSHClient
        command: Command to execute
        stream_output: Whether to stream output to logger
        timeout: Wall-clock backstop in seconds for this single command

    Returns:
        Tuple of (exit_code, stdout, stderr)

    Raises:
        TransportError: If the SSH transport dies during the command (host suspect)
        RemoteCommandTimeout: If the wall-clock backstop is exceeded
    """
    logger.debug(f"Executing command: {command}")

    transport = ssh_client.get_transport()
    if transport is None or not transport.is_active():
        raise TransportError("SSH transport is not active before command execution")

    stdin, stdout, stderr = ssh_client.exec_command(command, get_pty=False)

    stdout_lines = []
    stderr_lines = []

    # Set channels to non-blocking to avoid deadlock
    stdout.channel.setblocking(0)
    stderr.channel.setblocking(0)

    start_time = time.time()

    def _drain_stdout() -> bool:
        if stdout.channel.recv_ready():
            data = stdout.channel.recv(4096).decode('utf-8', errors='replace')
            for line in data.splitlines():
                line = line.rstrip()
                if line:
                    stdout_lines.append(line)
                    if stream_output:
                        logger.info(f"  {line}")
            return True
        return False

    def _drain_stderr() -> bool:
        if stderr.channel.recv_stderr_ready():
            data = stderr.channel.recv_stderr(4096).decode('utf-8', errors='replace')
            for line in data.splitlines():
                line = line.rstrip()
                if line:
                    stderr_lines.append(line)
                    if stream_output:
                        logger.warning(f"  {line}")
            return True
        return False

    # Read stdout and stderr concurrently to avoid buffer deadlock
    while not stdout.channel.exit_status_ready():
        got_data = _drain_stdout()
        got_data = _drain_stderr() or got_data

        if not got_data:
            # No data this poll — apply the liveness/backstop guards before sleeping.
            # Primary signal: is the transport still up? A dead host trips this within
            # ~60–90 s via keepalive, long before the wall-clock backstop.
            if not transport.is_active():
                raise TransportError(
                    "SSH transport died during remote command (host suspect)"
                )
            # Generous wall-clock backstop for a wedged-but-active channel.
            if time.time() - start_time > timeout:
                raise RemoteCommandTimeout(
                    f"Remote command exceeded {timeout}s wall-clock backstop; "
                    "assuming a wedged channel"
                )
            time.sleep(0.1)

    # Read any remaining data after command completes
    while _drain_stdout():
        pass
    while _drain_stderr():
        pass

    exit_code = stdout.channel.recv_exit_status()

    return exit_code, '\n'.join(stdout_lines), '\n'.join(stderr_lines)

def install_system_dependencies(ssh_client: paramiko.SSHClient) -> None:
    """
    Install system dependencies (git and gcc) on the instance via SSH.
    
    Requirements: 16.1
    
    Args:
        ssh_client: Connected paramiko SSHClient
        
    Raises:
        RuntimeError: If installation fails
    """
    logger.info("Installing system dependencies (git, gcc)...")
    
    # Install git and gcc via dnf package manager. A dnf failure here is
    # network-dominated (mirror hiccup) — treat as transient/retriable (D10).
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        "sudo dnf install -y git gcc",
        stream_output=True
    )
    if exit_code != 0:
        raise TransientInstallError(f"Failed to install system packages: {stderr}")

    logger.info("  ✓ git and gcc installed")

def install_rust(ssh_client: paramiko.SSHClient) -> None:
    """
    Install Rust toolchain on the instance via SSH.
    
    Downloads standalone Rust tarball, verifies GPG signature, and installs.
    Installation path: /home/ec2-user/.cargo/bin/
    
    Requirements: 16.2, 17.17
    
    Args:
        ssh_client: Connected paramiko SSHClient
        
    Raises:
        RuntimeError: If installation or GPG verification fails
    """
    logger.info("Installing Rust toolchain...")
    
    # Trust assumption: The signing key 85AB96E6FA1BE5FE is the official Rust project
    # release signing key. Verify it against https://www.rust-lang.org/tools/install
    # before use. (Requirement: 17.17)
    
    # Why standalone tarball: Standalone Rust installer tarballs (.tar.gz) are GPG-signed
    # with .asc files per https://forge.rust-lang.org/infra/other-installation-methods.html#standalone-installers
    # rustup-init binaries only have SHA-256 checksums, not GPG signatures. (Requirement: 17.17)
    
    # Step 1: Import the official Rust project GPG signing key
    # Fetch the key directly from the Rust project's own infrastructure via HTTPS
    # to avoid requiring dirmngr (keyserver daemon).
    # --batch and --no-tty suppress gpg-agent interaction errors that occur in
    # non-interactive SSH sessions where /dev/tty and gpg-agent are unavailable.
    # We verify success by checking stderr for the "imported" confirmation rather
    # than relying on the exit code, which may be non-zero due to agent errors
    # even when the key import itself succeeded.
    logger.info("  Importing Rust project GPG signing key...")
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        'curl --proto "=https" --tlsv1.2 -sSf "https://static.rust-lang.org/rust-key.gpg.ascii" | gpg --batch --no-tty --import',
        stream_output=True
    )
    if 'key 85AB96E6FA1BE5FE' not in stderr or ('imported' not in stderr and 'not changed' not in stderr):
        # Fetched over the network via curl | gpg — a failure is plausibly a
        # transient blip rather than a rotated key, so allow a retry (D10).
        raise TransientInstallError(f"Failed to import Rust GPG signing key: {stderr}")

    # Step 2: Download the standalone tarball
    logger.info("  Downloading Rust standalone tarball...")
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        'curl --proto "=https" --tlsv1.2 -sSf https://static.rust-lang.org/dist/rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz -o /tmp/rust-1.94.1.tar.gz',
        stream_output=True
    )
    if exit_code != 0:
        raise TransientInstallError(f"Failed to download Rust tarball: {stderr}")

    # Step 3: Download the detached GPG signature
    logger.info("  Downloading GPG signature...")
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        'curl --proto "=https" --tlsv1.2 -sSf https://static.rust-lang.org/dist/rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz.asc -o /tmp/rust-1.94.1.tar.gz.asc',
        stream_output=True
    )
    if exit_code != 0:
        raise TransientInstallError(f"Failed to download Rust GPG signature: {stderr}")
    
    # Step 4: Verify the GPG signature
    logger.info("  Verifying GPG signature...")
    exit_code, stdout, stderr = execute_remote_command(
        ssh_client,
        'gpg --verify /tmp/rust-1.94.1.tar.gz.asc /tmp/rust-1.94.1.tar.gz',
        stream_output=True
    )
    if exit_code != 0:
        # Clean up on verification failure
        execute_remote_command(
            ssh_client,
            'rm -f /tmp/rust-1.94.1.tar.gz /tmp/rust-1.94.1.tar.gz.asc',
            stream_output=False
        )
        raise RuntimeError(f"GPG signature verification failed for Rust tarball: {stderr}")
    
    # Step 5: Extract and install (only after successful verification)
    logger.info("  Extracting and installing Rust...")
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        'tar -xzf /tmp/rust-1.94.1.tar.gz -C /tmp/ && /tmp/rust-1.94.1-x86_64-unknown-linux-gnu/install.sh --prefix=/home/ec2-user/.cargo',
        stream_output=True
    )
    if exit_code != 0:
        raise RuntimeError(f"Failed to extract and install Rust: {stderr}")
    
    # Step 6: Clean up tarball, signature, and extracted directory
    logger.info("  Cleaning up installation files...")
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        'rm -rf /tmp/rust-1.94.1.tar.gz /tmp/rust-1.94.1.tar.gz.asc /tmp/rust-1.94.1-x86_64-unknown-linux-gnu',
        stream_output=False
    )
    if exit_code != 0:
        logger.warning(f"Failed to clean up Rust installation files: {stderr}")
    
    logger.info("  ✓ Rust toolchain installed at /home/ec2-user/.cargo/bin/")

def install_oras(ssh_client: paramiko.SSHClient) -> None:
    """
    Install ORAS CLI version 1.3.0 on the EC2 instance via SSH.
    
    Downloads ORAS CLI from GitHub releases (linux_amd64.tar.gz), verifies its
    SHA-256 checksum, extracts to /tmp, moves binary to /usr/local/bin/oras,
    and removes temporary tar.gz file.
    Verifies installation by executing oras version command.
    
    Requirements: 16.3, 16.4, 16.8, 17.13, 17.14
    
    Args:
        ssh_client: Connected paramiko SSHClient
        
    Raises:
        RuntimeError: If installation, checksum verification, or verification fails
    """
    logger.info("Installing ORAS CLI...")
    
    # ORAS version to install
    oras_version = "1.3.0"
    
    # Expected SHA-256 checksum for oras_1.3.0_linux_amd64.tar.gz
    # Source: https://github.com/oras-project/oras/releases/download/v1.3.0/oras_1.3.0_checksums.txt
    ORAS_SHA256_CHECKSUM = "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64"
    
    # Download ORAS archive
    download_cmd = f"""
    cd /tmp && \
    curl -LO "https://github.com/oras-project/oras/releases/download/v{oras_version}/oras_{oras_version}_linux_amd64.tar.gz"
    """
    
    exit_code, stdout, stderr = execute_remote_command(ssh_client, download_cmd, stream_output=True)

    if exit_code != 0:
        raise TransientInstallError(f"Failed to download ORAS: {stderr}")

    # Verify SHA-256 checksum of the downloaded archive (Requirements: 17.13, 17.14)
    checksum_cmd = f"sha256sum /tmp/oras_{oras_version}_linux_amd64.tar.gz"
    exit_code, stdout, stderr = execute_remote_command(ssh_client, checksum_cmd, stream_output=False)

    if exit_code != 0:
        raise TransientInstallError(f"Failed to compute ORAS checksum: {stderr}")
    
    computed_checksum = stdout.strip().split()[0]
    if computed_checksum != ORAS_SHA256_CHECKSUM:
        raise RuntimeError(
            f"ORAS integrity verification failed: expected checksum {ORAS_SHA256_CHECKSUM}, "
            f"got {computed_checksum}"
        )
    
    logger.info("  ✓ ORAS archive checksum verified")
    
    # Extract and install after checksum verification
    install_cmd = f"""
    cd /tmp && \
    tar -xzf oras_{oras_version}_linux_amd64.tar.gz && \
    sudo mv oras /usr/local/bin/ && \
    rm oras_{oras_version}_linux_amd64.tar.gz
    """
    
    exit_code, stdout, stderr = execute_remote_command(ssh_client, install_cmd, stream_output=True)
    
    if exit_code != 0:
        raise RuntimeError(f"Failed to install ORAS: {stderr}")
    
    # Verify installation by executing oras version command
    exit_code, stdout, _ = execute_remote_command(
        ssh_client,
        "oras version",
        stream_output=False
    )
    
    if exit_code == 0:
        logger.info(f"  ✓ ORAS installed: {stdout.strip()}")
    else:
        raise RuntimeError("Failed to verify ORAS installation")

def install_github_cli(ssh_client: paramiko.SSHClient) -> None:
    """
    Install GitHub CLI on the instance via SSH.
    
    Adds gh-cli.repo repository configuration via dnf config-manager,
    installs gh package via dnf, and verifies installation by executing gh version command.
    
    Requirements: 16.5, 16.9
    
    Args:
        ssh_client: Connected paramiko SSHClient
        
    Raises:
        RuntimeError: If installation or verification fails
    """
    logger.info("Installing GitHub CLI...")
    
    # Trust assumption: The gh-cli.repo DNF repository (https://cli.github.com/packages/rpm/gh-cli.repo)
    # is the official GitHub CLI distribution channel maintained by GitHub. The repository
    # is served over HTTPS and packages are signed by GitHub's GPG key. (Requirement: 17.16)
    
    # Add gh-cli.repo repository and install gh package via dnf
    install_cmd = f"""
    sudo dnf install dnf-utils -y && \
    sudo dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo && \
    sudo dnf install gh -y
    """
    
    exit_code, stdout, stderr = execute_remote_command(ssh_client, install_cmd, stream_output=True)

    if exit_code != 0:
        # dnf repo add + install is network-dominated — retriable (D10).
        raise TransientInstallError(f"Failed to install GitHub CLI: {stderr}")
    
    # Verify installation by executing gh version command
    exit_code, stdout, _ = execute_remote_command(
        ssh_client,
        "gh version",
        stream_output=False
    )
    
    if exit_code == 0:
        logger.info(f"  ✓ GitHub CLI installed: {stdout.strip()}")
    else:
        raise RuntimeError("Failed to verify GitHub CLI installation")

def install_coldsnap(ssh_client: paramiko.SSHClient) -> None:
    """
    Install coldsnap on the instance via SSH.
    
    Clones coldsnap from https://github.com/awslabs/coldsnap.git at a pinned
    version tag, builds and installs using cargo install --locked coldsnap.
    Installation path: /home/ec2-user/.cargo/bin/coldsnap
    Verifies installation by executing coldsnap --help command.

    See: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/build-sample-ami.html
    
    Requirements: 16.6, 16.7, 16.10, 17.15
    
    Args:
        ssh_client: Connected paramiko SSHClient
        
    Raises:
        RuntimeError: If installation or verification fails
    """
    logger.info("Installing coldsnap...")
    
    # Pinned coldsnap version (Requirements: 17.15)
    COLDSNAP_VERSION = "v0.9.0"
    
    # Clone coldsnap repository at pinned tag. A clone failure is network-dominated
    # (git rate-limit / connectivity) — retriable (D10). `rm -rf` first so a retry
    # after a partial clone starts from a clean tree.
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        f"rm -rf coldsnap && git clone --branch {COLDSNAP_VERSION} --depth 1 https://github.com/awslabs/coldsnap.git",
        stream_output=True
    )
    if exit_code != 0:
        raise TransientInstallError(f"Failed to clone coldsnap repository: {stderr}")

    # Build and install coldsnap using cargo install --locked.
    # PATH must include the cargo bin dir so that cargo can locate rustc internally —
    # non-login SSH sessions do not source ~/.bashrc or ~/.profile, so the directory
    # is not on PATH by default even though we installed Rust there.
    #
    # A compile failure is DETERMINISTIC (source that won't build, bad pin) — raise
    # plain RuntimeError so the per-step retry (D10) fails fast rather than burning
    # the expensive CPU-bound compile again for nothing.
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        "cd coldsnap && PATH=/home/ec2-user/.cargo/bin:$PATH /home/ec2-user/.cargo/bin/cargo install --locked coldsnap",
        stream_output=True
    )
    if exit_code != 0:
        raise RuntimeError(f"Failed to install coldsnap: {stderr}")
    
    # Verify installation by executing coldsnap --help command
    exit_code, stdout, _ = execute_remote_command(
        ssh_client,
        "/home/ec2-user/.cargo/bin/coldsnap --help",
        stream_output=False
    )
    
    if exit_code == 0:
        logger.info(f"  ✓ coldsnap installed at /home/ec2-user/.cargo/bin/coldsnap")
    else:
        raise RuntimeError("Failed to verify coldsnap installation")

def run_install_step(
    step_fn: Any,
    name: str,
    ssh_client: paramiko.SSHClient,
    max_attempts: int = INSTALL_MAX_ATTEMPTS,
    base_delay: int = INSTALL_BASE_DELAY,
) -> None:
    """
    Run one toolchain-install step with transient-retry / fail-fast semantics (D10).

    Reacts to the *kind* of failure (the same transient-vs-deterministic distinction
    D7 applies to the flavor loop, here applied to install):

    - **TransientInstallError** (download timeout, clone rate-limit, dnf mirror
      hiccup) → retry with exponential backoff, up to ``max_attempts``.
    - **RuntimeError** (GPG/checksum mismatch, compile error, upstream 404 —
      deterministic) → re-raise immediately; a retry would only burn the expensive
      compile again for nothing.

    Retries are scoped to a single step (the install functions are already
    separate), so a late cheap-step blip does not trigger a full coldsnap recompile.

    Args:
        step_fn: The install function to run (takes the ssh_client)
        name: Human-readable step name for logging
        ssh_client: Connected paramiko SSHClient
        max_attempts: Max attempts for transient failures
        base_delay: Base backoff delay in seconds (doubled each retry)

    Raises:
        RuntimeError: On a deterministic failure, or after transient retries exhaust
    """
    for attempt in range(1, max_attempts + 1):
        try:
            step_fn(ssh_client)
            return
        except TransientInstallError as e:
            if attempt >= max_attempts:
                logger.error(f"{name}: transient failure persisted after {max_attempts} attempts")
                raise RuntimeError(f"{name} failed after {max_attempts} attempts: {e}")
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(f"{name}: transient failure (attempt {attempt}/{max_attempts}): {e}")
            logger.warning(f"{name}: retrying in {delay}s...")
            time.sleep(delay)
        # Deterministic RuntimeError (not a TransientInstallError) propagates
        # immediately — fail fast, no retry.


def install_all_tools(ssh_client: paramiko.SSHClient) -> None:
    """
    Install all required tools on the build instance **once per run** (D10).

    Consolidating the toolchain install from once-per-flavor to once-per-run moves
    it *before* the flavor loop, where it is a **gate**: every flavor depends on it,
    so it is not an isolatable per-flavor unit. Any install failure therefore
    hard-aborts the whole run (zero results) — the caller must not enter the flavor
    loop. Each step runs through ``run_install_step`` for per-step transient-retry /
    deterministic-fail-fast handling.

    Executes installation functions in order: system dependencies, Rust, ORAS,
    GitHub CLI, and coldsnap. Streams output to logs; each step self-verifies.

    Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9, 16.10, 16.11, 16.12

    Args:
        ssh_client: Connected paramiko SSHClient

    Raises:
        RuntimeError: If any tool installation fails (hard-abort of the whole run)
    """
    logger.info("Installing all required tools (once per run, before the flavor loop)...")
    logger.info("")

    steps = [
        (install_system_dependencies, "system dependencies (git, gcc)"),
        (install_rust, "Rust toolchain"),
        (install_oras, "ORAS CLI"),
        (install_github_cli, "GitHub CLI"),
        (install_coldsnap, "coldsnap"),
    ]

    try:
        for step_fn, name in steps:
            run_install_step(step_fn, name, ssh_client)
            logger.info("")

        logger.info("✓ All tools installed successfully")

    except RuntimeError as e:
        logger.error(f"Tool installation failed: {e}")
        logger.error("Install is a gate — the whole run is aborted (zero AMIs produced)")
        raise

def reset_artifacts_dir(ssh_client: paramiko.SSHClient, artifacts_base: str) -> None:
    """
    Wipe and recreate the shared artifacts working directory (D4, wipe-and-reuse).

    On the single shared instance every flavor reuses one artifacts tree, so it must
    be reset to a known-empty state before each flavor's pull. This is required for
    both correctness — the upload path enforces exactly one ``.raw`` in
    ``build-output``, which a previous flavor's leftover would violate — and capacity
    — only one flavor's OCI blob + unpacked ``.raw`` occupies the 30 GB root at a
    time, instead of all flavors accumulating.

    Args:
        ssh_client: Connected paramiko SSHClient
        artifacts_base: Base artifacts directory on the instance (e.g. ~/artifacts)

    Raises:
        RuntimeError: If the reset fails
    """
    logger.info(f"Resetting artifacts working directory: {artifacts_base}")
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        f"rm -rf {artifacts_base} && mkdir -p {artifacts_base}",
        stream_output=False,
    )
    if exit_code != 0:
        raise RuntimeError(f"Failed to reset artifacts directory {artifacts_base}: {stderr}")


def pull_artifact_from_ghcr(
    ssh_client: paramiko.SSHClient,
    artifact_ref: str,
    artifacts_base: str = "~/artifacts",
) -> None:
    """
    Pull artifact bundle from GitHub Container Registry using ORAS.

    Executes oras pull to download the artifact bundle using the exact sha256 digest
    (ignoring any mutable tag), streams output to logger, verifies exit code is 0,
    and lists downloaded files with sizes. The caller is expected to have already
    reset ``artifacts_base`` (wipe-and-reuse, D4).

    Args:
        ssh_client: Connected paramiko SSHClient
        artifact_ref: GitHub Container Registry artifact reference (digest-pinned)
        artifacts_base: Base artifacts directory on the instance (e.g. ~/artifacts)

    Raises:
        RuntimeError: If directory creation, ORAS pull, or file listing fails

    Requirements: 15.19, 18.1, 18.2, 18.3, 18.8, 18.9
    """
    # Use only the digest for the pull — ignore any mutable tag
    digest_ref = get_digest_pinned_ref(artifact_ref)
    logger.info(f"Pulling artifact from GHCR using digest: {digest_ref}")

    # Ensure the working directory exists (reset_artifacts_dir normally created it)
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        f"mkdir -p {artifacts_base}",
        stream_output=False
    )

    if exit_code != 0:
        raise RuntimeError(f"Failed to create artifacts directory: {stderr}")

    # Pull artifacts using ORAS with digest-pinned reference (no authentication required for public repos)
    logger.info("Downloading artifacts with ORAS...")
    pull_cmd = f"cd {artifacts_base} && oras pull {digest_ref}"

    exit_code, stdout, stderr = execute_remote_command(ssh_client, pull_cmd, stream_output=True)

    if exit_code != 0:
        raise RuntimeError(f"ORAS pull failed: {stderr}")

    logger.info("Artifacts downloaded successfully")

    # List downloaded files in build-output using ls -lh
    logger.info("Listing downloaded artifacts...")
    exit_code, stdout, stderr = execute_remote_command(
        ssh_client,
        f"ls -lh {artifacts_base}/build-output",
        stream_output=False
    )

    if exit_code != 0:
        raise RuntimeError(f"Failed to list artifacts in build-output: {stderr}")

    logger.info(f"Downloaded artifacts:\n{stdout}")


def validate_artifact_files(
    ssh_client: paramiko.SSHClient,
    artifacts_base: str = "~/artifacts",
) -> None:
    """
    Validate that required artifact files exist after download.

    Verifies the raw disk image exists using ls <base>/build-output/*.raw
    and pcr_measurements.json exists using test -f command.

    Args:
        ssh_client: Connected paramiko SSHClient
        artifacts_base: Base artifacts directory on the instance (e.g. ~/artifacts)

    Raises:
        RuntimeError: If raw disk image or pcr_measurements.json is missing

    Requirements: 18.4, 18.5, 18.10, 18.11
    """
    logger.info("Validating downloaded artifact files...")

    # Verify raw disk image exists
    exit_code, stdout, _ = execute_remote_command(
        ssh_client,
        f"ls {artifacts_base}/build-output/*.raw",
        stream_output=False
    )

    if exit_code != 0:
        raise RuntimeError("Raw disk image (.raw file) not found in build-output directory")

    # Verify pcr_measurements.json exists
    exit_code, _, _ = execute_remote_command(
        ssh_client,
        f"test -f {artifacts_base}/build-output/pcr_measurements.json",
        stream_output=False
    )

    if exit_code != 0:
        raise RuntimeError("pcr_measurements.json not found in build-output directory")

    logger.info("All required artifact files verified successfully")


def check_debug_annotation(ssh_client: paramiko.SSHClient, artifact_ref: str, allow_debug: bool) -> None:
    """
    Check the debug annotation on the artifact and gate production builds.

    Runs `oras manifest fetch` on the remote instance to retrieve the manifest
    JSON, parses it to find the `debug` annotation, and enforces the production
    gate: debug artifacts are rejected unless --allow-debug is provided.

    Uses the digest-pinned reference to ensure the manifest check targets the
    exact same immutable content as the pull and verification steps.

    Args:
        ssh_client: Connected paramiko SSHClient
        artifact_ref: GitHub Container Registry artifact reference (digest-pinned)
        allow_debug: Whether --allow-debug CLI flag was provided

    Raises:
        RuntimeError: If debug=true and allow_debug is False

    Requirements: 46.3, 46.4, 46.5
    """
    logger.info("Checking debug annotation on artifact...")

    # Use digest-pinned reference for manifest fetch
    digest_ref = get_digest_pinned_ref(artifact_ref)

    exit_code, stdout, stderr = execute_remote_command(
        ssh_client,
        f"oras manifest fetch {digest_ref}",
        stream_output=False
    )

    if exit_code != 0:
        if not allow_debug:
            raise RuntimeError(
                f"REFUSING TO BUILD: Failed to fetch manifest for debug annotation check "
                f"(exit code {exit_code}): {stderr}. "
                "Cannot verify debug status — failing closed. "
                "Re-run with --allow-debug to proceed without debug verification."
            )
        logger.warning(f"Failed to fetch manifest for debug annotation check: {stderr}")
        logger.warning("Proceeding because --allow-debug was provided")
        return

    try:
        manifest = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as e:
        if not allow_debug:
            raise RuntimeError(
                f"REFUSING TO BUILD: Failed to parse manifest JSON: {e}. "
                "Cannot verify debug status — failing closed. "
                "Re-run with --allow-debug to proceed without debug verification."
            )
        logger.warning(f"Failed to parse manifest JSON: {e}")
        logger.warning("Proceeding because --allow-debug was provided")
        return

    # Look for debug annotation in manifest annotations
    annotations = manifest.get("annotations", {})
    debug_value = annotations.get("debug")

    if debug_value is None:
        logger.info("No debug annotation found on artifact, proceeding normally")
        return

    logger.info(f"Debug annotation value: {debug_value}")

    if debug_value == "true":
        if not allow_debug:
            raise RuntimeError(
                "REFUSING TO BUILD: Artifact has debug=true annotation (SSH-enabled debug image). "
                "Debug images must not be converted to production AMIs. "
                "If you intentionally want to build a debug AMI, re-run with --allow-debug flag."
            )
        else:
            logger.warning("=" * 80)
            logger.warning("WARNING: Building AMI from DEBUG artifact (debug=true)")
            logger.warning("This artifact was built with SSH debug access enabled.")
            logger.warning("The resulting AMI is NOT intended for production use.")
            logger.warning("=" * 80)
    else:
        logger.info("Artifact is not a debug image (debug=%s), proceeding normally", debug_value)


def validate_pcr_measurements(
    ssh_client: paramiko.SSHClient,
    artifacts_base: str = "~/artifacts",
) -> dict:
    """
    Read and validate PCR measurements from pcr_measurements.json.

    Reads pcr_measurements.json content using cat command, parses JSON,
    extracts PCR4 and PCR7 from Measurements field, validates they are
    non-empty hex strings, and returns a dict with pcr4 and pcr7.

    Args:
        ssh_client: Connected paramiko SSHClient
        artifacts_base: Base artifacts directory on the instance (e.g. ~/artifacts)

    Returns:
        Dict with structure: {"Measurements": {"PCR4": "...", "PCR7": "..."}}

    Raises:
        RuntimeError: If reading, parsing, or validation fails

    Requirements: 18.6, 18.7, 18.12
    """
    logger.info("Reading and validating PCR measurements...")

    # Read pcr_measurements.json content using cat command
    exit_code, stdout, _ = execute_remote_command(
        ssh_client,
        f"cat {artifacts_base}/build-output/pcr_measurements.json",
        stream_output=False
    )
    
    if exit_code != 0:
        raise RuntimeError("Failed getting pcr_measurements.json content")
    
    # Parse JSON in Python
    try:
        pcr_measurements = json.loads(stdout)
    except Exception as e:
        raise RuntimeError(f"Failed parsing pcr_measurements.json content: {e}")
    
    # Extract PCR4 and PCR7 from Measurements field
    try:
        measurements = pcr_measurements['Measurements']
        pcr4 = measurements['PCR4']
        pcr7 = measurements['PCR7']
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Failed extracting PCR values from measurements: {e}")
    
    # Validate PCR values are non-empty hex strings
    import re
    hex_pattern = re.compile(r'^[0-9a-fA-F]+$')
    
    if not pcr4 or not hex_pattern.match(pcr4):
        raise RuntimeError(f"Invalid PCR4 value: must be a non-empty hex string, got '{pcr4}'")
    
    if not pcr7 or not hex_pattern.match(pcr7):
        raise RuntimeError(f"Invalid PCR7 value: must be a non-empty hex string, got '{pcr7}'")
    
    logger.info(f"PCR4: {pcr4}")
    logger.info(f"PCR7: {pcr7}")
    logger.info("PCR measurements validated successfully")
    
    return pcr_measurements

def verify_artifact_signature(
    ssh_client: paramiko.SSHClient,
    artifact_ref: str,
    expected_workflow: Optional[str] = None,
) -> bool:
    """
    Verify artifact signature using gh attestation.
    
    Uses the exact sha256 digest from the artifact reference for verification,
    ensuring the same immutable content that was referenced is what gets verified
    (preventing TOCTOU attacks via tag movement).
    
    Args:
        ssh_client: Connected paramiko SSHClient
        artifact_ref: GitHub Container Registry artifact reference (digest-pinned)
        expected_workflow: Optional expected workflow file path for provenance verification.
            When provided, the attestation's workflow identity is verified against this path.
    
    Returns:
        True if verification succeeds, False otherwise
    
    Requirements: 15.20, 15.21
    """
    logger.info("Verifying artifact signature with gh attestation ...")
    
    # Extract the digest directly from the artifact reference — do NOT recompute
    # from a manifest fetch, which would be vulnerable to TOCTOU if the tag moved.
    digest = extract_digest_from_artifact_ref(artifact_ref)
    digest_ref = get_digest_pinned_ref(artifact_ref)
    logger.info(f"Using pinned digest for verification: {digest}")
    
    # Extract repository information from artifact reference
    # Format: ghcr.io/owner/repo/package[:tag]@sha256:digest
    base_path = artifact_ref.replace('ghcr.io/', '').split('@')[0].split(':')[0]
    parts = base_path.split('/')
    if len(parts) >= 2:
        owner = parts[0]
        repo = parts[1]
        identity = f"{owner}/{repo}"
    else:
        logger.error("✗ Artifact signature verification FAILED")
        logger.error(f"Cannot determine identity from artifact path")
        return False
    
    logger.info(f"Using attestation identity: {identity}")

    verify_cmd = f"""
    # Use the exact digest from the artifact reference (no manifest fetch needed)
    DIGEST="{digest}"

    # Download GitHub attestation bundle using the pinned digest
    curl -sL "https://api.github.com/repos/{owner}/{repo}/attestations/${{DIGEST}}" \
        | jq -cr '.attestations[0].bundle' > bundle.json

    # Offline attestation verify with JSON output for policy enforcement
    # Do NOT set GH_FORCE_TTY here — it injects ANSI escape codes that break jq parsing
    gh attestation verify oci://{digest_ref} \
        -R {identity} \
        -b bundle.json \
        --format json > attestation_result.json

    # Also print human-readable output for logging
    # Set GH_FORCE_TTY=1 to force gh outputting result
    GH_FORCE_TTY=1 gh attestation verify oci://{digest_ref} \
        -R {identity} \
        -b bundle.json
    """

    exit_code, stdout, stderr = execute_remote_command(
        ssh_client,
        verify_cmd,
        stream_output=True
    )
    
    if exit_code != 0:
        logger.error("✗ Artifact signature verification FAILED")
        logger.error(f"command output: {stderr}")
        return False

    logger.info("✓ Artifact attestation verification SUCCEEDED")

    # Workflow identity verification (Requirement 47.1-47.4)
    if expected_workflow is not None:
        logger.info(f"Verifying workflow identity against expected: {expected_workflow}")

        # Extract workflow identity from the certificate's SubjectAlternativeName (SAN).
        # The SAN is populated directly from GitHub's OIDC token and cannot be forged
        # by the workflow that produced the attestation, unlike the predicate payload.
        # SAN format: https://github.com/<owner>/<repo>/<path/to/workflow.yml>@refs/...
        workflow_extract_cmd = (
            "jq -r '.[0].verificationResult.signature.certificate.subjectAlternativeName'"
            " attestation_result.json"
        )

        wf_exit_code, wf_stdout, wf_stderr = execute_remote_command(
            ssh_client,
            workflow_extract_cmd,
            stream_output=False
        )

        if wf_exit_code != 0:
            logger.error("✗ Failed to extract workflow identity from attestation result")
            logger.error(f"command output: {wf_stderr}")
            return False

        actual_workflow = wf_stdout.strip()
        logger.info(f"Attestation workflow identity: {actual_workflow}")

        # Compare: the expected workflow path should appear as a substring
        # of the SAN URL (e.g. ".github/workflows/build.yml" within
        # "https://github.com/owner/repo/.github/workflows/build.yml@refs/heads/main")
        if expected_workflow not in actual_workflow:
            logger.error("✗ WORKFLOW IDENTITY MISMATCH")
            logger.error(f"  Expected workflow: {expected_workflow}")
            logger.error(f"  Actual workflow:   {actual_workflow}")
            return False

        logger.info("✓ Workflow identity verification SUCCEEDED")
    else:
        logger.info("Skipping workflow identity verification (--expected-workflow not provided)")

    return True

def upload_snapshot(
    ssh_client: paramiko.SSHClient,
    region: str,
    run_id: str,
    artifacts_base: str = "~/artifacts",
) -> str:
    """
    Upload the raw disk image to an EBS snapshot using coldsnap.

    The upload is tagged ``run_id=<run_id>`` (D13). coldsnap v0.9.0 applies
    ``--tag`` on the ``StartSnapshot`` call — i.e. at snapshot *birth* — so even a
    snapshot abandoned mid-upload (e.g. a D12 reconnect race) carries the run id
    from creation, giving orphan snapshots the same run-scoped sweep key as orphan
    instances.

    Args:
        ssh_client: Connected paramiko SSHClient
        region: AWS region for snapshot creation
        run_id: Run identifier applied as the ``run_id`` snapshot tag value
        artifacts_base: Base artifacts directory on the instance (e.g. ~/artifacts)

    Returns:
        Snapshot ID string
    """
    logger.info("Uploading raw disk image to EBS snapshot...")

    # Find the raw disk image file in build-output directory using programmatic listing
    exit_code, stdout, stderr = execute_remote_command(
        ssh_client,
        f"find {artifacts_base}/build-output -maxdepth 1 -name '*.raw' -type f",
        stream_output=False
    )
    
    if exit_code != 0:
        raise RuntimeError(f"Failed to list raw disk images: {stderr}")
    
    raw_files = [line.strip() for line in stdout.strip().split('\n') if line.strip()]
    
    # Enforce exactly one .raw file
    if len(raw_files) == 0:
        raise RuntimeError("No .raw file found in build-output directory")
    if len(raw_files) > 1:
        raise RuntimeError(
            f"Expected exactly one .raw file, found {len(raw_files)}: {raw_files}"
        )
    
    raw_image_path = raw_files[0]
    # Extract basename for validation
    raw_basename = os.path.basename(raw_image_path)
    
    # Validate the basename against a strict allowlist regex
    raw_filename_pattern = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*\.raw$')
    if not raw_filename_pattern.match(raw_basename):
        raise RuntimeError(
            f"Invalid raw image filename: '{raw_basename}'. "
            "Filename must match pattern: ^[a-zA-Z0-9][a-zA-Z0-9._-]*\\.raw$"
        )
    
    logger.info(f"Found raw disk image: {raw_image_path}")

    # Defense-in-depth: run_id is interpolated into the remote shell command below,
    # so re-assert the strict format even though the CLI already validated it.
    validate_run_id(run_id)

    # Upload using coldsnap, tagging the snapshot with the run id at StartSnapshot (D13).
    logger.info("Uploading snapshot with coldsnap (this may take several minutes)...")

    coldsnap_command = (
        f"/home/ec2-user/.cargo/bin/coldsnap upload "
        f"--tag Key=run_id,Value={run_id} {raw_image_path}"
    )
    exit_code, stdout, stderr = execute_remote_command(
        ssh_client,
        coldsnap_command,
        stream_output=True
    )

    if exit_code != 0:
        raise RuntimeError(f"coldsnap upload failed: {stderr}")
    
    # Parse snapshot ID from output
    snapshot_id = None
    for line in stdout.split('\n'):
        if 'snap-' in line:
            # Extract snapshot ID
            parts = line.split()
            for part in parts:
                if part.startswith('snap-'):
                    snapshot_id = part
                    break
            if snapshot_id:
                break
    
    if not snapshot_id:
        # Try to find it in the last line
        last_line = stdout.strip().split('\n')[-1]
        if last_line.startswith('snap-'):
            snapshot_id = last_line.strip()
    
    if not snapshot_id:
        raise RuntimeError(f"Failed to parse snapshot ID from coldsnap output: {stdout}")
    
    logger.info(f"Snapshot created successfully: {snapshot_id}")
    return snapshot_id

def wait_for_snapshot(ec2_client: Any, snapshot_id: str) -> None:
    """
    Wait for an EBS snapshot to complete using EC2 waiter.

    Creates an EC2 waiter for snapshot_completed, configured with 15-second
    delay and 40 max attempts (up to 10 minutes total).

    Args:
        ec2_client: Boto3 EC2 client
        snapshot_id: EBS snapshot ID to wait for

    Raises:
        WaiterError: If timeout exceeded or snapshot enters error state

    Requirements: 19.5, 19.6
    """
    logger.info(f"Waiting for snapshot {snapshot_id} to complete...")
    try:
        waiter = ec2_client.get_waiter('snapshot_completed')
        waiter.wait(
            SnapshotIds=[snapshot_id],
            WaiterConfig={'Delay': 15, 'MaxAttempts': 40}  # Up to 10 minutes
        )
        logger.info(f"Snapshot {snapshot_id} completed successfully")
    except WaiterError as e:
        logger.error(f"Snapshot {snapshot_id} failed to complete: {e}")
        raise


def register_ami(
    ec2_client: Any,
    snapshot_id: str,
    architecture: str,
    ami_name: str,
    container_image_digest: Optional[str] = None,
    producing_commit: Optional[str] = None,
) -> str:
    """
    Register an AMI with TPM 2.0 and UEFI boot mode.

    Args:
        ec2_client: Boto3 EC2 client
        snapshot_id: EBS snapshot ID
        architecture: CPU architecture (x86_64 or arm64)
        ami_name: Name for the AMI
        container_image_digest: Baked execution image manifest digest (sha256:...),
            tagged onto the AMI so verifiers can map AMI -> image digest. PCR4
            already binds the baked image bytes; this tag is build-output
            self-description, not part of the runtime attestation.
        producing_commit: The commit that produced this AMI (tagged for the record).

    Returns:
        AMI ID string
    """
    logger.info("Registering AMI with TPM 2.0 and UEFI boot mode...")
    logger.info(f"  Snapshot: {snapshot_id}")
    logger.info(f"  Architecture: {architecture}")
    logger.info(f"  Name: {ami_name}")

    # Tag the registered AMI with the verifier-record fields known at registration
    # time (the container image manifest digest and producing commit). The AMI id
    # and PCR4 round out the single-entry record emitted to the log/summary.
    tags = []
    if container_image_digest:
        tags.append({'Key': 'ContainerImageDigest', 'Value': container_image_digest})
    if producing_commit:
        tags.append({'Key': 'ProducingCommit', 'Value': producing_commit})

    register_kwargs = dict(
        Name=ami_name,
        VirtualizationType='hvm',
        BootMode='uefi',
        Architecture=architecture,
        RootDeviceName='/dev/xvda',
        BlockDeviceMappings=[
            {
                'DeviceName': '/dev/xvda',
                'Ebs': {
                    'SnapshotId': snapshot_id
                }
            }
        ],
        TpmSupport='v2.0',
        EnaSupport=True,
    )
    if tags:
        register_kwargs['TagSpecifications'] = [{'ResourceType': 'image', 'Tags': tags}]

    try:
        response = ec2_client.register_image(**register_kwargs)

        ami_id = response['ImageId']
        logger.info(f"AMI registered successfully: {ami_id}")
        if container_image_digest:
            logger.info(f"  Tagged ContainerImageDigest={container_image_digest}")
        return ami_id

    except ClientError as e:
        logger.error(f"Failed to register AMI: {e}")
        raise

def generate_build_result(
    ami_id: str,
    snapshot_id: str,
    region: str,
    pcr_measurements: dict,
    output_file: str,
    container_image_digest: Optional[str] = None,
    producing_commit: Optional[str] = None,
    relaxations: Optional[dict] = None,
) -> dict:
    """
    Generate build result dictionary and write it to the output file.

    Creates a build result containing AMI details, region, timestamp, and
    PCR measurements, then writes it as JSON with 2-space indentation.

    Also emits a single-entry **verifier record** mapping the baked image's
    manifest digest -> PCR4 -> AMI id -> producing commit (D-rec). Its field set
    is a clean subset of the multi-flavor ``flavors.lock`` the
    ``execution-build-images`` change introduces, so that change need not
    retrofit a record format: there it becomes one entry per flavor keyed by
    flavor, with these same fields.

    Args:
        ami_id: The registered AMI ID
        snapshot_id: The EBS snapshot ID
        region: AWS region where the AMI was created
        pcr_measurements: Dict with structure {"Measurements": {"PCR4": "...", "PCR7": "..."}}
        output_file: Path to the output JSON file
        container_image_digest: Baked execution image manifest digest (sha256:...)
        producing_commit: The commit that produced this AMI
        relaxations: Bucket-① fields that deviate from hardened defaults (task 7.2).
                     {} means fully hardened.

    Returns:
        The build result dictionary

    Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.6
    """
    pcr4 = pcr_measurements['Measurements']['PCR4']
    pcr7 = pcr_measurements['Measurements']['PCR7']

    build_result = {
        "ami_id": ami_id,
        "snapshot_id": snapshot_id,
        "region": region,
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "pcr_measurements": {
            "pcr4": pcr4,
            "pcr7": pcr7,
        },
        # Single-entry verifier record (seed of Change 2's per-flavor flavors.lock).
        "verifier_record": {
            "container_image_digest": container_image_digest,
            "pcr4": pcr4,
            "ami_id": ami_id,
            "producing_commit": producing_commit,
            # bucket-① fields relaxed from hardened defaults; {} = fully hardened (task 7.2)
            "relaxations": relaxations if relaxations is not None else {},
        },
    }

    with open(output_file, 'w') as f:
        json.dump(build_result, f, indent=2)

    logger.info(f"Build result: {json.dumps(build_result, indent=2)}")

    return build_result


# Transport/timeout exceptions that mean "host suspect" (D7): the host may have
# died, so the driver attempts a bounded reconnect (D12) rather than treating the
# failure as flavor-local.
_TRANSPORT_EXCEPTIONS = (TransportError, RemoteCommandTimeout, paramiko.SSHException, OSError)


def kill_stale_coldsnap(ssh_client: paramiko.SSHClient) -> None:
    """
    Best-effort terminate any abandoned ``coldsnap`` upload on the instance (D12).

    After a transport drop interrupted an in-flight upload, the remote ``coldsnap``
    process (spawned with ``get_pty=False``) is commonly reparented to init and keeps
    running: because the unlinked ``.raw`` inode survives while its fd is open, the
    zombie can reach ``CompleteSnapshot`` *after* the driver resumes — minting a
    fully-billed orphan snapshot the driver never captured AND briefly running a
    second set of 64 ``PutSnapshotBlock`` workers alongside the next flavor's,
    transiently violating D5's "sequential ⇒ never 2×64" guarantee. Killing it over
    the fresh channel before wiping closes both paths in one cheap step. Failures are
    swallowed — this is a courtesy cleanup, not a correctness gate.

    Args:
        ssh_client: Freshly reconnected paramiko SSHClient
    """
    try:
        logger.info("Best-effort: terminating any abandoned coldsnap upload...")
        # `|| true` so a "no matching process" exit (the common, healthy case) is not
        # itself treated as a failure.
        execute_remote_command(
            ssh_client,
            "pkill -f coldsnap || true",
            stream_output=False,
            timeout=60,
        )
    except Exception as e:
        logger.warning(f"Best-effort coldsnap kill failed (ignored): {e}")


def reconnect_ssh(
    host: str,
    username: str,
    ssh_key_path: str,
) -> Optional[paramiko.SSHClient]:
    """
    Attempt a bounded SSH reconnect after a transport/timeout error (D12).

    Reuses ``verify_ssh_connectivity``'s existing retry loop. The reconnect outcome
    is itself the **host-alive vs host-dead discriminator**: success ⇒ the host was
    alive (a transient TCP drop) ⇒ the driver resumes; failure after the bounded
    attempts ⇒ the host is genuinely dead ⇒ the driver falls through to D7's abort.

    Returns:
        A connected SSHClient on success, or None if all attempts failed.
    """
    logger.warning("Transport error — attempting bounded SSH reconnect (host-alive probe)...")
    try:
        return verify_ssh_connectivity(
            host,
            username,
            ssh_key_path,
            max_attempts=RECONNECT_MAX_ATTEMPTS,
            delay=RECONNECT_DELAY,
        )
    except (paramiko.SSHException, OSError) as e:
        logger.error(f"SSH reconnect failed after {RECONNECT_MAX_ATTEMPTS} attempts: {e}")
        return None


def build_flavor_pass1(
    ssh_client: paramiko.SSHClient,
    flavor: dict,
    region: str,
    run_id: str,
    artifacts_base: str,
    allow_debug: bool,
    expected_workflow: Optional[str],
) -> dict:
    """
    Pass 1 for a single flavor (D5): wipe → pull → verify → validate → upload.

    Runs the host-dependent portion of one flavor's build on the shared instance and
    returns the captured snapshot id plus the metadata Pass 2 needs to register the
    AMI. Once ``coldsnap upload`` returns a snapshot id the flavor is safe from host
    death (D8) — the snapshot lives server-side and Pass 2 runs off the runner's
    boto3 client — so the caller discards the ``.raw`` afterward (implicitly, via the
    next flavor's wipe).

    Application errors (bad signature, debug gate, validation) raise ``RuntimeError``
    (host healthy → flavor-local). Transport/timeout errors propagate as their own
    exception types (host suspect → driver reconnect path).

    Args:
        ssh_client: Connected paramiko SSHClient
        flavor: Manifest entry {flavor, artifact_ref, container_image_digest, relaxations}
        region: AWS region
        run_id: Run identifier (snapshot tag value)
        artifacts_base: Shared artifacts base dir on the instance
        allow_debug: Whether debug artifacts are permitted
        expected_workflow: Expected workflow path for provenance verification

    Returns:
        Dict: {flavor, snapshot_id, pcr_measurements, container_image_digest, relaxations}
    """
    name = flavor["flavor"]
    artifact_ref = flavor["artifact_ref"]

    logger.info("")
    logger.info("-" * 80)
    logger.info(f"Pass 1 — building flavor: {name}")
    logger.info("-" * 80)

    # Wipe-and-reuse: start every flavor from a known-empty tree (D4).
    reset_artifacts_dir(ssh_client, artifacts_base)

    # Verify the artifact signature BEFORE downloading the artifact bytes
    # (Requirements 17.9/17.10/17.12 — verify-before-download). `verify_artifact_signature`
    # targets the registry directly (`gh attestation verify oci://…`), so it needs no
    # local pull. Failure is an application error (host healthy) → RuntimeError.
    if not verify_artifact_signature(ssh_client, artifact_ref, expected_workflow=expected_workflow):
        raise RuntimeError(f"Signature verification FAILED for flavor '{name}'")

    # Pull the (now-verified) digest-pinned artifact bundle.
    pull_artifact_from_ghcr(ssh_client, artifact_ref, artifacts_base)

    # Validate the pulled files, enforce the debug production gate, read PCRs.
    validate_artifact_files(ssh_client, artifacts_base)
    check_debug_annotation(ssh_client, artifact_ref, allow_debug)
    pcr_measurements = validate_pcr_measurements(ssh_client, artifacts_base)

    # Upload to an EBS snapshot, tagged with the run id (D13). This is the last
    # host-dependent moment for this flavor (D8).
    snapshot_id = upload_snapshot(ssh_client, region, run_id, artifacts_base)

    logger.info(f"Pass 1 complete for '{name}': snapshot {snapshot_id}")

    return {
        "flavor": name,
        "snapshot_id": snapshot_id,
        "pcr_measurements": pcr_measurements,
        "container_image_digest": flavor.get("container_image_digest"),
        "relaxations": flavor.get("relaxations") or {},
    }


def run_build_driver(
    ssh_client: paramiko.SSHClient,
    ec2_client: Any,
    flavors: list,
    host: str,
    username: str,
    ssh_key_path: str,
    region: str,
    run_id: str,
    producing_commit: Optional[str],
    artifacts_base: str,
    output_dir: str,
    allow_debug: bool,
    expected_workflow: Optional[str],
) -> Tuple[dict, paramiko.SSHClient]:
    """
    Two-pass multi-flavor driver on the shared instance (D5, D7, D8, D12).

    **Pass 1 (sequential, host-dependent):** build each flavor and capture its
    snapshot id. React to the *kind* of failure (D7):

    - **Application error** (RuntimeError: bad signature / debug gate / validation)
      → host healthy → record the flavor failed and **continue**.
    - **Transport/timeout** (raised paramiko/timeout exception) → host suspect →
      attempt a bounded reconnect (D12). Reconnect success ⇒ host was alive ⇒
      best-effort kill the abandoned upload, record the interrupted flavor as
      failed/indeterminate, and **resume at the next flavor**. Reconnect failure ⇒
      host genuinely dead ⇒ **stop** further Pass 1 flavors (mark them skipped).

    **Pass 2 (batched, host-independent):** for every flavor that captured a snapshot
    id, wait for completion and register the AMI via the runner's boto3 client — this
    survives the build instance dying (D8). A Pass-1-failed flavor is excluded; a
    Pass-2 wait/register failure for one flavor does not abort the others.

    Returns:
        (results, ssh_client): a {flavor: status} map and the possibly-reconnected
        SSH client (so the caller's finally can close the live one).
    """
    results: dict = {}
    captured: list = []  # Pass-1 successes eligible for Pass 2

    # ---- Pass 1 -------------------------------------------------------------
    idx = 0
    while idx < len(flavors):
        flavor = flavors[idx]
        name = flavor["flavor"]
        try:
            result = build_flavor_pass1(
                ssh_client, flavor, region, run_id, artifacts_base,
                allow_debug, expected_workflow,
            )
            captured.append(result)
            results[name] = "pass1-ok"
            idx += 1
        except _TRANSPORT_EXCEPTIONS as e:
            # Host suspect — the reconnect outcome decides alive vs dead (D12).
            logger.error(f"Transport/timeout error building flavor '{name}': {e}")
            results[name] = "failed-transport-indeterminate"
            new_client = reconnect_ssh(host, username, ssh_key_path)
            if new_client is not None:
                # Host was alive: clean up the abandoned upload, resume at next flavor.
                ssh_client = new_client
                kill_stale_coldsnap(ssh_client)
                logger.warning(f"Reconnected — resuming at the flavor after '{name}'")
                idx += 1
                continue
            # Host genuinely dead: stop Pass 1, mark the rest skipped (D7).
            logger.error("Reconnect failed — host is genuinely dead; aborting remaining Pass 1 flavors")
            for skipped in flavors[idx + 1:]:
                results[skipped["flavor"]] = "skipped-host-dead"
            break
        except RuntimeError as e:
            # Application error — host healthy, continue to the next flavor (D7).
            logger.error(f"Application error building flavor '{name}': {e}")
            results[name] = "failed-application"
            idx += 1

    # ---- Pass 2 -------------------------------------------------------------
    logger.info("")
    logger.info("=" * 80)
    logger.info(f"Pass 2 — waiting on {len(captured)} snapshot(s) and registering AMIs")
    logger.info("=" * 80)

    architecture = "x86_64"
    for result in captured:
        name = result["flavor"]
        snapshot_id = result["snapshot_id"]
        try:
            wait_for_snapshot(ec2_client, snapshot_id)

            ami_name = (
                f"attestable-ami-{name}-{architecture}-"
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S')}"
            )
            ami_id = register_ami(
                ec2_client, snapshot_id, architecture, ami_name,
                container_image_digest=result["container_image_digest"],
                producing_commit=producing_commit,
            )

            output_file = os.path.join(output_dir, f"ami_build_result-{name}.json")
            generate_build_result(
                ami_id=ami_id,
                snapshot_id=snapshot_id,
                region=region,
                pcr_measurements=result["pcr_measurements"],
                output_file=output_file,
                container_image_digest=result["container_image_digest"],
                producing_commit=producing_commit,
                relaxations=result["relaxations"],
            )
            results[name] = "success"
            logger.info(f"✓ Flavor '{name}' registered: {ami_id}")
        except Exception as e:
            # Isolate Pass-2 failure to this flavor — do not abort the others (D5).
            logger.error(f"Pass 2 failed for flavor '{name}': {e}")
            results[name] = "failed-pass2"

    return results, ssh_client


def cleanup_script_resources(
    ssh_key_path: Optional[str],
    ssh_client: Optional[paramiko.SSHClient] = None,
) -> None:
    """
    Release the resources the *script* owns (D1): close the SSH connection and
    securely delete the temporary SSH key.

    The script no longer runs ``terraform destroy`` — teardown of the shared build
    instance is owned by the workflow's ``always()`` destroy step, on the same
    runner that ran ``terraform apply`` (the build-ami Terraform state is gitignored
    and local, so only that runner can destroy it). Keeping destroy out of the
    script is what lets the workflow own the single, retrying, fail-loud teardown.

    Note: when this script is handed a pre-provisioned key path (the normal path),
    the key file is the workflow's to manage; secure-deleting it here is a
    best-effort courtesy and is guarded so it never fails the run.

    Args:
        ssh_key_path: Path to the temporary SSH key file (may be None)
        ssh_client: Optional SSH client to close

    Requirements: 20.7, 20.8, 20.9, 20.10, 20.11, 20.13, 20.14
    """
    # Close SSH client connection if open
    if ssh_client:
        try:
            ssh_client.close()
            logger.info("SSH client connection closed")
        except Exception as e:
            logger.error(f"Failed to close SSH client: {e}")

    # Securely delete the temporary SSH key file (overwrite before unlink)
    if ssh_key_path and os.path.exists(ssh_key_path):
        try:
            # Overwrite with random bytes before unlinking to prevent recovery
            # (Requirement 21.15)
            file_size = os.path.getsize(ssh_key_path)
            with open(ssh_key_path, 'wb') as f:
                f.write(os.urandom(file_size))
            os.unlink(ssh_key_path)
            logger.info(f"Temporary SSH key file securely deleted: {ssh_key_path}")
        except Exception as e:
            logger.warning(f"Failed to securely delete SSH key {ssh_key_path}: {e}")

def load_flavors_manifest(manifest_path: str) -> list:
    """
    Load and validate the multi-flavor build manifest.

    The workflow builds this from the per-flavor ``build-context-<flavor>``
    artifacts. It is a JSON array; each entry describes one flavor to build on the
    shared instance:

        [
          {"flavor": "default",
           "artifact_ref": "ghcr.io/owner/repo/pkg@sha256:...",
           "container_image_digest": "sha256:...",
           "relaxations": {}},
          ...
        ]

    Each ``artifact_ref`` is validated against the same strict, digest-pinned
    allowlist the single-flavor path used.

    Args:
        manifest_path: Path to the manifest JSON file

    Returns:
        List of validated flavor entries

    Raises:
        ValueError: If the manifest is malformed or any entry is invalid
    """
    with open(manifest_path) as f:
        manifest = json.load(f)

    if not isinstance(manifest, list) or not manifest:
        raise ValueError(f"Flavors manifest must be a non-empty JSON array: {manifest_path}")

    for entry in manifest:
        if not isinstance(entry, dict):
            raise ValueError(f"Each manifest entry must be an object, got: {entry!r}")
        if not entry.get("flavor"):
            raise ValueError(f"Manifest entry missing 'flavor': {entry!r}")
        if not entry.get("artifact_ref"):
            raise ValueError(f"Manifest entry for '{entry.get('flavor')}' missing 'artifact_ref'")
        validate_artifact_reference(entry["artifact_ref"])

    logger.info(f"Loaded {len(manifest)} flavor(s) from manifest: "
                f"{', '.join(e['flavor'] for e in manifest)}")
    return manifest


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Build one AMI per flavor on a single pre-provisioned instance'
    )

    parser.add_argument(
        '--host',
        type=str,
        required=True,
        help='Public host/IP of the pre-provisioned build instance (provisioned by '
             'the workflow via terraform apply). The script does NOT provision it.'
    )

    parser.add_argument(
        '--ssh-key-path',
        type=str,
        required=True,
        help='Path to the SSH private key file for the pre-provisioned instance.'
    )

    parser.add_argument(
        '--run-id',
        type=str,
        required=True,
        help='Run identifier (${github.run_id}-${github.run_attempt}), used SOLELY '
             'as an EBS-snapshot tag value (coldsnap upload --tag Key=run_id,Value=<run_id>). '
             'The script runs no Terraform and does no resource naming (D13).'
    )

    parser.add_argument(
        '--flavors-manifest',
        type=str,
        required=True,
        help='Path to the JSON manifest listing the flavors to build (each with '
             'flavor, artifact_ref, container_image_digest, relaxations).'
    )

    parser.add_argument(
        '--region',
        type=str,
        default='us-east-1',
        help='AWS region for AMI creation (e.g., us-east-1)'
    )

    parser.add_argument(
        '--output-dir',
        type=str,
        default='.',
        help='Directory where per-flavor ami_build_result-<flavor>.json files are '
             'written (default: current directory).'
    )

    parser.add_argument(
        '--artifacts-base-path',
        type=str,
        default='~/artifacts',
        help='Base artifacts working directory on the build instance, reset before '
             'each flavor (wipe-and-reuse). Default: ~/artifacts.'
    )

    parser.add_argument(
        '--allow-debug',
        action='store_true',
        default=False,
        help='Allow building AMIs from debug (SSH-enabled) artifacts. '
             'Without this flag, debug artifacts are rejected.'
    )

    parser.add_argument(
        '--expected-workflow',
        type=str,
        default=None,
        help='Expected workflow file path for provenance verification '
             '(e.g., .github/workflows/build-attestable-image.yml). '
             'When provided, the attestation workflow identity is verified against this path.'
    )

    parser.add_argument(
        '--producing-commit',
        type=str,
        default=None,
        help='The source commit (C_src) that triggered this build — the git SHA '
             'of the commit being built, NOT any write-back commit the pipeline '
             'creates afterward. Defaults to the GITHUB_SHA environment variable.'
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point for the multi-flavor AMI build driver."""
    args = parse_arguments()

    logger.info("=" * 80)
    logger.info("Starting AMI Build Process (single shared instance, multi-flavor)")
    logger.info("=" * 80)
    logger.info(f"Host: {args.host}")
    logger.info(f"Region: {args.region}")
    logger.info(f"Run id: {args.run_id}")

    # Validate configuration
    logger.info("")
    logger.info("Validating configuration...")
    try:
        validate_aws_region(args.region)
        validate_run_id(args.run_id)
        flavors = load_flavors_manifest(args.flavors_manifest)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Configuration validation failed: {e}")
        return 1

    # Ensure the output directory exists.
    os.makedirs(args.output_dir, exist_ok=True)

    # Initialize AWS clients
    ec2_client = boto3.client('ec2', region_name=args.region)

    ssh_client: Optional[paramiko.SSHClient] = None
    # C_src: the source commit that triggered this build. Prefer the explicit CLI
    # arg (so the workflow can document intent); fall back to GITHUB_SHA.
    producing_commit = args.producing_commit or os.environ.get("GITHUB_SHA")

    try:
        # Connect to the already-provisioned instance (the workflow owns terraform
        # apply/destroy; the script only builds on the instance — D1).
        logger.info("")
        logger.info("=" * 80)
        logger.info("Connecting to the pre-provisioned build instance")
        logger.info("=" * 80)
        ssh_client = verify_ssh_connectivity(args.host, 'ec2-user', args.ssh_key_path)

        # Install the toolchain ONCE, before the flavor loop (gate — D10).
        logger.info("")
        logger.info("=" * 80)
        logger.info("Installing tools once (before the flavor loop)")
        logger.info("=" * 80)
        install_all_tools(ssh_client)

        # Drive all flavors in two passes with failure isolation (D5/D7/D8/D12).
        logger.info("")
        logger.info("=" * 80)
        logger.info("Building flavors (two-pass driver)")
        logger.info("=" * 80)
        results, ssh_client = run_build_driver(
            ssh_client=ssh_client,
            ec2_client=ec2_client,
            flavors=flavors,
            host=args.host,
            username='ec2-user',
            ssh_key_path=args.ssh_key_path,
            region=args.region,
            run_id=args.run_id,
            producing_commit=producing_commit,
            artifacts_base=args.artifacts_base_path,
            output_dir=args.output_dir,
            allow_debug=args.allow_debug,
            expected_workflow=args.expected_workflow,
        )

        # Summarize per-flavor outcomes.
        logger.info("")
        logger.info("=" * 80)
        logger.info("Build summary")
        logger.info("=" * 80)
        for flavor in flavors:
            name = flavor["flavor"]
            logger.info(f"  {name}: {results.get(name, 'unknown')}")

        succeeded = [f for f, s in results.items() if s == "success"]
        # The whole run fails only if NO flavor produced an AMI — per-flavor failures
        # are isolated and carried forward by update-flavors-lock (D8). This preserves
        # the matrix's failure-isolation contract on the shared instance.
        if not succeeded:
            logger.error("No flavor produced an AMI — failing the run")
            return 1

        logger.info(f"✓ {len(succeeded)}/{len(flavors)} flavor(s) succeeded: {', '.join(succeeded)}")
        return 0

    except Exception as e:
        # A gate failure (e.g. install) or connect failure lands here — the whole run
        # aborts with zero results, which is the intended hard-abort for the gate (D10).
        logger.error("")
        logger.error("=" * 80)
        logger.error("AMI BUILD FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {e}")
        logger.error("=" * 80)
        return 1

    finally:
        # Release only the resources the script owns — SSH + temp key. Infrastructure
        # teardown is the workflow's always() destroy step (D1).
        logger.info("Releasing script-owned resources (SSH + temp key)...")
        try:
            cleanup_script_resources(
                ssh_key_path=args.ssh_key_path,
                ssh_client=ssh_client,
            )
        except Exception as cleanup_error:
            logger.error(f"Failed to release script resources: {cleanup_error}")


if __name__ == '__main__':
    sys.exit(main())
