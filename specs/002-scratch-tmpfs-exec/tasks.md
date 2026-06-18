---
description: "Task list for Configurable Execution Permission on the Container Scratch tmpfs"
---

# Tasks: Configurable Execution Permission on the Container Scratch tmpfs

**Input**: Design documents from `/specs/002-scratch-tmpfs-exec/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — the spec lists test files per seam (plan.md §Project Structure), the quickstart drives the validation via `pytest`, and FR/SC reference test-backed behavior (fail-fast, byte-identical default, attested value).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths are included in every task

## Path Conventions

- Single project: `src/`, `tests/` at repository root; tooling under `.github/scripts/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish a known-good baseline before threading the new field through.

- [X] T001 Establish a green baseline: run `uv run pytest` from the repo root and confirm the existing suites pass before making changes (no new dependencies are required for this feature).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the single `container_tmpfs_exec` config field. Every user story (executor wiring, attestation, startup logging, build summary) reads this field, so it MUST exist first.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Add field `container_tmpfs_exec: bool = False` to the `ServerConfig` dataclass in `src/config.py`, immediately after `container_tmpfs_size` (~L142) in the Container Security block.
- [X] T003 In `ServerConfig.from_env()` in `src/config.py`, parse `CONTAINER_TMPFS_EXEC` with `parse_strict_bool(os.getenv("CONTAINER_TMPFS_EXEC", "false"), "CONTAINER_TMPFS_EXEC")` (mirroring the `container_tmpfs_size` parse site ~L314-315) and pass `container_tmpfs_exec=...` into the constructed `ServerConfig(...)` (~L383). No new `validate()` rule — `parse_strict_bool` fails fast on invalid input.

**Checkpoint**: `load_config().container_tmpfs_exec` resolves (default `False`); invalid values fail fast naming the variable. User stories can now begin.

---

## Phase 3: User Story 1 - Non-executable scratch by default (Priority: P1) 🎯 MVP

**Goal**: Build the `/tmp` scratch tmpfs mount path so that, with the variable unset, the mount is byte-identical to today's `noexec` behavior — and the field is threaded into the executor.

**Independent Test**: With `CONTAINER_TMPFS_EXEC` unset, resolve config (`container_tmpfs_exec == False`) and confirm the produced `/tmp` tmpfs options string is `size=<size>,mode=1777` (no `exec`); a binary under `/tmp` is non-executable (`Permission denied (os error 13)`).

### Implementation for User Story 1

- [X] T004 [US1] Add constructor parameter `tmpfs_exec: bool = False` to `ScriptExecutor.__init__` and store `self._tmpfs_exec = tmpfs_exec` in `src/script_executor.py` (constructor ~L32-106, alongside the existing `tmpfs_size` / `self._tmpfs_size`).
- [X] T005 [US1] In the `if self._tmpfs_size:` block in `src/script_executor.py` (~L278-279), conditionally append `",exec"` to the `/tmp` tmpfs options string **only** when `self._tmpfs_exec` is `True` (e.g. `f"size={self._tmpfs_size},mode=1777" + (",exec" if self._tmpfs_exec else "")`). Leave `size`, `mode=1777`, and the Docker-default `nosuid`/`nodev` untouched; the disabled path MUST stay byte-identical to today.
- [X] T006 [US1] Wire `config.container_tmpfs_exec` into the `ScriptExecutor(...)` construction as `tmpfs_exec=config.container_tmpfs_exec` in `src/server.py` (~L330, next to the existing `tmpfs_size=` argument).

### Tests for User Story 1

- [X] T007 [P] [US1] Add config tests in `tests/test_config.py` and `tests/test_config_properties.py`: unset → `container_tmpfs_exec is False`; falsy values (`false`/`0`/`no`, case-insensitive) → `False`.
- [X] T008 [P] [US1] Add executor tests in `tests/test_script_executor.py` and `tests/test_docker_container_properties.py`: with `tmpfs_exec=False` and a non-empty size, the `/tmp` tmpfs options string contains no `exec` and is byte-identical to the pre-feature string (INV-1); `mode=1777`/size/`nosuid`/`nodev` invariants hold.

