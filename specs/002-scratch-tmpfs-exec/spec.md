# Feature Specification: Configurable Execution Permission on the Container Scratch tmpfs

**Feature Branch**: `002-scratch-tmpfs-exec`

**Created**: 2026-06-18

**Status**: Implemented

**Input**: User description: "Configurable execution permission on the container scratch tmpfs so build toolchains that compile and run helper binaries (e.g. Cargo build scripts) work, defaulting to noexec to preserve hardening."

## Context & Motivation

The executor mounts a single writable tmpfs scratch area at `/tmp` for every
execution container (governed by `CONTAINER_TMPFS_SIZE`; see feature 001). Docker
brings a `--tmpfs` mount up with `rw,nosuid,nodev,noexec` unless `exec` is
explicitly requested, so today the scratch area is **non-executable**: a binary
written under `/tmp` cannot be `exec()`'d and the kernel returns `EACCES`
(`Permission denied`, os error 13).

This is the correct hardened default — it prevents a job from downloading or
emitting an arbitrary binary into scratch and running it inside the enclave. But
it also blocks a legitimate, common build pattern: toolchains that **compile a
helper binary into scratch and then execute it as part of the build**. The
motivating case is a Rust build: Cargo compiles a crate's `build.rs` into a
`build-script-build` binary under `CARGO_TARGET_DIR` (which, on a read-only
rootfs, must live under the `/tmp` scratch) and then runs it during
`cargo build`. Under `noexec` the build fails at the build-script step with
`could not execute process … (never executed) / Permission denied (os error 13)`.

There is currently **no operator-facing way** to permit this: the `noexec`
behavior comes from Docker's tmpfs defaults applied in the executor's container
creation code, and none of the existing eight container-security environment
variables toggle it.

This feature adds a ninth operator-facing container-security setting that, like
the others, **defaults to the secure choice** (`noexec`) and can be opted out of
per-deployment without code changes, with the effective value observable for
attestation.

## Clarifications

### Session 2026-06-18

- Q: When `CONTAINER_TMPFS_EXEC` is enabled but `CONTAINER_TMPFS_SIZE` is empty (no tmpfs mounted), what should happen? → A: Resolve & attest the value normally, apply no mount change, and emit a startup log warning that exec is enabled but has no effect because no tmpfs is mounted (no fail-fast).
- Q: Which exact string values does `CONTAINER_TMPFS_EXEC` accept? → A: Case-insensitive `true`/`1`/`yes` → enabled and `false`/`0`/`no` → disabled; any other value fails fast (matches the existing `parse_strict_bool`).
- Q: What is the attested `user_data` field name for the effective value? → A: `container_tmpfs_exec` (boolean), alongside `container_tmpfs_size`, following the existing `container_`-prefixed convention.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Non-executable scratch by default (Priority: P1)

An operator deploys the executor without setting the new variable. Every
execution container continues to mount its `/tmp` scratch tmpfs as `noexec`,
exactly as today. A job that tries to execute a binary it wrote into scratch is
denied by the kernel.

**Why this priority**: Preserving the hardened default is the non-negotiable
core of the feature. The new capability must never silently weaken an existing
deployment; an operator who does nothing keeps the strict posture.

**Independent Test**: With the variable unset, start the server and run a job
that writes a small executable into `/tmp` and tries to run it; confirm the
attempt fails with a permission error and the attested effective value reports
`noexec` (execution disabled).

**Acceptance Scenarios**:

1. **Given** `CONTAINER_TMPFS_EXEC` is unset, **When** the server resolves its
   configuration, **Then** the effective scratch-exec setting is `false`
   (disabled / `noexec`).
2. **Given** the scratch-exec setting is `false`, **When** an execution
   container is created with a non-empty `CONTAINER_TMPFS_SIZE`, **Then** the
   `/tmp` tmpfs is mounted without the `exec` option and a binary placed under
   `/tmp` cannot be executed.

---

### User Story 2 - Opt in to executable scratch for compile-and-run builds (Priority: P2)

