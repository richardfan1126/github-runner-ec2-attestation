## 1. Claims-document core (`src/attestation.py`)

- [ ] 1.1 Add a `build_claims_document(kind, fields)` helper that assembles the claims dict for `execution` vs `output`, prepends `schema_version` (`MAJOR.MINOR` string), and excludes the envelope-only fields (`v`, `claims_digest`, `timestamp`, `execution_id`) per the strict partition (D3)
- [ ] 1.2 Serialize the claims dict to raw bytes once (`claims_bytes`) and treat those exact bytes as the authoritative preimage — do not re-serialize downstream (D4)
- [ ] 1.3 Compute `claims_digest = "sha256:" + hex(sha256(claims_bytes))` with the `sha256:` algorithm prefix (D5)
- [ ] 1.4 Assemble the compact `user_data` envelope `{ v, claims_digest, timestamp, execution_id }` and assert it stays well under the 1024-byte cap regardless of claims size

## 2. Wire framing for `claims_raw` (D9)

- [ ] 2.1 Base64-encode `claims_bytes` and carry it as a single opaque string field `claims_raw` (NOT a nested JSON object) in the response assembly
- [ ] 2.2 Confirm the digest in 1.3 is computed over the pre-base64 `claims_bytes` (mirroring the shipped `server_public_key` fingerprint flow in `encryption.py`), so decode-then-hash reproduces `claims_digest`
- [ ] 2.3 Add an inline comment cross-referencing the `server_public_key` precedent so the house rule (hash raw bytes, transmit base64) is discoverable

## 3. Versioning (D10)

- [ ] 3.1 Define the envelope-format constant `v` (breaking integer) and the claims `schema_version` (`MAJOR.MINOR`) as named constants with a comment stating the bump-trigger table
- [ ] 3.2 Set the initial `schema_version` MAJOR to reflect this breaking cutover (no dual-format emission per Non-Goals)

## 4. Unify the attestation builders (D8)

- [ ] 4.1 Collapse the execute-time and output-time builders in `src/attestation.py` into one envelope-emitting path that differs only in the claims body
- [ ] 4.2 Move the output digest into the output claims document as an inner `output_digest` field computed over canonical JSON `{ stdout, stderr, exit_code }` (`sort_keys=True, separators=(',',':')`, `sha256:`-prefixed, `exit_code` a JSON number), replacing the delimiter-glued `stdout:…\nstderr:…\nexit_code:…` string (D11) and removing it from inline `user_data`
- [ ] 4.3 Verify the native COSE `nonce` threading (`--nonce`) is preserved unchanged through the unified builder for BOTH execution and output attestations (freshness regression guard — do not touch `user_data` nonce, there is none)

## 5. GPU claims block (`gpu-attestation`)

- [ ] 5.1 Add NVML-based device collection that reads `uuid`, `name`, `driver_version`, `cuda_version`, `vbios_version`, `compute_capability`, `memory_total_mib` per enumerated device
- [ ] 5.2 Emit `gpu: { enabled: true, visible_devices, devices: [...] }` when `ENABLE_GPU` is true, one entry per workload-visible GPU (multi-GPU), replacing the inline `gpu_enabled` boolean
- [ ] 5.3 Emit `gpu: { enabled: false }` (no `devices` array) when `ENABLE_GPU` is false or unset
- [ ] 5.4 Fail closed: if `ENABLE_GPU` is true but NVML cannot enumerate any device, record an attestation error rather than emitting `enabled: true` with an empty/missing `devices` array
- [ ] 5.6 Scope `gpu.devices` to the workload-visible set (D12): record `GPU_DEVICES` as `gpu.visible_devices`; under the default `all` emit the full NVML enumeration; if `GPU_DEVICES` is a subset the collector cannot resolve to the emitted set, fail closed rather than emit the unfiltered host list
- [ ] 5.5 Reserve the nested `gpu.attestation.report_digest` slot (unpopulated) following the inner digest-and-preimage pattern for future NRAS

## 6. Server response assembly (`src/server.py`)

- [ ] 6.1 Include the base64 `claims_raw` field alongside the attestation in the `/execute` response body
- [ ] 6.2 Include `claims_raw` alongside BOTH the stored execution attestation and the fresh output attestation in the `/execution/{id}/output` response body
- [ ] 6.3 Ensure `execution_id` remains readable from the signed envelope in `/execute` (body id) and `/execution/{id}/output` (URL path id) without hashing `claims_raw`
- [ ] 6.4 Confirm `claims_raw` rides inside the existing sealed response body (no `request-encryption` change — D7)

## 7. Config / sourcing (`src/config.py`, `src/main.py`)

- [ ] 7.1 Wire the GPU claim sourcing so NVML collection runs only when `ENABLE_GPU` is true, consistent with existing GPU runtime config
- [ ] 7.2 Retain the existing 1024-byte `user_data` overflow assertion as a safety net (should now never trigger)

## 8. Verifier contract & docs

- [ ] 8.1 Update the README attestation/verifier section: base64-decode `claims_raw` → hash decoded bytes → compare to `claims_digest` → reject unknown `sha256:`/algorithm → reject unknown MAJOR `schema_version` (tolerate higher MINOR, ignore unknown fields) → then read fields; mirror the existing `server_public_key` fingerprint wording
- [ ] 8.2 Document the fail-closed contract: missing `claims_raw` or digest mismatch ⇒ read no fields, reject (no trusted-but-empty read)
- [ ] 8.3 Document the `gpu` block, its measured-driver (not hardware) trust semantics, and the supported-instance bound (no A100/V100/bare-metal)
- [ ] 8.4 Update the `github-runner-ec2-attestation-rust-build-demo` caller / bundled verifier to the recompute-and-compare flow (tracked cross-repo; note the coordinated `schema_version`-keyed rollout)

## 9. Tests & validation

- [ ] 9.1 Unit test: envelope has exactly `{ v, claims_digest, timestamp, execution_id }` and none of the moved claim fields appear inline
- [ ] 9.2 Unit test: decode-then-hash of `claims_raw` reproduces `claims_digest`; tampering with `claims_raw` breaks the check
- [ ] 9.3 Unit test: unknown MAJOR `schema_version` rejected; higher MINOR tolerated with unknown fields ignored; unknown digest algorithm rejected
- [ ] 9.4 Unit test: GPU enabled populates per-device array + `visible_devices`; disabled yields `{ enabled: false }`; NVML failure with `ENABLE_GPU` true is an attestation error; `GPU_DEVICES=all` emits the full enumeration and an unresolvable subset fails closed (no host over-claim)
- [ ] 9.5 Unit test: output attestation uses the same envelope and carries `output_digest` over canonical JSON `{ stdout, stderr, exit_code }`; a delimiter-injection triple (e.g. `stdout` containing `\nexit_code:`) does NOT collide with a different genuine triple; duplicate nonce still rejected on both endpoints
- [ ] 9.6 Run `openspec validate attestation-claims-digest --strict` and confirm the change passes
