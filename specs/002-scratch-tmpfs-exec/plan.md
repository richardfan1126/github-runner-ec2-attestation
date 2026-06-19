# Implementation Plan: Configurable Execution Permission on the Container Scratch tmpfs

**Branch**: `002-scratch-tmpfs-exec` | **Date**: 2026-06-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-scratch-tmpfs-exec/spec.md`

## Summary

Add a ninth operator-facing container-security environment variable,
`CONTAINER_TMPFS_EXEC`, that controls whether the per-execution `/tmp` scratch
tmpfs is mounted with the `exec` option. It defaults to disabled (`noexec`),
preserving today's hardened behavior with no change for existing deployments,
and can be opted into per-deployment (for trusted compile-and-run build
toolchains such as Rust `build.rs`) without code changes. The effective value is
threaded into container creation and surfaced in attestation `user_data` (as the
boolean `container_tmpfs_exec`) and in the `Build Attestable Image` workflow's
configuration summary, so the relaxation is never hidden.

Technical approach: extend exactly the same four seams feature 001 established —
`config.py` (parse with `parse_strict_bool` / store one new field),
`script_executor.py` (one constructor param → conditionally append `,exec` to the
existing `/tmp` tmpfs mount string), `server.py` (wire config → executor and
config → both attestation call sites), `attestation.py` (one new `user_data`
field in `_build_security_user_data`) — plus `main.py` startup logging (including
the "enabled but no tmpfs mounted" warning), `.github/scripts/print_config.py`
(add the field to the `Container Security` category), and `.env.example` docs.
**No new modules, no new dependencies, no new mechanisms** — this feature adds
exactly one boolean field to the established pattern.

## Technical Context

**Language/Version**: Python 3.12 (matches feature 001; `list[str]`, `str | None`)

**Primary Dependencies**: FastAPI/uvicorn (HTTP server), `docker` SDK for Python
(container lifecycle), pytest + Hypothesis (tests). No new dependencies.

**Storage**: N/A (configuration is environment-variable driven; no persistent store)

**Testing**: pytest with unit + property-based (Hypothesis) suites under `tests/`.
Existing patterns to extend: `test_config.py`, `test_config_properties.py`,
`test_script_executor.py`, `test_docker_container_properties.py`,
`test_attestation_user_data_regression.py`, `test_attestation_properties.py`,
`test_print_config.py`, and the feature-001 `test_security_config_integration.py`.

**Target Platform**: Linux server (rootless Docker on a NitroTPM-capable EC2 instance)

**Project Type**: Single project (backend service) — `src/` + `tests/`

**Performance Goals**: N/A — config is read once at startup; no hot-path impact.
The mount string is built once per container creation; appending one option is O(1).

**Constraints**: Must preserve the existing config-subsystem error style
(`parse_strict_bool` raises `ValueError` naming the variable → fail fast before
the port binds). Enabling exec MUST add **only** the `exec` mount option — it MUST
NOT touch `size=`, `mode=1777`, `nosuid`, or `nodev` on the scratch mount, and MUST
NOT affect any other container-security control. The `user_data` 1024-byte
NitroTPM cap still applies; one short boolean field is well within budget.

**Scale/Scope**: 1 new config field; ~6 source/tooling files touched + tests; no
schema/migration. In-scope = request + surface the posture; out-of-scope = any
runtime enforcement beyond the `exec`/`noexec` mount option Docker honors.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution (`.specify/memory/constitution.md`) is an unpopulated
template — no principles have been ratified, so there are no concrete gates to
evaluate. **Status: PASS (no constraints defined).**

Observed (non-binding) repository conventions this plan follows anyway, to stay
consistent with the existing codebase and with feature 001:

- **Secure-by-default**: the new variable defaults to the hardened value
  (`noexec`); a no-config deployment is unchanged (FR-002, SC-001).
- **Fail-fast configuration**: invalid values raise during `load_config()` before
  the server starts, via the shared `parse_strict_bool` (FR-003, SC-004).
- **Observability / no drift**: the effective value is logged at startup, bound
  into attestation `user_data`, and rendered in the build summary from the same
  loader — mirroring the existing `container_tmpfs_size` field (FR-008–FR-010).
- **Test parity**: every existing container-security option has unit + property
  coverage; this field adds matching coverage.

No complexity-tracking entries required.

## Project Structure

### Documentation (this feature)

```text
specs/002-scratch-tmpfs-exec/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── config-env-contract.md
│   └── attestation-user-data-contract.md
├── checklists/
│   └── requirements.md  # Pre-plan requirements-quality checklist (from /speckit-clarify)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created here)
```

### Source Code (repository root)

```text
src/
├── config.py            # ADD: field `container_tmpfs_exec: bool = False` on ServerConfig;
│                        #      parse CONTAINER_TMPFS_EXEC (default "false") via parse_strict_bool
│                        #      in from_env(); pass into the constructed ServerConfig.
│                        #      No new validate() rule needed (parse_strict_bool fails fast).
├── script_executor.py   # ADD: constructor param `tmpfs_exec: bool = False` + self._tmpfs_exec;
│                        #      in the `if self._tmpfs_size:` block, append ",exec" to the mount
│                        #      options string only when self._tmpfs_exec is True. nosuid/nodev/
│                        #      mode/size untouched.
├── server.py            # WIRE: pass config.container_tmpfs_exec into ScriptExecutor(...) (~L330);
│                        #      pass it as container_tmpfs_exec= into generate_attestation (~L839)
│                        #      and generate_output_attestation (~L1169).
├── attestation.py       # ADD: container_tmpfs_exec param to _build_security_user_data and to
│                        #      both generate_attestation / generate_output_attestation signatures;
│                        #      emit user_data["container_tmpfs_exec"] when not None (same pattern
│                        #      as container_tmpfs_size).
└── main.py              # ADD: startup log line for the effective value (near the tmpfs-size line);
│                        #      WARN when container_tmpfs_exec is True AND container_tmpfs_size is
│                        #      empty ("exec enabled but no tmpfs is mounted; setting has no effect").

