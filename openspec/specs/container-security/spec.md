# container-security Specification

## Purpose

Expose the security posture of execution containers as operator-facing configuration, each setting defaulting to the secure choice, so that the sandbox can be tuned per-deployment without code changes. Every effective value is validated at startup (failing fast on invalid input), threaded through to container creation, and surfaced for attestation and in the build-time configuration summary so that any relaxation of a default is explicit and observable rather than silent.

Sources: spec-kit `001-container-security-config` (the eight settings) and `002-scratch-tmpfs-exec` (the ninth, tmpfs execution).

## Requirements

### Requirement: Container security configuration surface

The system SHALL accept nine operator-facing container-security configuration options, read at server startup: `CONTAINER_USER`, `CONTAINER_ALLOW_ROOT`, `CONTAINER_CAP_ADD`, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`, `CONTAINER_TMPFS_SIZE`, `CONTAINER_TMPFS_EXEC`, `WORKSPACE_MOUNT_MODE`, and `CONTAINER_NETWORK_MODE`. Each option SHALL default to the secure choice when unset, such that a deployment with none of these options set produces a hardened sandbox without operator action.

#### Scenario: Hardened sandbox by default

- **WHEN** the server starts and runs a job with none of the container-security variables set
- **THEN** the container process runs as the non-root user `65534:65534` (not the image's default user)
- **AND** the container root filesystem is mounted read-only with a bounded in-memory writable scratch mount (default `256m`) at `/tmp`
- **AND** the cloned-repository workspace is mounted read-only
- **AND** no-new-privileges is on, all Linux capabilities are dropped except the default seven-capability working set, and the container has no network access
- **AND** startup succeeds without error using the secure defaults

#### Scenario: Secure defaults per setting

- **WHEN** a setting is unset
- **THEN** `CONTAINER_USER` defaults to `65534:65534`, `CONTAINER_ALLOW_ROOT` to `false`, `NO_NEW_PRIVILEGES` to `true`, `CONTAINER_READ_ONLY_ROOTFS` to `true`, `CONTAINER_TMPFS_SIZE` to `256m`, `CONTAINER_TMPFS_EXEC` to `false` (noexec), `WORKSPACE_MOUNT_MODE` to `ro`, and `CONTAINER_NETWORK_MODE` to `none`
- **AND** `CONTAINER_CAP_ADD` defaults to the seven capabilities `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID`, `NET_BIND_SERVICE`, `KILL`

### Requirement: Capability set resolution

`CONTAINER_CAP_ADD` SHALL be validated against the Docker default-bounding capability allow-list (`CHOWN`, `DAC_OVERRIDE`, `FSETID`, `FOWNER`, `MKNOD`, `NET_RAW`, `SETGID`, `SETUID`, `SETFCAP`, `SETPCAP`, `NET_BIND_SERVICE`, `SYS_CHROOT`, `KILL`, `AUDIT_WRITE`), matched case-sensitively in upper case without a `CAP_` prefix. The capability set actually applied to a container SHALL be exactly the resolved `CONTAINER_CAP_ADD` set on top of dropping all capabilities — no broader. The system SHALL distinguish unset from empty.

#### Scenario: Unset preserves the default working set

- **WHEN** `CONTAINER_CAP_ADD` is unset
- **THEN** the default seven-capability set is applied on top of `drop ALL`

#### Scenario: Empty grants no extra capabilities

- **WHEN** `CONTAINER_CAP_ADD` is set to an empty value
- **THEN** no capabilities are added on top of `drop ALL`

#### Scenario: Narrower subset is honored

- **WHEN** `CONTAINER_CAP_ADD` is set to a narrower subset of the default capabilities
- **THEN** only that subset is granted on top of dropping all capabilities

#### Scenario: Capability outside the allow-list is rejected

- **WHEN** `CONTAINER_CAP_ADD` requests a capability not on the allow-list (e.g. `SYS_ADMIN`, `SYS_PTRACE`)
- **THEN** the server fails to start with an error naming the offending variable

### Requirement: Fail-fast validation at startup

The system SHALL validate all nine options at startup and SHALL fail fast — exiting with a non-zero status during configuration loading without binding its listen port — when any value is invalid. Each validation error message SHALL name the offending variable and state the accepted values or format.

#### Scenario: Strict boolean parsing

- **WHEN** a boolean option (`CONTAINER_ALLOW_ROOT`, `NO_NEW_PRIVILEGES`, `CONTAINER_READ_ONLY_ROOTFS`, `CONTAINER_TMPFS_EXEC`) is set to a value outside the recognized boolean set (case-insensitive `true`/`1`/`yes` or `false`/`0`/`no`)
- **THEN** the server fails to start with an error naming the variable and the accepted values

#### Scenario: Enum validation

- **WHEN** `WORKSPACE_MOUNT_MODE` is set outside {`ro`, `rw`} or `CONTAINER_NETWORK_MODE` outside {`none`, `bridge`, `host`}
- **THEN** the server fails to start with an error naming the variable and its allowed set

#### Scenario: uid:gid format validation

- **WHEN** `CONTAINER_USER` is a bare uid with no gid, has a missing part, or has non-integer or negative parts
- **THEN** the server fails to start with an error naming the variable and the `uid:gid` format

#### Scenario: tmpfs size format validation

- **WHEN** `CONTAINER_TMPFS_SIZE` is non-empty and is not a positive integer optionally followed by a single unit suffix `b`, `k`, `m`, or `g` (e.g. `0`, a negative number, a missing/unknown unit, or surrounding whitespace)
- **THEN** the server fails to start with an error naming the variable and the accepted size grammar

### Requirement: Root-user gate

When `CONTAINER_USER` resolves to uid 0, the system SHALL permit the configuration only when `CONTAINER_ALLOW_ROOT` is `true`. The gate SHALL only block the root-while-disallowed case.

#### Scenario: Root requested without escape hatch fails fast

- **WHEN** `CONTAINER_USER=0:0` and `CONTAINER_ALLOW_ROOT=false` (or unset)
- **THEN** the server fails to start with an error identifying both variables and explaining the conflict

#### Scenario: Root permitted with escape hatch

- **WHEN** `CONTAINER_USER=0:0` and `CONTAINER_ALLOW_ROOT=true`
- **THEN** startup succeeds and the container runs as root

#### Scenario: Escape hatch with non-root user is permitted

- **WHEN** `CONTAINER_ALLOW_ROOT=true` is combined with a non-root `CONTAINER_USER`
- **THEN** the configuration is permitted

### Requirement: Effective values flow through to container creation

The resolved effective value of every option SHALL flow through to the execution-container creation path so that each running container reflects the configured user, capability set, no-new-privileges setting, root-filesystem read-only setting, tmpfs scratch mount, workspace mount mode, and network mode. `CONTAINER_ALLOW_ROOT` is a startup root-gate only and is NOT threaded into container creation (it is still attested).

#### Scenario: Relaxed network mode applied

- **WHEN** `CONTAINER_NETWORK_MODE=bridge` and a job runs
- **THEN** the container has bridged outbound network access

#### Scenario: Writable workspace applied

- **WHEN** `WORKSPACE_MOUNT_MODE=rw` and a job runs
- **THEN** the workspace is mounted read-write and artifacts written there persist for collection

### Requirement: tmpfs scratch mount

When `CONTAINER_TMPFS_SIZE` is non-empty, the system SHALL mount a writable in-memory scratch area of the configured size at `/tmp` (the container's standard temporary directory), independent of `CONTAINER_READ_ONLY_ROOTFS`. When `CONTAINER_TMPFS_SIZE` is empty, no tmpfs SHALL be mounted. The attested `container_tmpfs_size` SHALL always equal the effective value because the mount is governed solely by `CONTAINER_TMPFS_SIZE`.

#### Scenario: Scratch available under read-only rootfs

- **WHEN** `CONTAINER_TMPFS_SIZE` is non-empty and `CONTAINER_READ_ONLY_ROOTFS=true`
- **THEN** a writable in-memory scratch of the configured size is mounted at `/tmp`

#### Scenario: No scratch when size empty

- **WHEN** `CONTAINER_TMPFS_SIZE` is empty/unset
- **THEN** no tmpfs is mounted and jobs that write outside the workspace may fail; the empty value is a valid explicit choice that is not silently overridden

### Requirement: tmpfs execution permission

The system SHALL expose `CONTAINER_TMPFS_EXEC` controlling whether the `/tmp` scratch tmpfs is mounted with execution permitted, defaulting to disabled (`noexec`) to preserve the hardened posture. Enabling it SHALL add only the `exec` option to the scratch mount; it SHALL NOT alter the configured size, `mode=1777`, or the `nosuid`/`nodev` protections.

#### Scenario: Default noexec blocks execution from scratch

- **WHEN** `CONTAINER_TMPFS_EXEC` is unset (effective `false`) and a container is created with a non-empty `CONTAINER_TMPFS_SIZE`
- **THEN** the `/tmp` tmpfs is mounted without the `exec` option and a binary placed under `/tmp` cannot be executed (kernel returns `EACCES`)

#### Scenario: Opt-in enables compile-and-run builds

- **WHEN** `CONTAINER_TMPFS_EXEC` is enabled and `CONTAINER_TMPFS_SIZE` is non-empty
- **THEN** the `/tmp` tmpfs is mounted with the `exec` option so a binary compiled into `/tmp` can be executed
- **AND** a Rust build whose `build.rs` is compiled into the scratch area and executed completes without the `Permission denied (os error 13)` build-script failure

#### Scenario: Enabled with no tmpfs warns without failing

- **WHEN** `CONTAINER_TMPFS_EXEC` is enabled but `CONTAINER_TMPFS_SIZE` is empty (no tmpfs mounted)
- **THEN** the value is resolved and attested normally, no mount change is applied, and the server emits a startup log warning that exec is enabled but has no effect because no tmpfs is mounted (it does not fail fast)

### Requirement: Effective posture is attested and observable

The effective value of every container-security setting SHALL be threaded into the attestation `user_data` via the attestation generator (the same way the existing `gpu_enabled` setting is), so all effective values are bound to the attestation document for that execution. The effective values SHALL also be recorded in startup output. The scratch-exec value SHALL appear as a boolean field named `container_tmpfs_exec` alongside `container_tmpfs_size`.

#### Scenario: Relaxed configuration visible in attestation

- **WHEN** a deployment relaxes one or more defaults and an execution is attested
- **THEN** the effective security-relevant values for that execution are included in the attestation `user_data` and reflect the relaxed configuration, distinguishable from the secure default

#### Scenario: Differing posture distinguishable

- **WHEN** two deployments with different container-security configurations attest their executions
- **THEN** the attestation surfaces differ in a way that reveals the difference in security posture

#### Scenario: Startup output records effective values

- **WHEN** the server starts
- **THEN** the effective value of each container-security setting is recorded in startup output

### Requirement: Build-time configuration summary

The `Build Attestable Image` GitHub Actions workflow SHALL print, on its run summary, the full server configuration built into the AMI artifact — every effective `ServerConfig` setting (a superset of the `.env.example` keys, including the nine container-security settings), enumerated from the configuration object so the set cannot drift. Settings SHALL be grouped by category into labeled per-category subsections in a stable order with a catch-all "Other" group last. Values SHALL be derived programmatically by resolving the image's baked-in environment file through the application's own configuration loader, printed verbatim with no redaction.

#### Scenario: Summary derived from baked-in config

- **WHEN** the workflow prints the server configuration summary
- **THEN** every effective `ServerConfig` setting (including `container_tmpfs_exec` and `container_tmpfs_size`) appears, grouped by category with "Other" last, with values matching what the image ships with

#### Scenario: Unresolvable config fails the build

- **WHEN** the workflow cannot resolve the effective configuration baked into the AMI (e.g. the config loader rejects the baked-in env file)
- **THEN** the workflow fails before publishing the artifact rather than publishing without the configuration summary

### Requirement: Documentation of settings and trade-offs

Each container-security variable SHALL be documented in the example environment configuration with its default value and a brief security rationale. The documentation SHALL call out, per breaking default, the symptom an affected job exhibits and the variable change that restores prior behavior, and SHALL describe the security implication of enabling `CONTAINER_TMPFS_EXEC`.

#### Scenario: Breaking defaults paired with opt-outs

- **WHEN** an operator reads the example environment configuration
- **THEN** each breaking default is paired with its named opt-out and symptom: non-root user → set `CONTAINER_USER` (with `CONTAINER_ALLOW_ROOT=true` if root is required); read-only rootfs → set `CONTAINER_READ_ONLY_ROOTFS=false` or size a tmpfs; `none` network → set `CONTAINER_NETWORK_MODE=bridge`; writable workspace → set `WORKSPACE_MOUNT_MODE=rw`