**Checkpoint**: Secure-by-default mount path complete and independently testable (SC-001).

---

## Phase 4: User Story 2 - Opt in to executable scratch (Priority: P2)

**Goal**: Allow an operator to enable execution from scratch, document the trade-off, and verify the enabled mount path adds exactly `,exec`.

**Independent Test**: With `CONTAINER_TMPFS_EXEC=true` and a non-empty `CONTAINER_TMPFS_SIZE`, config resolves to `True` and the `/tmp` tmpfs options string is `size=<size>,mode=1777,exec`; only the `exec` option differs from the disabled container.

### Implementation for User Story 2

- [ ] T009 [P] [US2] Document `CONTAINER_TMPFS_EXEC` in `.env.example`, next to `CONTAINER_TMPFS_SIZE`: state the secure default (`false` / `noexec`) and the security implication of enabling exec-from-scratch (unblocks trusted compile-and-run build toolchains, e.g. Rust `build.rs`, vs. relaxing the no-execute-from-scratch posture).

### Tests for User Story 2

- [ ] T010 [P] [US2] Add config tests in `tests/test_config.py` / `tests/test_config_properties.py`: truthy values (`true`/`1`/`yes`, case-insensitive) → `True`; an invalid value (e.g. `maybe`) raises a startup error naming `CONTAINER_TMPFS_EXEC` (SC-004).
- [ ] T011 [P] [US2] Add executor/integration tests in `tests/test_docker_container_properties.py` and `tests/test_security_config_integration.py`: with `tmpfs_exec=True` the `/tmp` options include `,exec`; toggling the flag changes **only** the `exec` option — `size`, `mode=1777`, `nosuid`, `nodev`, `read_only`, `cap_drop`, `no-new-privileges`, network mode, and limits are identical between enabled and disabled containers (SC-005).

**Checkpoint**: Opt-in path verified; operator docs in place. US1 + US2 both pass independently.

---

## Phase 5: User Story 3 - Effective value is attested and visible (Priority: P3)

**Goal**: Surface the effective setting in attestation `user_data`, the build-time configuration summary, and startup logs (including the enabled-but-no-tmpfs warning) so the relaxation is never hidden.

**Independent Test**: Resolve config both unset and enabled; confirm the attested `user_data` carries `container_tmpfs_exec` (both attest paths) and the build summary renders it under "Container Security"; enabled-with-empty-size emits a startup warning (no fail-fast).

### Implementation for User Story 3

- [ ] T012 [US3] In `src/attestation.py`, add a `container_tmpfs_exec` parameter to `_build_security_user_data` (~L52-88) and to the `generate_attestation` (~L119-200) and `generate_output_attestation` (~L362-410) signatures; emit `user_data["container_tmpfs_exec"]` only when the value is not `None`, paired alongside `container_tmpfs_size` (FR-009).
- [ ] T013 [US3] In `src/server.py`, pass `container_tmpfs_exec=config.container_tmpfs_exec` into `generate_attestation` (~L839) and `generate_output_attestation` (~L1169) so both attest paths report the same value used to build the mount (FR-008). (Same file as T006 but different call sites.)
- [ ] T014 [US3] In `src/main.py` (~L60-74), add a startup log line reporting the effective `container_tmpfs_exec` value near the existing tmpfs-size line, and emit a WARNING when `container_tmpfs_exec` is `True` AND `container_tmpfs_size` is empty ("exec enabled but no tmpfs is mounted; setting has no effect"). Do NOT fail fast (FR-007).
- [ ] T015 [P] [US3] In `.github/scripts/print_config.py`, add `"container_tmpfs_exec"` to `CONFIG_CATEGORIES["Container Security"]` immediately after `container_tmpfs_size` (~L78-88) so it renders grouped with its security siblings (FR-010).

