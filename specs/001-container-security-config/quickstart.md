# Quickstart / Validation Guide: Container Security Configuration

Runnable scenarios that prove the feature works end to end. See [data-model.md](./data-model.md) and [contracts/](./contracts/) for field-level detail.

## Prerequisites

- Repo checked out on branch `001-container-security-config`, venv active (`.venv`, Python 3.12).
- Test deps installed (pytest + Hypothesis). Docker SDK calls are exercised via the in-repo fakes in `tests/mock_docker.py`; no real Docker daemon needed for the suite.

## Run the test suite

```bash
# Full suite
.venv/bin/pytest -q

# Feature-focused subset
.venv/bin/pytest -q \
  tests/test_config.py tests/test_config_properties.py \
  tests/test_script_executor.py tests/test_docker_container_properties.py \
  tests/test_attestation_user_data_regression.py tests/test_attestation_properties.py \
  tests/test_security_config_integration.py
```

**Expected**: all pass, including new cases for the eight variables.

## Scenario A — Hardened by default (User Story 1 / SC-001)

1. Load config with none of the eight variables set (only the existing required vars).
2. Build a container create-spec via `ScriptExecutor`.
3. **Expect** the `containers.create()` kwargs to show:
   `user="65534:65534"`, `read_only=True`, `tmpfs` mounting `/tmp` at `size=256m`,
   `volumes[...]["mode"]=="ro"`, `security_opt==["no-new-privileges"]`,
   `cap_drop==["ALL"]`, `cap_add==[CHOWN,DAC_OVERRIDE,FOWNER,SETUID,SETGID,NET_BIND_SERVICE,KILL]`,
   `network_mode=="none"`.
4. **Expect** startup logs to print each effective value (FR-028).

## Scenario B — Validated relaxation (User Story 2 / SC-003, SC-006)

Valid overrides take effect:
- `CONTAINER_NETWORK_MODE=bridge` → `network_mode=="bridge"`.
- `WORKSPACE_MOUNT_MODE=rw` → workspace bind `mode=="rw"`.
- `CONTAINER_USER=0:0` + `CONTAINER_ALLOW_ROOT=true` → loads; `user=="0:0"`.

Invalid values fail fast (server refuses to start; error names the variable):
- `CONTAINER_ALLOW_ROOT=maybe`, `WORKSPACE_MOUNT_MODE=readwrite`, `CONTAINER_NETWORK_MODE=nat`,
  `CONTAINER_USER=1000` (no gid), `CONTAINER_CAP_ADD=SYS_ADMIN`, `CONTAINER_TMPFS_SIZE=256` (no unit),
  `CONTAINER_USER=0:0` with `CONTAINER_ALLOW_ROOT=false` (error names **both** vars).

Validate the fail-fast contract directly:

```bash
CONTAINER_NETWORK_MODE=nat .venv/bin/python -c "from src.config import load_config; load_config()"
# Expect: ConfigurationError naming CONTAINER_NETWORK_MODE and listing none/bridge/host; non-zero exit.
```

## Scenario C — Posture is attested (User Story 3 / SC-004)

1. Trigger an `/execute` (and `/output`) flow with one relaxed setting (e.g. `CONTAINER_NETWORK_MODE=bridge`).
2. Inspect the `user_data` passed to `nitro-tpm-attest` (the regression test captures it).
3. **Expect** all eight keys present with effective values; `container_network_mode=="bridge"` (relaxed), others at defaults; `container_cap_add` is the resolved list.

## Scenario D — Build-workflow configuration summary (FR-030 / SC-007)

Prove the effective configuration baked into the AMI is printed on the build summary
and that an unresolvable configuration fails the build.

```bash
# 1. Resolve the actual baked-in env file (what ships in the image) → Markdown table.
#    The helper is build tooling under .github/scripts/, run via uv from the repo root.
uv run python .github/scripts/print_config.py \
  --env-file kiwi-descriptions/root/etc/github-actions-remote-executor/env
# Expect: exit 0; "| Setting | Value |" tables grouped into per-category subsections
#   (#### HTTP Server, #### Execution, ... #### Container Security, #### Other last),
#   covering every ServerConfig field exactly once, including the eight security
#   defaults (container_user=65534:65534, ... container_network_mode=none).

# 2. Fail-fast: an invalid baked config aborts before publish.
printf 'SERVER_PORT=8080\nCONTAINER_NETWORK_MODE=nat\n' > /tmp/bad.env
uv run python .github/scripts/print_config.py --env-file /tmp/bad.env
# Expect: non-zero exit; stderr names the offending variable (CONTAINER_NETWORK_MODE).
```

In CI: the `build-and-publish` job's *Print effective configuration to summary* step
appends the table to `$GITHUB_STEP_SUMMARY`; a non-zero exit emits `::error::` and
fails the run **before** *Push artifact to GHCR* (FR-030).

## Manual smoke (optional, requires real rootless Docker + NitroTPM)

```bash
# Defaults
.venv/bin/python -m src.main
# Then run a representative local-compute job; confirm it succeeds under the hardened defaults.

# Network-dependent job under default 'none' is expected to FAIL until you set:
CONTAINER_NETWORK_MODE=bridge .venv/bin/python -m src.main
```

## Done When

- All listed test files pass, including the new `test_security_config_integration.py`.
- `test_attestation_user_data_regression.py` asserts the eight new keys.
- `test_print_config.py` passes: the output covers all `ServerConfig` fields incl. the eight security defaults — grouped into per-category subsections with the catch-all "Other" group last and every field present exactly once — a missing/invalid config exits non-zero, and the real baked env file resolves cleanly (FR-030 / SC-007).
- `.env.example` documents all eight variables with defaults, rationale, and the backward-compat + network-default trade-off callouts.
