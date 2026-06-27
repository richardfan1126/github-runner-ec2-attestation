# Security Review

Date: 2026-06-27 (refresh of 2026-05-15 review)

## Scope and method of this pass

This refresh re-read the server runtime source on `main`
(`src/main.py`, `server.py`, `validation.py`, `encryption.py`, `attestation.py`,
`script_executor.py`, `repository.py`, `nonce_cache.py`, `config.py`).

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
  `nitro-tpm-attest` stderr in its error string (`f"Output attestation failed ...: {stderr_text}"`),
  which can propagate into logs/responses.
- Required hardening: never include raw tool stderr in returned/loggable error strings;
  keep a sanitized category externally and full detail only in internal diagnostics.

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
- No test asserts the output-attestation error path omits raw tool stderr (S3).
- The infrastructure/build-chain items under S4 were not re-inspected in this pass and
  lack regression coverage (AWS trust restriction, workflow-identity allow-list,
  `--expected-workflow` default, signed/checksummed build sources, SSH key handling,
  runtime package allow-list).
- No live AMI was inspected; effective Docker seccomp/user-namespace/LSM enforcement at
  runtime was not verified.
- No dependency CVE triage was performed against the locked package sets.
