## ADDED Requirements

### Requirement: Flavor is a co-located directory

A build-environment **flavor** SHALL be defined entirely by a co-located directory `flavors/<flavor>/` whose contents fully describe one build environment: a `Dockerfile` (plus any supplements it references — checksummed out-of-band downloads, package lists, scripts) that implements the execution-container image, and an `env` file carrying that flavor's configuration deltas. There SHALL be no separate central manifest or index file enumerating flavors; the directory tree under `flavors/` IS the manifest.

#### Scenario: Flavor fully described by its directory

- **WHEN** a developer adds a new build environment
- **THEN** they create `flavors/<flavor>/` containing a `Dockerfile` and an `env` file, and no other file anywhere in the repository must be edited to register the flavor

#### Scenario: No central manifest to parse

- **WHEN** the pipeline enumerates flavors
- **THEN** it derives the set from the `flavors/` directory listing, not from parsing a `projects.yml`-style index, so adding a flavor cannot conflict in a shared index file

### Requirement: The buildable flavor set excludes `default`

The set of buildable flavors SHALL be `ls flavors/` minus the reserved `flavors/default/` entry. `flavors/default/` SHALL be a non-built shared-defaults base: it supplies shared configuration values inherited by every flavor, contains no `Dockerfile`, and produces no execution-container image and no AMI. The build matrix SHALL exclude `default` by explicit enumeration, not by an implicit naming convention.

#### Scenario: default is never built

- **WHEN** the pipeline computes the build matrix
- **THEN** `default` is excluded explicitly, no image is built for it, and no AMI is registered for it

#### Scenario: Real flavors enumerated

- **WHEN** `flavors/` contains `default/` and two real flavor directories
- **THEN** the buildable set is exactly the two real flavors

### Requirement: Flavor configuration schema is the executor env-file values

A flavor SHALL declare no bespoke configuration schema. Its configuration is expressed using the same `EnvironmentFile`-style `KEY=VALUE` keys that the executor's configuration loader (`src/config.py`) already reads. Those keys fall into three buckets that bound what a flavor `env` may contain:

- **Bucket ① — hardened defaults (code):** security-relevant keys (e.g. `CONTAINER_USER`, `NO_NEW_PRIVILEGES`, `CONTAINER_ALLOW_ROOT`, `ALLOW_NO_TPM`, `MAX_CONTAINER_PIDS`, and the rest of the container-security set) that are optional in `src/config.py` and fall back to their hardened value when unset. A flavor MAY set (relax) them, subject to the relaxation requirement.
- **Bucket ② — declared values:** operational keys (e.g. `ALLOWED_REPOSITORIES`, `EXPECTED_AUDIENCE`, resource limits, timeouts, rate limits, ports, paths) supplied by `flavors/default/env` and/or overridden per flavor.
- **Bucket ③ — derived / injected outputs:** `CONTAINER_IMAGE` and `CONTAINER_IMAGE_DIGEST`. These are pipeline outputs, never inputs.

#### Scenario: Configuration reuses the executor loader keys

- **WHEN** a flavor declares configuration
- **THEN** it does so with the same keys `src/config.py::load_config()` reads, so no second schema exists to drift from the executor's own

#### Scenario: A flavor env stays minimal

- **WHEN** a flavor needs only its own repository allowlist
- **THEN** its `env` may contain just `ALLOWED_REPOSITORIES`, with every other declared value inherited from `flavors/default/env` and every hardened default inherited from code

### Requirement: Effective configuration is a fixed precedence merge

The configuration baked into a flavor's verity root SHALL be computed by a fixed, highest-wins-last precedence chain: `src/config.py` hardened defaults (bucket ①) ◀ `flavors/default/env` (bucket ② shared values) ◀ `flavors/<flavor>/env` (this flavor's deltas) ◀ pipeline injection (bucket ③). The result is the **effective env** that is baked, measured into PCR4, printed in the build-time configuration summary, and recorded in `flavors.lock`. The merge SHALL be deterministic so the effective env is reconstructible from the committed inputs plus the recorded injected digest.

#### Scenario: Flavor delta overrides shared default

- **WHEN** `flavors/default/env` sets a declared value and `flavors/<flavor>/env` sets the same key to a different value
- **THEN** the flavor's value wins in the effective env

#### Scenario: Unset key falls through to hardened default

