# Quickstart / Validation Guide: Configurable Scratch tmpfs Execution

Runnable scenarios that prove the feature end-to-end. See
[data-model.md](./data-model.md) and [contracts/](./contracts/) for the field
details referenced below. Run all commands from the repository root.

## Prerequisites

- Python 3.12 environment per the existing project setup (`uv` / project venv).
- Docker available for the integration scenario (rootless, as in production).
- Feature implemented per [plan.md](./plan.md).

## Scenario 1 — Secure default is `noexec` (US1, SC-001)

```bash
# Resolve config with the variable unset
unset CONTAINER_TMPFS_EXEC
uv run python -c "from src.config import load_config; print(load_config().container_tmpfs_exec)"
# Expected: False
```

Run the unit/property/integration suites that assert the default-off path and that
the produced `/tmp` tmpfs options string is byte-identical to pre-feature output
(no `exec`, `nosuid`/`nodev`/`mode=1777` intact):

```bash
uv run pytest tests/test_config.py tests/test_script_executor.py \
  tests/test_docker_container_properties.py tests/test_security_config_integration.py
```

Expected: a tmpfs-mounted container created with the default config exposes a
non-executable `/tmp`; executing a binary written under `/tmp` fails with
`Permission denied (os error 13)`.

## Scenario 2 — Opt in enables exec (US2, SC-002)

```bash
CONTAINER_TMPFS_EXEC=true \
  uv run python -c "from src.config import load_config; print(load_config().container_tmpfs_exec)"
# Expected: True
```

Integration expectation: with the variable enabled and `CONTAINER_TMPFS_SIZE`
non-empty, the `/tmp` tmpfs mount options include `exec` (string
`size=<size>,mode=1777,exec`), and a build whose `build.rs` is compiled into the
scratch area and executed completes without the `Permission denied (os error 13)`
build-script failure.

## Scenario 3 — Effective value is attested and in the build summary (US3, SC-003)

```bash
# Attestation user_data carries the boolean
uv run pytest tests/test_attestation_user_data_regression.py tests/test_attestation_properties.py

# Build summary renders the field under "Container Security"
uv run pytest tests/test_print_config.py
uv run python .github/scripts/print_config.py \
  --env-file kiwi-descriptions/root/etc/github-actions-remote-executor/env
# Expected: a "Container Security" subsection listing container_tmpfs_exec
```

## Scenario 4 — Invalid value fails fast (SC-004)

```bash
CONTAINER_TMPFS_EXEC=maybe \
  uv run python -c "from src.config import load_config; load_config()" ; echo "exit=$?"
# Expected: non-zero exit; error message names CONTAINER_TMPFS_EXEC
```

## Scenario 5 — Enabled but no tmpfs: warn, do not fail (FR-007, edge case)

```bash
CONTAINER_TMPFS_EXEC=true CONTAINER_TMPFS_SIZE= \
  uv run python -c "from src.config import load_config; c=load_config(); print(c.container_tmpfs_exec, repr(c.container_tmpfs_size))"
# Expected: True ''  — resolves cleanly (no fail-fast)
```

Startup (`main.py`) must log a warning that exec is enabled but has no effect
because no tmpfs is mounted, and no `/tmp` mount is created.

## Scenario 6 — Only exec changes (SC-005)

Assert (covered by `test_docker_container_properties.py` /
`test_security_config_integration.py`) that toggling `CONTAINER_TMPFS_EXEC` changes
**only** the presence of the `exec` option — `size`, `mode=1777`, `nosuid`,
`nodev`, `read_only`, `cap_drop`, `no-new-privileges`, network mode, and limits are
identical between the enabled and disabled containers.

## Done when

- [ ] Scenarios 1–6 pass.
- [ ] `uv run pytest` is green across the touched suites.
- [ ] `.env.example` documents `CONTAINER_TMPFS_EXEC` with its secure default and
      the security implication of enabling it.
