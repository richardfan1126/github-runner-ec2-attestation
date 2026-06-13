# Phase 0 Research: Container Security Configuration

All NEEDS CLARIFICATION items from the spec were resolved during `/speckit-clarify` and the checklist review. This file records the decisions, rationale, and rejected alternatives that the implementation depends on.

## Decision 1 — Capability allow-list scope

**Decision**: The `CONTAINER_CAP_ADD` allow-list is the Docker default-bounding capability set (14 caps): `CHOWN`, `DAC_OVERRIDE`, `FSETID`, `FOWNER`, `MKNOD`, `NET_RAW`, `SETGID`, `SETUID`, `SETFCAP`, `SETPCAP`, `NET_BIND_SERVICE`, `SYS_CHROOT`, `KILL`, `AUDIT_WRITE`. The *default granted* set when the var is unset is the existing 7-cap working set (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID`, `NET_BIND_SERVICE`, `KILL`), a strict subset of the allow-list.

**Rationale**: The 7 caps preserve today's behavior (`script_executor.py:254`). The 14-cap Docker default set is a well-understood, vetted boundary — operators can grant beyond today's set without forking, but cannot request dangerous caps (`SYS_ADMIN`, `SYS_PTRACE`, `NET_ADMIN`, …) without a code change. Names are matched case-sensitively, upper-case, without the `CAP_` prefix (matches how the Docker SDK `cap_add` list is expressed).

**Alternatives considered**:
- *Exactly the 7 default caps* — rejected: too rigid; any legitimate new cap need forces a code change.
- *Any valid Linux capability* — rejected: weakest guardrail; defeats the point of an allow-list since `SYS_ADMIN` would be grantable.

## Decision 2 — `CONTAINER_USER` format

**Decision**: Require full `uid:gid`; both parts present and non-negative integers. A bare uid (`1000`) is rejected at startup.

**Rationale**: Unambiguous and fully attestable — the surfaced value is always `uid:gid`. Avoids relying on image/runtime gid defaults that would make the attested posture depend on the base image.

**Alternatives considered**: *Accept bare uid* (Docker's own `--user` permits it) — rejected for attestation determinism.

## Decision 3 — Validation & fail-fast mechanics

**Decision**: Parse in `ServerConfig.from_env()` and validate in `ServerConfig.validate()` (or small private helpers it calls), raising `ValueError` that `load_config()` wraps as `ConfigurationError`. "Fail fast" = non-zero process exit during `load_config()` in `main.py` before uvicorn binds the port; the listen socket is never opened.

**Rationale**: Identical to every existing option (`MAX_CONTAINER_PIDS`, `ENABLE_GPU`, `ALLOW_NO_TPM`, digest pinning). Booleans reuse `parse_strict_bool()`. Error messages name the variable and accepted form (FR-017).

**Alternatives considered**: A separate validation module — rejected: inconsistent with the single-`ServerConfig` pattern.

## Decision 4 — `CONTAINER_TMPFS_SIZE` format

**Decision**: When non-empty, accept a positive integer optionally followed by a single unit suffix `b`/`k`/`m`/`g`. Reject `0`, negative, missing/unknown unit, and surrounding whitespace. Empty/unset = no tmpfs mounted (an explicit, valid choice — must not be silently overridden).

**Rationale**: Matches the size grammar the container runtime accepts for tmpfs mounts and Docker's `mem_limit`-style sizes already used in the project. The empty-vs-default distinction mirrors the cap_add unset-vs-empty rule.

## Decision 5 — Flow-through to container creation (Docker SDK kwargs)

**Decision**: Map each resolved value onto `docker.containers.create()` kwargs in `script_executor.py`:
- `CONTAINER_USER` → `user="uid:gid"`
- `CONTAINER_CAP_ADD` → `cap_drop=["ALL"]` + `cap_add=[…resolved set…]` (empty list when explicitly empty)
- `NO_NEW_PRIVILEGES` → `security_opt=["no-new-privileges"]` when true, omitted when false
- `CONTAINER_READ_ONLY_ROOTFS` → `read_only=True/False`
- `CONTAINER_TMPFS_SIZE` (non-empty) → `tmpfs={"/tmp/execution": "size=<value>"}` (existing scratch mount path; see `script_executor.py` tmpfs readiness check ~L390)
- `WORKSPACE_MOUNT_MODE` → workspace volume bind `mode` (`ro`/`rw`) at the `/workspace` mount (currently hard-coded `ro`, `script_executor.py:249`)
- `CONTAINER_NETWORK_MODE` → `network_mode="none"|"bridge"|"host"`

**Rationale**: These are the exact kwargs the Docker SDK exposes; the executor already sets `security_opt`, `cap_drop`, `cap_add`, and the `ro` workspace bind, so this is a parameterization of existing literals rather than new machinery.

**Open implementation note**: confirm the current scratch/tmpfs mount path used by the executor's readiness retry (`/tmp/execution`) so `CONTAINER_TMPFS_SIZE` targets the same path the rest of the code expects. (Resolved against `script_executor.py` during Phase 1 data-model.)

## Decision 6 — Attestation/audit surface

**Decision**: Thread the eight effective values into attestation `user_data` via new optional parameters on `AttestationGenerator.generate_attestation()` and `.generate_output_attestation()`, exactly as `gpu_enabled` is added today (`attestation.py:146-147, 322-323`). Wire them at the two call sites in `server.py` (~L839 execute, ~L1155 output). The resolved `CONTAINER_CAP_ADD` is surfaced as its concrete list so unset-vs-empty is distinguishable.

**Rationale**: Reuses the existing attestation channel (spec Assumption) — no new attestation surface. A relying party reads the effective value per setting and compares to the documented default (SC-004).

**Alternatives considered**: A dedicated posture endpoint — rejected: out of scope and redundant with `user_data`.

## Decision 7 — Startup observability (FR-028)

**Decision**: Add `logger.info` lines in `main.py` after `load_config()` for all eight effective values, alongside the existing config log block (`main.py:52-62`).

**Rationale**: Posture is inspectable independent of any execution; consistent with how every other config value is logged at startup.
