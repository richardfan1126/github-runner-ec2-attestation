# request-encryption Specification

## Purpose

Protect the Remote Executor's request and response payloads — including the GitHub OIDC token and script output — against both classical and quantum adversaries, using a post-quantum/traditional hybrid key exchange (X25519 + ML-KEM-768) bound to a single execution. This capability covers the server keypair lifecycle, the unauthenticated `/attest` endpoint that publishes the composite public key bound to a NitroTPM attestation, the hybrid key agreement, execution-bound shared-key storage, payload encryption, anti-replay protection, and output-attestation rate limiting.

The attestation document semantics and endpoints themselves are exercised by `remote-executor`; this capability governs the cryptographic channel.

## Requirements

### Requirement: Server keypair generation and lifecycle

When the GHA_Server starts, it SHALL generate a Server_Keypair consisting of an X25519 key pair (via the `cryptography` library) and an ML-KEM-768 key pair (via wolfcrypt-py, `MlKemType.ML_KEM_768`), hold it in memory only for the entire process lifetime, and never persist it to disk.

#### Scenario: Keypair generated at startup

- **WHEN** the server starts
- **THEN** a composite Server_Keypair is generated, kept in memory, remains constant for the process lifetime, and is logged at INFO level without any private/decapsulation key material

#### Scenario: Composite public key serialization

- **WHEN** the Server_Public_Key is serialized
- **THEN** it is a length-prefixed concatenation of the 32-byte X25519 public key and the 1184-byte ML-KEM-768 encapsulation key, each preceded by a 4-byte big-endian length prefix

### Requirement: Attestation endpoint

The GHA_Server SHALL provide an unauthenticated HTTP GET `/attest` endpoint that returns a NitroTPM Attestation_Document binding the Server_Public_Key, alongside the full composite key in the JSON body. Because the composite key exceeds the 1024-byte `public_key` field limit, the document's `public_key` field SHALL carry a SHA-256 fingerprint of the serialized Server_Public_Key.

#### Scenario: Attest response shape

- **WHEN** a request to `/attest` is received
- **THEN** the response JSON contains the base64 Attestation_Document (whose `public_key` field holds the SHA-256 fingerprint of the Server_Public_Key) and the base64 serialized Server_Public_Key as separate fields, unencrypted, with no `user_data`

#### Scenario: Optional nonce echoed

- **WHEN** an optional `nonce` query parameter is provided
- **THEN** the nonce is included in the generated Attestation_Document; when absent, the document is generated without a nonce

#### Scenario: Client verifies key binding

- **WHEN** a client receives the `/attest` response
- **THEN** it can verify the Server_Public_Key by computing its SHA-256 fingerprint and comparing against the `public_key` field before using the key for key exchange

#### Scenario: Rate limiting and failure

- **WHEN** a client exceeds the per-IP rate limit on `/attest`
- **THEN** the server returns HTTP 429; if attestation generation fails the server returns HTTP 500 indicating attestation failure

### Requirement: Nonce support in attestation responses

Every endpoint that returns an Attestation_Document SHALL accept an optional client-provided nonce and pass it to the attestation tool for inclusion, so clients can verify freshness. `/attest` and `/execution/{id}/output` accept it as a query parameter; `/execute` accepts it as a field in the encrypted body.

#### Scenario: Nonce bound into document

- **WHEN** a nonce is provided on any attestation-generating request
- **THEN** the Attestation_Generator includes that nonce in the document so the client can confirm it was generated in response to its specific request

### Requirement: Server public key bound only to /attest

The Attestation_Generator SHALL include the Server_Public_Key fingerprint in the `public_key` field only for `/attest` documents, computed over the deterministic length-prefixed serialization. Documents for other endpoints SHALL NOT include the fingerprint.

#### Scenario: Fingerprint scoped to /attest

- **WHEN** generating an Attestation_Document for `/execute` or `/execution/{id}/output`
- **THEN** the Server_Public_Key fingerprint is not included; only `/attest` documents carry it

### Requirement: Post-quantum hybrid encrypted execute requests

The `/execute` request payload SHALL be encrypted under a Shared_Key derived from a hybrid key exchange: an X25519 ECDH plus an ML-KEM-768 encapsulation against the server's keys, combined via HKDF-SHA256 with the domain-separation info label `b"pq-hybrid-shared-key"`. The Client_Public_Key (X25519 public key + ML-KEM-768 ciphertext, length-prefixed) SHALL be sent unencrypted alongside the encrypted payload.

#### Scenario: Server derives shared key and decrypts

