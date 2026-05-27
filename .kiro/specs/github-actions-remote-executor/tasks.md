# Implementation Plan: GitHub Actions Remote Executor

## Overview

This implementation plan breaks down the GitHub Actions Remote Executor into discrete coding tasks. The system is an HTTP server running on an Attestable EC2 instance with NitroTPM that executes scripts from GitHub repositories with cryptographic attestation. The implementation follows an asynchronous execution model with polling-based output retrieval.

## Tasks

- [x] 1. Tasks 1–191 (completed): Project structure, configuration, data models, request validation, repository client, attestation generator, execution management, output collection, script executor, HTTP server, OIDC authentication, PQ Hybrid KEM encryption, anti-replay nonce cache, concurrency enforcement, contextvars logging, Docker container security, rootless Docker migration, KIWI image build infrastructure, GitHub Actions workflow, AMI converter, deployment, cleanup, debug SSH, security hardening rounds 1-3 (mandatory nonces, request body limits, encrypted error envelopes, execution_id binding, post-clone cleanup, strict boolean parsing, raw filename sanitization, debug gate fail-closed, lockfile-enforced deps, helper source integrity, UID pinning, LimitCORE=0, OutputCollector config passthrough, immutable artifact digest pinning, credential isolation via GIT_ASKPASS, strict nonce type/length/format validation, strict base64 decoding, CI action SHA pinning, Dockerfile base image digest pinning, libslirp checksum verification, script_env deny-list, symlink-safe script path validation, runtime image package minimization, log and error response sanitization, OIDC commit hash binding, immutable container image reference, production executor digest wiring, container PID limits, output attestation rate limiting, NitroTPM availability enforcement), script environment variable forwarding, health endpoint hardening, container image digest pinning, rootless Docker dependencies built from source, build-time package uninstall via KIWI, lockfile-enforced dependency installation, and comprehensive property/unit/integration tests for all of the above

- [ ] 192. GPU passthrough for Execution Containers via NVIDIA Container Toolkit in CDI mode

  - [x] 192.1 Add GPU configuration to ServerConfig
    - In `src/config.py`, add `enable_gpu: bool` field; read from `ENABLE_GPU` env var; default to `false`; use the same strict boolean parsing as other boolean config values
    - Add `gpu_devices: str` field; read from `GPU_DEVICES` env var; default to `"all"`
    - Add `nvidia_driver_capabilities: str` field; read from `NVIDIA_DRIVER_CAPABILITIES` env var; default to `"compute,utility"`
    - Validate `gpu_devices` is non-empty when `enable_gpu` is true; fail to start if empty
    - _Requirements: 56.1, 56.2, 56.3, 56.4, 9.22, 9.23, 9.24_

  - [x] 192.2 Add GPU parameters to ScriptExecutor
    - In `src/script_executor.py` `__init__`, accept `enable_gpu: bool = False`, `gpu_devices: str = "all"`, and `nvidia_driver_capabilities: str = "compute,utility"` parameters
    - Store as `self._enable_gpu`, `self._gpu_devices`, `self._nvidia_driver_capabilities`
    - In `_execute_in_container()`, when `self._enable_gpu` is True:
      - Pass `runtime="nvidia"` to `self._docker_client.containers.create()`
      - Merge `{"NVIDIA_VISIBLE_DEVICES": self._gpu_devices, "NVIDIA_DRIVER_CAPABILITIES": self._nvidia_driver_capabilities}` into the container environment dict, with these server-controlled values taking precedence over any same-named keys in `script_env`
    - When `self._enable_gpu` is False, do NOT pass `runtime` or GPU env vars
    - _Requirements: 56.5, 56.6, 56.7, 56.8, 56.9_

  - [x] 192.3 Add NVIDIA env vars to Script_Env_Deny_List
    - In `src/config.py`, extend the default `script_env_deny_list` to include `NVIDIA_VISIBLE_DEVICES` and `NVIDIA_DRIVER_CAPABILITIES`
    - This prevents callers from overriding the server's GPU access policy via the `script_env` field in execution requests
    - _Requirements: 56.10_

  - [x] 192.4 Wire GPU config to ScriptExecutor in create_app()
    - In `src/server.py` `create_app()`, pass `enable_gpu=config.enable_gpu`, `gpu_devices=config.gpu_devices`, `nvidia_driver_capabilities=config.nvidia_driver_capabilities` to the request-handling ScriptExecutor constructor
    - _Requirements: 56.5_

  - [ ] 192.5 Add GPU startup verification
    - In `src/main.py` (or `src/server.py` startup), when `config.enable_gpu` is True:
      - Verify the `nvidia` runtime is registered with the Docker daemon by inspecting `docker_client.info()["Runtimes"]` for an `nvidia` key; fail to start if not found
      - Check for CDI specification existence at `/var/run/cdi/nvidia.yaml` (or equivalent); log a warning if not found (non-fatal)
      - Create and immediately remove a test container with `runtime="nvidia"` and `NVIDIA_VISIBLE_DEVICES=all` to verify GPU access is functional; fail to start if the test container fails
    - _Requirements: 56.11, 56.12, 56.13, 9.25, 9.26_

  - [ ] 192.6 Add gpu_enabled to attestation user_data
    - In `src/attestation.py`, add `gpu_enabled: bool` to the `ExecutionMetadata` (or equivalent structure passed to `generate_attestation`)
    - Include `"gpu_enabled": config.enable_gpu` in the user_data JSON when generating attestation documents for /execute and /execution/{id}/output
    - Update the attestation user_data schema documentation to include `gpu_enabled` field
    - _Requirements: 56.23, 56.24_

  - [ ] 192.7 Add --enable-gpu flag to KIWI image build
    - In `.github/scripts/build-kiwi-image.sh`, accept an optional `--enable-gpu` flag
    - When `--enable-gpu` is passed, set `ENABLE_GPU=true` environment variable for the KIWI builder Docker container
    - In `kiwi-descriptions/config.sh`, when `ENABLE_GPU=true`:
      - Install NVIDIA Container Toolkit from the official NVIDIA RPM repository (pinned version)
      - Run `nvidia-ctk runtime configure --runtime=docker --config=/var/lib/gha-executor/.config/docker/daemon.json`
      - Run `nvidia-ctk config --set nvidia-container-cli.no-cgroups --in-place`
      - Run `nvidia-ctk config --in-place --set nvidia-container-runtime.mode=cdi`
      - Enable the `nvidia-cdi-refresh` systemd service
    - When `--enable-gpu` is NOT passed, skip all NVIDIA-related installation
    - _Requirements: 56.14, 56.17, 56.18, 56.19, 33.16, 33.17, 33.18, 33.19_

  - [ ] 192.8 Add NVIDIA GPU driver to KIWI image (GPU builds only)
    - When `--enable-gpu` is passed, include NVIDIA GPU driver packages in the KIWI image (e.g., from the official NVIDIA repository or AWS-provided packages for AL2023)
    - Document the driver version choice and how to update it
    - _Requirements: 56.15, 56.16_

  - [ ] 192.9 Update Terraform deploy to support GPU instance types
    - In `terraform/deploy/`, ensure the `instance_type` variable accepts GPU instance types (G4dn, G5, G6, G6e, P5) without additional configuration changes (NitroTPM is auto-enabled via the AMI)
    - Add a comment in the Terraform variables documentation noting GPU-compatible instance types
    - _Requirements: 24.11_

  - [ ] 192.10 Write tests for GPU passthrough
    - **Configuration tests**: Verify `ENABLE_GPU=true` is parsed correctly; verify `ENABLE_GPU=treu` fails startup; verify `GPU_DEVICES` and `NVIDIA_DRIVER_CAPABILITIES` defaults
    - **ScriptExecutor GPU tests**: Verify when `enable_gpu=True`, `containers.create()` receives `runtime="nvidia"` and environment includes `NVIDIA_VISIBLE_DEVICES=all` and `NVIDIA_DRIVER_CAPABILITIES=compute,utility`; verify when `enable_gpu=False`, no `runtime` or GPU env vars are passed
    - **Deny-list tests**: Verify `NVIDIA_VISIBLE_DEVICES` in `script_env` is rejected; verify `NVIDIA_DRIVER_CAPABILITIES` in `script_env` is rejected
    - **Attestation tests**: Verify `gpu_enabled` field is present in attestation user_data; verify it reflects the server's `enable_gpu` config value
    - **Startup verification tests** (mock-based): Verify startup fails when `enable_gpu=True` but nvidia runtime is not registered; verify warning is logged when CDI specs are missing; verify startup fails when test container creation fails
    - _Requirements: 56.5-56.13, 56.20-56.24_

