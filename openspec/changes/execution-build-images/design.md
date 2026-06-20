## Context

Today the repository builds **one** attestable AMI for **one** execution-container
image. The image-delivery mechanism — pull at runtime → bake an offline,
digest-preserving OCI layout into the dm-verity-sealed erofs root, verify
offline, bind by image-ID at runtime, anchor to the GHCR manifest digest — was
delivered single-flavor by the prerequisite change **`bake-image-into-ami`
(Change 1, now archived)**. The executor's hardened contract (rootless `65534`,
world-exec tools on PATH, pinned, no run-time install) and its config model
already exist in `src/config.py` and are baked into the AMI as an
`EnvironmentFile`-style env at
`kiwi-descriptions/root/etc/github-actions-remote-executor/env`.

This change (`execution-build-images`, Change 2) multiplies that single-flavor
pipeline to **N build-environment flavors**, each producing its own image
(published to GHCR by digest) and its own attestable AMI carrying that flavor's
baked image and effective sandbox config (bound by PCR4 per proposal D3).

The proposal settled the load-bearing architecture: **D3** (one AMI per flavor),
**D4** (selective rebuild + durable `flavors.lock`), **D5** (the two-change
split). This design resolves the open questions the proposal deferred — chiefly
**Q1 (flavor manifest schema)** — and pins the layout, config-merge, and
invalidation mechanics that `tasks.md` will implement.

Key constraint inherited from `src/config.py`: the executor's **hardened defaults
already live in code**. Every security-relevant container setting
(`CONTAINER_USER=65534:65534`, `NO_NEW_PRIVILEGES=true`, `CONTAINER_ALLOW_ROOT=false`,
`ALLOW_NO_TPM=false`, `MAX_CONTAINER_PIDS=256`, the 8-var container-security set, …)
is an *optional* env key that falls back to its hardened value when unset.
"Secure-by-default" is therefore not something this change has to build — it is
something this change must **preserve** through the new merge layers.

## Goals / Non-Goals

**Goals:**
- Define a flavor as a **co-located directory** that fully describes one build
  environment, with the env-file values as its only configuration schema.
- Specify the **effective-config precedence** that yields a deterministic,
  git-reconstructible env baked into each flavor's verity root (and thus PCR4).
- Preserve secure-by-default: any relaxation is explicit, attested, and visible.
- Refine D4's selective-rebuild invalidation graph to the co-located layout.
- Resolve, or scope, the remaining open questions: Q1 (schema), Q4 (`flavors.lock`
  location), Q9 (routing), terminology; carry Q6 (RAM) as a sized risk.

**Non-Goals:**
- The bake *mechanism* (OCI layout, offline verify, image-ID binding, GHCR-digest
  anchor) — delivered by Change 1; this change only multiplies it.
- A guest-side build wrapper / composite action (proposal Q-new) — separable,
  explicitly deferred.
- Deployment-side addressing/discovery of per-flavor executors beyond the
  authorization half of routing (see D9); endpoint provisioning stays in the
  `deployment` capability.
- Any change to the guest build flow (`remote-executor` execution, cloning,
  `request-encryption`). Guests still ship their own source and build scripts.

## Decisions

### D6 — A flavor is a co-located directory (`flavors/<f>/`) (resolves Fork A)

**Decision**: each flavor is a directory `flavors/<f>/` containing everything that
describes that build environment:

```
flavors/
  default/            # NON-BUILT base — see D8; supplies shared defaults only
    env
  <flavor>/
    Dockerfile        # the build-environment implementation (base image, toolchain,
    <supplements>     #   OS packages, checksummed out-of-band downloads, ...)
    env               # this flavor's config DELTAS (env-file KEY=VALUE overrides)
```

The set of buildable flavors is **`ls flavors/` minus `default`**. There is no
central `projects.yml` index; the directory tree *is* the manifest.

**Rationale**: the proposal's D4 originally implied a hybrid (per-flavor Dockerfile
*plus* a central manifest stanza). Co-location collapses that to one place per
flavor, which (a) makes D4's path-map trivial and conflict-free —
`flavors/<f>/**` maps to flavor `<f>` with no in-file diffing — and (b) keeps the
"what gets built" enumeration a directory listing rather than a parsed index.

