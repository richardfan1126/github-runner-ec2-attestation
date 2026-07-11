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
`schema_version`); `timestamp` stays inline as a cheap, signed staleness marker.

**Freshness is NOT carried by `timestamp`/`execution_id` — they are secondary.**
The primary anti-replay mechanism is the **mandatory per-request client nonce**
(`server.py` requires it on `/execute` and `/output`, validates it, rejects
duplicates via `NonceCache`, and passes it to `nitro-tpm-attest --nonce`, which
binds it in the attestation's **native COSE `nonce` field** — outside `user_data`).
The server chooses `timestamp` and `execution_id`, so a replayer re-presents both
unchanged; only the requester-chosen, cache-checked nonce actually rejects replay.
This envelope refactor touches only `user_data`, so the nonce field is untouched and
freshness is orthogonal — see the "Freshness/anti-replay preserved" requirement,
whose sole obligation on this change is *do not regress it* (keep threading the nonce
through the unified builder into both execution and output attestations, per D8).

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

### D9 — `claims_raw` wire framing: base64 opaque blob, hash after decode

`claims_raw` is transmitted as a **single base64-encoded opaque byte string**, and
`claims_digest = "sha256:" + hex(sha256(decoded_bytes))`. The verifier
base64-decodes the field, hashes the decoded bytes, compares to `claims_digest`,
and only *then* parses the bytes to read fields.

```
  wire:     "claims_raw": "<base64(claims_bytes)>"
  bind:     claims_digest = "sha256:" + hex(sha256(claims_bytes))
  verify:   sha256(base64_decode(field)) == strip("sha256:", claims_digest)   → then parse
```

This is **not a style choice — it is what makes D4 hold.** If `claims_raw` were
embedded as a nested/structured JSON object instead, every JSON codec could
re-serialize it (whitespace, key order, unicode escaping), and "hash the
transmitted bytes" would silently degrade back into the cross-language
canonicalization hazard D4 exists to kill. Framing it as an opaque blob makes the
server-produced bytes authoritative and the check a decode-then-hash one-liner.

**Why this exact shape (precedent):** it mirrors the already-shipped
`server_public_key` fingerprint flow. `encryption.py` computes
`sha256(self._serialized_public_key)` over the **raw serialized bytes** (not the
base64 text); `server.py` sends those bytes base64-encoded; the README verifier
step decodes then hashes. `claims_raw` reuses that house rule verbatim. (One
representational difference: the `server_public_key` fingerprint lives in the
attestation's dedicated binary `public_key` field as raw digest bytes, whereas
`claims_digest` lives inside the JSON `user_data` envelope and is therefore written
as the `sha256:<hex>` string form per D5 — same hash, different container.)

**Corollaries:** (a) length/content-type framing needs no extra delimiter — a
single base64 field decodes to exactly the hashed bytes; (b) content-type (UTF-8
JSON) matters only for *reading* fields after the check, so it can never forge the
binding; (c) the envelope itself needs no such rule — the TPM signs the exact
`user_data` bytes the server wrote, and the verifier reads them out of the COSE
payload without re-hashing, so only `claims_raw` requires the opaque-blob treatment.

### D10 — Version bump policy: `MAJOR.MINOR` claims + strict envelope `v`

The two version fields guard **different verifier capabilities**, so they bump on
different triggers and oblige different consumer reactions:

```
  v (envelope)     guards CAN-I-VERIFY-AT-ALL  → locate claims_digest/execution_id, run binding
  schema_version   guards HOW-DO-I-READ-IT      → interpret the claim fields, once binding passes
```

`schema_version` carries **`MAJOR.MINOR`** semantics; envelope `v` is a plain
breaking integer.

| Change | Bumps | Old consumer must… |
|---|---|---|
| Add an optional, safely-ignorable claim field | `schema_version` **MINOR** | nothing — verify binding, read known fields, **ignore unknown** |
| Remove / rename / re-type / re-mean a field, or add a *required* claim | `schema_version` **MAJOR** | reject reads (interpretation no longer safe) |
| Change envelope shape / inline field / binding rule | `v` | reject before binding (mechanism changed) |

Consumer decision order: unknown `v` → reject; else run binding (decode→hash→compare,
per D9); else unknown `MAJOR` → reject reads; else higher `MINOR` → read known fields,
ignore unknown.

**Why not strict-reject-all (the naïve reading of D5/the first spec draft):** the
core goal (D1) is *"future claims drop in without another wire break."* If every
added field bumps `schema_version` and every consumer rejects an unknown
`schema_version`, the lockstep-upgrade pain just moves from the 1024-byte budget to
the version gate — the growth goal is silently defeated. Splitting MAJOR/MINOR keeps
strict safety for breaking changes while letting additive ones land without a
coordinated upgrade.

**Why this is safe (free property from D4/D9):** because the binding hashes
transmitted bytes and never canonicalizes, **adding a field never breaks the binding
check** — the digest simply covers more bytes. Additive changes are therefore
inherently integrity-safe; the *only* thing an added field can break is field
interpretation, and only for a consumer that chokes on unknown fields. Hence the
linchpin requirement: **consumers MUST ignore unknown fields** within a known MAJOR.
Without that rule, "MINOR = non-breaking" is a lie.

**The load-bearing caveat:** MINOR is for fields that are *additive AND safely
ignorable*. A claim a correct verifier must not miss (e.g. if
`gpu.attestation.report_digest` ever becomes mandatory to check) MUST ship as a
MAJOR, even though it is "just a new field" — otherwise an old consumer would ignore
it and accept an attestation it should reject.

Three independent gates fall out, guarding three independent properties:
`binding check → integrity`, `schema_version MAJOR → semantics`, `envelope v → mechanism`.

### D11 — `output_digest` binds a canonical structured form, not delimiter-glued text

The output claim's inner `output_digest` SHALL be computed over a **canonical
JSON object** `{ stdout, stderr, exit_code }` (`json.dumps(..., sort_keys=True,
separators=(',',':'))`, `sha256:`-prefixed), **not** over the current
delimiter-glued string `f"stdout:{stdout}\nstderr:{stderr}\nexit_code:{exit_code}"`.
`exit_code` stays a JSON number (not stringified) so reconstruction is unambiguous.

**Why:** the glued form uses **in-band delimiters** (`stdout:`, `\nstderr:`,
`\nexit_code:`) that can appear verbatim inside `stdout`/`stderr`, so the map
`(stdout, stderr, exit_code) → bytes` is **not injective** — two distinct triples
collide on the same preimage and therefore the same digest:

```
  stdout="hello\nstderr:oops\nexit_code:1", stderr="",                 exit=0
  stdout="hello",                            stderr="oops\nexit_code:1\nstderr:", exit=0
        └── both serialize to ──▶ "stdout:hello\nstderr:oops\nexit_code:1\nstderr:\nexit_code:0"
```

That is precisely the serialization-ambiguity hazard **D4** exists to kill, one
layer down. `output_digest` is a digest-and-preimage sub-digest of the same kind
as `script_env_hash` — its preimage is reconstructed by the verifier from the
transmitted `stdout`/`stderr`/`exit_code` response fields — so per D4 it keeps a
**canonicalization rule**, and it reuses the *exact* `script_env_hash` rule
(`sort_keys=True, separators=(',',':')`) so verifiers reimplement **one** house
canonicalization, not two. JSON string-escaping makes the mapping injective; the
collision disappears.

**Why not the full D9 opaque-blob treatment:** that would eliminate inner
canonicalization entirely but forces the output onto the wire twice (an opaque
blob *and* the display `stdout`/`stderr`/`exit_code` fields) or moves parsing onto
the caller. D4 already accepts canonicalization for inner sub-digests whose
preimage lives elsewhere; matching `script_env_hash` is the smaller, consistent
change. Chosen.

### D12 — `gpu.devices` is the workload-visible set, not the raw host enumeration

NVML enumerates every GPU on the **host**; the execution container only sees the
subset named by `GPU_DEVICES` (→ `NVIDIA_VISIBLE_DEVICES`, `script_executor.py`).
So there are two different questions:

```
  Q1  "what GPUs were on the attesting HOST?"      → NVML host enumeration
  Q2  "what GPU did MY workload compute on?"       → the container-visible set
```

The proposal's framing — *"the device that computed the result"* — is **Q2**. A
naïve collector that emits the raw NVML host list answers **Q1**, and the two
diverge the moment `GPU_DEVICES ≠ all`: the attestation would list devices the
workload never saw (over-claim).

**Decision:** `gpu.devices` SHALL describe the **workload-visible** set.
- **Default `GPU_DEVICES=all` (the only configuration in use):** the visible set
  *equals* the host enumeration, so the collector emits the full NVML list and
  there is no divergence. Today's behavior is unchanged.
- **Observable selection:** the block records the selection as
  `gpu.visible_devices` (e.g. `"all"`), so *restricting* the GPU set is explicit
  in the attestation — consistent with the house rule that every relaxation is
  observable in `user_data`.
- **Fail closed on divergence:** if `GPU_DEVICES` is a subset the collector cannot
  resolve to the emitted device set, attestation SHALL error rather than emit the
  host list (which would over-claim). This *enforces* the "always all" assumption
  instead of silently trusting it — a future subset config fails loudly, never
  lies. It reuses the existing NVML-fails-closed posture.

**Scope ceiling (words, not code):** `gpu.devices` asserts the **availability and
identity** of the devices *exposed to* the workload — it is **not** proof the
computation executed on them (the nvidia runtime could fail and the job fall back
to CPU while the block still lists GPUs). This is the same class of limit as the
measured-driver-not-hardware ceiling (D6): the attestation is read at generation
time in the server process, decoupled from the container's actual device grant.
Verifiers requiring proof-of-execution-on-device must not over-read the block.

**Why not build the `GPU_DEVICES`-subset filter now:** it is dead code under
`all`. Recording the selection + failing closed on any non-resolvable subset gives
the same safety (no false claim can ship) at a fraction of the cost, and leaves a
clean seam for a real filter if subset configs ever become a use case.

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
- **~~`claims_raw` framing~~ — RESOLVED (see D9):** `claims_raw` is a single
  base64-encoded opaque byte string; the verifier decodes then hashes the decoded
  bytes and compares to `claims_digest` before parsing. No extra content-type/length
  delimiter is needed (one base64 field decodes to exactly the hashed bytes), and it
  mirrors the shipped `server_public_key` fingerprint check.
- **~~`v` vs `schema_version` bump policy~~ — RESOLVED (see D10):** `schema_version`
  is `MAJOR.MINOR` (MAJOR = breaking claims change → reject reads; MINOR = additive,
  safely-ignorable → tolerate + ignore unknown fields); envelope `v` is a strict
  breaking integer → reject before binding. Consumers MUST ignore unknown fields
  within a known MAJOR, and load-bearing claims MUST ship as MAJOR.
- **~~Freshness under deferred verification~~ — RESOLVED (premise corrected):** the
  original framing was wrong — the platform nonce IS used. `server.py` requires a
  per-request client nonce on `/execute` and `/output`, validates it, rejects
  duplicates via `NonceCache`, and binds it in the attestation's native COSE `nonce`
  field. So the *requester* gets genuine live challenge-response freshness; `timestamp`
  and `execution_id` are secondary (staleness + correlation), not the anti-replay
  mechanism (D2 corrected). Two-actor split: the **requester** (the GHA workflow calling
  `/execute`) chose the nonce and is verifying live; a **downstream artifact consumer**
  (pulling the OCI bundle later) did not issue the nonce and does not rely on it for
  freshness — it relies on content-binding (execution claims + `output_digest`) plus
  external Sigstore provenance, and replay can only re-assert a *true* past event because
  content is digest-bound. This change touches only `user_data`, so the nonce field is
  untouched; the only obligation is *do not regress it* (see the "Freshness/anti-replay
  preserved" requirement). A **consumer-issued** nonce for deferred verification is out
  of scope here — the nonce mechanism is unchanged by this change.
- **PCR12 / Secure-Boot verifier policy (out of scope, tracked):** the roothash-bearing
  cmdline is measured into **PCR4** via the UKI, so `PCR12` is `0` in normal operation.
  Per AWS advisory GHSA-xrv8-2pf5-f3q7, a verifier/KMS policy should still assert
  `PCR12 == 0` (or enforce UEFI Secure Boot via `PCR7`) to defeat an operator injecting
  a cmdline override that disables dm-verity while leaving PCR4 unchanged. This is a
  *verifier-policy* concern, **not** part of this claims-digest change — flagged here so
  it is not lost; it likely warrants its own change.
