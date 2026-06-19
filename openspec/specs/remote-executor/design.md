# remote-executor — Design Rationale

> Imported from `.kiro/specs/github-actions-remote-executor/design.md` (PART 1: Runtime Design). This captures the *why* behind the requirements in `spec.md`; it is not normative. The encrypted channel is documented in `request-encryption`; the per-container sandbox in `container-security`.

## Overview

The Remote Executor is an HTTP server on an attestable EC2 instance (NitroTPM) that runs scripts from GitHub repositories inside ephemeral Docker containers and returns cryptographic attestation of the execution environment. Each execution gets a fresh container that is destroyed afterwards, giving complete isolation between runs.

## Key design principles

1. **Asynchronous execution + polling.** `/execute` returns immediately with an execution ID and attestation document; the script runs in the background and the client polls `/execution/{id}/output`. This avoids long-held HTTP connections and lets clients observe progress.
2. **Ephemeral container isolation.** Containers are created per-execution from a configured `Container_Image`, never reused, and destroyed on completion/failure/timeout.
3. **Attestable environment.** NitroTPM attestation provides cryptographic proof of the environment; security-relevant metadata is bound into the attestation `user_data`.
4. **Stateless request handling.** Each request is independent; execution state lives in a separate in-memory store.
5. **PQ-hybrid encrypted communication.** All `/execute` and output payloads are encrypted (see `request-encryption`); the OIDC token travels *inside* the encrypted body rather than in an HTTP header, and output polling is authenticated purely by possession of the execution-bound shared key.

## Component architecture

Layered: **HTTP Server** (routing, per-IP rate limiting) → **Core Services** (Encryption Manager, Request Validator, Repository Client, Attestation Generator) → **Execution Management** (Execution Manager, Script Executor, Output Collector + Docker SDK) → **Storage** (execution store, temp storage, encryption contexts), all in-memory.

Notable component responsibilities:

- **Request Validator** uses PyJWT (RS256, `cryptography` backend), caches the GitHub JWKS and refreshes on unknown key ID. It rejects absolute script paths explicitly because `os.path.join(clone_path, script_path)` silently discards the clone prefix on an absolute path — which would let the pre-execution size check read arbitrary host files.
- **Repository Client** authenticates with a credential-isolation mechanism (`GIT_ASKPASS` helper or `http.extraHeader`) so the token never appears in `/proc/<pid>/cmdline`; then strips the token from `.git/config` and removes `.git` entirely (defense in depth) before the workspace is mounted. It rejects symlinked script paths and paths whose `realpath()` escapes the clone directory.
- **Script Executor** mounts the clone read-only at `/workspace`, runs `bash /workspace/{script_path}`, and uses a daemon **Log_Streaming_Thread** (`container.logs(stream=True, follow=True)`) feeding the Output Collector concurrently with `container.wait()`.
- **Execution Manager** checks `MAX_CONCURRENT_EXECUTIONS` atomically before creating a record, and stores execution-duration history in a bounded `collections.deque(maxlen=10000)` so it cannot grow without bound in long-running deployments.

## Key decisions & trade-offs

- **Why streaming via a daemon thread.** Polling clients must see partial output within one poll interval rather than waiting for container exit. The thread is a daemon so it never blocks process shutdown, and the executor deliberately does **not** re-capture the full logs in a batch after exit (the stream already captured everything) to avoid duplication.
- **Container security posture is enforced, not assumed.** `cap_drop=ALL` plus a minimal `cap_add` working set, `pids_limit` (default 256) to stop fork bombs, and explicit body-size limits checked *before* JSON/base64/decryption to prevent resource exhaustion. The detailed, operator-tunable sandbox is specified in `container-security`.
- **Why output polling needs no OIDC.** Only the original caller who completed the PQ-hybrid exchange on `/execute` possesses the execution-bound shared key, so successful decryption *is* the authentication — avoiding a second token round-trip and keeping the output channel bound to one execution.
- **Fail-fast, never silent-degrade.** Missing Docker daemon, missing required config, or (in production) missing NitroTPM cause a non-zero exit before binding the port. `ALLOW_NO_TPM` exists only for dev/test and logs a prominent warning. Strict boolean parsing rejects typos (`treu`, `enabled`, `on`) rather than silently treating them as false.
- **Log sanitization is centralized.** All subprocess stderr / external output passes through the Log_Sanitizer before logging or being returned; user-controlled fields are `repr()`/JSON-escaped and length-capped to prevent log injection; nonces are never logged verbatim. Per-request context uses `contextvars.ContextVar` so concurrent requests can't see each other's context.
- **GPU via CDI only.** When `ENABLE_GPU`, the executor passes `runtime="nvidia"` and the server-controlled `NVIDIA_*` env vars (overriding any caller `script_env`), and uses CDI mode exclusively — never `--gpus`/`device_requests`, manual `/dev/nvidia*` mappings, `SYS_ADMIN`, or `--privileged` — because CDI works with rootless Docker without cgroup device access, so none of the existing security constraints need relaxing.

## Data models (shapes)

In-memory records the runtime maintains (field-level detail in the source): `ExecutionRequest` (repository_url, commit_hash, script_path, github_token, oidc_token, optional script_env), `ExecutionRecord` (id, status, stored OIDC `repository` claim, attestation, timing), `ExecutionStatus` (queued/running/completed/failed/timed_out), `OutputData` (stdout, stderr, exit_code, truncated flag), `Configuration` (all tunables), and OIDC validation/claims structures. Attestation `user_data` for execution documents carries `repository_url`, `commit_hash`, `script_path`, `script_env_hash`, `execution_id`, `gpu_enabled`, `timestamp` (plus the container-security fields from `container-security`).
