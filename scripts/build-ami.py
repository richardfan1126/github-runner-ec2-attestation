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
import subprocess
import sys
import logging
import tempfile
import time
from typing import Any, Optional, Tuple
from urllib import request

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

def get_user_public_ip() -> str:
    """
    Get the user's public IP address for SSH access configuration.
    
    Returns:
        Public IP address as a string
    """
    with request.urlopen('https://checkip.amazonaws.com', timeout=5) as response:
        my_ip = response.read().decode('utf-8').strip()
        logger.info(f"Detected my public IP: {my_ip}")
        return my_ip

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

def provision_ami_build_instance(
    region: str,
    instance_type: str,
) -> Tuple[str, str, str]:
    """
    Provision AMI build EC2 instance using Terraform.
    
    Args:
        region: AWS region for the instance
        instance_type: EC2 instance type
    
    Returns:
        Tuple of (instance_id, instance_public_ip, ssh_private_key_pem)
    """
    logger.info("Provisioning AMI build EC2 instance with Terraform...")
    
    my_public_ip = get_user_public_ip()
    allowed_ssh_cidr = f"{my_public_ip}/32"
    
    logger.info(f"  Region: {region}")
    logger.info(f"  Instance Type: {instance_type}")
    logger.info(f"  Allowed SSH CIDR: {allowed_ssh_cidr}")
    
    # Initialize Terraform
    tf_working_dir = Path(__file__).parent.parent / 'terraform' / 'build-ami'
    
    # Initialize Terraform
    logger.info("Initializing Terraform...")
    result = subprocess.run(
        ['terraform', 'init'],
        cwd=tf_working_dir,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        logger.error(f"Terraform init failed: {result.stderr}")
        raise RuntimeError(f"Terraform init failed: {result.stderr}")
    
    logger.info("Terraform initialized successfully")
    
    # Prepare variables
    tf_vars = {
        'region': region,
        'instance_type': instance_type,
        'allowed_ssh_cidr': allowed_ssh_cidr
    }
    
    # Apply Terraform configuration
    logger.info("Applying Terraform configuration (this may take 2-3 minutes)...")
    cmd = ['terraform', 'apply', '-auto-approve']
    for key, value in tf_vars.items():
        cmd.extend(['-var', f'{key}={value}'])
    
    result = subprocess.run(
        cmd,
        cwd=tf_working_dir,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        logger.error(f"Terraform apply failed: {result.stderr}")
        raise RuntimeError(f"Terraform apply failed: {result.stderr}")
    
    logger.info("AMI build infrastructure provisioned successfully")
    
    # Retrieve outputs
    logger.info("Retrieving Terraform outputs...")
    result = subprocess.run(
        ['terraform', 'output', '-json'],
        cwd=tf_working_dir,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError("Failed to retrieve Terraform outputs")
    
    outputs = json.loads(result.stdout)
    
    # Parse outputs
    instance_id = outputs['instance_id']['value']
    instance_public_ip = outputs['instance_public_ip']['value']
    ssh_private_key = outputs['ssh_private_key']['value']
    
    logger.info(f"AMI build instance provisioned: {instance_id}")
    logger.info(f"Public IP: {instance_public_ip}")
    
    return instance_id, instance_public_ip, ssh_private_key

def save_ssh_private_key(ssh_private_key_pem: str) -> str:
    """
    Save SSH private key for SSH client to connect to the instance
    
    Args:
        ssh_private_key_pem: SSH private key in PEM format
    
    Returns:
        Path to the temporary key file
    """
    # Create temporary file
    fd, key_path = tempfile.mkstemp(suffix='.pem', prefix='import-key-')
    
    try:
        # Write key to file
        with os.fdopen(fd, 'w') as f:
            f.write(ssh_private_key_pem)
        
        # Set secure permissions (600 - owner read/write only)
        os.chmod(key_path, 0o600)
        
        logger.info(f"SSH private key saved to: {key_path}")
        return key_path
        
    except Exception as e:
        # Clean up on error
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.unlink(key_path)
        except Exception:
            pass
        raise RuntimeError(f"Failed to save SSH private key: {e}")

def wait_for_instance_ready(ec2_client: Any, instance_id: str, timeout: int = 300) -> None:
    """
    Wait for the instance to be running and status checks to pass.
    
    Args:
        ec2_client: Boto3 EC2 client
        instance_id: Instance ID to wait for
        timeout: Maximum time to wait in seconds
    """
    logger.info(f"Waiting for instance {instance_id} to be ready...")
    
    try:
        # Wait for instance to be running
        waiter = ec2_client.get_waiter('instance_running')
        waiter.wait(
            InstanceIds=[instance_id],
            WaiterConfig={'Delay': 15, 'MaxAttempts': timeout // 15}
        )
        logger.info("Instance is running")
        
        # Wait for status checks to pass
        waiter = ec2_client.get_waiter('instance_status_ok')
        waiter.wait(
            InstanceIds=[instance_id],
            WaiterConfig={'Delay': 15, 'MaxAttempts': timeout // 15}
        )
        logger.info("Instance status checks passed")
        
    except WaiterError as e:
        logger.error(f"Instance failed to become ready: {e}")
        raise

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
    stream_output: bool = True
) -> tuple[int, str, str]:
    """
    Execute a command on the remote instance via SSH.
    
    Args:
        ssh_client: Connected paramiko SSHClient
        command: Command to execute
        stream_output: Whether to stream output to logger
    
    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
    logger.debug(f"Executing command: {command}")
    
    stdin, stdout, stderr = ssh_client.exec_command(command, get_pty=False)
    
    stdout_lines = []
    stderr_lines = []
    
    # Set channels to non-blocking to avoid deadlock
    stdout.channel.setblocking(0)
    stderr.channel.setblocking(0)
    
    # Read stdout and stderr concurrently to avoid buffer deadlock
    while not stdout.channel.exit_status_ready():
        # Wait for data to be available
        if stdout.channel.recv_ready():
            data = stdout.channel.recv(4096).decode('utf-8', errors='replace')
            for line in data.splitlines():
                line = line.rstrip()
                if line:
                    stdout_lines.append(line)
                    if stream_output:
                        logger.info(f"  {line}")
        
        if stderr.channel.recv_stderr_ready():
            data = stderr.channel.recv_stderr(4096).decode('utf-8', errors='replace')
            for line in data.splitlines():
                line = line.rstrip()
                if line:
                    stderr_lines.append(line)
                    if stream_output:
                        logger.warning(f"  {line}")

        time.sleep(0.1)
    
    # Read any remaining data after command completes
    while stdout.channel.recv_ready():
        data = stdout.channel.recv(4096).decode('utf-8', errors='replace')
        for line in data.splitlines():
            line = line.rstrip()
            if line:
                stdout_lines.append(line)
                if stream_output:
                    logger.info(f"  {line}")
    
    while stderr.channel.recv_stderr_ready():
        data = stderr.channel.recv_stderr(4096).decode('utf-8', errors='replace')
        for line in data.splitlines():
            line = line.rstrip()
            if line:
                stderr_lines.append(line)
                if stream_output:
                    logger.warning(f"  {line}")
    
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
    
    # Install git and gcc via dnf package manager
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        "sudo dnf install -y git gcc",
        stream_output=True
    )
    if exit_code != 0:
        raise RuntimeError(f"Failed to install system packages: {stderr}")
    
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
        raise RuntimeError(f"Failed to import Rust GPG signing key: {stderr}")
    
    # Step 2: Download the standalone tarball
    logger.info("  Downloading Rust standalone tarball...")
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        'curl --proto "=https" --tlsv1.2 -sSf https://static.rust-lang.org/dist/rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz -o /tmp/rust-1.94.1.tar.gz',
        stream_output=True
    )
    if exit_code != 0:
        raise RuntimeError(f"Failed to download Rust tarball: {stderr}")
    
    # Step 3: Download the detached GPG signature
    logger.info("  Downloading GPG signature...")
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        'curl --proto "=https" --tlsv1.2 -sSf https://static.rust-lang.org/dist/rust-1.94.1-x86_64-unknown-linux-gnu.tar.gz.asc -o /tmp/rust-1.94.1.tar.gz.asc',
        stream_output=True
    )
    if exit_code != 0:
        raise RuntimeError(f"Failed to download Rust GPG signature: {stderr}")
    
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
        raise RuntimeError(f"Failed to download ORAS: {stderr}")
    
    # Verify SHA-256 checksum of the downloaded archive (Requirements: 17.13, 17.14)
    checksum_cmd = f"sha256sum /tmp/oras_{oras_version}_linux_amd64.tar.gz"
    exit_code, stdout, stderr = execute_remote_command(ssh_client, checksum_cmd, stream_output=False)
    
    if exit_code != 0:
        raise RuntimeError(f"Failed to compute ORAS checksum: {stderr}")
    
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
        raise RuntimeError(f"Failed to install GitHub CLI: {stderr}")
    
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
    
    # Clone coldsnap repository at pinned tag
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        f"git clone --branch {COLDSNAP_VERSION} --depth 1 https://github.com/awslabs/coldsnap.git",
        stream_output=True
    )
    if exit_code != 0:
        raise RuntimeError(f"Failed to clone coldsnap repository: {stderr}")
    
    # Build and install coldsnap using cargo install --locked.
    # PATH must include the cargo bin dir so that cargo can locate rustc internally —
    # non-login SSH sessions do not source ~/.bashrc or ~/.profile, so the directory
    # is not on PATH by default even though we installed Rust there.
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

def install_all_tools(ssh_client: paramiko.SSHClient) -> None:
    """
    Install all required tools on the build instance in sequence.
    
    Executes installation functions in order: system dependencies, Rust, ORAS,
    GitHub CLI, and coldsnap. Logs installation progress at INFO level.
    Terminates build immediately if any tool installation fails.
    
    Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9, 16.10, 16.11, 16.12
    
    Args:
        ssh_client: Connected paramiko SSHClient
        
    Raises:
        RuntimeError: If any tool installation fails with descriptive error
    """
    logger.info("Installing all required tools...")
    logger.info("")
    
    try:
        # Install system dependencies (git, gcc)
        install_system_dependencies(ssh_client)
        logger.info("")
        
        # Install Rust toolchain
        install_rust(ssh_client)
        logger.info("")
        
        # Install ORAS CLI
        install_oras(ssh_client)
        logger.info("")
        
        # Install GitHub CLI
        install_github_cli(ssh_client)
        logger.info("")
        
        # Install coldsnap
        install_coldsnap(ssh_client)
        logger.info("")
        
        logger.info("✓ All tools installed successfully")
        
    except RuntimeError as e:
        logger.error(f"Tool installation failed: {e}")
        logger.error("Build process will terminate immediately")
        raise

def pull_artifact_from_ghcr(ssh_client: paramiko.SSHClient, artifact_ref: str) -> None:
    """
    Pull artifact bundle from GitHub Container Registry using ORAS.
    
    Creates ~/artifacts directory on the build instance, executes oras pull
    to download the artifact bundle using the exact sha256 digest (ignoring
    any mutable tag), streams output to logger, verifies exit code is 0,
    and lists downloaded files with sizes.
    
    Args:
        ssh_client: Connected paramiko SSHClient
        artifact_ref: GitHub Container Registry artifact reference (digest-pinned)
    
    Raises:
        RuntimeError: If directory creation, ORAS pull, or file listing fails
    
    Requirements: 15.19, 18.1, 18.2, 18.3, 18.8, 18.9
    """
    # Use only the digest for the pull — ignore any mutable tag
    digest_ref = get_digest_pinned_ref(artifact_ref)
    logger.info(f"Pulling artifact from GHCR using digest: {digest_ref}")
    
    # Create working directory for artifacts
    exit_code, _, stderr = execute_remote_command(
        ssh_client,
        "mkdir -p ~/artifacts",
        stream_output=False
    )
    
    if exit_code != 0:
        raise RuntimeError(f"Failed to create artifacts directory: {stderr}")
    
    # Pull artifacts using ORAS with digest-pinned reference (no authentication required for public repos)
    logger.info("Downloading artifacts with ORAS...")
    pull_cmd = f"cd ~/artifacts && oras pull {digest_ref}"
    
    exit_code, stdout, stderr = execute_remote_command(ssh_client, pull_cmd, stream_output=True)
    
    if exit_code != 0:
        raise RuntimeError(f"ORAS pull failed: {stderr}")
    
    logger.info("Artifacts downloaded successfully")
    
    # List downloaded files in ~/artifacts/build-output using ls -lh
    logger.info("Listing downloaded artifacts...")
    exit_code, stdout, stderr = execute_remote_command(
        ssh_client,
        "ls -lh ~/artifacts/build-output",
        stream_output=False
    )
    
    if exit_code != 0:
        raise RuntimeError(f"Failed to list artifacts in build-output: {stderr}")
    
    logger.info(f"Downloaded artifacts:\n{stdout}")


def validate_artifact_files(ssh_client: paramiko.SSHClient) -> None:
    """
    Validate that required artifact files exist after download.
    
    Verifies the raw disk image exists using ls ~/artifacts/build-output/*.raw
    and pcr_measurements.json exists using test -f command.
    
    Args:
        ssh_client: Connected paramiko SSHClient
    
    Raises:
        RuntimeError: If raw disk image or pcr_measurements.json is missing
    
    Requirements: 18.4, 18.5, 18.10, 18.11
    """
    logger.info("Validating downloaded artifact files...")
    
    # Verify raw disk image exists
    exit_code, stdout, _ = execute_remote_command(
        ssh_client,
        "ls ~/artifacts/build-output/*.raw",
        stream_output=False
    )
    
    if exit_code != 0:
        raise RuntimeError("Raw disk image (.raw file) not found in build-output directory")
    
    # Verify pcr_measurements.json exists
    exit_code, _, _ = execute_remote_command(
        ssh_client,
        "test -f ~/artifacts/build-output/pcr_measurements.json",
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


def validate_pcr_measurements(ssh_client: paramiko.SSHClient) -> dict:
    """
    Read and validate PCR measurements from pcr_measurements.json.
    
    Reads pcr_measurements.json content using cat command, parses JSON,
    extracts PCR4 and PCR7 from Measurements field, validates they are
    non-empty hex strings, and returns a dict with pcr4 and pcr7.
    
    Args:
        ssh_client: Connected paramiko SSHClient
    
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
        "cat ~/artifacts/build-output/pcr_measurements.json",
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

def upload_snapshot(ssh_client: paramiko.SSHClient, region: str) -> str:
    """
    Upload the raw disk image to an EBS snapshot using coldsnap.
    
    Args:
        ssh_client: Connected paramiko SSHClient
        region: AWS region for snapshot creation
    
    Returns:
        Snapshot ID string
    """
    logger.info("Uploading raw disk image to EBS snapshot...")
    
    # Find the raw disk image file in build-output directory using programmatic listing
    exit_code, stdout, stderr = execute_remote_command(
        ssh_client,
        "find ~/artifacts/build-output -maxdepth 1 -name '*.raw' -type f",
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
    
    # Upload using coldsnap with subprocess list arguments (no shell interpolation)
    logger.info("Uploading snapshot with coldsnap (this may take several minutes)...")
    
    coldsnap_command = f"/home/ec2-user/.cargo/bin/coldsnap upload {raw_image_path}"
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
        },
    }

    with open(output_file, 'w') as f:
        json.dump(build_result, f, indent=2)

    logger.info(f"Build result: {json.dumps(build_result, indent=2)}")

    return build_result


def cleanup_infrastructure(
    region: str,
    instance_type: str,
    allowed_ssh_cidr: str,
    ssh_key_path: str,
    ssh_client: Optional[paramiko.SSHClient] = None
) -> None:
    """
    Destroy all resources:
    - Close SSH client connection if open
    - Terraform infrastructure
    - SSH key

    Args:
        region: AWS region for the instance
        instance_type: EC2 instance type for the instance
        allowed_ssh_cidr: CIDR block for SSH access
        ssh_key_path: Path to the temporary SSH key file
        ssh_client: Optional SSH client to close before cleanup

    Requirements: 20.7, 20.8, 20.9, 20.10, 20.11, 20.13, 20.14
    """
    # Close SSH client connection if open
    if ssh_client:
        try:
            ssh_client.close()
            logger.info("SSH client connection closed")
        except Exception as e:
            logger.error(f"Failed to close SSH client: {e}")

    logger.info("Destroying infrastructure with Terraform...")

    # Initialize Terraform
    tf_working_dir = Path(__file__).parent.parent / 'terraform' / 'build-ami'

    # Prepare variables (same as used during apply)
    tf_vars = {
        'region': region,
        'instance_type': instance_type,
        'allowed_ssh_cidr': allowed_ssh_cidr
    }

    # Destroy infrastructure with auto-approve flag and variables
    cmd = ['terraform', 'destroy', '-auto-approve']
    for key, value in tf_vars.items():
        cmd.extend(['-var', f'{key}={value}'])

    result = subprocess.run(
        cmd,
        cwd=tf_working_dir,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        logger.error(f"Terraform destroy failed: {result.stderr}")
    else:
        logger.info("Infrastructure destroyed successfully")

    # NOTE: Terraform state (terraform/build-ami/) contains sensitive SSH key
    # material. Ensure state files are not committed to version control and are
    # stored securely if retained.

    # Clean up temporary SSH key file
    if ssh_key_path and os.path.exists(ssh_key_path):
        try:
            # Overwrite with random bytes before unlinking to prevent recovery
            # (Requirement 21.15)
            file_size = os.path.getsize(ssh_key_path)
            with open(ssh_key_path, 'wb') as f:
                f.write(os.urandom(file_size))
            os.unlink(ssh_key_path)
            logger.info(f"Temporary SSH key file securely deleted: {ssh_key_path}")
        except Exception:
            pass

def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Convert pre-built KIWI image from GitHub Container Registry and create AMI'
    )
    
    parser.add_argument(
        '--artifact-ref',
        type=str,
        required=True,
        help='GitHub Container Registry artifact reference with digest pin '
             '(e.g., ghcr.io/owner/repo/package:tag@sha256:<64 hex chars> or '
             'ghcr.io/owner/repo/package@sha256:<64 hex chars>). '
             'A @sha256: digest is REQUIRED; tag-only references are rejected.'
    )
    
    parser.add_argument(
        '--region',
        type=str,
        default='us-east-1',
        help='AWS region for AMI creation (e.g., us-east-1)'
    )
    
    parser.add_argument(
        '--instance-type',
        type=str,
        default='c5.9xlarge',
        help='Instance type for AMI build instance (default: c5.9xlarge)'
    )
    
    parser.add_argument(
        '--output-file',
        type=str,
        default='ami_build_result.json',
        help='Output file for build result (default: ami_build_result.json)'
    )

    parser.add_argument(
        '--allow-debug',
        action='store_true',
        default=False,
        help='Allow building AMI from debug (SSH-enabled) artifacts. '
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
        '--container-image-digest',
        type=str,
        default=None,
        help='The baked execution container image manifest digest (sha256:...). '
             'Tagged onto the registered AMI and emitted in the single-entry '
             'verifier record so external verifiers can map AMI -> image digest.'
    )

    return parser.parse_args()

def main() -> int:
    """Main entry point for AMI build script."""
    args = parse_arguments()
    
    logger.info("=" * 80)
    logger.info("Starting AMI Build Process")
    logger.info("=" * 80)
    logger.info(f"Artifact Reference: {args.artifact_ref}")
    logger.info(f"Region: {args.region}")
    logger.info(f"Instance Type: {args.instance_type}")
    
    # Validate configuration (Requirement 14.2)
    logger.info("")
    logger.info("Validating configuration...")
    try:
        validate_artifact_reference(args.artifact_ref)
        validate_aws_region(args.region)
        validate_output_file_path(args.output_file)
    except ValueError as e:
        logger.error(f"Configuration validation failed: {e}")
        return 1

    # Initialize AWS clients
    ec2_client = boto3.client('ec2', region_name=args.region)
    
    instance_id: Optional[str] = None
    ssh_client: Optional[paramiko.SSHClient] = None
    ssh_key_path: Optional[str] = None
    ami_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    
    # Contruct allowed SSH CIDR from user public IP address
    my_public_ip = get_user_public_ip()
    allowed_ssh_cidr = f"{my_public_ip}/32"
    
    try:
        # Provision AMI build instance
        logger.info("")
        logger.info("=" * 80)
        logger.info("Provisioning AMI build instance")
        logger.info("=" * 80)
        
        instance_id, public_ip, ssh_private_key = provision_ami_build_instance(
            region=args.region,
            instance_type=args.instance_type,
        )

        # Save SSH private key to temporary file
        ssh_key_path = save_ssh_private_key(ssh_private_key)
        
        # Wait for instance to be ready
        wait_for_instance_ready(ec2_client, instance_id)
        
        # Verify SSH connectivity
        ssh_client = verify_ssh_connectivity(
            public_ip,
            'ec2-user',
            ssh_key_path
        )

        # Use SSH command to install tools on the instance
        logger.info("")
        logger.info("=" * 80)
        logger.info("Installing Tools on AMI build Instance")
        logger.info("=" * 80)
        
        install_all_tools(ssh_client)

        # Verify artifact signature
        logger.info("")
        logger.info("=" * 80)
        logger.info("Verifying Artifact Signature")
        logger.info("=" * 80)
        
        signature_valid = verify_artifact_signature(
            ssh_client,
            args.artifact_ref,
            expected_workflow=args.expected_workflow
        )

        if not signature_valid:
            # Signature verification failed - terminate without creating AMI
            logger.error("")
            logger.error("=" * 80)
            logger.error("SIGNATURE VERIFICATION FAILED")
            logger.error("=" * 80)
            logger.error("The artifact signature could not be verified.")
            logger.error("This could indicate:")
            logger.error("  - The artifact was not attested")
            logger.error("  - The signature does not match the expected GitHub identity")
            logger.error("  - The artifact has been tampered with")
            logger.error("")
            logger.error("AMI creation will NOT proceed.")
            logger.error("Please verify the artifact reference and try again.")
            logger.error("=" * 80)

            raise RuntimeError("SIGNATURE VERIFICATION FAILED")

        # Pull artifact from GHCR
        logger.info("")
        logger.info("=" * 80)
        logger.info("Pulling Artifact from GitHub Container Registry")
        logger.info("=" * 80)
        
        pull_artifact_from_ghcr(ssh_client, args.artifact_ref)

        # Validate artifact files
        logger.info("")
        logger.info("Validating artifact files...")
        validate_artifact_files(ssh_client)

        # Check debug annotation and enforce production gate (Requirement 46.3, 46.4, 46.5)
        logger.info("")
        logger.info("Checking debug annotation...")
        check_debug_annotation(ssh_client, args.artifact_ref, args.allow_debug)

        # Validate and extract PCR measurements
        logger.info("")
        logger.info("Validating PCR measurements...")
        pcr_measurement = validate_pcr_measurements(ssh_client)

        # Upload snapshot and register AMI
        logger.info("")
        logger.info("=" * 80)
        logger.info("Uploading Snapshot and Registering AMI")
        logger.info("=" * 80)
        
        architecture = "x86_64"
        snapshot_id = upload_snapshot(ssh_client, args.region)
        wait_for_snapshot(ec2_client, snapshot_id)
        ami_name = f"attestable-ami-imported-{architecture}-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S')}"
        # Producing commit for the verifier record (GitHub Actions sets GITHUB_SHA).
        producing_commit = os.environ.get("GITHUB_SHA")
        ami_id = register_ami(
            ec2_client, snapshot_id, architecture, ami_name,
            container_image_digest=args.container_image_digest,
            producing_commit=producing_commit,
        )

        # Save build results
        logger.info("")
        logger.info("=" * 80)
        logger.info("Save Results")
        logger.info("=" * 80)

        generate_build_result(
            ami_id=ami_id,
            snapshot_id=snapshot_id,
            region=args.region,
            pcr_measurements=pcr_measurement,
            output_file=args.output_file,
            container_image_digest=args.container_image_digest,
            producing_commit=producing_commit,
        )

        return 0

    except Exception as e:
        logger.error("")
        logger.error("=" * 80)
        logger.error("AMI BUILD FAILED")
        logger.error("=" * 80)
        logger.error(f"Error: {e}")
        logger.error("=" * 80)
        return 1

    finally:
        # Cleanup infrastructure
        logger.warning("Cleaning up infrastructure...")
        try:
            cleanup_infrastructure(
                region=args.region,
                instance_type=args.instance_type,
                allowed_ssh_cidr=allowed_ssh_cidr,
                ssh_key_path=ssh_key_path,
                ssh_client=ssh_client
            )
        except Exception as cleanup_error:
            logger.error(f"Failed to cleanup infrastructure: {cleanup_error}")

if __name__ == '__main__':
    sys.exit(main())
