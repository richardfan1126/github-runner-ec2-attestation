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
- `CONTAINER_TMPFS_SIZE` (non-empty) → `tmpfs={"/tmp": "size=<value>"}` (the container's standard temp directory, so jobs using `$TMPDIR`/`mktemp`/`tempfile` write to the scratch mount under a read-only rootfs — see Clarifications 2026-06-13)
- `WORKSPACE_MOUNT_MODE` → workspace volume bind `mode` (`ro`/`rw`) at the `/workspace` mount (currently hard-coded `ro`, `script_executor.py:249`)
- `CONTAINER_NETWORK_MODE` → `network_mode="none"|"bridge"|"host"`

**Rationale**: These are the exact kwargs the Docker SDK exposes; the executor already sets `security_opt`, `cap_drop`, `cap_add`, and the `ro` workspace bind, so this is a parameterization of existing literals rather than new machinery.

**Resolved implementation note**: the tmpfs is mounted at `/tmp` (Clarifications 2026-06-13). An earlier draft targeted `/tmp/execution`, but that path is referenced only by `_copy_script_to_container`, which is **dead code** (never called — the live flow runs `bash /workspace/{script_path}` from the read-only workspace). Mounting the scratch at the conventional `/tmp` is what makes a hardened-default local-compute job actually succeed, since standard tooling writes temp files to `/tmp`, not a sub-path.

## Decision 6 — Attestation/audit surface

**Decision**: Thread the eight effective values into attestation `user_data` via new optional parameters on `AttestationGenerator.generate_attestation()` and `.generate_output_attestation()`, exactly as `gpu_enabled` is added today (`attestation.py:146-147, 322-323`). Wire them at the two call sites in `server.py` (~L839 execute, ~L1155 output). The resolved `CONTAINER_CAP_ADD` is surfaced as its concrete list so unset-vs-empty is distinguishable.

**Rationale**: Reuses the existing attestation channel (spec Assumption) — no new attestation surface. A relying party reads the effective value per setting and compares to the documented default (SC-004).

**Alternatives considered**: A dedicated posture endpoint — rejected: out of scope and redundant with `user_data`.

## Decision 7 — Startup observability (FR-028)

**Decision**: Add `logger.info` lines in `main.py` after `load_config()` for all eight effective values, alongside the existing config log block (`main.py:52-62`).

**Rationale**: Posture is inspectable independent of any execution; consistent with how every other config value is logged at startup.

## Decision 8 — Build-time configuration dump mechanism (FR-030)

**Decision**: Add a small read-only helper `.github/scripts/print_config.py` (run as `uv run python .github/scripts/print_config.py --env-file <path>` from the repo root) that loads the baked-in env file through the application's own `load_config()` and renders every `dataclasses.fields(ServerConfig)` entry as a Markdown table (`| Setting | Value |`). It imports only from `src.config`.

**Location (Clarifications 2026-06-14)**: the helper lives in `.github/scripts/` (build-workflow tooling, alongside `build-kiwi-image.sh`), **not** under `src/`, which is reserved for the executor runtime. It still imports the executor's `load_config` so the executor stays the single source of truth; the repo root is the executor's uv project (`packages = ["src"]`), so `uv run` makes `src` importable regardless of the script's location. `scripts/` (repo root) was rejected — it is a separate self-contained uv project (boto3/terraform) that does not import `src`.

**Rationale**: Resolving through `load_config()` makes the printed values the *exact* values the server would resolve — the single source of truth FR-030 requires. Generating the row set from `dataclasses.fields()` (rather than a hand-maintained list or echoing the env file) means the table cannot drift from the code and automatically covers every `.env.example` key plus any field not in the env file (e.g. the eight security settings, which fall back to their hardened defaults) — satisfying SC-007. Importing only `src.config` keeps it from binding a port, touching the TPM, or pulling in FastAPI/Docker.

**Naming note (accepted tradeoff)**: the table labels are the `ServerConfig` attribute names (e.g. `port`, `container_user`), not the env-var spellings (`SERVER_PORT`). Favoring the dataclass field list keeps the output drift-proof; the small label/env-name divergence for a few keys is an accepted cosmetic cost.

**Alternatives considered**: (a) Echo/parse the env file directly in YAML — rejected: would miss settings absent from the file (the eight defaults) and drift from real parsing. (b) Hand-maintained value list in the workflow — rejected by clarification (drift). (c) Boot the full server with a `--print-config` flag — rejected: needs TPM/port and heavyweight imports. (d) Place the helper under `src/` — rejected by clarification (2026-06-14): `src/` is the executor runtime; the helper is build tooling.

## Decision 9 — Workflow placement & fail-fast (FR-030)

**Decision**: Add a step *"Print effective configuration to summary"* to the **`build-and-publish`** job, immediately after *Build KIWI image* and before *Push artifact to GHCR*. It appends a heading and the `print_config.py` table to `$GITHUB_STEP_SUMMARY` via `uv run python .github/scripts/print_config.py`; if it exits non-zero the step emits `::error::` and `exit 1`, failing the run before any artifact is published.

**Rationale**: `build-and-publish` already checks out the repo and installs `uv`, runs on every push (including `develop`), and reads the same env file baked into the image — so the summary reflects what ships with no extra setup. Placing the step before the GHCR push enforces FR-030's "fail before publishing": a loader rejection at build time is exactly the failure the server would hit at startup (FR-011), so catching it early is consistent with the system's fail-fast posture. GitHub's default `bash -eo pipefail` plus an explicit `if ! …; then exit 1` makes the failure deterministic.

**Alternatives considered**: Placing it in `build-ami` — rejected: that job only runs on `main`/dispatch and would not surface the posture on `develop` pushes; the config baked in is identical either way.

## Decision 10 — Baked env-file parsing

**Decision**: Parse the env file with systemd-`EnvironmentFile`-compatible rules: ignore blank lines and lines beginning with `#`, split each remaining line on the first `=`, and set `os.environ[key]=value`. The committed file uses simple unquoted values, so no shell expansion or quote-stripping is required.

**Rationale**: This matches how systemd actually loads the file at runtime (`EnvironmentFile=` in the unit), so the build-time resolution mirrors production. Keeping the parser minimal avoids reintroducing shell-quoting edge cases.