- **WHEN** neither `flavors/default/env` nor `flavors/<flavor>/env` sets a bucket-① key
- **THEN** the effective env carries the hardened default from `src/config.py`

#### Scenario: Effective env is reconstructible

- **WHEN** a verifier holds the producing commit and the recorded injected digest
- **THEN** it can deterministically re-derive the exact effective env that was baked from `flavors/default/env` ⊕ `flavors/<flavor>/env` ⊕ the injected digest

### Requirement: Per-flavor repository authorization defaults to deny-all

`ALLOWED_REPOSITORIES` (and its sibling `EXPECTED_AUDIENCE`) SHALL be a per-flavor declared value whose default is deny-all. A flavor that declares no allowlist SHALL be unusable (fail closed). Because the allowlist is part of the effective env baked into the verity root and bound by PCR4, which guests may use a flavor is enforced cryptographically at the executor, not merely by endpoint addressing.

#### Scenario: Flavor with no allowlist is unusable

- **WHEN** a flavor `env` declares no `ALLOWED_REPOSITORIES` and `flavors/default/env` supplies none
- **THEN** the effective configuration denies all callers (fail closed) rather than allowing any

#### Scenario: Allowlist is bound by PCR4

- **WHEN** a caller from a repository not in a flavor's baked allowlist reaches that flavor's executor
- **THEN** it is rejected by the baked OIDC allowlist, and changing who may call the flavor requires a full AMI rebuild (new PCR4)

### Requirement: Hardened defaults are preserved through the merge; relaxations are attested

The merge SHALL preserve secure-by-default: any bucket-① key left unset at every layer SHALL resolve to its hardened code value. A flavor MAY relax a hardened default (e.g. `CONTAINER_ALLOW_ROOT=true`, `ALLOW_NO_TPM=true`, a larger `MAX_CONTAINER_PIDS`), but every relaxation SHALL be non-silent: it changes the flavor's PCR4, appears in the build-time configuration summary, and is recorded in `flavors.lock`.

#### Scenario: No relaxation means hardened posture

- **WHEN** a flavor `env` relaxes no bucket-① key
- **THEN** the effective env carries the full hardened posture and the flavor's PCR4 reflects it

#### Scenario: Relaxation is measured and recorded

- **WHEN** a flavor relaxes a hardened default
- **THEN** the relaxed value is baked, changes the flavor's PCR4, is shown in the configuration summary, and is recorded in `flavors.lock` — never applied silently

### Requirement: Derived image identity is injected and validated, never hand-declared

The pipeline SHALL, after building `flavors/<flavor>/Dockerfile` and pushing it to GHCR by digest, inject `CONTAINER_IMAGE=ghcr.io/<owner>/<repo>/<flavor>` and `CONTAINER_IMAGE_DIGEST=sha256:…` into the effective env before bake. The injected digest SHALL be the per-platform **amd64 manifest** digest, never a multi-arch index digest. A **pre-bake validator** SHALL reject any committed `env` (whether `flavors/default/env` or a flavor `env`) that hand-sets a bucket-③ key, failing the build before bake. The validator runs at build time only; it does not run at executor startup, where these keys are legitimately present in the baked env.

#### Scenario: Hand-set bucket-③ key fails the build

- **WHEN** a committed `env` sets `CONTAINER_IMAGE` or `CONTAINER_IMAGE_DIGEST`
- **THEN** the pre-bake validator fails the build before any bake step, preventing a stale digest from shadowing the freshly built image

#### Scenario: Migration leftovers are caught

- **WHEN** `flavors/default/env` still carries a `CONTAINER_IMAGE`/`CONTAINER_IMAGE_DIGEST` left over from the pre-migration single-flavor env file
- **THEN** the same pre-bake validator fails the build, enforcing that bucket-③ keys were stripped during migration

#### Scenario: amd64 manifest digest injected

- **WHEN** the pipeline injects the digest
- **THEN** it injects the amd64 per-platform manifest digest, not the multi-arch index digest

### Requirement: Selective rebuild via a two-level invalidation graph

A `detect-changes` job SHALL map changed paths to affected flavors and emit a dynamic build matrix, rebuilding only the flavors whose inputs changed, on three levels:

