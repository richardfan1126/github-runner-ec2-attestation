# Feature Specification: Container Security Configuration via Environment Variables

**Feature Branch**: `001-container-security-config`

**Created**: 2026-06-13

**Status**: Draft

**Input**: User description: "Expose container security configuration via environment variables with secure-by-default values — add eight new operator-facing configuration options to the GitHub Actions Remote Executor so the security posture of execution containers can be tuned per-deployment without code changes, each defaulting to the secure choice, with every effective value observable for attestation and invalid combinations failing fast at startup."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Hardened sandbox by default (Priority: P1)

An operator deploys the executor without setting any of the new container-security variables. Every execution container they run is automatically hardened: the job process runs as a non-root user, the container root filesystem is read-only with a bounded writable scratch area for temporary files, the cloned-repository workspace is mounted read-only, privilege escalation is blocked, Linux capabilities are reduced to the minimal working set, and the sandbox has no outbound network access.

**Why this priority**: This is the core value of the feature — making the safe configuration the one an operator gets when they do nothing. It eliminates the silent reliance on insecure Docker defaults (containers running as root with a writable root filesystem and unrestricted network) that exists today. It is the minimum viable slice: even with no tuning capability at all, shipping hardened defaults materially improves the security posture of every deployment.

**Independent Test**: Deploy the server with none of the eight new variables set, run a representative job, and inspect the resulting container's effective configuration (user, root filesystem mount, workspace mount, privilege-escalation setting, granted capabilities, network mode, scratch mount). Confirm each reflects the secure default and that the job that only needs local compute still succeeds.

**Acceptance Scenarios**:

1. **Given** no container-security variables are set, **When** the server starts and runs a job, **Then** the container process runs as a non-root user (uid:gid `65534:65534`) rather than the image's default user.
2. **Given** no container-security variables are set, **When** a job runs, **Then** the container root filesystem is mounted read-only and a bounded in-memory writable scratch mount (default `256m`) is available for temporary files.
3. **Given** no container-security variables are set, **When** a job runs, **Then** the cloned-repository workspace is mounted read-only.
4. **Given** no container-security variables are set, **When** a job runs, **Then** privilege escalation is disabled (no-new-privileges is on), all Linux capabilities are dropped except the established minimal working set, and the container has no network access.
5. **Given** no container-security variables are set, **When** the server starts, **Then** startup succeeds without error using the secure defaults.

---

### User Story 2 - Explicit, validated relaxation of a default (Priority: P2)

An operator has a job that legitimately needs a relaxed setting — for example, outbound network access to install packages, a writable workspace to produce build artifacts, or (rarely) a container that must run as root. The operator sets the corresponding variable(s) explicitly. Valid relaxations are applied to the container; invalid values or contradictory combinations cause the server to refuse to start with a clear, specific error rather than silently degrading security.

**Why this priority**: The defaults from User Story 1 are intentionally restrictive and will block some legitimate jobs. Without a sanctioned, validated way to opt out, operators would be forced to fork or patch the code, defeating the "tune per-deployment without code changes" goal. Fail-fast validation ensures a relaxation is always a deliberate, visible act.

**Independent Test**: With User Story 1 in place, set each variable to a valid non-default value and confirm the container reflects it; then set invalid values (bad boolean, unknown enum, malformed uid:gid, disallowed capability, malformed tmpfs size, root user without the root escape hatch) and confirm the server fails to start with an error naming the offending variable.

**Acceptance Scenarios**:

