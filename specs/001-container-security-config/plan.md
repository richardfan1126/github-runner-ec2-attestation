# Implementation Plan: Container Security Configuration via Environment Variables

**Branch**: `001-container-security-config` | **Date**: 2026-06-13 (addendum 2026-06-14) | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-container-security-config/spec.md`

## Summary

Add eight operator-facing environment variables (`CONTAINER_USER`, `CONTAINER_ALLOW_ROOT`, `CONTAINER_CAP_ADD`, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`, `CONTAINER_TMPFS_SIZE`, `WORKSPACE_MOUNT_MODE`, `CONTAINER_NETWORK_MODE`) that govern the execution-container security posture, each defaulting to the secure choice so a no-config deployment is hardened by default. Values are parsed and validated at startup following the existing `ServerConfig.from_env()` / `validate()` patterns (fail fast with a specific error before the server binds its port), flow through `ScriptExecutor` into the `docker.containers.create()` call, and the effective values are surfaced in startup logs and threaded into attestation `user_data` exactly as the existing `gpu_enabled` field is.

Technical approach: extend the four existing seams — `config.py` (parse/validate), `script_executor.py` (constructor + container kwargs), `server.py` (wire config → executor and config → attestation call sites), `attestation.py` (add `user_data` fields) — plus `main.py` startup logging and `.env.example` docs. No new modules, no new dependencies.

**Addendum (2026-06-14, FR-030):** Surface the full effective server configuration that the build bakes into the AMI on the `Build Attestable Image` workflow run summary. A small read-only helper (`.github/scripts/print_config.py` — build tooling, **not** under `src/`) loads the image's baked-in env file through the application's own `load_config()` (single source of truth) and renders every `ServerConfig` field as a Markdown table; a new workflow step runs it against `kiwi-descriptions/root/etc/github-actions-remote-executor/env`, appends the table to `$GITHUB_STEP_SUMMARY`, and fails the build before publishing if the configuration cannot be resolved. See the [FR-030 addendum](#addendum-fr-030--configuration-summary-on-the-build-workflow-2026-06-14) below.

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

## Addendum: FR-030 — Configuration summary on the build workflow (2026-06-14)

The feature was reopened to add **FR-030 / SC-007**: the `Build Attestable Image` workflow must print, on its run summary, the full effective server configuration built into the AMI — every effective `ServerConfig` setting (a superset of the `.env.example` keys, including the eight container-security settings) — derived from the application's own config loader so it cannot drift, printed verbatim (no redaction), and the build must fail before publishing if the configuration cannot be resolved.

### Approach

1. **`.github/scripts/print_config.py` (new, read-only helper — build tooling, not `src/`)** — runnable as `uv run python .github/scripts/print_config.py --env-file <path>` from the repo root. It:
   - Parses the baked-in env file with systemd-`EnvironmentFile`-compatible rules (skip blank/`#` lines; split on first `=`; the file uses unquoted simple values), populating `os.environ`.
   - Calls the application's own `load_config()` (`from_env()` + `validate()`) — the **single source of truth**, so the printed values are exactly what the server would resolve (FR-030). It imports `from src.config import load_config, ServerConfig`; because the repo root is the executor's uv project (`packages = ["src"]`), `uv run` makes `src` importable from any location, so the helper lives outside `src/` while keeping the executor as the single source of truth (Clarifications 2026-06-14).
   - On success, enumerates `dataclasses.fields(ServerConfig)` and emits a Markdown table (`| Setting | Value |`) covering **every** field — a superset of the `.env.example` keys, drift-proof because the field list is read from the dataclass, not hand-maintained. Values are printed verbatim.
   - On any `ConfigurationError`/`ValueError`, writes the error to stderr and exits non-zero (FR-030 fail-fast). Imports only from `src.config`, so it pulls in no FastAPI/Docker/TPM machinery and does not bind a port or touch the TPM.

2. **Workflow step (new, in the `build-and-publish` job)** — added immediately after *Build KIWI image* and **before** *Push artifact to GHCR*, so a resolution failure aborts the run before anything is published. It appends a heading + the `print_config.py` table to `$GITHUB_STEP_SUMMARY`; if the command exits non-zero it emits `::error::` and `exit 1`. Placed in `build-and-publish` (not `build-ami`) because that job already has the repo + `uv` set up, runs on every push, and reads the same env file that is baked into the image.

### Source Code touched (addendum)

```text
.github/scripts/
└── print_config.py          # NEW (build tooling, NOT src/): load baked env file via src.config.load_config();
                             #   render all ServerConfig fields as Markdown; non-zero exit on failure

.github/workflows/build-attestable-image.yml
                             # ADD step "Print effective configuration to summary" in build-and-publish,
                             #   after "Build KIWI image", before "Push artifact to GHCR"

tests/
└── test_print_config.py     # NEW: table covers all ServerConfig fields incl. 8 security defaults; non-zero exit on
                             #      missing/invalid config; the real baked env file resolves cleanly (exit 0)
```

### Decisions (see research.md Decisions 8–10)

- **Single source of truth via `load_config()`** — never re-list values in YAML; the table is generated from `dataclasses.fields()` so it cannot drift (SC-007).
- **Verbatim, no redaction** — the baked env file is committed in the repo, so the summary discloses nothing new (spec Clarifications 2026-06-14).
- **Fail before publish** — a loader rejection at build time is the same failure the server would hit at startup (FR-011); catching it early is consistent with the system's fail-fast posture.

### Constitution / gate re-check (post-design)

Still **PASS** — the constitution remains an unpopulated template. No new dependencies; the new module is read-only and import-light. Repository conventions upheld: single source of truth for config, fail-fast on invalid config, observability of effective posture.
