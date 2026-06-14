---
description: "Task list for Container Security Configuration via Environment Variables"
---

# Tasks: Container Security Configuration via Environment Variables

**Input**: Design documents from `/specs/001-container-security-config/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓ (config-env-contract.md, attestation-user-data-contract.md), quickstart.md ✓

**Tests**: INCLUDED. The spec and plan require parity with existing config options ("every existing config option has both unit and property tests; this feature adds matching coverage before/with implementation"). Test tasks are written before the implementation they cover within each story.

**Organization**: Tasks are grouped by user story (P1 → P2 → P3) to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1, US2, US3)
- All paths are relative to the repository root

## Path Conventions

Single project: `src/` and `tests/` at repository root (per plan.md "Structure Decision").

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the reference pattern before touching the four seams.

- [X] T001 Review the existing `gpu_enabled` / `gpu_devices` extension pattern across the four seams it touches — `src/config.py` (`ServerConfig` field + `from_env()` + `validate()`), `src/script_executor.py` (constructor param + `docker.containers.create()` kwarg), `src/server.py` (executor wiring ~L312 and `generate_attestation` ~L839 / `generate_output_attestation` ~L1155 call sites), `src/attestation.py` (`user_data` field) — and confirm `parse_strict_bool` and the `ValueError`→`ConfigurationError` flow in `src/config.py`; record the exact line anchors to mirror.
- [X] T002 Confirm the test baseline is green before changes: run `.venv/bin/pytest -q` and note that `tests/test_config.py`, `tests/test_config_properties.py`, `tests/test_script_executor.py`, `tests/test_docker_container_properties.py`, `tests/test_attestation_user_data_regression.py`, `tests/test_attestation_properties.py`, and `tests/test_gpu_passthrough.py` pass.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Make the eight settings exist on `ServerConfig` with secure defaults and correct unset/empty resolution. Every user story depends on this — US1 reads the resolved values into container kwargs, US2 validates them, US3 surfaces them.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Add module-level constants to `src/config.py`: `CONTAINER_CAP_ALLOWLIST` (the 14-cap set: `CHOWN, DAC_OVERRIDE, FSETID, FOWNER, MKNOD, NET_RAW, SETGID, SETUID, SETFCAP, SETPCAP, NET_BIND_SERVICE, SYS_CHROOT, KILL, AUDIT_WRITE`) and `CONTAINER_DEFAULT_CAP_ADD` (the 7-cap default working set: `CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, NET_BIND_SERVICE, KILL`), case-sensitive upper-case names without `CAP_` prefix (FR-005, FR-015; data-model.md "Capability allow-list").
- [X] T004 Add the eight fields to the `ServerConfig` dataclass in `src/config.py` with their types and secure defaults: `container_user: str = "65534:65534"`, `container_allow_root: bool = False`, `container_cap_add: list[str] | None = None`, `no_new_privileges: bool = True`, `container_read_only_rootfs: bool = True`, `container_tmpfs_size: str = "256m"`, `workspace_mount_mode: str = "ro"`, `container_network_mode: str = "none"` (FR-001–FR-010; data-model.md entity table). Depends on T003.
- [X] T005 Implement `from_env()` parsing for the eight vars in `src/config.py`: parse the three booleans via the existing `parse_strict_bool`; resolve `CONTAINER_CAP_ADD` so **unset → `None`** (default 7-cap set applied later) and **empty string → `[]`** (no caps added), splitting comma-separated names otherwise; read `CONTAINER_TMPFS_SIZE` preserving empty as "no tmpfs"; read `CONTAINER_USER`, `WORKSPACE_MOUNT_MODE`, `CONTAINER_NETWORK_MODE` as raw strings for `validate()` to check (FR-005, FR-008; data-model.md "Resolved → container creation"). Depends on T004.

**Checkpoint**: `ServerConfig.from_env()` produces a config object carrying all eight resolved values with hardened defaults. User stories can now proceed.

---

## Phase 3: User Story 1 - Hardened sandbox by default (Priority: P1) 🎯 MVP

**Goal**: With none of the eight variables set, every execution container is hardened — non-root `65534:65534`, read-only rootfs with a bounded `256m` tmpfs scratch, read-only workspace, no-new-privileges on, `cap_drop ALL` + the 7-cap set, and `network=none`.

**Independent Test**: Build a container create-spec via `ScriptExecutor` from a default config and assert the `docker.containers.create()` kwargs show `user="65534:65534"`, `read_only=True`, `tmpfs` mounting `/tmp` at `size=256m`, workspace bind `mode=="ro"`, `security_opt==["no-new-privileges"]`, `cap_drop==["ALL"]`, `cap_add==[CHOWN,DAC_OVERRIDE,FOWNER,SETUID,SETGID,NET_BIND_SERVICE,KILL]`, `network_mode=="none"` (quickstart.md Scenario A).

### Tests for User Story 1 ⚠️ (write first, ensure they fail)

- [X] T006 [P] [US1] Add unit tests in `tests/test_script_executor.py` asserting that a `ScriptExecutor` built from a default `ServerConfig` passes the hardened-default kwargs above into `docker.containers.create()` (use the fakes in `tests/mock_docker.py`); cover the default 7-cap `cap_add` on top of `cap_drop=["ALL"]`.
- [X] T007 [P] [US1] Add property tests in `tests/test_docker_container_properties.py` asserting the invariants that hold for any config: `cap_drop` is always exactly `["ALL"]`; the applied `cap_add` is exactly the resolved set and never broader (FR-023); `read_only` and `network_mode` reflect the config; tmpfs is mounted at `/tmp` iff `container_tmpfs_size` is non-empty (independent of the rootfs read-only setting) (FR-022).

### Implementation for User Story 1

- [X] T008 [US1] Add the **seven** container-shaping constructor parameters and `self._` fields to `ScriptExecutor.__init__` in `src/script_executor.py` (`user`, `cap_add`, `no_new_privileges`, `read_only_rootfs`, `tmpfs_size`, `workspace_mount_mode`, `network_mode`), defaulting to the secure values so existing call sites stay safe. Do **not** add an `allow_root` parameter — `CONTAINER_ALLOW_ROOT` is a startup gate only and is sourced from config for attestation, not from the executor (see Clarifications 2026-06-13). Depends on T005.
- [X] T009 [US1] Inject the resolved values into the `docker.containers.create()` call in `src/script_executor.py` (currently hard-coded around L249–L254): set `user=`; `cap_drop=["ALL"]` + `cap_add=<resolved list>` (None → default 7-cap set, `[]` → none); `security_opt=["no-new-privileges"]` only when enabled (omit when false); `read_only=<bool>`; `tmpfs={"/tmp": "size=<value>"}` whenever `tmpfs_size` is non-empty (mounted at the container's standard temp dir, independent of the rootfs read-only setting — see Clarifications 2026-06-13); workspace volume bind `mode` = `workspace_mount_mode`; `network_mode=<value>` (FR-021–FR-023; data-model.md kwargs table; research.md Decision 5). Depends on T008.
- [X] T009a [US1] Delete the dead `_copy_script_to_container` method in `src/script_executor.py` (script_executor.py ~L385–414). It is never called (the live flow runs `bash /workspace/{script_path}`), and its docstring/`put_archive` target the stale `/tmp/execution` path — which this feature replaces with a `/tmp` tmpfs (T009), so leaving it is a latent trap for any future reviver. Confirm no remaining references (`grep -rn "_copy_script_to_container\|/tmp/execution" src/`) after removal (analyze finding U1). Depends on T009.
- [X] T010 [US1] Wire `config.*` into the `ScriptExecutor(...)` construction in `src/server.py` (~L312), passing the seven container-shaping resolved values through (not `container_allow_root` — see T008). Depends on T009.

**Checkpoint**: A no-config deployment produces fully hardened containers; T006/T007 pass. MVP is functional and independently testable.

---

## Phase 4: User Story 2 - Explicit, validated relaxation of a default (Priority: P2)

**Goal**: Valid non-default values take effect; invalid values or the root-while-disallowed combination cause the server to fail fast at startup with an error naming the offending variable(s).

**Independent Test**: Set each variable to a valid non-default value and confirm the container reflects it; set invalid values (bad boolean, unknown enum, malformed `uid:gid`, disallowed capability, malformed tmpfs size, `0:0` with `CONTAINER_ALLOW_ROOT` false) and confirm `load_config()` raises `ConfigurationError` naming the offending variable, before the listen port binds (quickstart.md Scenario B).

### Tests for User Story 2 ⚠️ (write first, ensure they fail)

- [X] T011 [P] [US2] Add unit tests in `tests/test_config.py`: valid overrides parse (`CONTAINER_NETWORK_MODE=bridge`, `WORKSPACE_MOUNT_MODE=rw`, `CONTAINER_USER=0:0`+`CONTAINER_ALLOW_ROOT=true`, a narrower `CONTAINER_CAP_ADD` subset, empty `CONTAINER_CAP_ADD` → `[]`, empty `CONTAINER_TMPFS_SIZE` → no tmpfs); each invalid value from the contract raises `ConfigurationError` whose message names the variable and accepted values; the root-gate error names **both** `CONTAINER_USER` and `CONTAINER_ALLOW_ROOT` (FR-011–FR-020; config-env-contract.md error contract).
- [X] T012 [P] [US2] Add property tests in `tests/test_config_properties.py`: any enum value outside its set is rejected; any `uid:gid` with a missing/negative/non-integer part is rejected while well-formed pairs pass; any capability outside the 14-cap allow-list is rejected (case-sensitive) while subsets of the allow-list pass; any tmpfs string that is `0`/negative/no-unit/whitespace-padded is rejected while `positive-int[+b|k|m|g]` passes (FR-013–FR-016).

### Implementation for User Story 2

- [X] T013 [US2] Implement the validation logic in `ServerConfig.validate()` (and small private helpers) in `src/config.py`, raising `ValueError` (wrapped as `ConfigurationError` by `load_config()`): enum checks for `workspace_mount_mode` ∈ {ro,rw} and `container_network_mode` ∈ {none,bridge,host}; `container_user` `uid:gid` format (both parts present, non-negative ints); `container_cap_add` ⊆ `CONTAINER_CAP_ALLOWLIST` (case-sensitive); `container_tmpfs_size` size grammar when non-empty. Each message names the offending variable and accepted values/format (FR-013–FR-017; config-env-contract.md). Depends on T005.
- [X] T014 [US2] Implement the root-user gate in `ServerConfig.validate()` in `src/config.py`: when `container_user` resolves to uid 0 and `container_allow_root` is false, reject with the both-variable message from config-env-contract.md; permit uid 0 with `allow_root=true`, and permit any non-root user regardless of `allow_root` (FR-018–FR-020; data-model.md cross-field rule). Depends on T013.

**Checkpoint**: Valid relaxations take effect (via the US1 flow-through) and every invalid configuration fails fast before binding; T011/T012 pass. US1 and US2 both independently testable.

---

## Phase 5: User Story 3 - Effective security posture is observable for attestation and audit (Priority: P3)

**Goal**: All eight effective values are bound into attestation `user_data` for both the execute and output surfaces, and recorded in startup output — so any relaxation is visible, never silent.

**Independent Test**: Configure one relaxed setting (e.g. `CONTAINER_NETWORK_MODE=bridge`), trigger `/execute` and `/output`, and confirm the `user_data` passed to `nitro-tpm-attest` contains all eight keys with effective values (relaxed where set, defaults otherwise), with `container_cap_add` as the resolved list; and confirm startup output prints all eight values (quickstart.md Scenario C, FR-028).

### Tests for User Story 3 ⚠️ (write first, ensure they fail)

- [X] T015 [P] [US3] Extend `tests/test_attestation_user_data_regression.py` to pin the eight new `user_data` keys (`container_user`, `container_allow_root`, `container_cap_add` as array, `no_new_privileges`, `container_read_only_rootfs`, `container_tmpfs_size`, `workspace_mount_mode`, `container_network_mode`) alongside the existing keys, for both `generate_attestation` and `generate_output_attestation`; assert a relaxed value (e.g. `container_network_mode=="bridge"`) is distinguishable from defaults (attestation-user-data-contract.md; SC-004).
- [X] T016 [P] [US3] Add property tests in `tests/test_attestation_properties.py` asserting that for any valid config the eight `user_data` values round-trip to the resolved config values, and that the execute-time and output-time attestations carry identical security values for a given execution (attestation-user-data-contract.md "Two surfaces stay consistent").

### Implementation for User Story 3

- [X] T017 [US3] Add the eight container-security parameters to `AttestationGenerator.generate_attestation()` and `.generate_output_attestation()` in `src/attestation.py` (mirroring `gpu_enabled` ~L146–147, L322–323), threading them into the `user_data` object; surface `container_cap_add` as its resolved list so unset (default 7) vs empty (`[]`) is unambiguous (FR-026, FR-027; research.md Decision 6). Depends on T005.
- [X] T018 [US3] Pass the eight effective security values into `generate_attestation` (~L839) and `generate_output_attestation` (~L1155) at the two call sites in `src/server.py` (FR-027), sourcing `container_allow_root` **directly from `config`** and the resolved `container_cap_add` list from the executor/config; the executor is not consulted for `allow_root` (see Clarifications 2026-06-13). Depends on T017.
- [X] T019 [P] [US3] Add `logger.info` startup lines for all eight effective values in `src/main.py`, alongside the existing config log block (~L52–62), after `load_config()` (FR-028; research.md Decision 7). Depends on T005.

**Checkpoint**: All three user stories independently functional; effective posture is both logged at startup and attested per execution.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, end-to-end coverage, and validation across stories.

- [X] T020 [P] Document all eight variables in `.env.example` with default value and a brief security rationale each; add explicit trade-off callouts for (a) the hardened-defaults backward-compat impact and (b) the `none` network default breaking outbound-network jobs; per breaking default, state the symptom and the variable change that restores prior behavior (FR-024, FR-025, FR-029; SC-005).
- [X] T021 Add the new end-to-end test file `tests/test_security_config_integration.py` covering: defaults produce a hardened container (US1), a valid relaxation flows through and is attested (US2+US3), and an invalid config fails fast before serving (US2) — exercising config → executor → attestation together (quickstart.md "Done When").
- [X] T022 Run the feature-focused suite and full suite per quickstart.md (`.venv/bin/pytest -q` and the listed subset) and confirm all pass, then execute the fail-fast smoke check `CONTAINER_NETWORK_MODE=nat .venv/bin/python -c "from src.config import load_config; load_config()"` and confirm a `ConfigurationError` naming `CONTAINER_NETWORK_MODE` with a non-zero exit.
- [X] T023 [P] Add a real-Docker end-to-end integration test (e.g. `tests/test_security_config_real_docker.py`) that actually runs a representative job under a relaxed setting (e.g. `CONTAINER_NETWORK_MODE=bridge` and/or `WORKSPACE_MOUNT_MODE=rw`) against a live Docker daemon and asserts the job succeeds and produces its expected effect (network reachable / artifact persisted). Gate it to skip when no Docker daemon (and NitroTPM, where required) is available — e.g. a `pytest.mark.skipif` on daemon probe or an opt-in marker/env flag — so it does not run in standard CI (SC-006; spec Clarifications 2026-06-13).

---

## Phase 7: Doc-Conformance Fixes (post-implementation review)

**Purpose**: Two defects surfaced by checking the implementation against the official Docker SDK / Docker Engine tmpfs docs and the AWS NitroTPM `nitro-tpm-attest` docs. Both affect the hardened-default path.

- [X] T024 Fix the `/tmp` tmpfs so the non-root default user can write to it. In `src/script_executor.py` (~L275) change the tmpfs option string from `{"/tmp": f"size={self._tmpfs_size}"}` to `{"/tmp": f"size={self._tmpfs_size},mode=1777"}`. Docker docs claim tmpfs defaults to mode 1777, but runc 1.1+/Docker 20.10.7+ actually bring an option-less tmpfs up root-owned `0755`, so the hardened default (`CONTAINER_USER=65534:65534` + `CONTAINER_READ_ONLY_ROOTFS=true`) leaves jobs unable to write `/tmp` (EACCES) — which defeats research.md Decision 5 (the `/tmp` scratch is what makes a hardened-default job succeed). Refs: [docker/docs#15594](https://github.com/docker/docs/issues/15594), [runc#4971](https://github.com/opencontainers/runc/issues/4971). Then update the tests that pin the exact tmpfs string to expect `size=<value>,mode=1777`: `tests/test_docker_script_executor.py` (~L197, L236), `tests/test_docker_container_properties.py` (~L260, L634), `tests/test_security_config_integration.py` (~L189), `tests/test_script_executor.py` (~L1027). Confirm `.venv/bin/pytest -q` is green.
- [X] T025 Guard the attestation `user_data` against the AWS NitroTPM 1024-byte field limit. The eight new container-security keys (T017) materially grow the `/execute` and `/output` `user_data`; with a long `repository_url` + `script_path` and an operator-expanded `CONTAINER_CAP_ADD`, the JSON can cross the documented **0–1024 byte** `user_data` cap, causing `nitro-tpm-attest` to reject the request and `/execute` + `/output` to fail with only a generic non-zero exit. In `src/attestation.py`, before invoking `nitro-tpm-attest` in both `generate_attestation()` and `generate_output_attestation()`, validate `len(user_data_json.encode("utf-8")) <= 1024` and return a clear `AttestationError` / error string naming the limit when exceeded (or shrink the payload — e.g. omit security keys equal to their documented default, or carry `container_cap_add` as a short token — while preserving SC-004 "relaxation stays visible"). Refs: [Get the NitroTPM Attestation Document](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/attestation-get-doc.html). Add a regression assertion in `tests/test_attestation_user_data_regression.py` that the serialized `user_data` stays within 1024 bytes (and that an oversize input is rejected with the limit-naming error), so this can't regress silently. Depends on T017.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup. BLOCKS all user stories. T003 → T004 → T005 (same file, sequential).
- **User Stories (Phase 3–5)**: All depend on Foundational (specifically T005). Once T005 lands, US1/US2/US3 can proceed in parallel (different files): US1 in `script_executor.py`/`server.py` executor wiring, US2 in `config.py` `validate()`, US3 in `attestation.py`/`server.py` call sites/`main.py`.
- **Polish (Phase 6)**: Depends on the user stories it validates (T021/T022 after US1–US3; T020 doc-only, can start any time after T005).

### User Story Dependencies

- **US1 (P1)**: After Foundational. No dependency on US2/US3. Delivers the MVP (hardened defaults).
- **US2 (P2)**: After Foundational. Independent of US1 — its relaxed values take effect through the US1 flow-through if present, but its validation/fail-fast is testable on `config.py` alone.
- **US3 (P3)**: After Foundational. Independent of US1/US2 — surfaces resolved config values regardless of whether relaxation or flow-through exist.

### Within Each Story

- Tests are written first and should fail before implementation.
- US1: T008 → T009 → T010 (all in the executor/server flow, sequential on the same files).
- US2: T013 → T014 (T014 builds on the validate() scaffolding from T013).
- US3: T017 → T018 (call sites depend on the new params); T019 is independent ([P]).

### Parallel Opportunities

- T006 and T007 (US1 tests) are parallel; T011 and T012 (US2 tests) are parallel; T015, T016 (US3 tests) and T019 (main.py logging) are parallel.
- With multiple developers, after T005: Dev A → US1, Dev B → US2, Dev C → US3.
- T020 (`.env.example`) is parallel with all implementation once T005 lands.

---

## Parallel Example: After Foundational (T005) completes

```bash
# US1 tests (parallel):
Task: "Unit tests for hardened-default container kwargs in tests/test_script_executor.py"
Task: "Property tests for container kwarg invariants in tests/test_docker_container_properties.py"

