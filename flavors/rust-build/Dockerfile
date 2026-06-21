# Execution-container image for the rust-build flavor.
# Satisfies the hardened contract: rootless 65534:65534, world-exec tools on PATH,
# digest-pinned base image, no run-time installs after the USER instruction.
#
# Base image pinning:
#   The FROM digest is the linux/amd64 per-platform manifest digest for ubuntu:24.04.
#   To refresh it:
#     docker manifest inspect ubuntu:24.04 \
#       | jq -r '.manifests[] | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest'
FROM ubuntu:24.04@sha256:cdb5fd928fced577cfecf12c8966e830fcdf42ee481fb0b91904eeddc2fe5eff

# --- System build dependencies ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        build-essential \
        pkg-config \
        git \
    && rm -rf /var/lib/apt/lists/*

# --- Rust toolchain ---
# Installed to /usr/local/{cargo,rustup} so user 65534 (nobody) can read and execute
# all binaries — no writable $HOME required at run time.
#
# rustup-init pinning: SHA-256 is the linux/x86_64-unknown-linux-musl rustup-init binary
# for RUSTUP_VERSION below.  To refresh:
#   curl -fsSL \
#     "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-musl/rustup-init" \
#     | sha256sum
ARG RUST_VERSION=1.87.0
ARG RUSTUP_VERSION=1.27.1
ARG RUSTUP_INIT_SHA256=6aeece6993e902708983b209d04c0d088d10d9c9e6b8a6b61d8e5a24d4bfed40

RUN curl -fsSL \
        "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-musl/rustup-init" \
        -o /tmp/rustup-init && \
    echo "${RUSTUP_INIT_SHA256}  /tmp/rustup-init" | sha256sum -c - && \
    chmod +x /tmp/rustup-init && \
    RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo \
        /tmp/rustup-init \
            --no-modify-path \
            --default-toolchain "${RUST_VERSION}" \
            --default-host x86_64-unknown-linux-gnu \
            --profile minimal \
            -y && \
    rm /tmp/rustup-init

# Pre-install useful components; no network access is permitted at run time.
RUN RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo \
    /usr/local/cargo/bin/rustup component add clippy rustfmt

# Grant world-read + world-execute so user 65534 can invoke cargo, rustc, rustup, etc.
# All binaries must be a+x; all directories a+rx (hence a+rX).
RUN chmod -R a+rX /usr/local/cargo /usr/local/rustup && \
    find /usr/local/cargo/bin /usr/local/rustup/bin -type f -exec chmod a+x {} +

ENV PATH="/usr/local/cargo/bin:${PATH}" \
    CARGO_HOME="/usr/local/cargo" \
    RUSTUP_HOME="/usr/local/rustup"

# Run as nobody:nogroup (65534:65534) — the rootless container user required by the
# hardened contract.  No package installs, toolchain downloads, or writes to system
# paths may occur after this USER instruction.
USER 65534:65534
