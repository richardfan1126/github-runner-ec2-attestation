# Phase 0 Research: Configurable Execution Permission on the Container Scratch tmpfs

All open questions for this feature were resolved during `/speckit-clarify`
(spec §Clarifications, Session 2026-06-18). The Technical Context in `plan.md` has
**no remaining NEEDS CLARIFICATION** markers — the feature reuses mechanisms that
already exist in the codebase (feature 001). This document records the design
decisions and the existing-code facts they rest on.

## Decision 1 — Reuse the existing `/tmp` tmpfs mount; add only the `exec` option

- **Decision**: Implement exec-permission as a conditional `,exec` appended to the
  options of the **same** `/tmp` tmpfs mount string that `container_tmpfs_size`
  already builds, inside the existing `if self._tmpfs_size:` block in
  `script_executor.py` (currently `{"/tmp": f"size={self._tmpfs_size},mode=1777"}`).
- **Rationale**: Docker brings a `--tmpfs` mount up `rw,nosuid,nodev,noexec` unless
  `exec` is explicitly requested. The scratch mount already exists and is governed
  solely by tmpfs size; adding execution is a single extra option on that one mount.
  This guarantees `nosuid`/`nodev`/`mode=1777`/`size` are untouched (FR-006, SC-005)
  because the implementation only conditionally concatenates the `exec` token.
- **Alternatives considered**:
  - *A second/separate mount or a remount* — rejected: introduces a new mount and
    new failure modes for zero benefit; the option belongs on the existing mount.
  - *Always emit an explicit `noexec` token when disabled* — rejected: Docker's
    default is already `noexec`, so emitting it is redundant; keeping the disabled
    path byte-identical to today's string preserves the "no change for existing
    deployments" guarantee (FR-004) and keeps existing executor tests green.

## Decision 2 — Strict boolean parsing, secure default, fail-fast on invalid

- **Decision**: Parse `CONTAINER_TMPFS_EXEC` with the existing
  `parse_strict_bool(value, "CONTAINER_TMPFS_EXEC")`, defaulting the env lookup to
  `"false"`. Store as `container_tmpfs_exec: bool = False` on `ServerConfig`.
- **Rationale**: FR-002/FR-003 require the same grammar as the other toggles
  (case-insensitive `true`/`1`/`yes` → enabled; `false`/`0`/`no` → disabled; any
  other value fails fast naming the variable). `parse_strict_bool` already does
  exactly this and raises `ValueError` → surfaced as a startup `ConfigurationError`
  before the port binds (SC-004), so **no new `validate()` rule is needed**.
- **Alternatives considered**: A bespoke parser — rejected; would duplicate
  behavior and risk divergence from `NO_NEW_PRIVILEGES` / `CONTAINER_READ_ONLY_ROOTFS`.

## Decision 3 — Enabled-but-no-tmpfs: resolve, attest, warn, do not fail

- **Decision**: When `container_tmpfs_exec` is `True` but `container_tmpfs_size` is
  empty (no tmpfs mounted), still resolve and attest the value; apply **no** mount
  change (the `if self._tmpfs_size:` guard already skips mounting); and emit a
  **startup log warning** in `main.py` that exec is enabled but has no effect
  because no tmpfs is mounted. Do **not** fail fast.
- **Rationale**: Spec Clarification 2026-06-18 and FR-007. The setting is
  orthogonal to whether a mount exists; failing fast would punish a benign config.
  A warning keeps the no-op visible. The container-creation guard means no special
  executor logic is required for this case — the `exec` token is simply never built
  because the whole tmpfs block is skipped.
- **Alternatives considered**: Fail fast — rejected by clarification. Silent no-op
  — rejected; the relaxation intent should be observable (warning required).

## Decision 4 — Attested field name `container_tmpfs_exec` (boolean)

- **Decision**: Add `container_tmpfs_exec` (boolean) to the security subset of
  `user_data`, built in `attestation._build_security_user_data`, included only when
  not `None`, paired alongside `container_tmpfs_size`.
- **Rationale**: Spec Clarification 2026-06-18 / FR-009 — follows the existing
  `container_`-prefixed naming and the established "include when provided" pattern
  used for every other security field, so both `generate_attestation` and
  `generate_output_attestation` carry it identically with no drift (FR-008).
- **Alternatives considered**: `tmpfs_exec` / `scratch_exec` — rejected for
  consistency with the `container_tmpfs_size` sibling and the prefix convention.

## Decision 5 — Build-summary rendering via the existing single source of truth

- **Decision**: Add `"container_tmpfs_exec"` to
  `CONFIG_CATEGORIES["Container Security"]` in `.github/scripts/print_config.py`
  (after `container_tmpfs_size`). No workflow YAML change.
- **Rationale**: FR-010 / SC-003. `print_config.py` enumerates
  `dataclasses.fields(ServerConfig)` and renders from `load_config()`, so the field
  would appear under the catch-all "Other" group even if unlisted — but listing it
  in the category map keeps it grouped with its security siblings. The value is
  therefore derived programmatically from the application config and cannot drift
  from the image. The workflow step that appends the summary already exists.
- **Alternatives considered**: Hand-maintaining the YAML/summary — rejected; the
  whole point of the feature-001 helper is drift-proofing from the dataclass.

## Decision 6 — Documentation in `.env.example`

- **Decision**: Document `CONTAINER_TMPFS_EXEC` in `.env.example` with its secure
  default (`false` / `noexec`) and the security implication of enabling exec from
  scratch (FR-011), next to `CONTAINER_TMPFS_SIZE`.
- **Rationale**: Parity with the other eight documented security variables; the
  operator-facing trade-off (unblocks trusted compile-and-run builds vs. relaxing
  the no-execute-from-scratch posture) must be stated where operators configure it.

## Existing-code facts this plan relies on (verified)

| Seam | Location | Current behavior reused |
|------|----------|-------------------------|
| Strict bool parser | `src/config.py:13` `parse_strict_bool` | truthy/falsy grammar + fail-fast naming the key |
| Config field + parse | `src/config.py:142,314-315,383` `container_tmpfs_size` | sibling field/parse/construct pattern |
| tmpfs mount build | `src/script_executor.py:278-279` | `if self._tmpfs_size:` → `{"/tmp": "size=…,mode=1777"}` |
| Executor constructor | `src/script_executor.py:32-106` | `tmpfs_size` param + `self._tmpfs_size` |
| Attestation subset | `src/attestation.py:52-88` `_build_security_user_data` | include-when-not-None per field |
| Attestation callers | `src/attestation.py:119-200,362-410` | both attest paths pass the same security kwargs |
| Server wiring | `src/server.py:312-330,839-852,1169-1177` | config → executor + → both attest calls |
| Startup logging | `src/main.py:60-74` | per-field effective-value log lines |
| Build summary | `.github/scripts/print_config.py:49,78-88` | `CONFIG_CATEGORIES["Container Security"]` |

**Output**: research.md with all decisions recorded; no NEEDS CLARIFICATION remain.
