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
# Install Python Dependencies  #
################################
echo "=== Installing Python Dependencies ==="

# Check if pyproject.toml exists
if [ ! -f "/tmp/kiwi-build/pyproject.toml" ]; then
    echo "ERROR: pyproject.toml not found in /tmp/kiwi-build/"
    exit 1
fi

if [ ! -f "/tmp/kiwi-build/uv.lock" ]; then
    echo "ERROR: uv.lock not found in /tmp/kiwi-build/"
    exit 1
fi

# Install uv package manager
echo "Installing uv package manager..."
curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH for this session
export PATH="/root/.cargo/bin:$PATH"

# Verify uv installation
if ! command -v uv &> /dev/null; then
    echo "ERROR: uv installation failed"
    exit 1
fi

echo "uv installed successfully: $(uv --version)"

# Install dependencies from pyproject.toml to system Python
echo "Installing dependencies from pyproject.toml..."
cd /tmp/kiwi-build

if ! uv sync --frozen --no-dev; then
    echo "ERROR: Failed to install dependencies with uv sync"
    exit 1
fi

# Verify critical packages are importable
echo "Verifying critical packages..."
python3 -c "import fastapi" || { echo "ERROR: fastapi not importable"; exit 1; }
python3 -c "import uvicorn" || { echo "ERROR: uvicorn not importable"; exit 1; }
python3 -c "import requests" || { echo "ERROR: requests not importable"; exit 1; }

echo "✓ All critical packages verified successfully"
echo "✓ Python dependency installation complete"

echo "System configuration complete"
