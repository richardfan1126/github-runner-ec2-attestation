#!/bin/bash
#
# KIWI Image Build Script
# ------------------------
# This script builds a KIWI image inside a Docker container with loop device support.
# It integrates the GitHub Actions Remote Executor service into the image and generates
# PCR measurements for TPM attestation.
#
# Requirements:
#   - Docker with privileged access
#   - Loop devices available on host
#   - KIWI builder Docker image built
#
# Outputs:
#   - build-output/*.raw (raw disk image)
#   - build-output/pcr_measurements.json (PCR4 and PCR7 values)
#

set -e -o pipefail

# Configuration
IMAGE_DESCRIPTION_DIR="${GITHUB_WORKSPACE}/kiwi-descriptions"
BUILD_OUTPUT_DIR="${GITHUB_WORKSPACE}/build-output"
EXECUTOR_SRC_DIR="${GITHUB_WORKSPACE}/src"

echo "=== KIWI Image Build Script ==="
echo "Image description directory: ${IMAGE_DESCRIPTION_DIR}"
echo "Build output directory: ${BUILD_OUTPUT_DIR}"
echo ""

# Validate required directories exist
if [ ! -d "${IMAGE_DESCRIPTION_DIR}" ]; then
    echo "::error::Image description directory not found: ${IMAGE_DESCRIPTION_DIR}"
    exit 1
fi

if [ ! -d "${EXECUTOR_SRC_DIR}" ]; then
    echo "::error::Executor source directory not found: ${EXECUTOR_SRC_DIR}"
    exit 1
fi

# Create build output directory
mkdir -p "${BUILD_OUTPUT_DIR}"

# Create temporary working directory for image customization
TEMP_IMAGE_DIR=$(mktemp -d)
trap "rm -rf ${TEMP_IMAGE_DIR}" EXIT

echo "Copying image description files to temporary directory..."
cp -r "${IMAGE_DESCRIPTION_DIR}"/* "${TEMP_IMAGE_DIR}/"

# Make scripts executable
chmod +x "${TEMP_IMAGE_DIR}/config.sh"
chmod +x "${TEMP_IMAGE_DIR}/edit_boot_install.sh"
chmod +x "${TEMP_IMAGE_DIR}/add-gpg-key.sh"

################################
# Integrate Remote Executor    #
################################
echo ""
echo "=== Integrating GitHub Actions Remote Executor ==="

# Create directories for executor service files
mkdir -p "${TEMP_IMAGE_DIR}/root/opt/github-actions-remote-executor"
mkdir -p "${TEMP_IMAGE_DIR}/root/usr/local/bin"

# Copy executor source files
echo "Copying executor source files..."
cp -r "${EXECUTOR_SRC_DIR}"/* "${TEMP_IMAGE_DIR}/root/opt/github-actions-remote-executor/"

# Create wrapper script for the executor
cat > "${TEMP_IMAGE_DIR}/root/usr/local/bin/github-actions-remote-executor" << 'EOF'
#!/bin/bash
cd /opt/github-actions-remote-executor
exec python3 -m src.main
EOF

chmod +x "${TEMP_IMAGE_DIR}/root/usr/local/bin/github-actions-remote-executor"

echo "GitHub Actions Remote Executor integration complete"

################################
# Configure Loop Devices       #
################################
echo ""
echo "=== Configuring Loop Devices ==="

# Ensure loop devices are available
if ! ls /dev/loop* > /dev/null 2>&1; then
    echo "::warning::No loop devices found, attempting to create them..."
    for i in {0..7}; do
        if [ ! -e "/dev/loop${i}" ]; then
            mknod -m 0660 "/dev/loop${i}" b 7 "${i}" || true
        fi
    done
fi

echo "Loop devices configured"

################################
# Build KIWI Image             #
################################
echo ""
echo "=== Building KIWI Image ==="

# Run KIWI build inside Docker container
if ! docker run --rm \
    --privileged \
    -v /dev:/dev \
    -v "${TEMP_IMAGE_DIR}:/workspace" \
    -v "${BUILD_OUTPUT_DIR}:/output" \
    kiwi-builder:latest \
    bash -c "cd /workspace && kiwi-ng system build --description . --target-dir /output"; then
    echo "::error::KIWI NG build failed. Check the build logs above for details."
    exit 1
fi

################################
# Validate Build Outputs       #
################################
echo ""
echo "=== Validating Build Outputs ==="

# Check for raw disk image
RAW_IMAGE=$(find "${BUILD_OUTPUT_DIR}" -name "*.raw" -type f | head -n 1)
if [ -z "${RAW_IMAGE}" ]; then
    echo "::error::Raw disk image (.raw) not found in build output directory"
    exit 1
fi
echo "✓ Found raw disk image: $(basename ${RAW_IMAGE})"

# Check for PCR measurements file
PCR_FILE="${BUILD_OUTPUT_DIR}/pcr_measurements.json"
if [ ! -f "${PCR_FILE}" ]; then
    echo "::error::PCR measurements file not found: ${PCR_FILE}"
    exit 1
fi
echo "✓ Found PCR measurements file: pcr_measurements.json"

# Validate PCR measurements JSON structure
if ! python3 -c "import json; data = json.load(open('${PCR_FILE}')); assert 'Measurements' in data; assert 'PCR4' in data['Measurements']; assert 'PCR7' in data['Measurements']" 2>/dev/null; then
    echo "::error::PCR measurements file has invalid structure"
    cat "${PCR_FILE}"
    exit 1
fi
echo "✓ PCR measurements file is valid"

# Display PCR measurements
echo ""
echo "=== PCR Measurements ==="
cat "${PCR_FILE}"

echo ""
echo "=== Build Complete ==="
echo "Raw disk image: ${RAW_IMAGE}"
echo "PCR measurements: ${PCR_FILE}"
echo "Build output directory: ${BUILD_OUTPUT_DIR}"
