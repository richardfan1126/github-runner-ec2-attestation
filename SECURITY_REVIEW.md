# Security Review

Date: 2026-07-22 (refresh of 2026-06-27 review)

## Scope and method of this pass

This refresh re-read the full server runtime source on `main` at `75b35ce`
(`src/main.py`, `server.py`, `validation.py`, `encryption.py`, `attestation.py`,
`script_executor.py`, `repository.py`, `nonce_cache.py`, `output_collector.py`,
`execution_manager.py`, `config.py`, `models.py`). The `/execute` and `/output`
request paths were traced end to end.

Prior findings S1–S3 were re-verified and remain open (S1, S3 confirmed against
current code; S3 additionally leaks to the caller, not just logs — see below).
S4 (infra/build chain) is again carried forward un-inspected. Four new findings
(S5–S8) were identified in the output-attestation / streaming path and the
key-exchange design.

The archived OpenSpec changes under `openspec/changes/archive/` were also mined
this pass. That review surfaced two verifier-contract gaps (S9, S10) around the
attestation trust anchor — the design of `attestation-claims-digest` explicitly
flagged the PCR12/Secure-Boot item as "out of scope, tracked, likely its own
change" and it was never implemented or written into the verifier contract.

The **consumer repository** `github-runner-ec2-attestation-rust-build-demo` was also
reviewed in this pass; its findings (workflow expression injection, fail-open attestation
primitives, etc.) live in that repository's own `SECURITY_REVIEW.md` and are not
duplicated here.

Most High/Medium findings from the 2026-05-15 review are now fixed in code and have
been moved to **Resolved Findings** below. Several prior findings that live in files
**not re-inspected in this pass** (Terraform under `terraform/`, `scripts/build-ami.py`,
`.github/scripts/build-kiwi-image.sh`, `kiwi-descriptions/appliance.kiwi`) are carried
forward as **Open (not re-verified)** so they are not silently dropped — they should be
re-checked against current code.

## Open Findings — Server repository

### S1. Medium: JWKS cache has no TTL (carried over, still open)

- `src/validation.py` caches the GitHub OIDC JWKS in `_jwks_cache` and only refreshes on
  a `kid` lookup miss (`_find_signing_key`); there is no time-based expiry.
- Required hardening: add TTL-based JWKS refresh and honor cache-control where practical.
- Impact: key rotation or revocation is not reflected promptly in a long-running process.

### S2. Medium: default deployment is plaintext HTTP on `0.0.0.0` with shared audience (carried over)

- `src/main.py` binds `0.0.0.0` and the service speaks HTTP. The default AMI env bakes a
  non-instance-specific `EXPECTED_AUDIENCE`.
- The attestation-bound hybrid-KEM channel means a network MITM cannot read or forge
  payloads (it would need the attested server private key) and nonces block replay, so
  confidentiality and integrity hold even over HTTP — but request metadata/existence
  leaks and overall safety depends on deployment networking and an instance-specific
  audience.
- Required hardening: private networking, TLS, and an instance-specific audience as
  deployment requirements.

### S3. Medium: some attestation/error paths still return or log raw subprocess stderr (carried over, partially addressed)

- `src/repository.py` clone paths now route stderr through `sanitize_log_message` /
  `truncate_field`, and `/execute` clone errors return categorized codes via
  `_categorize_clone_error` rather than raw stderr — a clear improvement over the prior
  review.
- However, `src/attestation.py generate_output_attestation()` still returns raw
  `nitro-tpm-attest` stderr in its error string (`attestation.py:673-675`,
  `f"Output attestation failed ...: {stderr_text}"`).
- Confirmed this pass: that raw string does not stay internal. `server.py:1200`
  assigns it to `response_data["attestation_error"]`, which is then AES-GCM
  encrypted and returned to the caller. So raw subprocess stderr reaches both the
  server log and the (semi-trusted) caller. By contrast the `/execute`
  attestation path is clean — it returns a generic message and logs only
  `attestation_error.context` (no raw stderr), so the fix belongs solely in the
  output path.
- Required hardening: never include raw tool stderr in returned/loggable error strings;
  keep a sanitized category externally and full detail only in internal diagnostics.

### S5. Medium (new): output truncation is silent — never surfaced in the response or bound in attestation

- `OutputCollector` caps combined stdout+stderr at `max_output_size_bytes` (10 MB
  default) and sets `OutputBuffer.truncated` / `OutputData.truncated = True` once
  the cap is hit (`output_collector.py:63-77`).
