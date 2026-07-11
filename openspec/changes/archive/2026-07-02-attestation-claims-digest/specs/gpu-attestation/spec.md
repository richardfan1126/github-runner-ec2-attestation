## ADDED Requirements

### Requirement: GPU claims block

The attestation claims document SHALL carry a `gpu` block that replaces the bare `gpu_enabled` boolean. When GPU is enabled, the block SHALL be `{ enabled: true, visible_devices, devices: [ … ] }`, where `visible_devices` records the configured `GPU_DEVICES` selection (e.g. `"all"`) and each `devices` entry carries `uuid`, `name`, `driver_version`, `cuda_version`, `vbios_version`, `compute_capability`, and `memory_total_mib`. The `enabled` field subsumes the former `gpu_enabled` boolean for continuity.

#### Scenario: Enabled GPU block populated

- **WHEN** `ENABLE_GPU` is true and an attestation is generated
- **THEN** `claims_raw` contains `gpu.enabled: true`, `gpu.visible_devices` recording the `GPU_DEVICES` selection, and a `gpu.devices` array whose entries each carry `uuid`, `name`, `driver_version`, `cuda_version`, `vbios_version`, `compute_capability`, and `memory_total_mib`

### Requirement: Measured-driver trust semantics

The GPU device fields SHALL be collected at attestation time from the NVIDIA driver via NVML. The driver is installed at image-build time and baked into the sealed erofs root, whose dm-verity roothash is embedded in the PCR4-measured UKI command line, so the driver is bound by PCR4. The claim is therefore a measured-driver self-report — trustworthy only to the extent the measured driver is trustworthy — and SHALL NOT be represented as hardware (silicon/firmware) attestation.

#### Scenario: Fields sourced from the measured driver

- **WHEN** the `gpu.devices` entries are assembled
- **THEN** their values are read via NVML from the PCR4-measured NVIDIA driver, not from caller-supplied input

#### Scenario: Not hardware attestation

- **WHEN** a verifier reads the `gpu` block
- **THEN** it treats the block as a measured-software-stack self-report and does not infer hardware/firmware genuineness of the GPU from it

#### Scenario: Availability, not proof of execution

- **WHEN** a verifier reads the `gpu.devices` array
- **THEN** it treats the entries as the identity of the devices *exposed to* the workload at attestation time, and does NOT infer that the computation actually executed on them (the block is read in the server process at generation time, decoupled from the container's runtime device grant, and a runtime failure could leave the job on CPU)

### Requirement: GPU device set is the workload-visible set

The `gpu.devices` array SHALL describe the set of GPUs exposed to the execution container (the `GPU_DEVICES` → `NVIDIA_VISIBLE_DEVICES` selection), NOT the unfiltered host enumeration, so the attestation cannot over-claim devices the workload never saw. Under the default `GPU_DEVICES=all`, the workload-visible set equals the full NVML host enumeration and the array reflects every enumerated device. If `GPU_DEVICES` is a subset the collector cannot resolve to the emitted device set, the Attestation_Generator SHALL fail closed (attestation error) rather than emit the host enumeration.

#### Scenario: Default all reflects full enumeration

- **WHEN** `GPU_DEVICES` is `all` (the default) and an attestation is generated
- **THEN** `gpu.visible_devices` is `all` and `gpu.devices` contains one entry per NVML-enumerated device on the host, since the workload-visible set equals the host set

#### Scenario: Unresolvable subset fails closed

- **WHEN** `GPU_DEVICES` names a subset the collector cannot resolve to the emitted `gpu.devices` set
- **THEN** attestation generation records an error rather than emitting the unfiltered host enumeration, so a restricted GPU set can never be reported as more devices than the workload could see

#### Scenario: Selection is observable

- **WHEN** the GPU set is restricted below the host set
- **THEN** the restriction is visible in `gpu.visible_devices`, consistent with recording every relaxation in the attestation rather than silently

### Requirement: Per-device array for multi-GPU instances

The `gpu.devices` array SHALL contain one entry per enumerated GPU so that multi-GPU instances are fully described. Ordering and count SHALL reflect what NVML enumerates on the attesting host.

#### Scenario: Multiple GPUs each described

- **WHEN** the attesting instance exposes more than one GPU
- **THEN** `gpu.devices` contains one entry per enumerated device, each with its own `uuid` and version fields

### Requirement: Disabled GPU block shape

When `ENABLE_GPU` is false or unset, the `gpu` block SHALL be exactly `{ enabled: false }` with no `devices` array.

#### Scenario: Disabled block

- **WHEN** `ENABLE_GPU` is false or unset and an attestation is generated
- **THEN** `claims_raw` contains `gpu: { enabled: false }` and no `gpu.devices` array

### Requirement: NVML collection fails closed

When `ENABLE_GPU` is true but NVML cannot enumerate devices at attestation time, the Attestation_Generator SHALL treat this as an attestation error and SHALL NOT emit a `gpu` block with an empty or omitted `devices` array. A verifier SHALL never see `enabled: true` without a populated `devices` array.

#### Scenario: NVML enumeration failure is an attestation error

- **WHEN** `ENABLE_GPU` is true and NVML cannot enumerate any device
- **THEN** attestation generation records an error rather than emitting `gpu.enabled: true` with an empty or missing `devices` array

### Requirement: Reserved nested slot for hardware GPU attestation

The `gpu` block SHALL reserve a nested `gpu.attestation.report_digest` slot for a future NVIDIA hardware GPU-attestation (NRAS) report, referenced via the digest-and-preimage idiom rather than inlined. Until that capability lands, the slot is unpopulated and the block remains a measured-driver self-report.

#### Scenario: Reserved slot referenced by digest when present

- **WHEN** a hardware GPU-attestation report is eventually bound
- **THEN** it is referenced by an algorithm-prefixed `gpu.attestation.report_digest` and the report is not inlined into the `gpu` block

### Requirement: Supported-instance bound

NitroTPM attestation exists only on specific virtualized accelerated-computing instance types (e.g. G4dn, G5, G6, G6e/G6f, Gr6, G7/G7e, and P5/P5e/P5en, P6-B200/B300, P6e-GB200). It is NOT available on P4d/P4de (A100), P3 (V100), or any bare-metal `.metal` SKU. The `gpu-attestation` capability SHALL document this bound: a GPU workload requiring an unsupported accelerator cannot produce this attestation at all, and a verifier reading the `gpu` block SHALL NOT infer support for an accelerator NitroTPM cannot attest.

#### Scenario: Unsupported accelerator cannot attest

- **WHEN** a GPU workload requires an accelerator NitroTPM does not support (e.g. A100, V100, or a bare-metal instance)
- **THEN** no attestation is produced for that workload, and the absence of a `gpu` block for such hardware is not read as a supported-but-disabled GPU
