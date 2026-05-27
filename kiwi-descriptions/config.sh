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

################################
# Rootless Docker User Setup   #
################################
# Create a dedicated non-root service user for rootless Docker.
# The gha-executor user runs the Remote Executor service and owns the
# rootless Docker daemon. Its home directory is at /home/gha-executor
# (writable via the tmpfs overlay on the read-only erofs root filesystem).
echo "Creating gha-executor service user..."
useradd --uid 1000 -m -s /bin/bash gha-executor
echo "✓ gha-executor user created (UID 1000)"

# Create directories that rootless Docker needs at runtime.
# The erofs root filesystem is read-only, so these directories must exist in
# the base image layer. The tmpfs overlay makes them writable at boot.
# - /var/lib/gha-executor/docker: Docker data-root (images, containers, volumes)
# - /home/gha-executor/.local/share: default XDG data directory (Docker may probe it)
echo "Creating rootless Docker runtime directories..."
mkdir -p /var/lib/gha-executor/docker
mkdir -p /home/gha-executor/.local/share
chown -R gha-executor:gha-executor /var/lib/gha-executor
chown -R gha-executor:gha-executor /home/gha-executor/.local
echo "✓ Rootless Docker runtime directories created"

# Configure subordinate UID/GID ranges for rootless Docker user namespace mapping.
# Two separate ranges are required because rootlesskit generates a third UID/GID
# mapping entry to cover container UIDs above the subordinate count. If only one
# range is provided, the third entry overlaps the second on the host side, causing
# "newuidmap: Invalid argument". Two non-overlapping ranges solve this.
echo "Configuring subordinate UID/GID mappings..."
echo "gha-executor:100000:65536" > /etc/subuid
echo "gha-executor:200000:65536" >> /etc/subuid
echo "gha-executor:100000:65536" > /etc/subgid
echo "gha-executor:200000:65536" >> /etc/subgid
echo "✓ /etc/subuid and /etc/subgid configured with two non-overlapping 65536-ID ranges"

# Enable lingering for gha-executor so that the user's systemd instance
# (and therefore the rootless Docker daemon) starts at boot and persists
# without requiring an active login session.
# During image build, systemd/logind is not running, so we create the linger
# file directly (equivalent to `loginctl enable-linger gha-executor`).
echo "Enabling loginctl linger for gha-executor..."
mkdir -p /var/lib/systemd/linger
touch /var/lib/systemd/linger/gha-executor
echo "✓ loginctl linger enabled for gha-executor"

# Set up rootless Docker for the gha-executor user.
# During KIWI image build, systemd is not running, so we cannot use
# `dockerd-rootless-setuptool.sh install` directly. Instead, we manually
# install the rootless Docker systemd user service unit and enable it.
# This is equivalent to what dockerd-rootless-setuptool.sh would create.
echo "Installing rootless Docker systemd user service for gha-executor..."

# Create the systemd user service directory for gha-executor
GHA_USER_HOME="/home/gha-executor"
mkdir -p "${GHA_USER_HOME}/.config/systemd/user"

# Create the rootless Docker systemd user service unit.
# This service runs dockerd-rootless.sh which wraps dockerd with rootlesskit
# for user namespace isolation, networking (slirp4netns), and storage (fuse-overlayfs).
cat > "${GHA_USER_HOME}/.config/systemd/user/docker.service" << 'DOCKER_SERVICE'
[Unit]
Description=Docker Application Container Engine (Rootless)
Documentation=https://docs.docker.com/go/rootless/

[Service]
Environment=PATH=/usr/bin:/sbin:/usr/sbin:/usr/local/bin
ExecStart=/usr/local/bin/dockerd-rootless.sh
ExecReload=/bin/kill -s HUP $MAINPID
TimeoutSec=0
RestartSec=2
Restart=always
StartLimitBurst=3
StartLimitInterval=60s
LimitNOFILE=infinity
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Delegate=yes
Type=notify
NotifyAccess=all
KillMode=mixed

[Install]
WantedBy=default.target
DOCKER_SERVICE