**Alternative considered**: central `projects.yml` with a stanza per flavor.
Rejected: `detect-changes` would have to diff *inside* one file to attribute a
change to a flavor, the file churns and conflicts on every flavor edit, and a
malformed diff would have to fail-safe to "build ALL" more often.

### D7 — The config schema *is* the env-file values, in three buckets (resolves Q1)

**Decision**: a flavor declares no bespoke schema. Its configuration is the same
`EnvironmentFile`-style keys `src/config.py::load_config()` already reads. Those
keys fall into three buckets that behave differently and bound what a flavor's
`env` may contain:

| Bucket | Examples | Behaviour |
| --- | --- | --- |
| ① Hardened defaults (code) | `CONTAINER_USER`, `NO_NEW_PRIVILEGES`, `CONTAINER_ALLOW_ROOT`, `ALLOW_NO_TPM`, `MAX_CONTAINER_PIDS`, 8-var security set | Already optional in `src/config.py`; unset → hardened value. A flavor *may* set (relax) them — see D10. |
| ② Declared values | `MAX_CONCURRENT_EXECUTIONS`, `EXECUTION_TIMEOUT_SECONDS`, `MAX_SCRIPT_SIZE_BYTES`, `RATE_LIMIT_*`, `OUTPUT_RETENTION_HOURS`, `CONTAINER_MEMORY_LIMIT`, `CONTAINER_CPU_LIMIT`, `ALLOWED_REPOSITORIES`, `EXPECTED_AUDIENCE`, `SERVER_PORT`, `TEMP_STORAGE_PATH`, `TPM_ATTEST_PATH` | Supplied by `default/env` and/or overridden per flavor (D8). |
| ③ Derived / injected | `CONTAINER_IMAGE`, `CONTAINER_IMAGE_DIGEST` | **Outputs**, not inputs. Produced by building the flavor's Dockerfile and pushing to GHCR; injected by the pipeline (D11). A flavor `env` MUST NOT set them. |

**Rationale**: reusing `load_config()` keeps a single source of truth and means
`print_config.py` (already routed through `load_config()`) keeps working as the
build-time config summary with no schema duplication.

### D8 — Effective-config precedence; `flavors/default` is a non-built base (resolves migration)

**Decision**: the env baked into a flavor's verity root is computed by a fixed
precedence chain, highest-wins last:

```
  src/config.py defaults        (bucket ①; hardened, fallback for any unset key)
        ◀── flavors/default/env (bucket ② shared values: ports, timeouts, rate
        │                         limits, retention, audience, ... — once for all)
        ◀── flavors/<f>/env     (this flavor's DELTAS: ALLOWED_REPOSITORIES,
        │                         resource limits, any deliberate relaxation)
        ◀── pipeline inject     (bucket ③: CONTAINER_IMAGE + CONTAINER_IMAGE_DIGEST)
              = effective env  →  baked → PCR4 → config summary → flavors.lock
```

`flavors/default/` is **the migrated old env file** and serves only as shared
defaults. It is **never built**: it is excluded from the build matrix, produces no
image and no AMI. A real flavor stays minimal — a `Dockerfile` plus an `env` that
in practice needs little beyond its own `ALLOWED_REPOSITORIES` (everything else
falls through to `default/env`, then to code).

**Consequences**:
- **`flavors/default/**` is a GLOBAL invalidator** (D12): because every flavor
  inherits it, editing it rebuilds *all* flavors. This is the cost of
  de-duplicating boilerplate and is an explicit D4 line item.
- **Auditability is preserved**: the effective env is deterministically
  reconstructible from `default/env` ⊕ `flavor/env` ⊕ the (recorded) injected
  digest — all committed or recorded in `flavors.lock`.
