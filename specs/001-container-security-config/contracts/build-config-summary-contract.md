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
  `kiwi-descriptions/root/etc/github-actions-remote-executor/env`). Parsed with a
  practical subset of systemd `EnvironmentFile=` rules: blank lines and lines whose
  first non-whitespace character is `#` or `;` are ignored as comments; each
  remaining line is split on the first `=`; a single matched pair of surrounding
  single or double quotes is stripped from the value (unquoted/unbalanced values
  kept verbatim). Backslash line-continuation and in-value escapes are not handled.

**Behavior**

- Populates `os.environ` from the file, then calls the application's own
  `load_config()` (`ServerConfig.from_env()` + `.validate()`) — the single source
  of truth.
- The full field set is derived from `dataclasses.fields(ServerConfig)`, so it covers
  **every** field (a superset of the `.env.example` keys, including the eight
  container-security settings). Fields absent from the env file appear with their
  resolved defaults.
- Settings are **grouped by configuration category** into labeled subsections, not a
  single flat table (Clarifications 2026-06-14; research Decision 11). Grouping is
  driven by an ordered `CONFIG_CATEGORIES` map (category label → ordered field names).
  Each category renders as a `####` heading + its own `| Setting | Value |` table, in
  map order. Any field not listed in the map falls into a catch-all **"Other"**
  subsection rendered **last** (only when non-empty), so every field still appears
  exactly once and no field is ever silently dropped — grouping stays drift-proof.
- Values are printed **verbatim** — no redaction (the source file is committed in
  the repo; see Clarifications 2026-06-14).

**Output (stdout)** — GitHub-Flavored Markdown, grouped subsections, e.g.:

```markdown
#### HTTP Server

| Setting | Value |
|---|---|
| port | 8080 |

#### Execution

| Setting | Value |
|---|---|
| max_concurrent_executions | 10 |
| ... | ... |

#### Container Security

| Setting | Value |
|---|---|
| container_user | 65534:65534 |
| container_allow_root | False |
| container_cap_add | (default 7-cap set) |
| no_new_privileges | True |
| container_read_only_rootfs | True |
| container_tmpfs_size | 256m |
| workspace_mount_mode | ro |
| container_network_mode | none |

#### Other

| Setting | Value |
|---|---|
| ... any field not yet assigned a category ... |
```

(Subsection headings are category labels; row labels are `ServerConfig` attribute
names — drift-proof; see research Decisions 8 & 11.)

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
  into the produced AMI on its summary (SC-007), grouped into per-category
  subsections in a stable map-defined order with the catch-all "Other" group last.
- If the configuration cannot be resolved, the run **fails before** any artifact is
  published (FR-030), consistent with the server's fail-fast startup (FR-011).
