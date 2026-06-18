# Contract: `CONTAINER_TMPFS_EXEC` environment variable

Operator-facing configuration contract for the scratch-exec setting. Extends the
feature-001 container-security env contract with one variable.

## Variable

| Name | `CONTAINER_TMPFS_EXEC` |
|------|------------------------|
| Purpose | Permit execution of binaries from the `/tmp` scratch tmpfs |
| Required | No |
| Default (unset) | `false` — scratch is `noexec` (hardened) |
| Resolved field | `ServerConfig.container_tmpfs_exec: bool` |

## Accepted values

Parsed by the shared `parse_strict_bool` (case-insensitive):

| Input | Result |
|-------|--------|
| `true`, `1`, `yes` (any case) | enabled (`exec`) |
| `false`, `0`, `no` (any case) | disabled (`noexec`) |
| unset | disabled (`noexec`) — default |
| anything else | **startup failure** — `ConfigurationError` naming `CONTAINER_TMPFS_EXEC`, raised before the server binds its port |

## Effect on container creation

| Condition | `/tmp` tmpfs mount |
|-----------|--------------------|
| `CONTAINER_TMPFS_SIZE` empty | no tmpfs mounted; this setting has no mount effect. If `CONTAINER_TMPFS_EXEC` is enabled, a **startup warning** is logged; startup does **not** fail. |
| `CONTAINER_TMPFS_SIZE` non-empty, exec disabled | `size=<size>,mode=1777` (Docker default `noexec`, `nosuid`, `nodev`) |
| `CONTAINER_TMPFS_SIZE` non-empty, exec enabled | `size=<size>,mode=1777,exec` (`nosuid`, `nodev` still enforced) |

## Guarantees

- Enabling exec adds **only** the `exec` mount option; `size`, `mode=1777`,
  `nosuid`, `nodev`, and every other container-security control are unchanged.
- An unset variable yields behavior byte-identical to the pre-feature deployment.
- The effective value is observable in startup logs, attestation `user_data`
  (`container_tmpfs_exec`), and the `Build Attestable Image` summary.
