# Implementation Plan: Container Security Configuration via Environment Variables

**Branch**: `001-container-security-config` | **Date**: 2026-06-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-container-security-config/spec.md`

## Summary

Add eight operator-facing environment variables (`CONTAINER_USER`, `CONTAINER_ALLOW_ROOT`, `CONTAINER_CAP_ADD`, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`, `CONTAINER_TMPFS_SIZE`, `WORKSPACE_MOUNT_MODE`, `CONTAINER_NETWORK_MODE`) that govern the execution-container security posture, each defaulting to the secure choice so a no-config deployment is hardened by default. Values are parsed and validated at startup following the existing `ServerConfig.from_env()` / `validate()` patterns (fail fast with a specific error before the server binds its port), flow through `ScriptExecutor` into the `docker.containers.create()` call, and the effective values are surfaced in startup logs and threaded into attestation `user_data` exactly as the existing `gpu_enabled` field is.

Technical approach: extend the four existing seams — `config.py` (parse/validate), `script_executor.py` (constructor + container kwargs), `server.py` (wire config → executor and config → attestation call sites), `attestation.py` (add `user_data` fields) — plus `main.py` startup logging and `.env.example` docs. No new modules, no new dependencies.

## Technical Context

**Language/Version**: Python 3.12 (runtime venv is 3.12; uses `list[str]`, `str | None`, `from __future__`-free modern syntax)

**Primary Dependencies**: FastAPI/uvicorn (HTTP server), `docker` SDK for Python (container lifecycle), pytest + Hypothesis (tests). No new dependencies required.

**Storage**: N/A (configuration is environment-variable driven; no persistent store)

**Testing**: pytest with extensive property-based (Hypothesis) and unit suites under `tests/`; existing patterns: `test_config.py`, `test_config_properties.py`, `test_script_executor.py`, `test_docker_container_properties.py`, `test_attestation_user_data_regression.py`, `test_gpu_passthrough.py`

**Target Platform**: Linux server (rootless Docker on a NitroTPM-capable EC2 instance)

**Project Type**: Single project (backend service) — `src/` + `tests/`

**Performance Goals**: N/A — config is read once at startup; no hot-path impact. Container creation already passes these kwargs; adding fields is O(1).

**Constraints**: Must preserve existing config-subsystem error style (`ValueError` → `ConfigurationError`, message names the offending variable). Hardened defaults are a deliberate breaking change (see spec Assumptions). Capability allow-list is the Docker default-bounding 14-cap set; the default granted set is the existing 7-cap working set.

**Scale/Scope**: 8 new config fields; ~6 source files touched; no schema/migration. In-scope = request + surface the posture; out-of-scope = runtime enforcement beyond what Docker honors (spec Assumptions).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution (`.specify/memory/constitution.md`) is an unpopulated template — no principles have been ratified, so there are no concrete gates to evaluate. **Status: PASS (no constraints defined).**

Observed (non-binding) repository conventions this plan follows anyway, to stay consistent with the existing codebase:
- **Test-first / parity**: every existing config option has both unit and property tests; this feature adds matching coverage before/with implementation.
- **Fail-fast configuration**: invalid config raises during `load_config()` before the server starts (FR-011) — consistent with current behavior.
- **Observability**: effective security-relevant values are logged at startup and bound into attestation `user_data`, mirroring `gpu_enabled`.

No complexity-tracking entries required.

## Project Structure

### Documentation (this feature)

```text
specs/001-container-security-config/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── config-env-contract.md
│   └── attestation-user-data-contract.md
├── checklists/
│   └── security.md      # Pre-plan requirements-quality checklist (all items resolved)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/
├── config.py            # ADD: 8 fields on ServerConfig; parse in from_env(); validate in validate()
│                        #      (new helpers: parse uid:gid, cap allow-list, tmpfs size, network/workspace enums,
│                        #       root-while-disallowed gate)
├── script_executor.py   # ADD: 8 constructor params + self._ fields; inject into containers.create()
│                        #      (user, cap_add, security_opt no-new-privileges, read_only, tmpfs, network_mode,
│                        #       workspace volume mode); honor unset-vs-empty cap_add and tmpfs-empty=no-mount
├── server.py            # WIRE: pass config.* into ScriptExecutor(...) (~L312); pass effective security
│                        #       values into generate_attestation (~L839) and generate_output_attestation (~L1155)
├── attestation.py       # ADD: container-security fields to user_data in generate_attestation and
│                        #      generate_output_attestation (same pattern as gpu_enabled)
└── main.py              # ADD: startup log lines for the 8 effective values (FR-028)

tests/
├── test_config.py / test_config_properties.py                 # parsing + validation, fail-fast cases
├── test_script_executor.py / test_docker_container_properties.py  # container kwargs reflect config
├── test_attestation_user_data_regression.py / test_attestation_properties.py  # user_data carries values
└── test_security_config_integration.py (new)                  # end-to-end: defaults hardened, relaxation works

.env.example             # ADD: 8 documented vars with defaults + security rationale + trade-off callouts
```

**Structure Decision**: Single-project backend service. The feature threads through the four existing configuration/execution/attestation seams already used by the comparable `enable_gpu` / `gpu_devices` feature (committed in f847466/58e4d71), so it adopts that exact extension pattern rather than introducing new structure.

## Complexity Tracking

> No Constitution Check violations. Section intentionally empty.
