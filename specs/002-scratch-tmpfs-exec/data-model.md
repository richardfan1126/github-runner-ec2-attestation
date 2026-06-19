# Phase 1 Data Model: Configurable Execution Permission on the Container Scratch tmpfs

This feature adds **one** field to the existing `ServerConfig` dataclass. There is
no persistent storage and no schema migration; "data model" here means the config
entity and the value's flow through the system.

## Entity: Scratch execution setting (`container_tmpfs_exec`)

| Property | Value |
|----------|-------|
| Owner | `ServerConfig` (`src/config.py`) |
| Type | `bool` |
| Default | `False` (secure-by-default: `noexec`) |
| Source env var | `CONTAINER_TMPFS_EXEC` |
| Env default lookup | `"false"` |
| Parser | `parse_strict_bool(value, "CONTAINER_TMPFS_EXEC")` |
| Accepted inputs | case-insensitive `true`/`1`/`yes` → `True`; `false`/`0`/`no` → `False` |
| Invalid input | raises `ValueError` → `ConfigurationError` at startup, naming the variable (fail fast) |
| Validation rule | none beyond the parser (no cross-field constraint; orthogonal to tmpfs size) |
| Attested as | `user_data["container_tmpfs_exec"]` (boolean), included when not `None` |
| Build summary | rendered under `Container Security` category by `print_config.py` |

### Relationships

- **Pairs with `container_tmpfs_size`** (the sibling field governing the same
  `/tmp` mount). The exec setting only has a *mount* effect when `container_tmpfs_size`
  is non-empty; when empty, the value is still resolved and attested but applies no
  mount change (and triggers a startup warning if enabled).
- **Independent of** `container_read_only_rootfs`, `no_new_privileges`,
  `container_cap_add`, `container_user`, `workspace_mount_mode`,
  `container_network_mode` — enabling exec changes none of them (FR-006, SC-005).

### State / value flow

```text
CONTAINER_TMPFS_EXEC (env)
   │  parse_strict_bool (fail fast on invalid)
   ▼
ServerConfig.container_tmpfs_exec : bool        (src/config.py from_env)
   │
   ├─► ScriptExecutor(tmpfs_exec=…)             (src/server.py ~L330)
   │        └─ self._tmpfs_exec
   │             └─ if self._tmpfs_size:         (src/script_executor.py)
   │                    options = "size=…,mode=1777" + (",exec" if self._tmpfs_exec else "")
   │                    create_kwargs["tmpfs"] = {"/tmp": options}
   │             └─ else: no mount (exec has no effect)
   │
   ├─► generate_attestation(container_tmpfs_exec=…)         (src/server.py ~L839)
   ├─► generate_output_attestation(container_tmpfs_exec=…)  (src/server.py ~L1169)
   │        └─ _build_security_user_data → user_data["container_tmpfs_exec"]
   │
   ├─► main.py startup log: effective value
   │        └─ WARN if container_tmpfs_exec and not container_tmpfs_size
   │
   └─► print_config.py → build summary (Container Security category)
```

### Mount-option truth table (only when a tmpfs is mounted, i.e. size non-empty)

| `container_tmpfs_exec` | `/tmp` tmpfs options string | Executable from `/tmp`? |
|------------------------|-----------------------------|--------------------------|
| `False` (default) | `size=<size>,mode=1777` (Docker default `noexec`) | No (EACCES, os error 13) |
| `True` | `size=<size>,mode=1777,exec` | Yes |

In both rows `nosuid` and `nodev` remain in force (Docker tmpfs defaults, never
relaxed). When `container_tmpfs_size` is empty, no row applies — no mount is created.

### Invariants

- **INV-1**: With the variable unset, the produced `/tmp` tmpfs options string is
  byte-identical to pre-feature output (`size=…,mode=1777`) — no behavior change.
- **INV-2**: Enabling exec adds exactly the substring `,exec` and nothing else.
- **INV-3**: The attested `container_tmpfs_exec` boolean equals the value used to
  build the mount (no drift between reported and effective behavior).
