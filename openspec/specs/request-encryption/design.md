# request-encryption — Design Rationale

> Imported from `.kiro/specs/github-actions-remote-executor/design.md` (Encryption Manager, Security Hardening Components). Captures the *why* behind `spec.md`; not normative.

## Why a post-quantum hybrid channel

Sensitive data — most importantly the GitHub OIDC token and script output — must be protected against both classical and future quantum adversaries. The channel combines **X25519 ECDH** (classical, fast, well-trusted) with **ML-KEM-768** (FIPS 203, post-quantum). Combining both via HKDF-SHA256 means the channel stays secure even if one primitive is later broken (defense in depth), and matches the `X25519MLKEM768` naming used by IETF/OpenSSL.

- X25519 via the `cryptography` library; ML-KEM-768 via `wolfcrypt-py` (`wolfcrypt.ciphers`: `MlKemType`, `MlKemPrivate`, `MlKemPublic`).
- HKDF-SHA256 uses the domain-separation info label `b"pq-hybrid-shared-key"` to distinguish this derivation from any other HKDF use in the system.
- Symmetric encryption is AES-256-GCM.

## Server keypair & the /attest fingerprint trick

The server generates the composite keypair once at startup, holds it **in memory only** (never persisted), and keeps it constant for the process lifetime so all concurrent requests share one read-only key. It is logged at INFO without any private/decapsulation material.

The composite public key (32-byte X25519 + 1184-byte ML-KEM-768 encapsulation key, length-prefixed) is **1216+ bytes**, which exceeds the NitroTPM attestation document's 1024-byte `public_key` field. The design therefore puts a **SHA-256 fingerprint** of the serialized composite key in the attestation `public_key` field and returns the **full** composite key in the `/attest` JSON body. The client recomputes the fingerprint and compares — binding the key to the attested environment without exceeding the field limit. This fingerprint appears **only** on `/attest` documents; execution/output documents carry `user_data` instead.

## Execution-bound keys

On successful `/execute` decryption the derived shared key is stored in an `Encryption_Context` keyed by execution ID (in memory only) and reused to encrypt the `/execute` response and to decrypt/encrypt that execution's output requests/responses. The context is removed when the execution record is cleaned up. Binding one key per execution is what lets output polling be authenticated by key possession alone.

## Key decisions & trade-offs

- **OIDC token inside the ciphertext, not a header.** Putting the token in the encrypted body protects it end-to-end and removes any plaintext-header exposure; the server extracts it from the decrypted `oidc_token` field.
- **Encrypted error envelopes after decryption.** Once a request decrypts successfully, *all* subsequent application errors (validation, auth, clone, attestation, capacity) are returned as encrypted envelopes (`error` + `error_code`) rather than plaintext HTTP errors — so an observer can't distinguish failure modes, and the client always operates within the encrypted channel. `/attest` and `/health` are the only unencrypted endpoints (they must work before a channel exists).
- **Mandatory, strictly-validated nonces (anti-replay).** Nonces are required on every `/execute` and output request, validated as a string of 16–256 URL-safe characters before the cache check, then checked against an in-memory `Nonce_Cache` whose entries expire after a TTL matching the OIDC token lifetime. Base64 fields are decoded with `validate=True` so malformed encodings are rejected early. Without mandatory nonces, a captured valid ciphertext could be replayed to cause duplicate executions.
- **Output-attestation rate limiting.** Every output poll otherwise triggers a fresh NitroTPM attestation, so frequent polling could become a resource-exhaustion path. A per-execution-ID limiter (`MAX_OUTPUT_ATTESTATIONS_PER_WINDOW`=10 / `OUTPUT_ATTESTATION_WINDOW_SECONDS`=60) caps generations per window. It never blocks the poll itself — when the budget is exhausted the response still returns output/status with `output_attestation_document: null` and `attestation_rate_limited: true`, and the budget resets when the window expires.

## Data models (shapes)

`EncryptedRequest` (base64 `encrypted_payload` + unencrypted `client_public_key`), `DecryptedExecuteRequest` (execution fields + `oidc_token` + mandatory `nonce` + optional `script_env`), `DecryptedOutputRequest` (`nonce` + `offset`), and `EncryptionContext` (execution_id → shared_key). The `Client_Public_Key` is the client's X25519 public key plus the ML-KEM-768 ciphertext, length-prefixed so the server can derive the same shared key.
