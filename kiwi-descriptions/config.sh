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

# Check if pre-downloaded wheels exist (downloaded by build-kiwi-image.sh which has network access)
if [ ! -d "/tmp/kiwi-build/wheels" ]; then
    echo "ERROR: Pre-downloaded wheels not found in /tmp/kiwi-build/wheels/"
    exit 1
fi

# Install dependencies from pre-downloaded wheels (fully offline)
echo "Installing dependencies from pre-downloaded wheels..."
pip3 install --no-index --find-links /tmp/kiwi-build/wheels /tmp/kiwi-build/wheels/*.whl

# Verify critical packages are importable
echo "Verifying critical packages..."
python3 -c "import fastapi" || { echo "ERROR: fastapi not importable"; exit 1; }
python3 -c "import uvicorn" || { echo "ERROR: uvicorn not importable"; exit 1; }
python3 -c "import requests" || { echo "ERROR: requests not importable"; exit 1; }

echo "✓ All critical packages verified successfully"
echo "✓ Python dependency installation complete"

echo "System configuration complete"
