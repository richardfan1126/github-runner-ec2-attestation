## Context

The NitroTPM attestation document is the trust anchor of the Remote Executor: a
verifier reads its `user_data` field to learn *what* was executed (repository,
commit, script, environment) and *under what posture* (the nine-field container
security block), bound to a measured-boot signature over PCR4/PCR7. Today those
fields are packed **inline** into `user_data`, which is hard-capped at **1024
bytes** — `src/attestation.py` rejects any overflow at assembly time. The
current layout already sits close to the ceiling (a long `repository_url` plus an
expanded `CONTAINER_CAP_ADD` can cross it, per the code's own comment), so the
field set is effectively frozen.

Two forces collide with that ceiling:

1. **GPU enrichment (the feature).** Attested GPU workloads (e.g. CI model
   quantization) need the *measured GPU identity* — device name, UUID, driver /
   CUDA / VBIOS versions, per-device — not today's bare `gpu_enabled: true`
   boolean. A per-device array of UUIDs and versions does not fit inline.
2. **Every future claim.** Any new field makes the overflow worse. The inline
   layout has no headroom left to grow.

The system already resolves this exact tension twice with a **digest-and-preimage**
idiom: `server_public_key` stores a SHA-256 fingerprint with the full key
delivered alongside, and `script_env_hash` stores a digest of an external
preimage. This design generalizes that idiom to the whole `user_data`, then
spends the headroom it creates on the GPU claim.

`user_data` is **not** part of measured boot, so this is a software/schema change
only — PCR4/PCR7, the sealed erofs image, `flavors.lock`, and the AMI pipeline
are all untouched. No AMI rebuild.

## Goals / Non-Goals

**Goals:**
- Remove the 1024-byte cap as a growth blocker by moving variable-length claims
  behind a fixed-size digest in `user_data`.
- Add measured GPU identity to the attestation, sourced from the PCR4-measured
  NVIDIA driver via NVML.
- Keep the verifier contract simple and language-agnostic — no cross-language
  JSON canonicalization required to check the binding.
- Preserve the existing security guarantees exactly: everything a verifier trusts
  today stays covered by the TPM signature, directly or through the digest.
- Provide a versioned, forward-compatible envelope so future claims (and a future
  hardware GPU-attestation report) drop in without another wire break.

**Non-Goals:**
- No hardware GPU attestation now (NVIDIA NRAS / H100 CC mode); we only *reserve*
  a nested slot for it. The GPU claim is a measured-driver self-report.
- No change to PCR measurements, the sealed image, flavors, or the AMI pipeline.
- No change to the encryption capability — `claims_raw` rides inside the existing
  sealed response body.
- No backward-compatible dual-format emission; this is a clean BREAKING bump
  gated on `schema_version`.

## Decisions

### D1 — Generalize the digest-and-preimage idiom to all of `user_data`

`user_data` becomes a compact signed **envelope**; the bulky, variable-length
claims move into a **claims document** carried alongside the attestation.

```
   attestation_document  (NitroTPM COSE — signature covers ALL of user_data)
   └── user_data = { v, claims_digest, timestamp, execution_id }   ≪ 1024 B, fixed shape
                          │
                          │  sha256(claims_raw) == claims_digest
                          ▼
   claims_raw  (bytes, in the sealed response body — unbounded)
   └── { schema_version, repository_url, commit_hash, script_path,
         script_env_hash, security{…}, gpu{…} [, output_digest] }
```

**Why over the alternatives:**
- *Keep inlining, just raise the budget* — impossible; 1024 B is a hardware/format
  limit of the NitroTPM `user_data` field, not our choice.
- *Compress inline fields* — buys a little headroom, fragile, still bounded, and
  makes the wire format harder to read/verify. Doesn't survive the GPU array.
- *Digest-and-preimage* — already proven twice in this codebase, unbounded
  preimage, fixed-size on-chain footprint. Chosen.

### D2 — Envelope shape `{ v, claims_digest, timestamp, execution_id }`; keep `execution_id` inline

The TPM signature covers the **whole** `user_data`, not just `claims_digest`. So
any field placed inline is signed. `execution_id` (~36 B) stays inline because it
is the correlation key tying `/execute` → `/execution/{id}/output` → the request,
and inlining lets a verifier answer *"is this attestation for execution X?"* from
the signed envelope alone — **no `claims_raw` fetch or hash required**.

```
  execution_id in claims doc   → bound, but verifier must fetch+hash claims_raw first
  execution_id inline (chosen) → bound AND readable from the signed doc alone
```

`v` is the envelope-format version (distinct from the claims doc's
`schema_version`); `timestamp` stays inline as a cheap, signed freshness marker.

### D3 — Strict envelope/claims partition (no duplication)

Fields that live inline in the envelope are **authoritative there and appear only
there**. The claims document carries strictly the complement. No field is in both
places, so a verifier never has to reconcile two copies or decide which wins.
`execution_id` therefore does **not** reappear inside `claims_raw`.

### D4 — Bind by hashing the transmitted bytes, not a re-canonicalized form

`claims_digest = sha256(claims_raw)` over the **exact bytes transmitted**. The
verifier hashes the bytes it received and compares — it never re-serializes or
canonicalizes the claims document.

**Why:** cross-language JSON canonicalization (key ordering, number formatting,
Unicode escaping, whitespace) is a notorious interop hazard. Requiring every
independent verifier to reproduce our canonical form byte-for-byte would make the
binding fragile and hard to reimplement. Hashing transmitted bytes makes the check
a one-liner in any language.

Canonicalization rules are retained **only for inner sub-digests whose preimage
lives elsewhere** — specifically `script_env_hash` (SHA-256 of the canonicalized
script env, `json.dumps(..., sort_keys=True, separators=(',',':'))`), because that
preimage is not transmitted in `claims_raw` and so must be independently
reconstructable. The reserved `gpu.attestation.report_digest` follows the same
inner-digest pattern.

### D5 — Digest algorithm agility via a prefix

Digests are written `sha256:<hex>`. Verifiers select the hash by prefix and
**reject unknown algorithms**. Only `sha256:` is defined now; the prefix keeps the
door open without a wire break.

### D6 — GPU claim from NVML (PCR4-measured driver); reserved NRAS slot

The `gpu` block replaces the bare boolean:

```
  gpu: {
    enabled: true,
    devices: [ { uuid, name, driver_version, cuda_version,
                 vbios_version, compute_capability, memory_total_mib }, … ]
    // reserved: attestation: { report_digest: "sha256:…" }  ← future NRAS
  }
  // when ENABLE_GPU is false:  gpu: { enabled: false }
```

Fields are read at attestation time from the NVIDIA driver via NVML. The driver is
installed at image-build time (DKMS, baked into the sealed erofs root, whose
dm-verity roothash is embedded in the **PCR4-measured UKI** command line) and is
therefore **bound by PCR4** — the self-report is only as trustworthy as the
measured driver, and the driver *is* measured. `enabled` subsumes the old
`gpu_enabled` for continuity.

**Trust ceiling (explicit):** this attests the *software* GPU stack (driver, CUDA),
not the *silicon/firmware*. Genuine hardware attestation needs NVIDIA's separate
root of trust (NRAS / H100 confidential-compute mode), which is largely
unavailable on AWS today. We do not claim hardware genuineness; we reserve
`gpu.attestation.report_digest` (nested digest-and-preimage, per D4) for when it
lands, rather than inlining a large report.

**Platform support (verified against AWS docs):** NitroTPM attestation is available
only on specific *virtualized* accelerated-computing instance types — G4dn, G5, G6,
G6e/G6f, Gr6, G7/G7e, and P5/P5e/P5en, P6-B200/B300, P6e-GB200 (plus Inf/Trn). It is
**not** available on P4d/P4de (A100), P3 (V100), or any bare-metal `.metal` SKU. A
GPU workload that requires an unsupported accelerator cannot produce this attestation
at all. The `gpu-attestation` spec MUST state this bound so operators/verifiers do not
assume, e.g., an attested A100.

### D7 — `request-encryption` is out of scope

`claims_raw` is application payload placed **inside** the already-sealed response
body. It inherits confidentiality and transport integrity (AES-256-GCM auth tag)
from the existing "seal the body" requirement. No KEM/AEAD/key-schedule change, so
no encryption-spec change. The two guarantees remain independent: AEAD tag =
transport integrity; TPM signature over `claims_digest` = attestation binding.

### D8 — Unify the two attestation builders

The execute-time and output-time builders in `src/attestation.py` collapse to one
envelope shape. They differ only in their claims body: `execution` claims vs
`output` claims, the latter still carrying the `stdout‖stderr‖exit_code` digest
(as `output_digest`, an inner digest per D4).

## Risks / Trade-offs

- **[Attestation is no longer self-describing]** → The verifier contract makes the
  recompute step mandatory and fail-closed: if `claims_raw` is missing or its hash
  ≠ `claims_digest`, the verifier reads *no* claims and rejects. Stripping the
  preimage cannot yield a "trusted-but-empty" read. `claims_raw` must travel
  bundled with the attestation (it does — same sealed response body).
- **[BREAKING wire format]** → All consumers (the `rust-build-demo` caller and any
  downstream verifier) must adopt recompute-and-compare and read from `claims_raw`.
  Mitigated by `schema_version` gating and a coordinated docs/consumer update.
- **[GPU self-report trust ceiling]** → Documented as measured-driver, not
  hardware, attestation; reserved NRAS slot signals the boundary. Verifiers that
  need hardware genuineness must wait for the report and must not over-read the
  software claim.
- **[Two version fields (`v` and `schema_version`)]** → Slight conceptual overhead,
  but they version independent things (envelope format vs claims content) and can
  evolve separately; conflating them would couple unrelated changes.
- **[NVML query failure at attestation time]** → Fail-closed: if `ENABLE_GPU` is
  true but NVML cannot enumerate devices, treat as an attestation error rather than
  emitting an empty/omitted `devices` array that a verifier might misread. (Exact
  behavior to be pinned in the gpu-attestation spec.)
- **[GPU attestation is bounded by NitroTPM instance support]** → The measured GPU
  claim only exists where NitroTPM does (see D6): no A100/V100, no bare-metal. →
  State the supported-instance bound in the `gpu-attestation` spec; a verifier reading
  the `gpu` block must not infer support for an accelerator NitroTPM cannot attest.

## Migration Plan

1. Bump `schema_version` (claims doc) and `v` (envelope) to the new format; emit
   only the new format — no dual emission.
2. Update the verifier contract in the README: recompute `sha256(claims_raw)` →
   compare `claims_digest` → reject unknown `schema_version`/algorithm → read
   fields. Mirror the wording of the existing `server_public_key` fingerprint check.
3. Update the `github-runner-ec2-attestation-rust-build-demo` caller and any
   bundled verifier to the new flow.
4. **Rollback:** revert the software change; because `user_data` is unmeasured and
   no AMI/PCR/flavor artifact changed, rollback is a code revert with no rebuild.

## Open Questions

- **NRAS transport:** when the hardware report exists, where does its *preimage*
  travel — a second alongside blob in the response body, or fetched by the verifier
  from NRAS directly using `report_digest` as the integrity check? (Reserved slot
  is agnostic; transport decided later.)
- **`claims_raw` framing:** does the response label it with an explicit content
  type / length so verifiers hash exactly the right bytes, avoiding any ambiguity
  about surrounding transport encoding (e.g. base64) before hashing?
- **`v` vs `schema_version` bump policy:** which classes of change bump which
  version — to be documented so consumers know what a bump obligates them to do.
- **Freshness under deferred verification:** AWS provides a native `nonce` field for
  live challenge-response, but this architecture verifies attestations *after the fact*
  (the consumer checks later, not in a live handshake), so the platform nonce does not
  map. Freshness therefore rests on the signed inline `timestamp` (D2) plus the
  `execution_id` binding. Is that sufficient anti-replay for downstream consumers, or
  does a consumer-supplied nonce need to thread through at request time?
- **PCR12 / Secure-Boot verifier policy (out of scope, tracked):** the roothash-bearing
  cmdline is measured into **PCR4** via the UKI, so `PCR12` is `0` in normal operation.
  Per AWS advisory GHSA-xrv8-2pf5-f3q7, a verifier/KMS policy should still assert
  `PCR12 == 0` (or enforce UEFI Secure Boot via `PCR7`) to defeat an operator injecting
  a cmdline override that disables dm-verity while leaving PCR4 unchanged. This is a
  *verifier-policy* concern, **not** part of this claims-digest change — flagged here so
  it is not lost; it likely warrants its own change.
