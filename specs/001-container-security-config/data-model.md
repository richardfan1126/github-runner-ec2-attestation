# Phase 1 Data Model: Container Security Configuration

The feature has no persistent storage. The "data model" is the in-memory configuration entity (`ServerConfig`) and the derived container-creation kwargs. This document specifies the fields, accepted forms, defaults, validation rules, and the resolved values that flow to container creation and attestation.

## Entity: Container security configuration (fields added to `ServerConfig`)

| Field (env var) | ServerConfig attr | Type | Default | Accepted form | Validation (FR) |
|---|---|---|---|---|---|
| `CONTAINER_USER` | `container_user` | `str` | `"65534:65534"` | `uid:gid`, both non-negative ints, both parts required | FR-014 — reject bare uid, missing/negative/non-int part |
| `CONTAINER_ALLOW_ROOT` | `container_allow_root` | `bool` | `False` | strict bool | FR-012 via `parse_strict_bool` |
| `CONTAINER_CAP_ADD` | `container_cap_add` | `list[str] | None` | `None` → 7-cap default set | comma-separated cap names ⊆ allow-list; **unset → None (default set); empty → [] (none added)** | FR-015 — reject any cap outside the 14-cap allow-list |
| `NO_NEW_PRIVILEGES` | `no_new_privileges` | `bool` | `True` | strict bool | FR-012 |
| `CONTAINER_READ_ONLY_ROOTFS` | `container_read_only_rootfs` | `bool` | `True` | strict bool | FR-012 |
| `CONTAINER_TMPFS_SIZE` | `container_tmpfs_size` | `str` | `"256m"` | positive int + optional unit `b`/`k`/`m`/`g`; empty = no tmpfs | FR-016 — reject 0/negative/no-unit/whitespace when non-empty |
| `WORKSPACE_MOUNT_MODE` | `workspace_mount_mode` | `str` | `"ro"` | enum {`ro`,`rw`} | FR-013 |
| `CONTAINER_NETWORK_MODE` | `container_network_mode` | `str` | `"none"` | enum {`none`,`bridge`,`host`} | FR-013 |

### Default capability set (when `CONTAINER_CAP_ADD` unset)
`["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "NET_BIND_SERVICE", "KILL"]`

### Capability allow-list (closed set operators may request)
`{CHOWN, DAC_OVERRIDE, FSETID, FOWNER, MKNOD, NET_RAW, SETGID, SETUID, SETFCAP, SETPCAP, NET_BIND_SERVICE, SYS_CHROOT, KILL, AUDIT_WRITE}` — case-sensitive upper-case, no `CAP_` prefix.

## Cross-field rule: root-user gate (FR-018 – FR-020)

Evaluated in `validate()` after parsing:

| `CONTAINER_USER` resolves to uid | `CONTAINER_ALLOW_ROOT` | Result |
|---|---|---|
| `0` | `false` (default) | **REJECT** — error names both vars, explains conflict (FR-018) |
| `0` | `true` | PERMIT — container runs as root (FR-019) |
| non-zero | `true` | PERMIT — gate only blocks root-while-disallowed (FR-020) |
| non-zero | `false` | PERMIT |

## State / lifecycle

Configuration is resolved once at startup (`ServerConfig.from_env()` → `validate()`), then immutable for the process lifetime. No runtime transitions. Invalid configuration ⇒ `ValueError` → `ConfigurationError` ⇒ process exits non-zero before the listen port is bound (FR-011).

## Resolved → container creation kwargs (FR-021 – FR-023)

`ScriptExecutor` receives the resolved values and maps them onto `docker.containers.create()`:

| Resolved value | `containers.create()` kwarg |
|---|---|
| `container_user` | `user=container_user` |
| `container_cap_add` (None → default 7; [] → []) | `cap_drop=["ALL"]`, `cap_add=<resolved list>` |
| `no_new_privileges` | `security_opt=["no-new-privileges"]` if True, else omit |
| `container_read_only_rootfs` | `read_only=<bool>` |
| `container_tmpfs_size` (non-empty) | `tmpfs={"/tmp": "size=<value>"}` — mounted at the container's standard temp dir whenever size is non-empty, **independent of read-only rootfs** (FR-022) |
| `workspace_mount_mode` | `volumes={host_repo_path: {"bind": "/workspace", "mode": <ro|rw>}}` |
| `container_network_mode` | `network_mode=<value>` |

The capability set applied is **exactly** the resolved list on top of `drop ALL` — no broader (FR-023).

## Attestation user_data fields (FR-026 – FR-027)

Added to `user_data` in `generate_attestation` and `generate_output_attestation` (mirroring `gpu_enabled`):

```
container_user, container_allow_root, container_cap_add (resolved list),
no_new_privileges, container_read_only_rootfs, container_tmpfs_size,
workspace_mount_mode, container_network_mode
```

The resolved `container_cap_add` list (not the raw env string) is surfaced so unset (default 7) vs empty ([]) is unambiguous to a relying party.

`container_allow_root` is sourced **directly from `ServerConfig`** at the attestation call sites — it is a startup root-gate, not a container-creation kwarg (note its absence from the "Resolved → container creation kwargs" table above), so it is not threaded through `ScriptExecutor`. The other seven values, which do shape `containers.create()`, flow through the executor.
