# Contract: Build-workflow configuration summary (FR-030 / SC-007)

Surfaces the full effective server configuration baked into the AMI on the
`Build Attestable Image` run summary. Two contracts: the `print_config.py`
helper and the workflow step that consumes it.

## 1. `.github/scripts/print_config.py` helper

Build tooling, **not** under `src/` (which is reserved for the executor runtime).
It imports `from src.config import load_config, ServerConfig`; because the repo root
is the executor's uv project (`packages = ["src"]`), running via `uv run` from the
repo root makes `src` importable regardless of the script's location.

**Invocation**

```bash
uv run python .github/scripts/print_config.py --env-file <path-to-env-file>
```

**Inputs**

- `--env-file PATH` (required): an `EnvironmentFile`-style file (the AMI's baked-in
  `kiwi-descriptions/root/etc/github-actions-remote-executor/env`). Parsed with
  systemd-compatible rules: blank lines and lines starting with `#` are ignored;
  each remaining line is split on the first `=`; values are taken verbatim
  (the committed file uses simple unquoted values).

**Behavior**

- Populates `os.environ` from the file, then calls the application's own
  `load_config()` (`ServerConfig.from_env()` + `.validate()`) — the single source
  of truth.
- The set of rows is derived from `dataclasses.fields(ServerConfig)`, so it covers
  **every** field (a superset of the `.env.example` keys, including the eight
  container-security settings). Fields absent from the env file appear with their
  resolved defaults.
- Values are printed **verbatim** — no redaction (the source file is committed in
  the repo; see Clarifications 2026-06-14).

**Output (stdout)** — GitHub-Flavored Markdown table, e.g.:

```markdown
| Setting | Value |
|---|---|
| port | 8080 |
| max_concurrent_executions | 10 |
| ... | ... |
| container_user | 65534:65534 |
| container_allow_root | False |
| container_cap_add | (default 7-cap set) |
| container_network_mode | none |
```

(Labels are `ServerConfig` attribute names — drift-proof; see research Decision 8.)

**Exit codes**

| Condition | Exit | Effect in workflow |
|---|---|---|
| Configuration resolves and validates | `0` | Table appended to summary |
| Missing required var / invalid value / any `ConfigurationError`/`ValueError` | non-zero | Step emits `::error::` and fails the run |

**Import constraint**: imports only from `src.config`; does not bind a port, touch
the NitroTPM, or import FastAPI/Docker.

## 2. Workflow step

Added to the **`build-and-publish`** job, after *Build KIWI image* and **before**
*Push artifact to GHCR* (so failure aborts before publishing):

```yaml
- name: Print effective configuration to summary
  run: |
    ENV_FILE="kiwi-descriptions/root/etc/github-actions-remote-executor/env"
    {
      echo "### Effective Server Configuration (built into image)"
      echo ""
    } >> "$GITHUB_STEP_SUMMARY"
    if ! uv run python .github/scripts/print_config.py --env-file "$ENV_FILE" >> "$GITHUB_STEP_SUMMARY"; then
      echo "::error::Failed to resolve effective configuration from $ENV_FILE"
      exit 1
    fi
```

**Contract guarantees**

- Every run of `Build Attestable Image` shows the effective configuration baked
  into the produced AMI on its summary (SC-007).
- If the configuration cannot be resolved, the run **fails before** any artifact is
  published (FR-030), consistent with the server's fail-fast startup (FR-011).