An operator who runs trusted build workloads that compile and execute helper
binaries during the build (such as Rust crates with `build.rs` build scripts)
sets the new variable to enable execution from scratch. Their builds that
previously failed at the build-script step now complete.

**Why this priority**: This is the capability the feature exists to deliver. It
is independently valuable once the safe default (Story 1) is in place: a single
opt-in setting unblocks an otherwise-impossible class of build without any code
change.

**Independent Test**: With the variable enabled, run a build whose build step
compiles a helper binary into the scratch area and executes it (e.g. a Rust
project with a `build.rs`); confirm the build succeeds and the attested
effective value reports execution enabled.

**Acceptance Scenarios**:

1. **Given** `CONTAINER_TMPFS_EXEC` is set to its enabling value, **When** the
   server resolves its configuration, **Then** the effective scratch-exec
   setting is `true` (enabled).
2. **Given** the scratch-exec setting is `true` and `CONTAINER_TMPFS_SIZE` is
   non-empty, **When** an execution container is created, **Then** the `/tmp`
   tmpfs is mounted with the `exec` option and a binary compiled into `/tmp`
   can be executed.
3. **Given** the scratch-exec setting is `true`, **When** a Rust build whose
   `build.rs` is compiled into the scratch area and executed runs in the
   container, **Then** the build completes without the
   `Permission denied (os error 13)` build-script failure.

---

### User Story 3 - Effective value is attested and visible (Priority: P3)

A verifier inspecting the executor's attestation, and an operator inspecting the
build-time configuration summary, can both see whether scratch execution is
enabled for the running/built image, so the relaxation is never hidden.

**Why this priority**: Relaxing a hardening control must be transparent. The
existing container-security settings are already surfaced in attestation and in
the build summary; the new setting must join them so the security posture
remains fully observable.

**Independent Test**: Resolve configuration with the variable both unset and
enabled; confirm the build-time configuration summary and the attested
`user_data` each report the corresponding effective value.

**Acceptance Scenarios**:

1. **Given** any resolved configuration, **When** attestation is produced,
   **Then** the effective scratch-exec setting appears in the attested
   configuration alongside the other container-security settings.
2. **Given** the `Build Attestable Image` workflow runs, **When** it prints the
   server configuration summary, **Then** the scratch-exec setting appears with
   the value baked into the image.

---

### Edge Cases

- **Scratch disabled entirely**: When `CONTAINER_TMPFS_SIZE` is empty (no tmpfs
  scratch mounted), the scratch-exec setting has no mount to apply to. The
  setting is still resolved and attested, but it has no effect on container
  creation because no tmpfs is mounted. When the setting is *enabled* in this
  case, the server MUST emit a startup log warning that exec is enabled but has
  no effect because no tmpfs is mounted; it MUST NOT fail fast.
- **Invalid value**: A value that is neither a recognized truthy nor falsy
  string MUST fail fast at startup, naming the offending variable, consistent
  with the strict boolean parsing used by the other security toggles.