.github/scripts/
└── print_config.py      # ADD: "container_tmpfs_exec" to the CONFIG_CATEGORIES["Container Security"]
                         #      list (after container_tmpfs_size). Field is auto-rendered from the
                         #      dataclass regardless, but listing it keeps category grouping correct.

.github/workflows/build-attestable-image.yml
                         # No change required — the summary step already renders all ServerConfig
                         #   fields via print_config.py.

.env.example             # ADD: documented CONTAINER_TMPFS_EXEC with secure default (false) and the
                         #      security implication of enabling exec-from-scratch.

tests/
├── test_config.py / test_config_properties.py                 # parse default=false; truthy/falsy;
│                                                              #   invalid value fails fast naming var
├── test_script_executor.py / test_docker_container_properties.py  # tmpfs mount gains ",exec" iff
│                                                              #   enabled; nosuid/nodev/mode/size/
│                                                              #   noexec-by-default invariants hold
├── test_attestation_user_data_regression.py / test_attestation_properties.py  # container_tmpfs_exec
│                                                              #   present in user_data both attest paths
├── test_print_config.py                                       # field rendered under Container Security
└── test_security_config_integration.py                        # defaults → noexec; enabled → exec;
                                                               #   enabled+no-tmpfs → warning, no mount change
```

**Structure Decision**: Single-project backend service. This feature threads
through the identical configuration/execution/attestation/build-summary seams that
feature 001 introduced for `container_tmpfs_size` (its closest sibling — same
mount, same `if self._tmpfs_size:` block). It adopts that exact extension pattern
rather than introducing any new structure, module, or mechanism.

## Complexity Tracking

> No Constitution Check violations. Section intentionally empty.