- Bucket ③ must be **stripped** from the old file as it becomes `default/env`
  (today it carries `CONTAINER_IMAGE=ubuntu:24.04` + a pinned digest; those cannot
  survive — each flavor's baked image is its own built image).

**Alternative considered**: `flavors/default/env` as a copy-paste seed (no runtime
inheritance; each flavor a full standalone env). Rejected: flavors would duplicate
all required keys, and `default/env` would not be a global invalidator — defeating
the de-duplication goal.

### D9 — Per-flavor `ALLOWED_REPOSITORIES`; routing-by-authorization (resolves Fork B; partially resolves Q9)

**Decision**: `ALLOWED_REPOSITORIES` (and its sibling `EXPECTED_AUDIENCE`) is a
per-flavor declared value (bucket ②). Its **default is deny-all** — a flavor that
declares no allowlist is unusable (fail closed), so each flavor must name the
guest repos permitted to use it.

Because the allowlist is baked into the flavor's verity root, "which guests may
use this flavor" is enforced **cryptographically at the executor and bound by
PCR4** — not merely by endpoint addressing. This is the *authorization* half of
Q9: a caller reaching the wrong flavor's executor is rejected by the baked OIDC
allowlist. The *addressing* half (provisioning/discovering one endpoint per
flavor) is a `deployment` concern and out of scope here (Non-Goals).

**Trade-off**: changing who may call a flavor is a full AMI rebuild (PCR4-bound),
consistent with D3's attested-at-rest posture.

### D10 — Relaxations are allowed but attested

**Decision**: a flavor MAY relax a bucket-① hardened default (e.g.
`CONTAINER_ALLOW_ROOT=true`, `ALLOW_NO_TPM=true`, a larger `MAX_CONTAINER_PIDS`).
Because the effective env is baked, every such relaxation (a) changes that
flavor's PCR4, (b) appears in the build-time config summary, and (c) is recorded
in `flavors.lock`. Secure-by-default-*overridable* therefore holds end-to-end:
relaxations are never silent — they are measured and auditable.

### D11 — Derived-digest injection + a validating guard for bucket ③

**Decision**: the pipeline, after building `flavors/<f>/Dockerfile` and pushing
to GHCR by digest, injects `CONTAINER_IMAGE=ghcr.io/<owner>/<repo>/<flavor>` and
`CONTAINER_IMAGE_DIGEST=sha256:…` into the effective env before bake. A
**validator rejects any committed `env` (default or flavor) that hand-sets a
bucket-③ key**, failing the build fast — preventing a stale/mismatched digest
from silently shadowing the freshly built image. The validator lives alongside
`print_config.py`/`load_config()` (the existing config-resolution path).

The injected digest is the per-platform **amd64 manifest** digest (never a
multi-arch index), per Change 1's D2 byte-identity constraint, carried forward
unchanged per flavor.

### D12 — Selective-rebuild invalidation graph for the co-located layout (refines D4)

**Decision**: `detect-changes` maps changed paths to affected flavors using a
two-level graph, refined for D6's layout:

- **Global invalidators → rebuild ALL flavors**: shared hardened base image, build
  machinery (`.github/scripts/**`, KIWI builder Dockerfile), shared executor OS
  (`kiwi-descriptions/**`), reproducibility pins (`uv.lock`, `pyproject.toml`,
  `appliance.kiwi`), the workflow, the path-map logic, **and `flavors/default/**`** (D8).
- **Per-flavor, IMAGE level → full rebuild of that flavor** (new image + new AMI):
  anything under `flavors/<f>/` **except** `env` — i.e. `Dockerfile` and its
  supplements (image inputs).
- **Per-flavor, AMI-ONLY level → re-bake AMI, reuse image digest from
  `flavors.lock`**: `flavors/<f>/env` (config inputs only; per D3 still a full AMI
  rebuild, but no image rebuild).

Clean rule: **everything under `flavors/<f>/` except `env` is image-level; `env`
alone is AMI-only.**

Fail-safe / edge rules carry over from the proposal's D4: no diff baseline →
build ALL; empty changed set → empty matrix, `flavors.lock` untouched;
`workflow_dispatch` override forces a flavor or `all`; bounded `max-parallel`;
serialized `flavors.lock` updates via a concurrency group; on `develop` build/
publish changed-flavor images but skip AMIs; `detect-changes` records its
decision in the run summary.

### D13 — `flavors.lock` is the git-committed source of truth (resolves Q4)

**Decision**: `flavors.lock` is **committed to git** as the durable record
mapping each flavor → image manifest digest, PCR4, AMI id, producing commit. Git
history gives the verifier a fully auditable flavor → PCR4 → digest → AMI lineage
tied to the producing commit, and generalizes Change 1's single-entry verifier
record to N flavors with the same fields. An **optional SSM Parameter Store
mirror** for the `deployment` side to consume is left as an open question (it is a
consumption convenience, not the source of truth).

### D14 — Terminology: "flavor" (resolves terminology)

**Decision**: the user-facing term is **flavor** (a "build-environment flavor"),
matching the `flavors/` directory and `flavors.lock`. "Project" is avoided
(collides with guest projects); "build environment" remains the descriptive long
form.

## Risks / Trade-offs

- **Per-flavor RAM budget (Q6)** → the decompressed baked image is resident in the
  rootless `fuse-overlayfs` RAM overlay (Change 1's D1), *on top of* the `256m`
  `/tmp` tmpfs default — unlike the demo's ≥4 GiB assumption. A large toolchain
  image can exceed a small instance's memory. **Mitigation**: treat per-flavor
  memory as a sized input — derive a floor from the built image size and let the
  flavor influence instance-type selection at deploy time. Carried as an open
  question; see below.
- **Global-invalidator cost** → editing `flavors/default/env` (or the shared base)
  rebuilds every flavor — up to N full KIWI→AMI builds, each an EC2 instance.
  **Mitigation**: bounded matrix `max-parallel`; keep `default/env` stable; AMI-only
  changes skip image rebuilds.
- **Accidental `default` build** → a stray `flavors/default` AMI would be wrong.
  **Mitigation**: `default` is an *explicit* exclusion in matrix enumeration, not an
  implicit convention; covered by a test.
- **Merge precedence vs. one-file auditability** → the baked env is a merge result,
  not a single committed file. **Mitigation**: the merge is deterministic and all
  inputs are committed/recorded; the config summary prints the *effective* result,
  and `flavors.lock` records the derived digest.
- **Stale derived digest** → a hand-set bucket-③ key could shadow the built image.
  **Mitigation**: D11's validator fails the build fast.

## Migration Plan

1. Create `flavors/default/env` from the current
   `kiwi-descriptions/root/etc/github-actions-remote-executor/env`, **stripping the
   bucket-③ keys** (`CONTAINER_IMAGE`, `CONTAINER_IMAGE_DIGEST`).
2. Create the first real flavor directory (e.g. the existing rust-build demo) with
   its `Dockerfile` (+ supplements) and a minimal `env` declaring its
   `ALLOWED_REPOSITORIES` and any resource overrides.
3. Add `detect-changes` + dynamic matrix to the workflow (D12); parameterize the
   KIWI build 1 → N; bake each flavor's OCI layout (reusing Change 1's path).
4. Introduce the bucket-③ validator (D11) and extend the config summary per flavor.
5. Initialize `flavors.lock` (D13); wire selective carry-forward.
6. **Rollback**: the change is additive at the layout level — reverting to a single
   AMI means building only one flavor; Change 1's single-flavor bake path is
   unchanged underneath.

## Open Questions

- **Q6 RAM / instance type**: exact per-flavor memory floor and whether instance
  type becomes a per-flavor deploy parameter (vs. a fixed family). Needs the first
  real flavor's measured image size.
- **`flavors.lock` SSM mirror (Q4 remainder)**: whether/how to mirror the
  git-committed record to SSM for the `deployment` side, and where a consumer is
  expected to run the runtime NitroTPM attestation check.
- **Q9 addressing remainder**: per-flavor endpoint provisioning/discovery (deferred
  to `deployment`).
- **Q-new guest-side wrapper**: explicitly a Non-Goal here; revisit as a separate
  change if demand appears.