- **Interaction with read-only rootfs**: As with `CONTAINER_TMPFS_SIZE`, the
  scratch tmpfs (and therefore this setting's effect) is governed solely by
  whether a tmpfs is mounted, independent of `CONTAINER_READ_ONLY_ROOTFS`.
- **`nosuid`/`nodev` unchanged**: Enabling execution MUST only add the `exec`
  mount option; it MUST NOT relax `nosuid` or `nodev` on the scratch mount.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose a single operator-facing configuration
  variable, `CONTAINER_TMPFS_EXEC`, that controls whether the `/tmp` scratch
  tmpfs is mounted with execution permitted.
- **FR-002**: `CONTAINER_TMPFS_EXEC` MUST default to disabled (`noexec`) when
  unset, preserving the current hardened behavior with no change for existing
  deployments.
- **FR-003**: The variable MUST be parsed with the same strict boolean grammar
  as the other container-security toggles (e.g. `NO_NEW_PRIVILEGES`,
  `CONTAINER_READ_ONLY_ROOTFS`): case-insensitive `true`/`1`/`yes` resolve to
  enabled and `false`/`0`/`no` resolve to disabled. Any other value MUST cause
  the server to fail fast at startup, naming the variable.
- **FR-004**: When the setting is disabled, the `/tmp` scratch tmpfs MUST be
  mounted without the `exec` option (Docker's default `noexec`), and binaries
  under `/tmp` MUST NOT be executable.
- **FR-005**: When the setting is enabled and a tmpfs scratch is mounted
  (`CONTAINER_TMPFS_SIZE` non-empty), the `/tmp` scratch tmpfs MUST be mounted
  with the `exec` option so binaries compiled into scratch can be executed.
- **FR-006**: Enabling the setting MUST add only the `exec` option to the
  scratch mount; it MUST NOT alter the existing `mode=1777`, the configured
  size, or the `nosuid`/`nodev` protections of the scratch mount.
- **FR-007**: The setting MUST have no effect on container creation when no
  tmpfs scratch is mounted (`CONTAINER_TMPFS_SIZE` empty); resolution and
  attestation of the value MUST still occur. When the setting is enabled while
  no tmpfs is mounted, the server MUST emit a startup log warning that exec is
  enabled but has no effect, and MUST NOT fail fast.
- **FR-008**: The effective value of the setting MUST be threaded from the
  server configuration to the container-creation path so that the attested
  posture matches the mount actually applied (no drift between reported and
  effective behavior).
- **FR-009**: The effective value MUST be included in the executor's attested
  configuration (`user_data`) alongside the existing container-security
  settings, as a boolean field named `container_tmpfs_exec` (following the
  existing `container_`-prefixed naming, paired with `container_tmpfs_size`).
- **FR-010**: The effective value MUST appear in the `Build Attestable Image`
  workflow's server-configuration summary, derived programmatically from the
  application's configuration so it cannot drift from the value baked into the
  image.
- **FR-011**: The `.env.example` and operator documentation MUST describe the
  new variable, its secure default, and the security implication of enabling it.

### Key Entities

- **Scratch execution setting**: A boolean container-security configuration
  value, secure-by-default (disabled / `noexec`), that determines whether the
  per-execution `/tmp` tmpfs scratch mount permits execution. It joins the
  existing set of container-security settings resolved by the server config
  loader, threaded into container creation, and reported in attestation and the
  build summary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the variable unset, 100% of execution containers mount `/tmp`
  scratch as `noexec`, identical to pre-feature behavior, and a job attempting
  to execute a binary from scratch is denied.
- **SC-002**: With the variable enabled, a Rust build whose `build.rs` is
  compiled into scratch and executed completes successfully where it previously
  failed with `Permission denied (os error 13)` at the build-script step.
- **SC-003**: The effective scratch-exec value is observable in both the attested
  `user_data` and the build-time configuration summary for every resolved
  configuration.
- **SC-004**: An invalid value for the variable causes startup to fail fast with
  an error naming the variable, before any request is served.
- **SC-005**: Enabling the setting changes only the execution permission of the
  scratch mount; the scratch size, `mode=1777`, `nosuid`, and `nodev` remain
  unchanged, and no other container-security control is affected.

## Assumptions

- The motivating workloads (e.g. Rust builds with `build.rs`) are trusted by the
  operator who chooses to enable execution; enabling `exec` is an explicit,
  attested relaxation of the enclave's no-execute-from-scratch posture and is
  expected to be left disabled for untrusted or minimal-trust deployments.
- The scratch tmpfs continues to be mounted at `/tmp` and governed by
  `CONTAINER_TMPFS_SIZE`, as established in feature 001; this feature only adds
  an execution-permission option to that same mount.
- The boolean parsing, fail-fast startup validation, attestation (`user_data`),
  and build-time configuration-summary mechanisms introduced in feature 001 are
  reused; this feature extends them with one additional field rather than
  introducing new mechanisms.
- Permitting execution from scratch does not require relaxing any other
  container constraint (`cap_drop=ALL`, `no-new-privileges`, read-only
  workspace, network mode, memory/CPU/pids limits remain enforced).
