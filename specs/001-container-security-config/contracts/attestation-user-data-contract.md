# Contract: Attestation `user_data` — Container-Security Fields

The effective container-security posture is bound into the attestation `user_data` JSON produced by `nitro-tpm-attest`, via `AttestationGenerator.generate_attestation()` (execute path) and `.generate_output_attestation()` (output path). This extends the existing `user_data` object the same way `gpu_enabled` was added — additive, backward-compatible.

## Added fields

All eight are added to the `user_data` object when the security configuration is passed in (always, for `/execute` and `/output` responses):

| Field | JSON type | Example | Source |
|---|---|---|---|
| `container_user` | string | `"65534:65534"` | resolved `container_user` |
| `container_allow_root` | boolean | `false` | resolved `container_allow_root` |
| `container_cap_add` | array[string] | `["CHOWN","DAC_OVERRIDE","FOWNER","SETUID","SETGID","NET_BIND_SERVICE","KILL"]` | **resolved** list (default 7 when unset; `[]` when explicitly empty) |
| `no_new_privileges` | boolean | `true` | resolved `no_new_privileges` |
| `container_read_only_rootfs` | boolean | `true` | resolved `container_read_only_rootfs` |
| `container_tmpfs_size` | string | `"256m"` | resolved `container_tmpfs_size` (`""` when no tmpfs) |
| `workspace_mount_mode` | string | `"ro"` | resolved `workspace_mount_mode` |
| `container_network_mode` | string | `"none"` | resolved `container_network_mode` |

## Guarantees

- **Distinguishability (SC-004)**: a relying party determines the effective value of all eight settings from `user_data` and compares each to the documented default; any relaxation is visible. `container_cap_add` is the resolved list, so unset (default 7) vs empty (`[]`) is unambiguous.
- **Backward compatibility**: existing `user_data` keys (`repository_url`, `commit_hash`, `script_path`, `script_env_hash`, `timestamp`, `execution_id`, `gpu_enabled`) are unchanged; the eight keys are added alongside.
- **Two surfaces stay consistent**: the execute-time attestation and the output attestation carry the same eight values for a given execution.

## Compatibility note for verifiers

Consumers MUST treat unknown/extra `user_data` keys as non-fatal (the document is an open JSON object). Verifiers that pin an exact key set must be updated to include the eight keys above. See `tests/test_attestation_user_data_regression.py` for the pinned-shape regression test that must be extended.