1. **Given** `CONTAINER_NETWORK_MODE=bridge`, **When** the server starts and runs a job, **Then** the container has bridged outbound network access and startup succeeds.
2. **Given** `WORKSPACE_MOUNT_MODE=rw`, **When** a job runs, **Then** the workspace is mounted read-write and artifacts written there persist for collection.
3. **Given** `CONTAINER_USER=0:0` and `CONTAINER_ALLOW_ROOT=true`, **When** the server starts, **Then** startup succeeds and the container runs as root.
4. **Given** `CONTAINER_USER=0:0` and `CONTAINER_ALLOW_ROOT=false` (or unset), **When** the server starts, **Then** startup fails fast with an error stating that a root user was requested while root is disallowed and naming both variables involved.
5. **Given** any of the new variables is set to a value outside its allowed form (non-boolean for a boolean, value outside the enum, malformed `uid:gid`, a capability not on the allow-list, a malformed tmpfs size), **When** the server starts, **Then** startup fails fast with an error identifying the offending variable and the accepted values, consistent with existing configuration-validation behavior.
6. **Given** `CONTAINER_CAP_ADD` set to a narrower subset of the default capabilities, **When** a job runs, **Then** only that subset is granted on top of dropping all capabilities.

---

### User Story 3 - Effective security posture is observable for attestation and audit (Priority: P3)

A relying party or operator needs to confirm what security posture a given execution actually ran under — not what the defaults are, but the effective values in force for this deployment. The effective value of every security-relevant setting is surfaced so that any relaxation of a default is visible and measurable, never silent.

**Why this priority**: This is an attestation system; the integrity guarantee depends on relaxations being observable. It builds on User Stories 1 and 2 (there must be defaults and overrides before there is anything to surface), so it is sequenced last, but it is essential to the system's purpose.

**Independent Test**: Configure a deployment with one or more relaxed settings, trigger an execution, and confirm the effective container-security values are present in the execution's attestation/audit surface and in startup output, and that they reflect the relaxed values rather than the defaults.

**Acceptance Scenarios**:

1. **Given** a deployment with all secure defaults, **When** the server starts, **Then** the effective value of each of the eight settings is recorded in startup output.
2. **Given** a deployment that relaxes one or more defaults, **When** an execution is attested, **Then** the effective security-relevant values for that execution are included in the attestation/audit surface and reflect the relaxed configuration.
3. **Given** two deployments with different container-security configurations, **When** their executions are attested, **Then** the attestation surfaces differ in a way that reveals the difference in security posture.

---

### Edge Cases

- **Root requested without escape hatch**: `CONTAINER_USER` resolves to uid 0 while `CONTAINER_ALLOW_ROOT=false` → fail fast at startup.
- **Escape hatch enabled but user is non-root**: `CONTAINER_ALLOW_ROOT=true` with a non-root `CONTAINER_USER` → permitted; the gate only blocks the unsafe case.
- **Read-only root filesystem with no scratch space**: `CONTAINER_READ_ONLY_ROOTFS=true` and `CONTAINER_TMPFS_SIZE` empty/unset → no tmpfs is mounted; jobs that write outside the workspace may fail. The empty value is a valid, explicit choice and must not be silently overridden.
- **Network-dependent jobs under the new default**: jobs that today rely on outbound network (apt/pip/etc.) will fail under the `none` default until the operator sets `CONTAINER_NETWORK_MODE` — this is an intended behavior change, not a defect.
- **Workspace mount mode interaction with artifacts**: with the default `ro` workspace, jobs that write build artifacts into the workspace fail until the operator sets `WORKSPACE_MOUNT_MODE=rw`.
- **Empty vs. unset capability set**: `CONTAINER_CAP_ADD` empty means no capabilities are added on top of `drop ALL`; this differs from leaving it unset (which preserves the default working set). The two cases must be distinguishable.
- **Malformed or out-of-range values**: non-numeric tmpfs size, tmpfs size missing a unit, uid:gid with non-numeric or negative parts, capability names with wrong casing or unknown names, enum values with surrounding whitespace or wrong case.
- **Whole-host network exposure**: `CONTAINER_NETWORK_MODE=host` grants the container the host network namespace; it is an allowed value but represents the weakest network isolation and must be surfaced as such.

## Requirements *(mandatory)*

### Functional Requirements

#### Configuration surface and defaults