### Tests for User Story 3

- [ ] T016 [P] [US3] Add attestation tests in `tests/test_attestation_user_data_regression.py` and `tests/test_attestation_properties.py`: `container_tmpfs_exec` appears in `user_data` for both `generate_attestation` and `generate_output_attestation`, and equals the value passed in (INV-3).
- [ ] T017 [P] [US3] Add a test in `tests/test_print_config.py` asserting `container_tmpfs_exec` is rendered under the "Container Security" category of the build summary.

**Checkpoint**: Posture fully observable across attestation, build summary, and startup logs (SC-003).

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T018 [P] If any operator-facing doc beyond `.env.example` (e.g. `README` / docs) enumerates the container-security variables, add `CONTAINER_TMPFS_EXEC` with its secure default and security note for parity (FR-011).
- [ ] T019 Run the quickstart validation end-to-end: execute Scenarios 1–6 in `specs/002-scratch-tmpfs-exec/quickstart.md` and confirm `uv run pytest` is green across all touched suites.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately.
- **Foundational (Phase 2, T002-T003)**: Depends on Setup — BLOCKS all user stories (the config field is read by every story).
- **User Stories (Phases 3-5)**: All depend on Foundational completion.
  - US1 (P1) is the MVP and builds the shared executor mount path that US2 verifies.
  - US2 (P2) verification of the enabled branch depends on US1's executor change (T004-T005).
  - US3 (P3) is independent of US1/US2 implementation; needs only the Foundational field.
- **Polish (Phase 6)**: Depends on the desired user stories being complete.

### Key cross-file note

- `src/server.py` is edited in **T006 (US1)** and **T013 (US3)** at different call sites (executor construction vs. attestation calls). Do them sequentially; they do not conflict.
- `src/config.py` edits T002 and T003 are the same file — sequential.

### Within Each User Story

- US1: T004 → T005 (same file, ordered) → T006; tests T007/T008 after the implementation they exercise.
- US2: docs T009 independent; tests T010/T011 after Foundational + US1 executor change.
- US3: T012 → T013; T014, T015 independent of each other; tests T016 after T012/T013, T017 after T015.

### Parallel Opportunities

- **Within US1**: T007 and T008 can run in parallel (different test files) once T004-T006 land.
- **Within US2**: T009, T010, T011 are all `[P]` (different files).
- **Within US3**: T015, T016, T017 are `[P]`; T012/T013/T014 touch shared seams and are ordered as noted.
- **Across stories**: US3 (attestation/summary/logging) can be developed in parallel with US1/US2 by a different person, since it only needs the Foundational field — but final test runs should include all changes.

---

## Parallel Example: User Story 3

```bash
# After T012/T013 land, launch the independent US3 tasks together:
Task: "Add container_tmpfs_exec to CONFIG_CATEGORIES['Container Security'] in .github/scripts/print_config.py"
Task: "Attestation user_data tests in tests/test_attestation_user_data_regression.py / tests/test_attestation_properties.py"
Task: "print_config render test in tests/test_print_config.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1: Setup (green baseline).
2. Phase 2: Foundational — add + parse the config field.
3. Phase 3: US1 — executor mount path with secure default + wiring.
4. **STOP and VALIDATE**: unset variable → `noexec`, byte-identical mount string, tests green (SC-001).

### Incremental Delivery

1. Setup + Foundational → field exists.
2. US1 → secure-default mount path (MVP).
3. US2 → opt-in exec + operator docs + verification.
4. US3 → attestation, build summary, and startup logging/observability.
5. Polish → quickstart validation + any remaining docs.

---

## Notes

- `[P]` = different files, no dependency on incomplete tasks.
- The disabled path MUST remain byte-identical to pre-feature output (INV-1) — no `noexec` token is emitted; Docker's default already applies.
- Enabling adds exactly the substring `,exec` and nothing else (INV-2).
- Commit after each logical group; the `user_data` 1024-byte NitroTPM cap is unaffected by one short boolean field.
