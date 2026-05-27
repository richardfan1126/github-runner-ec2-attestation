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
ENABLE_GPU="false"
while [ $# -gt 0 ]; do
    case "$1" in
        --enable-ssh)
            ENABLE_SSH="true"
            shift
            ;;
        --enable-gpu)
            ENABLE_GPU="true"
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

# Conditionally increase image size for GPU builds.
# The NVIDIA GPU driver and Container Toolkit add ~1.5GB to the image.
# The base image is 4GB which is insufficient for GPU builds.
if [ "${ENABLE_GPU}" = "true" ]; then
    echo "=== GPU Build: Increasing image size to 8GB ==="
    sed -i 's/<size unit="G">4<\/size>/<size unit="G">8<\/size>/' "${TEMP_IMAGE_DIR}/appliance.kiwi"
    echo "✓ Image size increased to 8GB for GPU driver packages"
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
# Use uv lockfile for reproducible, hash-verified dependency resolution.
# This ensures the exact versions from uv.lock are installed, not version ranges.

echo "Exporting locked dependencies from uv.lock..."
# Export requirements with hashes from the frozen lockfile
# Note: uv export includes hashes by default (use --no-hashes to suppress)
# --no-emit-project excludes the `-e .` line which would break --require-hashes
if ! uv export --frozen --format requirements-txt --no-dev --no-emit-project \
    --project "${GITHUB_WORKSPACE}" \
    -o "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/requirements.txt"; then
    echo "::error::Failed to export dependencies from uv.lock (is uv.lock present and valid?)"
    exit 1
fi

echo "Exported requirements:"
cat "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/requirements.txt"

# Split the exported requirements.txt into binary deps and wolfcrypt.
# wolfcrypt>=5.x only publishes source distributions (no pre-built wheels),
# so it must be built from source inside the Docker builder container which
# has the correct target architecture and compilers (gcc, cargo, etc.).
# The exported requirements.txt (from uv export --frozen) is the single source
# of truth — we do NOT read version ranges from pyproject.toml.
REQUIREMENTS_FILE="${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/requirements.txt"
REQUIREMENTS_BINARY="${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/requirements-binary.txt"
REQUIREMENTS_WOLFCRYPT="${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/requirements-wolfcrypt.txt"

echo "Splitting requirements into binary and wolfcrypt..."
# The requirements.txt uses multi-line entries: a package line followed by indented
# --hash= and "# via" continuation lines. We use awk to route each complete block
# to the correct output file based on whether the package line starts with "wolfcrypt".
awk '
BEGIN { target = "binary" }
# Skip top-level comments (e.g., "# This file was autogenerated...")
/^#/ { next }
# Non-indented, non-empty line = start of a new package block
/^[^ \t]/ {
    if (tolower($0) ~ /^wolfcrypt/) { target = "wolfcrypt" }
    else { target = "binary" }
}
# Route current line to the appropriate file
{ if (target == "wolfcrypt") print >> wolfcrypt_file; else print >> binary_file }
' binary_file="${REQUIREMENTS_BINARY}" wolfcrypt_file="${REQUIREMENTS_WOLFCRYPT}" "${REQUIREMENTS_FILE}"
# Ensure both files exist (even if empty) so downstream commands don't fail
touch "${REQUIREMENTS_BINARY}" "${REQUIREMENTS_WOLFCRYPT}"

echo "Binary requirements:"
cat "${REQUIREMENTS_BINARY}"
echo ""
echo "Wolfcrypt requirements:"
cat "${REQUIREMENTS_WOLFCRYPT}"

mkdir -p "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/wheels"

echo "Pre-downloading binary dependency wheels with hash verification..."
pip3 download \
    --require-hashes \
    --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform linux_x86_64 \
    --platform any \
    --python-version 3.11 \
    --implementation cp \
    --abi cp311 \
    -r "${REQUIREMENTS_BINARY}" \
    -d "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/wheels"