- **Global invalidators → rebuild ALL flavors:** the shared hardened base image, build machinery (`.github/scripts/**`, the KIWI builder Dockerfile), the shared executor OS (`kiwi-descriptions/**`), reproducibility pins (`uv.lock`, `pyproject.toml`, `appliance.kiwi`), the workflow, the path-map logic, and `flavors/default/**`.
- **Per-flavor IMAGE level → full rebuild of that flavor (new image + new AMI):** anything under `flavors/<flavor>/` except `env`.
- **Per-flavor AMI-ONLY level → re-bake AMI, reuse the image digest from `flavors.lock`:** `flavors/<flavor>/env` alone.

The clean rule SHALL be: everything under `flavors/<flavor>/` except `env` is image-level; `env` alone is AMI-only.

#### Scenario: Editing a flavor Dockerfile rebuilds only that flavor's image and AMI

- **WHEN** only `flavors/<flavor>/Dockerfile` changes
- **THEN** the matrix contains only that flavor at image level, building a new image and a new AMI

#### Scenario: Editing a flavor env re-bakes only that flavor's AMI

- **WHEN** only `flavors/<flavor>/env` changes
- **THEN** the matrix contains only that flavor at AMI-only level, re-baking its AMI while reusing the existing image digest from `flavors.lock`

#### Scenario: Editing default rebuilds all flavors

- **WHEN** `flavors/default/env` changes
- **THEN** every flavor is rebuilt, because all flavors inherit the shared defaults

#### Scenario: flavors.lock-only diff produces an empty matrix (loop guard)

- **WHEN** a push changes only `flavors.lock` (e.g. the pipeline's own write-back commit)
- **THEN** `detect-changes` maps it to no affected flavors and emits an empty matrix, so the pipeline's commit does not trigger another build

### Requirement: Change detection fails safe and is auditable

`detect-changes` SHALL fail safe and record its decision:

- No diff baseline (initial commit, force-push, fork PR, shallow clone) → build ALL; never silently build nothing.
- Empty changed set (e.g. docs-only) → empty matrix; downstream jobs skip cleanly and `flavors.lock` is untouched.
- `workflow_dispatch` override → force a specific flavor or `all`; a debug/SSH build targets a single flavor and SHALL NOT overwrite that flavor's production `flavors.lock` entry.
- Bounded `max-parallel` on the matrix (each AMI build is an EC2 instance).
- On `develop`, build/publish changed-flavor images but skip AMIs; full vertical builds only on `main`.
- The rebuild decision (commit → flavors) SHALL be recorded in the run summary.

#### Scenario: Missing baseline builds all

- **WHEN** no diff baseline is available
- **THEN** the matrix is all flavors, never empty

#### Scenario: Decision recorded for audit

- **WHEN** `detect-changes` resolves the matrix
- **THEN** it records the commit-to-flavors decision in the run summary, since the changed-set logic is itself a verifier trust input

### Requirement: flavors.lock is the git-committed durable record written by the pipeline

`flavors.lock` SHALL be committed to git as the durable source of truth mapping each flavor to its image manifest digest, PCR4, AMI id, and producing commit. It SHALL be machine-written and committed back by the pipeline (GitHub Actions) after a flavor's AMI is registered; flavors not rebuilt in a run SHALL be carried forward unchanged. Updates SHALL be serialized via a concurrency group. The `producing commit` field SHALL record the source commit that supplied the flavor's inputs (`C_src`), not the pipeline's own write-back commit, so a verifier dereferences the correct tree for `flavors/default/env` and `flavors/<flavor>/env`.

#### Scenario: Pipeline writes and commits the record

- **WHEN** a flavor's AMI is registered
- **THEN** the pipeline writes that flavor's `{image manifest digest, PCR4, AMI id, producing commit}` entry into `flavors.lock` and commits it back to the repository

#### Scenario: Unchanged flavors carried forward

- **WHEN** a run rebuilds a subset of flavors
- **THEN** the entries for flavors not rebuilt are preserved unchanged in `flavors.lock`

#### Scenario: Producing commit points at the inputs

- **WHEN** the pipeline records `producing commit` for a flavor
- **THEN** it stores the source commit `C_src` that supplied `flavors/<flavor>/**` and `flavors/default/env`, not the bot's write-back commit, so reconstruction reads the env inputs from the right tree

#### Scenario: Record generalizes the single-entry seed

- **WHEN** `flavors.lock` is serialized
- **THEN** each entry's field set matches the single-entry verifier-record seed emitted by the `ami-build` capability, generalized from one image to N flavors
