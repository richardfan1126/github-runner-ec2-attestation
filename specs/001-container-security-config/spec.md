# Feature Specification: Container Security Configuration via Environment Variables

**Feature Branch**: `001-container-security-config`

**Created**: 2026-06-13

**Status**: Implemented; reopened 2026-06-14 to add FR-030 (build-workflow configuration summary)

**Input**: User description: "Expose container security configuration via environment variables with secure-by-default values — add eight new operator-facing configuration options to the GitHub Actions Remote Executor so the security posture of execution containers can be tuned per-deployment without code changes, each defaulting to the secure choice, with every effective value observable for attestation and invalid combinations failing fast at startup."

## Clarifications

### Session 2026-06-14

- Q: Which configuration values should the `Build Attestable Image` workflow print on its run summary? → A: All server configuration options (the eight container-security settings together with the other server settings), not only the eight new ones.
- Q: Given the workflow runs at build time (no deploy-time env vars present), what should the printed values represent? → A: The configuration actually built into the AMI — the values the image ships with (the baked-in environment configuration resolved through the application's own config loader), rather than abstract defaults.
- Q: How should the workflow obtain the values to avoid drift from the code? → A: Derive them programmatically from the application's configuration at build time (single source of truth), so the summary cannot diverge from the values the image actually ships with.
- Q: The summary's "all server configuration" scope includes non-security keys documented in `.env.example` (e.g. `ALLOWED_REPOSITORIES`, `EXPECTED_AUDIENCE`) and the run summary is public on public repos — how should those be handled? → A: Print every value verbatim, with no redaction; the baked-in environment file is itself committed to the repository, so the summary discloses nothing that is not already public.
- Q: If the `Build Attestable Image` workflow cannot resolve/print the effective configuration at build time (e.g., the app's config loader rejects the baked-in env file), how should the workflow behave? → A: Fail the workflow before publishing the artifact; a config-loader failure means the baked-in env would also fail the server's fail-fast startup (FR-011), so catching it at build time is valuable and consistent with the system's fail-fast posture.
- Q: Where should the build-time config-dump helper that backs FR-030 live, given `src/` is reserved for the executor runtime? → A: In the build-workflow script directory `.github/scripts/` (alongside `build-kiwi-image.sh`), not under `src/`. It still imports the executor's config loader (run via `uv run` from the repo root) so the executor remains the single source of truth.
- Q: FR-030/SC-007 said the summary prints "every key documented in `.env.example`", but the drift-proof implementation enumerates every `ServerConfig` field (a superset, labeled by configuration field name). How should this be reconciled? → A: Reword FR-030/SC-007 to state the summary prints every effective `ServerConfig` setting — a superset of the `.env.example` keys, enumerated from the configuration object so it cannot drift — rather than implying a `.env.example`-keyed set. Settings are labeled by their resolved field name (e.g. `port` for `SERVER_PORT`).
- Q: How should the build-workflow configuration summary (FR-030) organize the settings? → A: Group related settings by configuration category; render each category as its own labeled subsection (a heading followed by a small table of that category's settings) in a fixed order, rather than one flat table.
- Q: How should each setting be assigned to its category, given FR-030's "cannot drift / superset of all settings" guarantee? → A: Maintain a category→fields map in the build helper, plus a catch-all "Other" group that automatically collects any enumerated `ServerConfig` field not explicitly mapped, so every effective setting still appears (no field is ever dropped) even before it is categorized.
- Q: In what order should the category subsections and the settings within each appear? → A: Category subsections appear in the order defined by the maintained category map (catch-all "Other" last); settings within each category appear in the order listed in that map.

### Session 2026-06-13

- Q: When `CONTAINER_TMPFS_SIZE` is non-empty but `CONTAINER_READ_ONLY_ROOTFS=false`, how should the system behave so the attested posture matches reality? → A: Mount the tmpfs at the execution scratch path whenever `CONTAINER_TMPFS_SIZE` is non-empty, independent of the rootfs read-only setting; attestation reports the configured size, which is then always the effective value (Option A). The tmpfs mount is governed solely by `CONTAINER_TMPFS_SIZE`, not by `CONTAINER_READ_ONLY_ROOTFS`.
- Q: How should SC-006 ("relaxation lets a job that needs it run") be validated, given the suite is mock-Docker only? → A: Add a real-Docker integration test that actually runs a job under a relaxed setting, gated to environments with a Docker daemon (and NitroTPM where required) and skipped in standard CI (Option B).
- Q: Where should the attested `container_allow_root` value be sourced from? → A: Directly from `ServerConfig` at the attestation call sites (Option A). `CONTAINER_ALLOW_ROOT` is a startup root-gate only and does not affect container creation, so it is NOT threaded through the executor — only the seven settings that shape `containers.create()` are; `container_allow_root` is still attested in `user_data` per FR-027.
- Q: At what path should the `CONTAINER_TMPFS_SIZE` scratch mount be mounted, so a hardened-default job that writes to the conventional temp directory still works under a read-only root filesystem? → A: Mount the tmpfs at `/tmp`, the container's standard temporary directory (Option A). This ensures tools that use `$TMPDIR`/`mktemp`/`tempfile` (which default to `/tmp`) land on the in-memory scratch rather than failing against the read-only rootfs. The mount path is `/tmp`, not a sub-path such as `/tmp/execution`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Hardened sandbox by default (Priority: P1)

An operator deploys the executor without setting any of the new container-security variables. Every execution container they run is automatically hardened: the job process runs as a non-root user, the container root filesystem is read-only with a bounded writable scratch area for temporary files, the cloned-repository workspace is mounted read-only, privilege escalation is blocked, Linux capabilities are reduced to the minimal working set, and the sandbox has no outbound network access.

**Why this priority**: This is the core value of the feature — making the safe configuration the one an operator gets when they do nothing. It eliminates the silent reliance on insecure Docker defaults (containers running as root with a writable root filesystem and unrestricted network) that exists today. It is the minimum viable slice: even with no tuning capability at all, shipping hardened defaults materially improves the security posture of every deployment.

**Independent Test**: Deploy the server with none of the eight new variables set, run a representative job, and inspect the resulting container's effective configuration (user, root filesystem mount, workspace mount, privilege-escalation setting, granted capabilities, network mode, scratch mount). Confirm each reflects the secure default and that the job that only needs local compute still succeeds.

**Acceptance Scenarios**:

1. **Given** no container-security variables are set, **When** the server starts and runs a job, **Then** the container process runs as a non-root user (uid:gid `65534:65534`) rather than the image's default user.
2. **Given** no container-security variables are set, **When** a job runs, **Then** the container root filesystem is mounted read-only and a bounded in-memory writable scratch mount (default `256m`) is available at `/tmp` for temporary files.
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
- **Unresolvable configuration at build time**: if the `Build Attestable Image` workflow cannot resolve the effective configuration baked into the AMI (e.g., the configuration loader rejects the baked-in environment file), the workflow fails before publishing the artifact rather than publishing without the configuration summary (FR-030).

## Requirements *(mandatory)*

### Functional Requirements

#### Configuration surface and defaults

- **FR-001**: The system MUST accept eight new operator-facing configuration options, read at server startup: `CONTAINER_USER`, `CONTAINER_ALLOW_ROOT`, `CONTAINER_CAP_ADD`, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`, `CONTAINER_TMPFS_SIZE`, `WORKSPACE_MOUNT_MODE`, and `CONTAINER_NETWORK_MODE`.
- **FR-002**: Each option MUST default to the secure choice when unset, such that a deployment with none of these options set produces a hardened sandbox without operator action.
- **FR-003**: `CONTAINER_USER` MUST default to a non-root user `65534:65534`; when unset, containers MUST NOT run as the image's default user.
- **FR-004**: `CONTAINER_ALLOW_ROOT` MUST default to `false`.
- **FR-005**: `CONTAINER_CAP_ADD` MUST default to the capability set that preserves today's working behavior — the seven capabilities currently granted by the executor: `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID`, `NET_BIND_SERVICE`, `KILL`. Operators MAY set any subset or superset within the allow-list (FR-015), and the granted set is always applied on top of dropping all capabilities. The system MUST distinguish unset from empty: when `CONTAINER_CAP_ADD` is unset the default seven-capability set is applied, whereas when it is set to an empty value no capabilities are added on top of `drop ALL`. This distinction MUST be testable and reflected in the surfaced effective value (FR-027).
- **FR-006**: `NO_NEW_PRIVILEGES` MUST default to `true`, keeping the no-new-privileges protection on unless the operator explicitly opts out.
- **FR-007**: `CONTAINER_READ_ONLY_ROOTFS` MUST default to `true`, mounting the container root filesystem read-only.
- **FR-008**: `CONTAINER_TMPFS_SIZE` MUST default to a bounded size (`256m`) providing a writable in-memory scratch mount at `/tmp`; an empty/unset value MUST mean no tmpfs is mounted.
- **FR-009**: `WORKSPACE_MOUNT_MODE` MUST default to `ro` for the cloned-repository workspace mount and MUST accept `rw` as the only other valid value.
- **FR-010**: `CONTAINER_NETWORK_MODE` MUST default to `none`, MUST accept `none`, `bridge`, and `host` as the only valid values, and MUST give the sandbox no outbound network access under the default.

#### Validation at startup

- **FR-011**: The system MUST validate all eight options at startup and MUST fail fast when any value is invalid, before serving any request. "Fail fast" is observable as: the process exits with a non-zero status during configuration loading and never binds its listen port or accepts a connection, emitting a clear, specific error — consistent with existing configuration-validation behavior.
- **FR-012**: Boolean options (`CONTAINER_ALLOW_ROOT`, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`) MUST be parsed with the same strict boolean rules already used for existing boolean settings, rejecting unrecognized values.
- **FR-013**: Enumerated options MUST reject any value outside their allowed set: `WORKSPACE_MOUNT_MODE` ∈ {`ro`, `rw`}; `CONTAINER_NETWORK_MODE` ∈ {`none`, `bridge`, `host`}.
- **FR-014**: `CONTAINER_USER` MUST be validated against the `uid:gid` format, where both parts are present and are non-negative integers; a bare uid with no gid (e.g. `1000`), a missing part, or non-integer/negative parts MUST be rejected at startup.
- **FR-015**: `CONTAINER_CAP_ADD` MUST be validated against a defined allow-list of permitted capabilities; any requested capability outside the allow-list MUST be rejected at startup. The allow-list is the Docker default-bounding capability set (a superset of the FR-005 default working set): `CHOWN`, `DAC_OVERRIDE`, `FSETID`, `FOWNER`, `MKNOD`, `NET_RAW`, `SETGID`, `SETUID`, `SETFCAP`, `SETPCAP`, `NET_BIND_SERVICE`, `SYS_CHROOT`, `KILL`, `AUDIT_WRITE`. Capability names are matched case-sensitively in upper case without a `CAP_` prefix.
- **FR-016**: `CONTAINER_TMPFS_SIZE`, when non-empty, MUST be validated against the accepted size format: a positive integer optionally followed by a single unit suffix `b`, `k`, `m`, or `g` (bytes/kibibytes/mebibytes/gibibytes, matching the size grammar the container runtime accepts for tmpfs). A value of `0`, a negative number, a missing/unknown unit, or surrounding whitespace MUST be rejected at startup.
- **FR-017**: Each validation error message MUST name the offending variable and state the accepted values or format.

#### Root-user gate interaction

- **FR-018**: When `CONTAINER_USER` resolves to uid 0 and `CONTAINER_ALLOW_ROOT` is `false`, the system MUST reject the configuration at startup with an error identifying both variables and explaining the conflict.
- **FR-019**: When `CONTAINER_USER` resolves to uid 0 and `CONTAINER_ALLOW_ROOT` is `true`, the system MUST permit the configuration and run the container as root.
- **FR-020**: `CONTAINER_ALLOW_ROOT=true` combined with a non-root `CONTAINER_USER` MUST be permitted; the gate MUST only block the root-while-disallowed case.

#### Flow-through to container creation

- **FR-021**: The resolved effective value of every option MUST flow through to the execution-container creation path so that each running container reflects the configured user, capability set, no-new-privileges setting, root-filesystem read-only setting, tmpfs scratch mount, workspace mount mode, and network mode.
- **FR-022**: When `CONTAINER_TMPFS_SIZE` is non-empty, the system MUST mount a writable in-memory scratch area of the configured size at `/tmp` (the container's standard temporary directory), **independent of `CONTAINER_READ_ONLY_ROOTFS`**, so jobs retain temporary space (including under a read-only root filesystem). When `CONTAINER_TMPFS_SIZE` is empty, no tmpfs is mounted. Because the mount is governed solely by `CONTAINER_TMPFS_SIZE`, the attested `container_tmpfs_size` (FR-027) is always the effective value.
- **FR-023**: The capability set actually applied to a container MUST be exactly the resolved `CONTAINER_CAP_ADD` set on top of dropping all capabilities — no broader.

#### Documentation

- **FR-024**: Each of the eight variables MUST be documented in the example environment configuration with its default value and a brief security rationale.
- **FR-025**: The documentation MUST call out, as explicit trade-offs, (a) the backward-compatibility impact of the hardened defaults and (b) specifically that the default `none` network mode breaks jobs relying on outbound network until an operator opts in.
- **FR-029**: Each breaking default MUST be paired with its named opt-out and the documentation MUST state, per breaking default, the symptom an affected job exhibits and the variable change that restores prior behavior: non-root user (`65534:65534`) → set `CONTAINER_USER` (with `CONTAINER_ALLOW_ROOT=true` if root is required); read-only root filesystem → set `CONTAINER_READ_ONLY_ROOTFS=false` (or size a tmpfs via `CONTAINER_TMPFS_SIZE`); `none` network → set `CONTAINER_NETWORK_MODE=bridge`; writable-workspace needs → set `WORKSPACE_MOUNT_MODE=rw`. (The cloned-repository workspace is already mounted `ro` today, so its default is not a behavior change.)

#### Observability / attestation

- **FR-026**: The effective value of every security-relevant setting MUST be observable, so that an operator who relaxes a default does so explicitly and visibly, never silently.
- **FR-027**: The effective security-relevant values MUST be surfaced through the system's attestation/audit surface for an execution — specifically, threaded into the attestation `user_data` via the attestation generator the same way the existing `gpu_enabled` setting is, so all eight effective values (`CONTAINER_USER`, `CONTAINER_ALLOW_ROOT`, the resolved `CONTAINER_CAP_ADD` set, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`, `CONTAINER_TMPFS_SIZE`, `WORKSPACE_MOUNT_MODE`, `CONTAINER_NETWORK_MODE`) are bound to the attestation document for that execution.
- **FR-028**: The effective values of all eight settings MUST be recorded in startup output so the running posture is inspectable independent of any individual execution.
- **FR-030**: The `Build Attestable Image` GitHub Actions workflow MUST print, on its workflow run summary (GitHub Step Summary), the full server configuration built into the AMI artifact it produces — the complete set of effective server configuration values the image ships with, covering the eight container-security settings of FR-001 together with the other server configuration options (not only the eight new ones). The printed set MUST be every effective `ServerConfig` setting the server resolves — a **superset** of the keys documented in `.env.example`, covering server, execution, rate-limiting, storage, NitroTPM, OIDC authentication, container-execution, and the eight container-security settings — enumerated from the configuration object itself so the set cannot drift. Settings are labeled by their resolved configuration field name, which for a few keys differs from the env-var spelling (e.g. `port` ↔ `SERVER_PORT`).

The settings MUST be **grouped by configuration category** in the summary rather than printed as a single flat table: each category is rendered as its own labeled subsection (a heading followed by a small table of that category's settings). Category assignment is driven by a category→fields map maintained in the build helper; any enumerated `ServerConfig` field not explicitly mapped MUST fall into a catch-all "Other" group so that every effective setting still appears and no field is ever silently dropped (preserving the superset/no-drift guarantee). Category subsections MUST appear in the order defined by the map with the catch-all "Other" group last, and the settings within each category MUST appear in the order listed in the map, so the summary layout is stable across runs. The printed values MUST represent the configuration actually built into the AMI, obtained by resolving the image's baked-in environment configuration (`/etc/github-actions-remote-executor/env`) through the application's own configuration loader, and MUST be derived programmatically at build time (single source of truth) so the summary cannot drift from the values the image actually ships with. All values MUST be printed verbatim with no redaction: the baked-in environment file is itself committed to the repository, so the summary discloses nothing that is not already public. If the effective configuration cannot be resolved at build time (e.g., the configuration loader rejects the baked-in environment file), the workflow MUST fail before publishing the artifact rather than publishing without the configuration summary — consistent with the server's fail-fast startup behavior (FR-011).

### Key Entities *(include if feature involves data)*

- **Container security configuration**: The set of eight operator-facing settings governing the execution-container sandbox (process user, root escape-hatch flag, added capability set, no-new-privileges flag, read-only-root-filesystem flag, scratch mount size, workspace mount mode, network mode). Each has a secure default, a defined accepted form, and an effective resolved value used for container creation and surfaced for attestation.
- **Capability allow-list**: The closed set of Linux capabilities an operator is permitted to grant via `CONTAINER_CAP_ADD` — the Docker default-bounding set (`CHOWN`, `DAC_OVERRIDE`, `FSETID`, `FOWNER`, `MKNOD`, `NET_RAW`, `SETGID`, `SETUID`, `SETFCAP`, `SETPCAP`, `NET_BIND_SERVICE`, `SYS_CHROOT`, `KILL`, `AUDIT_WRITE`); requests outside it are rejected. The default working set is the seven-capability subset listed in FR-005.
- **Execution container**: The ephemeral sandbox in which a job runs; its effective security posture is determined by the resolved container security configuration.
- **Attestation/audit surface**: The observable record tying an execution to the effective security-relevant values under which it ran.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A deployment that sets none of the eight new variables runs every execution container as a non-root user, with a read-only root filesystem, a read-only workspace, privilege escalation disabled, the minimal capability set, and no outbound network — verifiable by inspecting any running container.
- **SC-002**: 100% of invalid configurations (bad boolean, out-of-enum value, malformed uid:gid, disallowed capability, malformed tmpfs size, root-while-disallowed) cause the server to refuse to start, before any request is served, with an error naming the offending variable.
- **SC-003**: An operator can change any single security setting to a valid non-default value and have it take effect on the next server start with no code changes.
- **SC-004**: For any execution, a relying party can determine the effective value of all eight security settings from the attestation/audit surface, and a relaxed default is always distinguishable from the secure default.
- **SC-005**: The example environment configuration documents all eight variables, each with its default and security rationale, and explicitly states the backward-compatibility and network-default trade-offs.
- **SC-006**: A deployment that explicitly relaxes a default (e.g., enables network, makes the workspace writable, or runs as root with the escape hatch) successfully runs a job that requires that relaxation — verified end to end in a container-runtime-capable environment, with the relaxed value additionally shown to flow through to container creation in the standard test suite.
- **SC-007**: Every run of the `Build Attestable Image` workflow displays, on its run summary, the full effective server configuration built into the produced AMI — every effective `ServerConfig` setting (a superset of the `.env.example` keys), including the eight container-security settings — grouped into labeled per-category subsections in a stable order (catch-all "Other" group last), with any unmapped field still shown under "Other" so no setting is dropped, and with values printed verbatim that match what the image ships with and are derived from the application's configuration rather than a hand-maintained list, so the summary cannot silently drift from the code.

## Assumptions

- The hardened defaults are a deliberate breaking change relative to today's behavior; existing jobs that depend on root, a writable root filesystem, a writable workspace, or outbound network will require explicit opt-in after this change, and this is accepted.
- The default capability set that "preserves today's working behavior" is the seven capabilities currently granted by the executor (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID`, `NET_BIND_SERVICE`, `KILL`). The allow-list for `CONTAINER_CAP_ADD` is the broader Docker default-bounding set enumerated in FR-015 (a superset of the default), and is the closed set of capabilities operators may request — capabilities outside it (e.g. `SYS_ADMIN`, `SYS_PTRACE`) cannot be granted without a code change.
- The non-root default user `65534:65534` (nobody/nogroup) is appropriate for the supported base images; images requiring a specific non-root uid:gid are configured explicitly via `CONTAINER_USER`.
- The tmpfs scratch mount default size of `256m` and its mount location are reasonable defaults for typical jobs; operators needing more set `CONTAINER_TMPFS_SIZE` explicitly.
- "Surfaced for attestation/audit" reuses the existing mechanism by which security-relevant settings are already included in the execution attestation surface, rather than introducing a separate channel.
- Validation, parsing, and fail-fast startup follow the existing configuration subsystem's patterns and error-reporting style.
- Setting a security option to its insecure value where the underlying runtime cannot honor it is out of scope; the feature governs what is requested and surfaced, and relies on the runtime to enforce honored values.
- The build-time helper that backs FR-030 is build tooling, not executor runtime code: it lives outside `src/` (in `.github/scripts/`), while still importing the executor's configuration loader so the resolved values remain a single source of truth. `src/` is reserved for the actual executor.
