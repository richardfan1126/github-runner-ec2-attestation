# Execution-container image for the rust-build flavor.
# Satisfies the hardened contract: rootless 65534:65534, world-exec tools on PATH,
# digest-pinned base image, no run-time installs after the USER instruction.
#
# Base image pinning:
#   The FROM digest is the linux/amd64 per-platform manifest digest for debian:bookworm-slim.
#   To refresh it:
#     docker manifest inspect debian:bookworm-slim \
#       | jq -r '.manifests[] | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest'
FROM debian:bookworm-slim@sha256:96e378d7e6531ac9a15ad505478fcc2e69f371b10f5cdf87857c4b8188404716

# Install pinned C toolchain (gcc registers /usr/bin/cc via update-alternatives) and curl.
# Versions resolved against the pinned base digest.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates=20230311+deb12u1 \
        gcc=4:12.2.0-3 \
        libc6-dev=2.36-9+deb12u14 \
        curl=7.88.1-10+deb12u14 && \
    rm -rf /var/lib/apt/lists/*

# Install Rust 1.96.0 into /opt/rust (world-readable; real cargo/rustc, not proxy shims).
#
# rustup-init pinning: SHA-256 is the x86_64-unknown-linux-gnu rustup-init binary.
# To refresh:
#   curl -fsSL \
#     "https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init" \
#     | sha256sum
ENV RUSTUP_HOME=/opt/rust
RUN curl -fsSL \
        https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init \
        -o /tmp/rustup-init && \
    echo "4acc9acc76d5079515b46346a485974457b5a79893cfb01112423c89aeb5aa10  /tmp/rustup-init" \
        | sha256sum -c - && \
    chmod +x /tmp/rustup-init && \
    /tmp/rustup-init -y --no-modify-path --default-toolchain 1.96.0 --profile minimal && \
    chmod -R a+rX /opt/rust && \
    rm /tmp/rustup-init

# Install oras 1.3.2.
#
# SHA-256 is over the linux_amd64 tarball. To refresh:
#   curl -fsSL \
#     "https://github.com/oras-project/oras/releases/download/v1.3.2/oras_1.3.2_checksums.txt" \
#     | grep linux_amd64.tar.gz
RUN curl -fsSL \
        https://github.com/oras-project/oras/releases/download/v1.3.2/oras_1.3.2_linux_amd64.tar.gz \
        -o /tmp/oras.tar.gz && \
    echo "9229ccc6d17bb282039ad4a69abb16dcb887a5bce567c075d731d9b3c7ad8eaf  /tmp/oras.tar.gz" \
        | sha256sum -c - && \
    tar -xzf /tmp/oras.tar.gz -C /tmp oras && \
    mv /tmp/oras /usr/local/bin/oras && \
    chmod +x /usr/local/bin/oras && \
    rm /tmp/oras.tar.gz

# Expose the real cargo/rustc binaries (not rustup proxy shims) and oras on PATH.
# RUSTUP_HOME is read-only at run time — the real binaries do not write to it.
ENV PATH="/opt/rust/toolchains/1.96.0-x86_64-unknown-linux-gnu/bin:/usr/local/bin:${PATH}"

# Run as nobody:nogroup (65534:65534) — the rootless container user required by the
# hardened contract.  No package installs, toolchain downloads, or writes to system
# paths may occur after this USER instruction.
USER 65534:65534