# Download wolfcrypt source distribution with hash verification and build a wheel
# inside the builder container so it is compiled for the correct target (x86_64 AL2023 / cp311).
if [ -s "${REQUIREMENTS_WOLFCRYPT}" ]; then
    echo "Downloading wolfcrypt source distribution with hash verification..."
    WOLFCRYPT_SRC_DIR=$(mktemp -d)
    pip3 download \
        --require-hashes \
        --no-deps \
        --no-binary=:all: \
        -r "${REQUIREMENTS_WOLFCRYPT}" \
        -d "${WOLFCRYPT_SRC_DIR}"

    echo "Building wolfcrypt wheel inside builder container..."
    docker run --rm \
        -v "${WOLFCRYPT_SRC_DIR}:/wolfcrypt-src" \
        -v "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/wheels:/wheels-out" \
        kiwi-builder:latest \
        bash -c "pip3.11 install cffi wheel setuptools && pip3.11 wheel --no-deps --wheel-dir /wheels-out /wolfcrypt-src/wolfcrypt-*.tar.gz"

    # Compute SHA-256 of the built wolfcrypt wheel and create a final
    # requirements-install.txt that includes hashes for ALL packages.
    WOLFCRYPT_WHEEL=$(ls "${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/wheels"/wolfcrypt-*.whl 2>/dev/null | head -n 1)
    if [ -z "${WOLFCRYPT_WHEEL}" ]; then
        echo "::error::wolfcrypt wheel not found after build"
        exit 1
    fi
    WOLFCRYPT_WHEEL_HASH=$(sha256sum "${WOLFCRYPT_WHEEL}" | cut -d ' ' -f 1)
    WOLFCRYPT_WHEEL_BASENAME=$(basename "${WOLFCRYPT_WHEEL}")
    # Extract version from wheel filename (e.g., wolfcrypt-5.1.0-cp311-cp311-linux_x86_64.whl)
    WOLFCRYPT_VERSION=$(echo "${WOLFCRYPT_WHEEL_BASENAME}" | sed -E 's/wolfcrypt-([^-]+)-.*/\1/')
    echo "✓ wolfcrypt wheel built: ${WOLFCRYPT_WHEEL_BASENAME} (sha256:${WOLFCRYPT_WHEEL_HASH})"

    rm -rf "${WOLFCRYPT_SRC_DIR}"
    echo "✓ wolfcrypt wheel built successfully"
else
    WOLFCRYPT_VERSION=""
    WOLFCRYPT_WHEEL_HASH=""
fi

# Create the final requirements-install.txt that combines binary requirements
# (with their original hashes from uv.lock) and the wolfcrypt wheel hash.
# This file is used by config.sh for --require-hashes installation.
REQUIREMENTS_INSTALL="${TEMP_IMAGE_DIR}/root/tmp/kiwi-build/requirements.txt"
# Start with the binary requirements (already has hashes from uv export)
cp "${REQUIREMENTS_BINARY}" "${REQUIREMENTS_INSTALL}"
# Append wolfcrypt with its computed wheel hash
if [ -n "${WOLFCRYPT_VERSION}" ] && [ -n "${WOLFCRYPT_WHEEL_HASH}" ]; then
    echo "wolfcrypt==${WOLFCRYPT_VERSION} --hash=sha256:${WOLFCRYPT_WHEEL_HASH}" >> "${REQUIREMENTS_INSTALL}"
fi

echo ""
echo "Final requirements-install.txt for config.sh:"
cat "${REQUIREMENTS_INSTALL}"

echo "✓ pyproject.toml, uv.lock, and dependency wheels copied to image description directory"

################################################################################
# Compile Rootless Docker Dependencies from Source
################################################################################
# rootlesskit, slirp4netns, and fuse-overlayfs are not available as packages in
# the AL2023 core repository. They are compiled from source inside the KIWI
# builder Docker container (which has the correct target architecture and
# compilers) and placed into the KIWI image overlay at /usr/local/bin/.
#
# To update versions:
#   1. Change the *_VERSION variable below to the desired release tag
#   2. Verify the new tag exists on the upstream repository
#   3. Run the build and confirm compilation succeeds
#   4. Test rootless Docker functionality on the resulting image
################################################################################

echo ""
echo "=== Compiling Rootless Docker Dependencies from Source ==="

