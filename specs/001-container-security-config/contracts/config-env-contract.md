# Contract: Container-Security Environment Variables

The executor's external configuration contract is the set of environment variables it reads at startup. This feature adds eight. All are optional; each has a secure default. Invalid values cause the process to exit non-zero during configuration load, before the HTTP listener binds (consistent with existing config validation).

## Variables

| Variable | Default | Accepted values | Reject (fail-fast) examples |
|---|---|---|---|
| `CONTAINER_USER` | `65534:65534` | `uid:gid`, both non-negative integers, both present | `1000` (no gid), `1000:`, `-1:0`, `root:root`, `0:0` when `CONTAINER_ALLOW_ROOT≠true` |
| `CONTAINER_ALLOW_ROOT` | `false` | `true,1,yes,false,0,no` (case-insensitive) | `maybe`, `2` |
| `CONTAINER_CAP_ADD` | *(unset → default 7-cap set)* | comma-separated, each ∈ allow-list (14 caps); empty string = add none | `SYS_ADMIN`, `cap_chown` (wrong form), `chown` (wrong case) |
| `NO_NEW_PRIVILEGES` | `true` | strict bool | `on` |
| `CONTAINER_READ_ONLY_ROOTFS` | `true` | strict bool | `readonly` |
| `CONTAINER_TMPFS_SIZE` | `256m` | positive int + optional unit `b`/`k`/`m`/`g`; empty = no tmpfs | `256`, `0m`, `-5m`, `256mb`, `256 m`, `big` |
| `WORKSPACE_MOUNT_MODE` | `ro` | `ro`, `rw` | `readwrite`, `RW` (case/whitespace), `r` |
| `CONTAINER_NETWORK_MODE` | `none` | `none`, `bridge`, `host` | `nat`, `bridge ` (whitespace), `None` |

**Capability allow-list (14):** `CHOWN, DAC_OVERRIDE, FSETID, FOWNER, MKNOD, NET_RAW, SETGID, SETUID, SETFCAP, SETPCAP, NET_BIND_SERVICE, SYS_CHROOT, KILL, AUDIT_WRITE`
**Default granted set (7):** `CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, NET_BIND_SERVICE, KILL`

## Error contract

- Each validation error message MUST name the offending variable and state the accepted values/format (FR-017), e.g.
  `Invalid CONTAINER_NETWORK_MODE value: 'nat'. Accepted values: none, bridge, host`.
- The root-while-disallowed conflict names **both** variables:
  `CONTAINER_USER resolves to root (uid 0) but CONTAINER_ALLOW_ROOT is false. Set CONTAINER_ALLOW_ROOT=true to permit running as root.`
- Errors surface as `ConfigurationError` (wrapping `ValueError`) and abort startup before the listen socket binds.

## Behavioral guarantees

- **Hardened-by-default**: with none of the eight set, the container runs as `65534:65534`, read-only rootfs + `256m` tmpfs scratch, read-only `/workspace`, `no-new-privileges` on, `cap_drop ALL` + the 7-cap set, `network=none`.
- **Unset vs empty `CONTAINER_CAP_ADD`**: unset ⇒ default 7-cap set; empty ⇒ no caps added on top of `drop ALL`.
- **Empty `CONTAINER_TMPFS_SIZE`**: no tmpfs mounted (explicit, not overridden), even when rootfs is read-only.