- **FR-001**: The system MUST accept eight new operator-facing configuration options, read at server startup: `CONTAINER_USER`, `CONTAINER_ALLOW_ROOT`, `CONTAINER_CAP_ADD`, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`, `CONTAINER_TMPFS_SIZE`, `WORKSPACE_MOUNT_MODE`, and `CONTAINER_NETWORK_MODE`.
- **FR-002**: Each option MUST default to the secure choice when unset, such that a deployment with none of these options set produces a hardened sandbox without operator action.
- **FR-003**: `CONTAINER_USER` MUST default to a non-root user `65534:65534`; when unset, containers MUST NOT run as the image's default user.
- **FR-004**: `CONTAINER_ALLOW_ROOT` MUST default to `false`.
- **FR-005**: `CONTAINER_CAP_ADD` MUST default to the capability set that preserves today's working behavior; operators MAY set a narrower subset, and the granted set is always applied on top of dropping all capabilities.
- **FR-006**: `NO_NEW_PRIVILEGES` MUST default to `true`, keeping the no-new-privileges protection on unless the operator explicitly opts out.
- **FR-007**: `CONTAINER_READ_ONLY_ROOTFS` MUST default to `true`, mounting the container root filesystem read-only.
- **FR-008**: `CONTAINER_TMPFS_SIZE` MUST default to a bounded size (`256m`) providing a writable in-memory scratch mount; an empty/unset value MUST mean no tmpfs is mounted.
- **FR-009**: `WORKSPACE_MOUNT_MODE` MUST default to `ro` for the cloned-repository workspace mount and MUST accept `rw` as the only other valid value.
- **FR-010**: `CONTAINER_NETWORK_MODE` MUST default to `none`, MUST accept `none`, `bridge`, and `host` as the only valid values, and MUST give the sandbox no outbound network access under the default.

#### Validation at startup

- **FR-011**: The system MUST validate all eight options at startup and MUST fail fast with a clear, specific error — consistent with existing configuration-validation behavior — when any value is invalid, before serving any request.
- **FR-012**: Boolean options (`CONTAINER_ALLOW_ROOT`, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`) MUST be parsed with the same strict boolean rules already used for existing boolean settings, rejecting unrecognized values.
- **FR-013**: Enumerated options MUST reject any value outside their allowed set: `WORKSPACE_MOUNT_MODE` ∈ {`ro`, `rw`}; `CONTAINER_NETWORK_MODE` ∈ {`none`, `bridge`, `host`}.
- **FR-014**: `CONTAINER_USER` MUST be validated against the `uid:gid` format, where both parts are non-negative integers; malformed values MUST be rejected at startup.
- **FR-015**: `CONTAINER_CAP_ADD` MUST be validated against a defined allow-list of permitted capabilities; any requested capability outside the allow-list MUST be rejected at startup.
- **FR-016**: `CONTAINER_TMPFS_SIZE`, when non-empty, MUST be validated against the accepted size format (a positive number with a recognized size unit); malformed values MUST be rejected at startup.
- **FR-017**: Each validation error message MUST name the offending variable and state the accepted values or format.

#### Root-user gate interaction

- **FR-018**: When `CONTAINER_USER` resolves to uid 0 and `CONTAINER_ALLOW_ROOT` is `false`, the system MUST reject the configuration at startup with an error identifying both variables and explaining the conflict.
- **FR-019**: When `CONTAINER_USER` resolves to uid 0 and `CONTAINER_ALLOW_ROOT` is `true`, the system MUST permit the configuration and run the container as root.
- **FR-020**: `CONTAINER_ALLOW_ROOT=true` combined with a non-root `CONTAINER_USER` MUST be permitted; the gate MUST only block the root-while-disallowed case.

#### Flow-through to container creation

- **FR-021**: The resolved effective value of every option MUST flow through to the execution-container creation path so that each running container reflects the configured user, capability set, no-new-privileges setting, root-filesystem read-only setting, tmpfs scratch mount, workspace mount mode, and network mode.
- **FR-022**: When `CONTAINER_READ_ONLY_ROOTFS` is `true` and `CONTAINER_TMPFS_SIZE` is non-empty, the system MUST mount a writable in-memory scratch area of the configured size so jobs retain temporary space despite the read-only root filesystem.
- **FR-023**: The capability set actually applied to a container MUST be exactly the resolved `CONTAINER_CAP_ADD` set on top of dropping all capabilities — no broader.

