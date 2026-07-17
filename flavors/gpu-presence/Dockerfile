# Execution-container image for the gpu-presence flavor.
#
# Deliberately has essentially no GPU content (D4): the `gpu` claims block is a
# host-side NVML read in the server process, and `nvidia-smi` + driver `.so`s
# are runtime CDI injections from the PCR4-measured host driver — not baked
# here. Baking them would fork the container's view from the measured driver.
# The demo's gpu-presence-workload.sh invokes only CDI-injected `nvidia-smi`
# plus bash builtins, so no RUN layer / no apt packages are needed; `bash`
# ships in the base image.
#
# Base image pinning:
#   The FROM digest is the linux/amd64 per-platform manifest digest for debian:bookworm-slim.
#   To refresh it:
#     docker manifest inspect debian:bookworm-slim \
#       | jq -r '.manifests[] | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest'
FROM debian:bookworm-slim@sha256:63a496b5d3b99214b39f5ed70eb71a61e590a77979c79cbee4faf991f8c0783e

# Run as nobody:nogroup (65534:65534) — the rootless container user required by the
# hardened contract. No package installs, toolchain downloads, or writes to system
# paths may occur after this USER instruction.
USER 65534:65534
