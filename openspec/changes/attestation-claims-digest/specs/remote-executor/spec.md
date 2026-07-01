## MODIFIED Requirements

### Requirement: Attestation document generation and execution initiation

When a script is successfully retrieved, the Attestation_Generator SHALL generate a NitroTPM-signed Attestation_Document including PCR measurements and a `user_data` carrying the compact signed claims-digest envelope `{ v, claims_digest, timestamp, execution_id }` (per the `attestation-claims` capability), store it against the Execution_ID, and include the corresponding `claims_raw` claims document in the response body; the Script_Executor SHALL then initiate execution. `user_data` SHALL NOT carry the documented claim fields inline — they move into `claims_raw`, bound by `claims_digest`.

#### Scenario: Envelope and claims document present

- **WHEN** an execution attestation document is generated
- **THEN** its `user_data` carries the `{ v, claims_digest, timestamp, execution_id }` envelope, and the response body carries a `claims_raw` claims document that hashes to `claims_digest` and contains `repository_url`, `commit_hash`, `script_path`, `script_env_hash`, and the `security` posture block
- **AND** `script_env_hash` is the SHA-256 hex digest of the canonicalized `script_env` (keys sorted, JSON with no whitespace), or of `{}` when `script_env` is empty

#### Scenario: execution_id binds the attestation

- **WHEN** the `/execute` response and any `/execution/{id}/output` response include an attestation document
- **THEN** the `execution_id` in its signed `user_data` envelope matches the execution record (the response-body id for `/execute`, the URL path id for output polling), readable from the signed envelope without hashing `claims_raw`

#### Scenario: Attestation failure recorded

- **WHEN** attestation generation fails
- **THEN** the server records an attestation error for the Execution_ID

### Requirement: Output polling endpoint

The GHA_Server SHALL provide an HTTP POST `/execution/{id}/output` endpoint (POST because the body carries an encrypted payload) that, regardless of execution status (running, completed, failed, timed_out), returns HTTP 200 with the current status, Script_Output, the stored Attestation_Document, and a freshly generated Output_Attestation_Document. Both attestation documents SHALL be accompanied by their `claims_raw` preimages so the caller can perform the binding check.

#### Scenario: Output and attestations returned

- **WHEN** the output endpoint is polled for an existing execution
- **THEN** the response includes stdout, stderr, exit code, the base64 Attestation_Document, and a base64 Output_Attestation_Document whose signed `user_data` envelope carries the `execution_id` and whose accompanying `claims_raw` carries an `output_digest` over the current Script_Output (`stdout‖stderr‖exit_code`)

#### Scenario: Unknown execution

- **WHEN** the Execution_ID does not exist
- **THEN** the server returns HTTP 404 Not Found

#### Scenario: Output attestation failure is non-fatal

- **WHEN** Output_Attestation_Document generation fails
- **THEN** the server still returns the Script_Output and Attestation_Document with an error field indicating attestation failure

#### Scenario: Results retained

- **WHEN** an execution completes
- **THEN** results are retained for at least 1 hour

### Requirement: GPU passthrough at runtime

When `ENABLE_GPU` is true, the Script_Executor SHALL create each Execution_Container with `runtime="nvidia"` and the `NVIDIA_VISIBLE_DEVICES` (from `GPU_DEVICES`, default `all`) and `NVIDIA_DRIVER_CAPABILITIES` (default `compute,utility`) environment variables, using CDI mode exclusively. When `ENABLE_GPU` is false, no GPU access SHALL be granted. All existing container security constraints SHALL remain enforced.

#### Scenario: GPU enabled

- **WHEN** `ENABLE_GPU` is true and a container is created
- **THEN** `runtime="nvidia"` and the server-configured `NVIDIA_VISIBLE_DEVICES`/`NVIDIA_DRIVER_CAPABILITIES` are set (overriding any caller-supplied values), without adding `/dev/nvidia*` mappings, `SYS_ADMIN`, or `--privileged`

#### Scenario: GPU disabled

- **WHEN** `ENABLE_GPU` is false or unset
- **THEN** the container is created with no `runtime="nvidia"` and no NVIDIA environment variables, and has no GPU access

#### Scenario: Startup verification when enabled

- **WHEN** `ENABLE_GPU` is true at startup
- **THEN** the server verifies the `nvidia` runtime is registered (failing to start otherwise), warns if no CDI specs are found, and verifies functionality by creating and removing a test `runtime="nvidia"` container

#### Scenario: GPU posture attested

- **WHEN** GPU is enabled or disabled
- **THEN** the attestation records the GPU posture in the `gpu` claims block of `claims_raw` (per the `gpu-attestation` capability): `{ enabled: true, devices: [ … ] }` when enabled or `{ enabled: false }` when disabled, in place of the former inline `gpu_enabled` boolean
