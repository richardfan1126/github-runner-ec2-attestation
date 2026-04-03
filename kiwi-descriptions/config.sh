#!/bin/bash
# KIWI image configuration script
# This script runs during image creation to configure the system

set -e

# Enable the set-hostname-imds service. This will set the hostname
# based on IMDS in place of cloud-init
echo "enable set-hostname-imds.service" >> /usr/lib/systemd/system-preset/80-amzn-overrides.preset
systemctl preset set-hostname-imds

# Enable the GitHub Actions Remote Executor service
systemctl enable github-actions-remote-executor.service

# Enable the Docker daemon so containers can be managed at runtime
echo "Enabling Docker service..."
systemctl enable docker
echo "✓ Docker service enabled"

################################
# Load Container Image          #
################################
echo "=== Loading Container Image ==="

if [ ! -f "/tmp/kiwi-build/container-image.tar" ]; then
    echo "ERROR: Container image tar not found at /tmp/kiwi-build/container-image.tar"
    exit 1
fi

echo "Loading container image from /tmp/kiwi-build/container-image.tar..."
if ! docker load -i /tmp/kiwi-build/container-image.tar; then
    echo "ERROR: Failed to load container image from /tmp/kiwi-build/container-image.tar"
    exit 1
fi

echo "✓ Container image loaded successfully"

################################
# Conditional sshd Enablement  #
################################
if [ "${ENABLE_SSH}" = "true" ]; then
    echo "SSH debug access enabled — enabling sshd service"
    systemctl enable sshd

    # Pre-generate SSH host keys so sshd can start on the read-only root filesystem.
    # The overlayroot configuration uses a tmpfs overlay with a read-only erofs base,
    # and sshd-keygen may fail to write keys before the overlay is ready at boot.
    echo "Pre-generating SSH host keys..."
    ssh-keygen -A
    echo "✓ SSH host keys generated"

    # Enable cloud-init services so it can provision SSH keys from IMDS at boot.
    # cloud-init is included in the image (ignore directives removed by build-kiwi-image.sh)
    # but its services must be explicitly enabled.
    echo "Enabling cloud-init services..."
    systemctl enable cloud-init-local.service
    systemctl enable cloud-init.service
    systemctl enable cloud-config.service
    systemctl enable cloud-final.service
    echo "✓ cloud-init services enabled"

    # Create the ec2-user account. On standard AL2023 AMIs this user is pre-created,
    # but this KIWI image has no <users> section. cloud-init's default_user config
    # expects ec2-user to exist (or be creatable). Creating it at build time in the
    # base image ensures it persists in the read-only erofs layer and cloud-init only
    # needs to write the authorized_keys file to the tmpfs overlay at boot.
    echo "Creating ec2-user account..."
    useradd -m -s /bin/bash ec2-user
    echo "ec2-user ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/ec2-user
    chmod 440 /etc/sudoers.d/ec2-user
    echo "✓ ec2-user account created with passwordless sudo"
else
    echo "SSH debug access disabled (default secure behavior)"
fi

################################
# Install Python Dependencies  #
################################
echo "=== Installing Python Dependencies ==="

# Check if pre-downloaded wheels exist (downloaded by build-kiwi-image.sh which has network access)
if [ ! -d "/tmp/kiwi-build/wheels" ]; then
    echo "ERROR: Pre-downloaded wheels not found in /tmp/kiwi-build/wheels/"
    exit 1
fi

# Install dependencies from pre-downloaded wheels (fully offline)
echo "Installing dependencies from pre-downloaded wheels..."
pip3.11 install --no-index --find-links /tmp/kiwi-build/wheels /tmp/kiwi-build/wheels/*.whl

# Verify critical packages are importable
echo "Verifying critical packages..."
python3.11 -c "import fastapi" || { echo "ERROR: fastapi not importable"; exit 1; }
python3.11 -c "import uvicorn" || { echo "ERROR: uvicorn not importable"; exit 1; }
python3.11 -c "import requests" || { echo "ERROR: requests not importable"; exit 1; }
python3.11 -c "import jwt" || { echo "ERROR: PyJWT not importable"; exit 1; }
python3.11 -c "import docker" || { echo "ERROR: docker not importable"; exit 1; }

echo "✓ All critical packages verified successfully"
echo "✓ Python dependency installation complete"

echo "System configuration complete"