- That flag is dropped on the way out: `get_execution_output()` builds
  `response_data` from `output_data` but never copies `truncated`
  (`server.py:1140-1168`), and `generate_output_attestation()` computes
  `output_digest` over `{stdout, stderr, exit_code}` with **no** truncation
  indicator in the claims document (`attestation.py:587-597`).
- Impact: a consumer that verifies the output attestation cannot distinguish a
  complete result from one silently capped at 10 MB. The signed `output_digest`
  covers only the truncated bytes, so the attestation *looks* authoritative while
  hiding that data was dropped. This is an integrity gap for the "output
  exfiltration/integrity" threat: a workload that emits >10 MB (or a malicious one
  that pads output to bury a line past the cap) yields a trusted-looking but
  incomplete attested output.
- Required hardening: include `truncated` in the `/output` response **and** in the
  output claims document so it is bound by `claims_digest`; verifiers should treat
  `truncated: true` as "output incomplete".

### S6. Low (new): single `offset` applied to two independently-growing streams corrupts incremental reads

- `/output` accepts one integer `offset` and `OutputCollector.get_output()` slices
  **both** buffers with it: `buffer.stdout[offset:]` and `buffer.stderr[offset:]`
  (`output_collector.py:129-130`). The response returns *separate*
  `stdout_offset` and `stderr_offset` (each buffer's length).
- Because stdout and stderr grow independently, there is no single `offset` the
  client can send on the next poll that correctly advances both streams. Whatever
  value it picks re-sends part of one stream and/or skips part of the other, so a
  client polling incrementally will duplicate or drop output — and each chunk is
  individually attested, so the corruption is inside signed data, not just a
  display glitch.
- Impact: incremental output reassembly is unreliable whenever the two streams
  differ in length (the normal case). Correctness bug with output-integrity
  consequences.
- Required hardening: track and accept per-stream offsets (`stdout_offset` /
  `stderr_offset`) rather than one shared `offset`.

### S7. Low (new): no forward secrecy — static server KEM keypair for the process lifetime

- `EncryptionManager.__init__` generates one X25519 + one ML-KEM-768 keypair at
  startup and holds them in memory for the whole process (`encryption.py:48-78`).
  Every session's `Shared_Key` is derived from these static server secrets plus
  the client's ephemeral material.
- Impact: there is no forward secrecy. An adversary who records ciphertext now and
  later extracts the server private keys (memory disclosure, post-mortem core, or
  full host compromise) can decrypt **all** previously recorded sessions.
- This is partly inherent: the `/attest` flow binds the server public-key
  fingerprint into the attestation, so the key cannot rotate per request without
  breaking attest→execute continuity. Noted as a residual design property, not a
  defect — but it should be an explicit, documented assumption, and key rotation
  on a schedule (with re-attestation) would bound the exposure window.

### S8. Low (new): concurrency limit is enforced only after the clone completes

- `try_create_execution()` atomically enforces `max_concurrent_executions`
  (`execution_manager.py:132-147`), but in `server.py` it is called only *after*
  `repo_client.clone_repo(...)` (`server.py:753` vs `:826`). The concurrency gate
  therefore bounds container executions, not the clone stage.
- Impact: an authenticated caller (valid OIDC + repo/commit binding) can drive
  many parallel `git clone` operations — disk and network amplification — before
  any are rejected at the container gate. Bounded by per-IP rate limiting, so this
  is minor and requires valid credentials.
- Required hardening: reserve a concurrency slot (or a separate clone-stage
  semaphore) before starting the clone, releasing it on early failure.

### S9. High (new, from archive): documented verifier contract never pins PCR4 to the expected known-good value

- The whole trust model rests on tracing a live attestation's **PCR4 → the
  producing GHA run → the commit** (`bake-image-into-ami/design.md:76-79, 281-282`;
  matches the project's own stated trust anchor). The build records the expected
  `pcr4`/`pcr7` per flavor (`edit_boot_install.sh` via `nitro-tpm-pcr-compute`,
  surfaced in `verifier_record` and `flavors.lock`, `README.md:224-238`).
- But the **client-facing verification flow the README documents never uses those
  values.** The `/attest` client flow (`README.md:310-311`) says only "Verify the
  attestation document against the NitroTPM root of trust. Confirm the SHA-256
  fingerprint of the returned `server_public_key`…", and the "Verifying the
  binding" contract (`README.md:356-365`) covers only the `claims_digest` →
  `claims_raw` hash check. Neither step tells the consumer to compare the
  attestation's **PCR4** (nor PCR7) against the known-good per-flavor value from
  `flavors.lock`.
- Impact: a consumer that follows the documented flow validates the COSE signature
  chain and the claims binding but **not which image booted**. Any validly-signed
  NitroTPM attestation — from a different instance in the account, or an instance
  booted from a different/malicious image whose PCR4 differs — passes every
  documented check. The encrypted channel would be established with, and attested
  output trusted from, an unverified boot state. This nullifies the attestation's
  core purpose (proving *this* reviewed image is running) even though every
  building block to prevent it (recorded PCR4, live PCR4 in the doc) exists.
- Required hardening: make PCR4 (and PCR7) comparison against the recorded
  known-good value a MUST step in the documented verifier contract and in any
  bundled verifier; reject on mismatch before trusting `server_public_key` or any
  claim field.

### S10. Medium (new, from archive): verifier contract omits the PCR12==0 / Secure-Boot (PCR7) assertion (GHSA-xrv8-2pf5-f3q7)

- The dm-verity roothash that seals the root is measured into PCR4 via the UKI
  kernel command line. Per AWS advisory **GHSA-xrv8-2pf5-f3q7**, an operator who
  appends a boot-time cmdline override that disables dm-verity (or repoints the
  root) may not disturb PCR4; the defense is for the verifier to assert
  **`PCR12 == 0`** (no unexpected cmdline extension) and/or enforce **UEFI Secure
  Boot via a pinned `PCR7`**.
- The `attestation-claims-digest` design itself raised this
  (`design.md:424-430`) and explicitly deferred it — "*a verifier-policy concern,
  not part of this change … likely warrants its own change*". Grep confirms PCR12
  appears **nowhere** in the repo except that one deferred note: no spec, no
  README verifier step, no code. PCR7 is recorded at build time but the verifier
  contract never requires asserting it to a known-good value.
- Impact: even a consumer that adds the S9 PCR4 pin can be fooled by a privileged
  operator disabling dm-verity through an unmeasured cmdline path, since PCR4
  alone does not capture it. The measured-boot integrity guarantee is incomplete
  without the PCR12/PCR7 assertion.
- Required hardening: add `PCR12 == 0` (or a pinned Secure-Boot `PCR7`) to the
  verifier contract and any bundled verifier, and document the requirement
  alongside the PCR4 pin from S9.

### S4. Open (not re-verified this pass): infrastructure / build-chain findings

The following 2026-05-15 findings live in files not re-inspected in this refresh and are
carried forward verbatim pending re-verification against current code:

- AWS GitHub Actions role trust is repo-wide and `workflow_dispatch` AMI builds are not
  branch-restricted (`terraform/github-actions-iam-role`, `build-attestable-image.yml`).
- OIDC policy is repo-scoped with no server-side `workflow_ref` / `job_workflow_ref`
  allow-list, and production defaults for `ALLOWED_BRANCHES` / `REQUIRE_PROTECTED_REF`
  are not asserted.
- Artifact provenance verification (`--expected-workflow`) defaults to `None` outside the
  CI wrapper (`scripts/build-ami.py`).
- Build-time source supply chain has unsigned source inputs (rootlesskit, slirp4netns,
  fuse-overlayfs, coldsnap) (`.github/scripts/build-kiwi-image.sh`, `build-ami.py`).
- AMI build instance SSH private key is exported through Terraform outputs/state
  (`terraform/build-ami`).
- SSH host key not verified during AMI build (`paramiko.AutoAddPolicy()` in
  `build-ami.py`).
- Runtime AMI includes packages not justified for executor runtime
  (`kiwi-descriptions/appliance.kiwi`).
- User-controlled log fields not consistently bounded/escaped (partially mitigated by
  `truncate_field` / `sanitize_nonce_for_logging`).

## Resolved Findings

### Remediated since the 2026-05-15 review (verified in this pass)

| 2026-05-15 # | Severity | Description | Resolution (current code) |
|---|---|---|---|
| 1 | High | Requested commit not bound to OIDC `sha` claim | `validation.validate_commit_hash_binding()` and `server.py` reject `/execute` unless `commit_hash` equals the token `sha` claim (case-insensitive). |
| 3 | High | Runtime image verified at startup but executed by mutable tag | `script_executor` binds every `containers.create()` to `execution_image_ref` (the derived config-digest image ID) via `load_baked_image()` verify→derive→load→bind; immutable ref computed in `_compute_immutable_image_ref`. |
| 4 | High | Real `ScriptExecutor` not given the verified digest | `create_app()` passes `container_image_digest` and `bound_image_id`; `main.py` derives the image ID and threads it through. |
| 9 | Medium | GitHub token in `git clone` argv | `repository.py` uses a `GIT_ASKPASS` helper (`_create_askpass_helper`); token no longer placed in argv. |
| 10 | Medium | Nonce and base64 validation too permissive | `_validate_nonce_strict` enforces str type, 16–256 length, URL-safe charset; all `b64decode(..., validate=True)`. |
| 12 | Medium | Execution container sandbox gaps | Defaults: non-root `user=65534:65534`, `read_only` rootfs, `cap_drop=ALL` with bounded `cap_add`, `network_mode=none`, `tmpfs` `noexec`; relaxations are validated and attested. |
| 13 | Medium | No container PID limit | `containers.create(pids_limit=config.container_pids_limit)` (default 256). |
| 19 | Medium | Output polling attests on every request | `OutputAttestationRateLimiter.check_and_record()` gates TPM work per execution. |
| 20 | Medium | Service starts when NitroTPM unavailable | `main.py` returns non-zero unless `ALLOW_NO_TPM=true` (explicit dev/test opt-in). |
| 17 (partial) | Medium | Raw subprocess stderr in clone error paths | Clone logging sanitized; encrypted error envelopes return categorized codes, not raw stderr. (Output-attestation path still open — see S3.) |

### Previously resolved (from earlier review passes, unchanged)

The 2026-04-18 remediation table from the prior review remains valid; see git history for
the full list (OIDC repository binding, `MAX_CONCURRENT_EXECUTIONS` enforcement, bounded
output buffers and periodic cleanup, token stripping from `.git/config`, script-size
enforcement, replay-protecting nonce cache, execution/output attestation bound to
`execution_id`, `artifact_ref` shell-injection allowlist, contextvars logging,
shared-key cleanup, debug-image annotation gate, encrypted post-decryption errors,
symlink-safe script validation, digest-pinned AMI verify/pull, `--require-hashes` wheel
installs, SHA-pinned actions/base images, rootless Docker socket, GPG-verified Rust,
`script_env` deny-list, bounded duration/rate-limiter structures).

## Threat Model

The trust assumptions from the prior review still hold and are unchanged; the consumer
repository is now in scope as a **trusted AMI consumer** that must verify the NitroTPM
attestation chain, PCR values, nonce freshness, server public-key fingerprint, attested
request metadata, `execution_id`, `script_env_hash`, and output digest before relying on
results.

- Trusted operators: AWS and repository administrators.
- Trusted build workflow: only the reviewed AMI build workflow on a protected branch.
- Trusted caller workflows: only explicitly authorized GitHub Actions workflows.
- Partially trusted repository maintainers: branch protection and workflow identity
  determine production trust.
- Untrusted network clients: hostile until encryption, OIDC, nonce, and repo allow-list
  checks pass. The attestation-bound encrypted channel keeps payload confidentiality and
  integrity even on plaintext HTTP.
- Untrusted script code: adversarial re: container escape, egress, output exfiltration,
  and resource exhaustion.
- Trusted infrastructure roots: AWS NitroTPM, GitHub OIDC/Sigstore, pinned package
  repositories and source artifacts, Terraform provider hashes.
- Out of scope unless explicitly hardened: compromised AWS/GitHub admin, compromised
  GitHub-hosted runner, or code that escapes the execution container.

## Coverage Gaps

- No test asserts JWKS cache entries expire (S1).
- No test or deployment safeguard requires TLS, private networking, or an
  instance-specific audience (S2).
- No test asserts the output-attestation error path omits raw tool stderr from
  either the log or the encrypted `attestation_error` field returned to the caller (S3).
- No test asserts `truncated` is surfaced in the `/output` response or bound in the
  output claims document (S5).
- No test covers incremental `/output` retrieval when stdout and stderr differ in
  length, so the shared-offset corruption (S6) is uncaught.
- No test or doc assertion requires the verifier to pin PCR4 to the recorded
  known-good value (S9) or to assert PCR12==0 / a pinned Secure-Boot PCR7 (S10);
  the trust anchor these guard is only described in prose and a deferred design note.
- The infrastructure/build-chain items under S4 were not re-inspected in this pass and
  lack regression coverage (AWS trust restriction, workflow-identity allow-list,
  `--expected-workflow` default, signed/checksummed build sources, SSH key handling,
  runtime package allow-list).
- No live AMI was inspected; effective Docker seccomp/user-namespace/LSM enforcement at
  runtime was not verified.
- No dependency CVE triage was performed against the locked package sets.
