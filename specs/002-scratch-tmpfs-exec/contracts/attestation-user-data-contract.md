# Contract: `container_tmpfs_exec` in attestation `user_data`

Extends the feature-001 attestation `user_data` contract with one field. Applies
to **both** `generate_attestation` (execution attestation) and
`generate_output_attestation` (output attestation).

## Field

| Key | `container_tmpfs_exec` |
|-----|------------------------|
| JSON type | boolean |
| Source | `ServerConfig.container_tmpfs_exec` threaded through `server.py` |
| Inclusion | present **iff** the value is provided (not `None`) to the attestation call, matching every other security field built in `_build_security_user_data` |
| Position | alongside `container_tmpfs_size` within the container-security subset |

## Example (execution attestation `user_data`, security subset abbreviated)

```json
{
  "repository_url": "...",
  "commit_hash": "...",
  "script_path": "...",
  "execution_id": "...",
  "container_read_only_rootfs": true,
  "container_tmpfs_size": "256m",
  "container_tmpfs_exec": false,
  "container_network_mode": "none"
}
```

## Guarantees

- **No drift**: the attested boolean equals the value used to build the `/tmp`
  mount, because both derive from the same `ServerConfig` field passed at the call
  site (FR-008).
- **Size budget**: the field adds ≤ ~30 bytes; the existing 1024-byte NitroTPM
  `user_data` cap and its over-limit error path are unchanged.
- **Backward shape**: callers that do not supply security config (e.g. `/attest`)
  still omit the entire security subset, including this field.