- **WHEN** the server receives an `/execute` request
- **THEN** it parses the Client_Public_Key, performs X25519 ECDH and ML-KEM-768 decapsulation with the Server_Keypair, derives the same Shared_Key via HKDF-SHA256 with the domain-separation label, and decrypts the payload with AES-256-GCM

#### Scenario: OIDC token carried inside encryption

- **WHEN** an `/execute` payload is constructed
- **THEN** it includes the OIDC token in an `oidc_token` field (not an Authorization header) along with all Execution_Request fields, and the server extracts the token from the decrypted body and applies the same validation/execution logic as an unencrypted request

#### Scenario: Decryption or key parse failure

- **WHEN** payload decryption fails, or the Client_Public_Key cannot be parsed or has invalid X25519/ML-KEM-768 components
- **THEN** the server returns HTTP 400 Bad Request indicating decryption failure or an invalid client public key

### Requirement: Execution-bound shared key storage

When the server successfully decrypts an `/execute` request, it SHALL store the Shared_Key in an Encryption_Context keyed by the Execution_ID, in memory only, for the lifetime of the execution, and use it for all subsequent payloads of that execution.

#### Scenario: Shared key reused for the execution

- **WHEN** an execution's `/execute` request is decrypted
- **THEN** the Shared_Key encrypts the `/execute` response and both decrypts and encrypts the `/execution/{id}/output` request and response payloads

#### Scenario: Context cleaned up

- **WHEN** the execution record is cleaned up
- **THEN** the associated Encryption_Context is removed from memory; it is never persisted to disk

### Requirement: Encrypted request and response payloads

Request and response payloads on `/execute` and `/execution/{id}/output` SHALL be encrypted with the execution-bound Shared_Key. After successful request decryption, all subsequent application-level errors SHALL be returned as encrypted error envelopes rather than plaintext HTTP errors. Attestation documents inside encrypted responses SHALL NOT be separately re-encrypted.

#### Scenario: Encrypted envelopes after decryption

- **WHEN** a post-decryption application error occurs (validation, auth, clone, attestation, or capacity failure)
- **THEN** it is returned as an encrypted envelope containing an `error` description and an `error_code` field holding the HTTP status that would have been returned

#### Scenario: Missing encryption context

- **WHEN** a `/execution/{id}/output` request arrives with no Encryption_Context for that id, or its payload fails to decrypt
- **THEN** the server returns HTTP 400 Bad Request indicating no encryption context is available or decryption failure

#### Scenario: Unencrypted exemptions

- **WHEN** responding from `/attest` or `/health`
- **THEN** the response is not encrypted; encryption applies only to endpoints operating within an Encryption_Context

### Requirement: Encrypted request anti-replay protection

The GHA_Server SHALL maintain a nonce cache and SHALL require a valid, non-empty, previously-unseen `nonce` in the decrypted payload of every `/execute` and `/execution/{id}/output` request. Cache entries SHALL expire after a configurable TTL matching the OIDC token lifetime.

#### Scenario: Duplicate nonce rejected

- **WHEN** a decrypted request carries a nonce already present in the cache
- **THEN** the request is rejected with HTTP 400 Bad Request indicating a duplicate nonce

#### Scenario: Nonce presence and format enforced

- **WHEN** the decrypted payload's `nonce` is missing, empty, non-string, shorter than 16 or longer than 256 characters, or contains characters outside the URL-safe set (alphanumeric, hyphen, underscore, period, tilde)
- **THEN** the request is rejected with HTTP 400 Bad Request

#### Scenario: Strict base64 decoding

- **WHEN** decoding the `encrypted_payload` or `client_public_key` base64 fields
- **THEN** `base64.b64decode(..., validate=True)` is used and malformed base64 (padding errors, illegal characters) is rejected with HTTP 400 before further processing

### Requirement: Output attestation rate limiting

The GHA_Server SHALL maintain a per-Execution_ID Output_Attestation_Rate_Limiter that caps attestation generations per time window (`MAX_OUTPUT_ATTESTATIONS_PER_WINDOW`, default 10; `OUTPUT_ATTESTATION_WINDOW_SECONDS`, default 60), so frequent polling cannot turn TPM attestation into a resource-exhaustion path. It SHALL NOT block the poll request itself.

#### Scenario: Within budget

- **WHEN** the output endpoint is polled within the per-id attestation budget
- **THEN** an Output_Attestation_Document is generated normally

#### Scenario: Budget exhausted

- **WHEN** the attestation budget for an Execution_ID is exhausted in the current window
- **THEN** the response still returns the current Script_Output and status, sets `output_attestation_document` to null, and includes `attestation_rate_limited: true`

#### Scenario: Budget resets

- **WHEN** the time window expires
- **THEN** the attestation budget for the Execution_ID resets, allowing new generations