#### Documentation

- **FR-024**: Each of the eight variables MUST be documented in the example environment configuration with its default value and a brief security rationale.
- **FR-025**: The documentation MUST call out, as explicit trade-offs, (a) the backward-compatibility impact of the hardened defaults and (b) specifically that the default `none` network mode breaks jobs relying on outbound network until an operator opts in.

#### Observability / attestation

- **FR-026**: The effective value of every security-relevant setting MUST be observable, so that an operator who relaxes a default does so explicitly and visibly, never silently.
- **FR-027**: The effective security-relevant values MUST be surfaced through the system's attestation/audit surface for an execution, consistent with how existing security-relevant settings are surfaced there.
- **FR-028**: The effective values of all eight settings MUST be recorded in startup output so the running posture is inspectable independent of any individual execution.

### Key Entities *(include if feature involves data)*

- **Container security configuration**: The set of eight operator-facing settings governing the execution-container sandbox (process user, root escape-hatch flag, added capability set, no-new-privileges flag, read-only-root-filesystem flag, scratch mount size, workspace mount mode, network mode). Each has a secure default, a defined accepted form, and an effective resolved value used for container creation and surfaced for attestation.
- **Capability allow-list**: The closed set of Linux capabilities an operator is permitted to grant via `CONTAINER_CAP_ADD`; requests outside it are rejected.
- **Execution container**: The ephemeral sandbox in which a job runs; its effective security posture is determined by the resolved container security configuration.
- **Attestation/audit surface**: The observable record tying an execution to the effective security-relevant values under which it ran.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A deployment that sets none of the eight new variables runs every execution container as a non-root user, with a read-only root filesystem, a read-only workspace, privilege escalation disabled, the minimal capability set, and no outbound network — verifiable by inspecting any running container.
- **SC-002**: 100% of invalid configurations (bad boolean, out-of-enum value, malformed uid:gid, disallowed capability, malformed tmpfs size, root-while-disallowed) cause the server to refuse to start, before any request is served, with an error naming the offending variable.
- **SC-003**: An operator can change any single security setting to a valid non-default value and have it take effect on the next server start with no code changes.
- **SC-004**: For any execution, a relying party can determine the effective value of all eight security settings from the attestation/audit surface, and a relaxed default is always distinguishable from the secure default.
- **SC-005**: The example environment configuration documents all eight variables, each with its default and security rationale, and explicitly states the backward-compatibility and network-default trade-offs.
- **SC-006**: A deployment that explicitly relaxes a default (e.g., enables network, makes the workspace writable, or runs as root with the escape hatch) successfully runs a job that requires that relaxation.

## Assumptions

- The hardened defaults are a deliberate breaking change relative to today's behavior; existing jobs that depend on root, a writable root filesystem, a writable workspace, or outbound network will require explicit opt-in after this change, and this is accepted.
- The default capability set that "preserves today's working behavior" is the capability set currently granted by the executor; the allow-list for `CONTAINER_CAP_ADD` is at least that set and is the closed set of capabilities operators may request.
- The non-root default user `65534:65534` (nobody/nogroup) is appropriate for the supported base images; images requiring a specific non-root uid:gid are configured explicitly via `CONTAINER_USER`.
- The tmpfs scratch mount default size of `256m` and its mount location are reasonable defaults for typical jobs; operators needing more set `CONTAINER_TMPFS_SIZE` explicitly.
- "Surfaced for attestation/audit" reuses the existing mechanism by which security-relevant settings are already included in the execution attestation surface, rather than introducing a separate channel.
- Validation, parsing, and fail-fast startup follow the existing configuration subsystem's patterns and error-reporting style.
- Setting a security option to its insecure value where the underlying runtime cannot honor it is out of scope; the feature governs what is requested and surfaced, and relies on the runtime to enforce honored values.