# Pinned versions for rootless Docker tools (immutable commit SHAs)
# rootlesskit: https://github.com/rootless-containers/rootlesskit/releases
# NOTE: Using v1.1.1 (the last v1.x release) because v2.x generates a third
# UID/GID mapping entry ("fill mappings") that causes "Invalid argument" errors
# from newuidmap on kernels that reject overlapping host ranges. v1.x produces
# the standard 2-entry mapping which is fully compatible with Docker 25.0.x.
# Tag: v1.1.1, Date: 2023-05-30
ROOTLESSKIT_COMMIT="a2c596ff9b3fddc0c2becb38f2ef4004f15765b5"
ROOTLESSKIT_VERSION="v1.1.1"
# slirp4netns: https://github.com/rootless-containers/slirp4netns/releases
# Tag: v1.3.3, Date: 2025-06-02
SLIRP4NETNS_COMMIT="944fa94090e1fd1312232cbc0e6b43585553d824"
SLIRP4NETNS_VERSION="v1.3.3"
# fuse-overlayfs: https://github.com/containers/fuse-overlayfs/releases
# Tag: v1.14, Date: 2024-06-28
FUSE_OVERLAYFS_COMMIT="33cb788edc05f5e3cbb8a7a241f5a04bee264730"
FUSE_OVERLAYFS_VERSION="v1.14"

echo "Versions:"
echo "  rootlesskit:    ${ROOTLESSKIT_VERSION} (${ROOTLESSKIT_COMMIT})"
echo "  slirp4netns:    ${SLIRP4NETNS_VERSION} (${SLIRP4NETNS_COMMIT})"
echo "  fuse-overlayfs: ${FUSE_OVERLAYFS_VERSION} (${FUSE_OVERLAYFS_COMMIT})"

# Create output directories for compiled binaries and libraries
mkdir -p "${TEMP_IMAGE_DIR}/root/usr/local/bin"
mkdir -p "${TEMP_IMAGE_DIR}/root/usr/local/lib64"

# Compile all three tools inside the KIWI builder Docker container.
# Also copy the libslirp shared library (built from source in the Dockerfile)
# since it is not available as a package in AL2023.
echo "Building rootless Docker tools inside builder container..."
if ! docker run --rm \
    -v "${TEMP_IMAGE_DIR}/root/usr/local/bin:/output" \
    -v "${TEMP_IMAGE_DIR}/root/usr/local/lib64:/output-lib" \
    kiwi-builder:latest \
    bash -c "
set -e -o pipefail