# Enable the rootless Docker user service by creating the symlink.
# This ensures the service starts automatically when the user's systemd
# instance starts (which happens at boot due to lingering).
mkdir -p "${GHA_USER_HOME}/.config/systemd/user/default.target.wants"
ln -sf "${GHA_USER_HOME}/.config/systemd/user/docker.service" \
    "${GHA_USER_HOME}/.config/systemd/user/default.target.wants/docker.service"

# Ensure correct ownership of all files created for gha-executor
chown -R gha-executor:gha-executor "${GHA_USER_HOME}/.config"

echo "✓ Rootless Docker systemd user service installed and enabled"

################################
# Rootless Docker Binary Check #
################################
# Verify that rootlesskit, slirp4netns, and fuse-overlayfs binaries
# (compiled from source by build-kiwi-image.sh) are present and executable
# at /usr/local/bin/. These are required for rootless Docker operation.
# Also refresh the dynamic linker cache so libslirp.so (in /usr/local/lib64/)
# is discoverable at runtime.
echo "Refreshing dynamic linker cache for /usr/local/lib64..."
ldconfig
echo "✓ ldconfig completed"

echo "Verifying rootless Docker binaries..."

ROOTLESS_BINARIES="rootlesskit slirp4netns fuse-overlayfs dockerd-rootless.sh"
for binary in ${ROOTLESS_BINARIES}; do
    if [ ! -f "/usr/local/bin/${binary}" ]; then
        echo "ERROR: Required binary /usr/local/bin/${binary} is missing"
        exit 1
    fi
    if [ ! -x "/usr/local/bin/${binary}" ]; then
        echo "ERROR: Binary /usr/local/bin/${binary} exists but is not executable"
        exit 1
    fi
    echo "  ✓ /usr/local/bin/${binary} present and executable"
done

echo "✓ All rootless Docker binaries verified"

################################
# NVIDIA Container Toolkit     #
# (GPU builds only)            #
################################
if [ "${ENABLE_GPU}" = "true" ]; then
    echo "=== Installing NVIDIA Container Toolkit (GPU mode) ==="

    # Add the official NVIDIA Container Toolkit RPM repository.
    # The repo URL is stable and provides packages for RHEL/CentOS/AL2023.
    cat > /etc/yum.repos.d/nvidia-container-toolkit.repo << 'NVIDIA_REPO'
[nvidia-container-toolkit]
name=nvidia-container-toolkit
baseurl=https://nvidia.github.io/libnvidia-container/stable/rpm/$basearch
repo_gpgcheck=1
gpgcheck=0
enabled=1
gpgkey=https://nvidia.github.io/libnvidia-container/gpgkey
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
NVIDIA_REPO

    # Install NVIDIA Container Toolkit at a pinned version for reproducibility.
    # To update: change the version below and verify compatibility with the
    # NVIDIA driver version installed in task 192.8.
    # Using v1.18.2 because it includes the nvidia-cdi-refresh systemd service
    # (introduced in v1.18.0) which automatically regenerates CDI specs at boot.
    NVIDIA_CTK_VERSION="1.18.2-1"
    echo "Installing nvidia-container-toolkit-${NVIDIA_CTK_VERSION}..."
    dnf install -y "nvidia-container-toolkit-${NVIDIA_CTK_VERSION}"
    echo "✓ nvidia-container-toolkit ${NVIDIA_CTK_VERSION} installed"

    # Configure the NVIDIA runtime for rootless Docker.
    # The daemon.json lives under the gha-executor user's config directory
    # because rootless Docker reads its config from $HOME/.config/docker/daemon.json.
    GHA_DOCKER_CONFIG="/home/gha-executor/.config/docker"

    echo "Configuring NVIDIA runtime for rootless Docker..."
    nvidia-ctk runtime configure --runtime=docker --config="${GHA_DOCKER_CONFIG}/daemon.json"
    echo "✓ NVIDIA runtime configured in ${GHA_DOCKER_CONFIG}/daemon.json"

    # Disable cgroup device access (required for rootless Docker which cannot
    # access the cgroup device controller). This setting is retained for backward
    # compatibility with toolkit versions prior to v1.18.
    echo "Configuring nvidia-container-cli no-cgroups..."
    nvidia-ctk config --set nvidia-container-cli.no-cgroups --in-place
    echo "✓ no-cgroups configured"

    # Set CDI mode for the container runtime. CDI (Container Device Interface)
    # is the officially recommended approach for rootless GPU access.
    echo "Configuring CDI mode..."
    nvidia-ctk config --in-place --set nvidia-container-runtime.mode=cdi
    echo "✓ CDI mode configured"

    # Enable the nvidia-cdi-refresh systemd units so CDI specs are regenerated
    # at boot when the GPU hardware is available. As of v1.18.0, the toolkit
    # ships nvidia-cdi-refresh.path (monitors for driver changes) and
    # nvidia-cdi-refresh.service (generates /var/run/cdi/nvidia.yaml).
    echo "Enabling nvidia-cdi-refresh systemd units..."
    systemctl enable nvidia-cdi-refresh.path || {
        echo "WARNING: nvidia-cdi-refresh.path not found"
    }
    systemctl enable nvidia-cdi-refresh.service || {
        echo "WARNING: nvidia-cdi-refresh.service not found"
    }
    echo "✓ nvidia-cdi-refresh units enabled"

    # Ensure correct ownership of the Docker config directory
    chown -R gha-executor:gha-executor "${GHA_DOCKER_CONFIG}"
    echo "✓ Docker config ownership set to gha-executor"

    echo "=== NVIDIA Container Toolkit installation complete ==="