# US2 tests (parallel):
Task: "Validation unit tests in tests/test_config.py"
Task: "Validation property tests in tests/test_config_properties.py"

# US3 tests + logging (parallel):
Task: "user_data regression keys in tests/test_attestation_user_data_regression.py"
Task: "Attestation property tests in tests/test_attestation_properties.py"
Task: "Startup logging for eight values in src/main.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1: Setup (T001–T002).
2. Phase 2: Foundational (T003–T005) — CRITICAL, blocks all stories.
3. Phase 3: User Story 1 (T006–T010).
4. **STOP and VALIDATE**: a no-config deployment yields hardened containers (SC-001).

### Incremental Delivery

1. Setup + Foundational → config fields exist with secure defaults.
2. US1 → hardened-by-default flow-through → MVP.
3. US2 → validated relaxation + fail-fast → safe opt-outs.
4. US3 → attestation + startup observability → posture is provable.
5. Polish → docs, integration test, quickstart validation.

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- Every task names exact file paths; line anchors (e.g. ~L312) are starting points to confirm against the current file, not literal guarantees.
- The `gpu_enabled` feature is the exact precedent for all four seams — mirror it.
- Preserve the existing `ValueError` → `ConfigurationError` error style; messages must name the offending variable (FR-017).
- Verify tests fail before implementing; commit after each task or logical group.