echo '--- Compiling rootlesskit ${ROOTLESSKIT_VERSION} (${ROOTLESSKIT_COMMIT}) (Go) ---'
git clone https://github.com/rootless-containers/rootlesskit.git /tmp/rootlesskit
cd /tmp/rootlesskit
git checkout ${ROOTLESSKIT_COMMIT}
ACTUAL_SHA=\$(git rev-parse HEAD)
if [ \"\${ACTUAL_SHA}\" != \"${ROOTLESSKIT_COMMIT}\" ]; then
    echo \"ERROR: rootlesskit HEAD mismatch: expected ${ROOTLESSKIT_COMMIT}, got \${ACTUAL_SHA}\"
    exit 1
fi
go build -o /output/rootlesskit ./cmd/rootlesskit
go build -o /output/rootlesskit-docker-proxy ./cmd/rootlesskit-docker-proxy
echo '✓ rootlesskit compiled successfully'

echo '--- Compiling slirp4netns ${SLIRP4NETNS_VERSION} (${SLIRP4NETNS_COMMIT}) (C/autotools) ---'
git clone https://github.com/rootless-containers/slirp4netns.git /tmp/slirp4netns
cd /tmp/slirp4netns
git checkout ${SLIRP4NETNS_COMMIT}
ACTUAL_SHA=\$(git rev-parse HEAD)
if [ \"\${ACTUAL_SHA}\" != \"${SLIRP4NETNS_COMMIT}\" ]; then
    echo \"ERROR: slirp4netns HEAD mismatch: expected ${SLIRP4NETNS_COMMIT}, got \${ACTUAL_SHA}\"
    exit 1
fi
./autogen.sh
./configure --prefix=/usr
make
cp slirp4netns /output/slirp4netns
echo '✓ slirp4netns compiled successfully'

echo '--- Compiling fuse-overlayfs ${FUSE_OVERLAYFS_VERSION} (${FUSE_OVERLAYFS_COMMIT}) (C/autotools) ---'
git clone https://github.com/containers/fuse-overlayfs.git /tmp/fuse-overlayfs
cd /tmp/fuse-overlayfs
git checkout ${FUSE_OVERLAYFS_COMMIT}
ACTUAL_SHA=\$(git rev-parse HEAD)
if [ \"\${ACTUAL_SHA}\" != \"${FUSE_OVERLAYFS_COMMIT}\" ]; then
    echo \"ERROR: fuse-overlayfs HEAD mismatch: expected ${FUSE_OVERLAYFS_COMMIT}, got \${ACTUAL_SHA}\"
    exit 1
fi
./autogen.sh
./configure --prefix=/usr
make
cp fuse-overlayfs /output/fuse-overlayfs
echo '✓ fuse-overlayfs compiled successfully'

echo '--- All rootless Docker tools compiled ---'

echo '--- Copying libslirp shared library ---'
cp -a /usr/local/lib64/libslirp.so* /output-lib/ 2>/dev/null || \
cp -a /usr/local/lib/libslirp.so* /output-lib/ 2>/dev/null || {
    echo 'ERROR: libslirp shared library not found in builder container'
    exit 1
}
echo '✓ libslirp shared library copied'

echo '--- Setting executable permissions ---'
chmod +x /output/rootlesskit /output/rootlesskit-docker-proxy /output/slirp4netns /output/fuse-overlayfs
echo '✓ Permissions set'
"; then
    echo "::error::Failed to compile rootless Docker dependencies from source"
    exit 1
fi

# Verify all expected binaries were produced
for binary in rootlesskit rootlesskit-docker-proxy slirp4netns fuse-overlayfs; do
    if [ ! -f "${TEMP_IMAGE_DIR}/root/usr/local/bin/${binary}" ]; then
        echo "::error::Compiled binary not found: ${binary}"
        exit 1
    fi
done

# Verify libslirp shared library was copied
if ! ls "${TEMP_IMAGE_DIR}/root/usr/local/lib64"/libslirp.so* > /dev/null 2>&1; then
    echo "::error::libslirp shared library not found in image overlay"
    exit 1
fi

echo "✓ All rootless Docker binaries compiled and placed in image overlay"
echo "✓ libslirp shared library placed in /usr/local/lib64/"

################################################################################
# Install dockerd-rootless.sh
################################################################################
# The AL2023 'docker' package provides dockerd but does NOT include
# dockerd-rootless.sh (the wrapper script that launches dockerd under
# rootlesskit with user namespace isolation). We download it from the
# official Moby repository, pinned to a specific commit for reproducibility.
################################################################################

echo ""
echo "=== Installing dockerd-rootless.sh ==="

# Pinned commit from https://github.com/moby/moby
# Using v20.10.27 (the last 20.10.x release) because its dockerd-rootless.sh
# is compatible with rootlesskit v1.x. The v25.0.x version of the script
# passes --detach-netns which requires rootlesskit v2.1+, but v2.x generates
# a third UID mapping entry that causes "Invalid argument" errors from
# newuidmap on AL2023 kernels.
MOBY_COMMIT="v20.10.27"
# SHA-256 checksum of dockerd-rootless.sh at the pinned commit
DOCKERD_ROOTLESS_SHA256="07fd43a5adad652bb9d15d5cec851c0f563fe1cf8c5f0d5123b45b0e118404dd"

echo "Downloading dockerd-rootless.sh from moby/moby (${MOBY_COMMIT})..."
curl -fsSL "https://raw.githubusercontent.com/moby/moby/${MOBY_COMMIT}/contrib/dockerd-rootless.sh" \
    -o "${TEMP_IMAGE_DIR}/root/usr/local/bin/dockerd-rootless.sh"

# Verify SHA-256 checksum of downloaded script
ACTUAL_SHA256=$(sha256sum "${TEMP_IMAGE_DIR}/root/usr/local/bin/dockerd-rootless.sh" | cut -d ' ' -f 1)
if [ "${ACTUAL_SHA256}" != "${DOCKERD_ROOTLESS_SHA256}" ]; then
    echo "::error::dockerd-rootless.sh checksum mismatch: expected ${DOCKERD_ROOTLESS_SHA256}, got ${ACTUAL_SHA256}"
    exit 1
fi
echo "✓ dockerd-rootless.sh checksum verified"

chmod +x "${TEMP_IMAGE_DIR}/root/usr/local/bin/dockerd-rootless.sh"

# Verify the script was downloaded successfully
if [ ! -s "${TEMP_IMAGE_DIR}/root/usr/local/bin/dockerd-rootless.sh" ]; then
    echo "::error::Failed to download dockerd-rootless.sh"
    exit 1
fi

echo "✓ dockerd-rootless.sh installed to /usr/local/bin/"

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
    -e "ENABLE_GPU=${ENABLE_GPU}" \
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