else
    echo "GPU support disabled (default) — skipping NVIDIA Container Toolkit installation"
fi

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
# Harden Console / Login Access#
################################

# Lock the root account so it cannot be used for password-based login,
# even if a console or serial interface were somehow accessible.
echo "Locking root account..."
passwd -l root
echo "✓ Root account locked"

# Mask the serial getty so no login prompt is ever spawned on ttyS0.
# The serial console is still active for read-only log output (console=ttyS0
# in the kernel cmdline), but masking the unit prevents interactive login.
echo "Masking serial-getty@ttyS0.service..."
systemctl mask serial-getty@ttyS0.service
echo "✓ serial-getty@ttyS0.service masked"

################################
# Install Python Dependencies  #
################################
echo "=== Installing Python Dependencies ==="

# Check if pre-downloaded wheels exist (downloaded by build-kiwi-image.sh which has network access)
if [ ! -d "/tmp/kiwi-build/wheels" ]; then
    echo "ERROR: Pre-downloaded wheels not found in /tmp/kiwi-build/wheels/"
    exit 1
fi

# Check if requirements.txt with hashes exists (created by build-kiwi-image.sh)
if [ ! -f "/tmp/kiwi-build/requirements.txt" ]; then
    echo "ERROR: requirements.txt not found in /tmp/kiwi-build/"
    exit 1
fi

# Install dependencies from pre-downloaded wheels with hash verification (fully offline).
# The requirements.txt contains hashes for all packages: original sdist/wheel hashes
# from uv.lock for binary deps, plus the computed wheel hash for wolfcrypt.
# This ensures that even the offline installation step verifies every wheel's integrity.
echo "Installing dependencies from pre-downloaded wheels with hash verification..."
pip3.11 install --no-index --find-links /tmp/kiwi-build/wheels --require-hashes -r /tmp/kiwi-build/requirements.txt

# Verify critical packages are importable
echo "Verifying critical packages..."
python3.11 -c "import fastapi" || { echo "ERROR: fastapi not importable"; exit 1; }
python3.11 -c "import uvicorn" || { echo "ERROR: uvicorn not importable"; exit 1; }
python3.11 -c "import requests" || { echo "ERROR: requests not importable"; exit 1; }
python3.11 -c "import jwt" || { echo "ERROR: PyJWT not importable"; exit 1; }
python3.11 -c "import docker" || { echo "ERROR: docker not importable"; exit 1; }
python3.11 -c "import wolfcrypt" || { echo "ERROR: wolfcrypt not importable"; exit 1; }

echo "✓ All critical packages verified successfully"
echo "✓ Python dependency installation complete"

echo "System configuration complete"