- [ ] 193. Checkpoint - Ensure all GPU passthrough tests pass
  - Run the full test suite and verify all new tests from 192.10 pass
  - Verify no regressions in existing tests

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["192.1", "192.3", "192.6", "192.9"] },
    { "id": 1, "tasks": ["192.2"] },
    { "id": 2, "tasks": ["192.4", "192.5"] },
    { "id": 3, "tasks": ["192.7", "192.8"] },
    { "id": 4, "tasks": ["192.10"] },
    { "id": 5, "tasks": ["193"] }
  ]
}
```

## Notes

- Tasks 1-191 cover the full implementation through three rounds of security hardening. See git history for the detailed subtask breakdowns of tasks 184-191.
- Task 192 implements GPU passthrough using the NVIDIA Container Toolkit in CDI mode, which is the officially recommended approach for rootless Docker GPU access
- The CDI approach uses `runtime="nvidia"` + `NVIDIA_VISIBLE_DEVICES` environment variable rather than the legacy `device_requests` / `--gpus` Docker flag, because CDI provides better compatibility with rootless Docker and does not require cgroup device controller access
- GPU configuration (192.1) and deny-list extension (192.3) are independent and can be done in parallel
- The ScriptExecutor GPU parameters (192.2) depend on 192.1 because it needs the config values
- The create_app() wiring (192.4) and startup verification (192.5) depend on 192.2 because they need the ScriptExecutor to accept GPU parameters
- The KIWI image build changes (192.7, 192.8) are independent of the runtime code changes and can be done in parallel with waves 1-2
- The attestation user_data change (192.6) is independent and can be done in wave 0
- All existing container security constraints (cap_drop=ALL, minimal cap_add, no-new-privileges, memory/CPU/pids limits) remain enforced when GPU is enabled — CDI handles device injection at the runtime level without requiring additional Linux capabilities
- The `no-cgroups` setting is retained for backward compatibility with toolkit versions prior to v1.18; on v1.18+ with `--runtime=nvidia`, this setting is not consulted (confirmed by NVIDIA maintainer, Nov 2025)
- GPU-equipped EC2 instance types that support NitroTPM: G4dn, G5, G6, G6e, G6f, Gr6, Gr6f, G7e, P5, P5e, P5en (per AWS documentation)
