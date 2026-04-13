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

# Parse command-line arguments
ENABLE_SSH="false"
while [ $# -gt 0 ]; do
    case "$1" in
        --enable-ssh)
            ENABLE_SSH="true"
            shift
            ;;
        *)
            echo "::error::Unknown argument: $1"
            exit 1
            ;;
    esac
done

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

# Conditionally remove SSH-related ignore directives when --enable-ssh is passed
if [ "${ENABLE_SSH}" = "true" ]; then
    echo "=== SSH Debug Mode Enabled ==="
    echo "Removing SSH-related ignore directives from appliance.kiwi..."
    sed -i '/<ignore name="openssh-server"\/>/d' "${TEMP_IMAGE_DIR}/appliance.kiwi"
    sed -i '/<ignore name="cloud-init"\/>/d' "${TEMP_IMAGE_DIR}/appliance.kiwi"
    sed -i '/<ignore name="cloud-init-cfg-ec2"\/>/d' "${TEMP_IMAGE_DIR}/appliance.kiwi"
    sed -i '/<ignore name="ec2-instance-connect"\/>/d' "${TEMP_IMAGE_DIR}/appliance.kiwi"
    echo "✓ SSH packages will be included in the image"
fi

# Copy pyproject.toml and uv.lock to the image description directory
echo "Copying pyproject.toml and uv.lock..."
if [ ! -f "${GITHUB_WORKSPACE}/pyproject.toml" ]; then
    echo "::error::pyproject.toml not found in workspace root"
    exit 1
fi

if [ ! -f "${GITHUB_WORKSPACE}/uv.lock" ]; then
    echo "::error::uv.lock not found in workspace root"
    exit 1
fi

# Create a directory for build dependencies
mkdir -p "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build"
cp "${GITHUB_WORKSPACE}/pyproject.toml" "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/"
cp "${GITHUB_WORKSPACE}/uv.lock" "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/"

# Pre-download Python dependency wheels (config.sh has no network access)
# Detect the Python version inside the KIWI builder image so we download
# wheels that are compatible with the target image (AL2023 ships Python 3.9,
# while the GitHub Actions runner may have a newer version).
TARGET_PYTHON_VERSION=$(docker run --rm kiwi-builder:latest python3.11 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Target Python version inside builder image: ${TARGET_PYTHON_VERSION}"

echo "Extracting dependencies from pyproject.toml..."
DEPS=$(python3 -c "
import tomllib, pathlib
data = tomllib.loads(pathlib.Path('${GITHUB_WORKSPACE}/pyproject.toml').read_text())
for dep in data['project']['dependencies']:
    print(dep)
")
if [ -z "${DEPS}" ]; then
    echo "::error::No dependencies found in pyproject.toml"
    exit 1
fi
echo "Dependencies: ${DEPS}"

# Separate wolfcrypt from other dependencies.
# wolfcrypt>=5.x only publishes source distributions (no pre-built wheels),
# so it must be built from source inside the Docker builder container which
# has the correct target architecture and compilers (gcc, cargo, etc.).
BINARY_DEPS=""
WOLFCRYPT_DEP=""
for dep in ${DEPS}; do
    if echo "${dep}" | grep -qi "^wolfcrypt"; then
        WOLFCRYPT_DEP="${dep}"
    else
        BINARY_DEPS="${BINARY_DEPS} ${dep}"
    fi
done

mkdir -p "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/wheels"

echo "Pre-downloading binary dependency wheels..."
pip3 download \
    --dest "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/wheels" \
    --python-version "${TARGET_PYTHON_VERSION}" \
    --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform linux_x86_64 \
    --platform any \
    --implementation cp \
    --abi cp311 \
    ${BINARY_DEPS}

# Download wolfcrypt source distribution and build a wheel inside the builder
# container so it is compiled for the correct target (x86_64 AL2023 / cp311).
if [ -n "${WOLFCRYPT_DEP}" ]; then
    echo "Downloading wolfcrypt source distribution..."
    WOLFCRYPT_SRC_DIR=$(mktemp -d)
    pip3 download \
        --dest "${WOLFCRYPT_SRC_DIR}" \
        --no-binary=:all: \
        "${WOLFCRYPT_DEP}"

    echo "Building wolfcrypt wheel inside builder container..."
    docker run --rm \
        -v "${WOLFCRYPT_SRC_DIR}:/wolfcrypt-src" \
        -v "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/wheels:/wheels-out" \
        kiwi-builder:latest \
        bash -c "pip3.11 install cffi wheel setuptools && pip3.11 wheel --no-deps --wheel-dir /wheels-out /wolfcrypt-src/wolfcrypt-*.tar.gz"

    rm -rf "${WOLFCRYPT_SRC_DIR}"
    echo "✓ wolfcrypt wheel built successfully"
fi

echo "✓ pyproject.toml, uv.lock, and dependency wheels copied to image description directory"

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
mkdir -p "${TEMP_IMAGE_DIR}/root/opt/github-actions-remote-executor/src"
cp -r "${EXECUTOR_SRC_DIR}"/* "${TEMP_IMAGE_DIR}/root/opt/github-actions-remote-executor/src/"

# Create wrapper script for the executor
cat > "${TEMP_IMAGE_DIR}/root/usr/local/bin/github-actions-remote-executor" << 'EOF'
#!/bin/bash
cd /opt/github-actions-remote-executor
exec python3.11 -m src.main
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
    -e "ENABLE_SSH=${ENABLE_SSH}" \
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

# Check for raw disk image (maxdepth 1: .raw is output directly to BUILD_OUTPUT_DIR)
RAW_IMAGE=$(find "${BUILD_OUTPUT_DIR}" -maxdepth 1 -name "*.raw" -type f | head -n 1)
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
