## ADDED Requirements

### Requirement: Signed claims-digest envelope in user_data

The Attestation_Generator SHALL place in the NitroTPM attestation `user_data` a compact, fixed-shape signed envelope `{ v, claims_digest, timestamp, execution_id }` rather than the variable-length inline claim fields. `v` is the envelope-format version; `claims_digest` binds a claims document (see below); `timestamp` is a signed staleness marker; `execution_id` is the signed correlation key. `timestamp` and `execution_id` are secondary signals and SHALL NOT be relied on as the anti-replay mechanism — the server chooses both, so a replayer re-presents them unchanged; anti-replay is provided by the mandatory client nonce bound in the attestation's native COSE `nonce` field (see the `remote-executor` "Freshness/anti-replay preserved" requirement). The envelope SHALL fit well within the NitroTPM 1024-byte `user_data` limit regardless of how many claims the bound document carries, and the NitroTPM COSE signature over `user_data` SHALL therefore cover every envelope field.

#### Scenario: Envelope replaces inline fields

- **WHEN** an execution attestation document is generated
- **THEN** its `user_data` contains exactly the envelope fields `v`, `claims_digest`, `timestamp`, and `execution_id`, and none of the variable-length claim fields (`repository_url`, `commit_hash`, `script_path`, `script_env_hash`, `security`, `gpu`) appear inline in `user_data`

#### Scenario: Envelope stays within the size limit

- **WHEN** the bound claims document grows (e.g. a multi-GPU `devices` array is added)
- **THEN** the `user_data` envelope size is unaffected because only the fixed-length `claims_digest` changes, and assembly never approaches the 1024-byte limit

#### Scenario: Inline envelope fields are signed

- **WHEN** a verifier reads `execution_id` or `timestamp` from `user_data`
- **THEN** those values are covered by the NitroTPM COSE signature and are trustworthy without fetching or hashing the claims document

### Requirement: Versioned claims document schema

The Attestation_Generator SHALL emit a claims document (`claims_raw`) carrying a top-level `schema_version` and the claim fields removed from the inline layout — at minimum `repository_url`, `commit_hash`, `script_path`, `script_env_hash`, and the `security` posture block for execution claims. `schema_version` is distinct from the envelope's `v`: `v` versions the envelope format, `schema_version` versions the claims content, and they evolve independently. `schema_version` SHALL carry `MAJOR.MINOR` semantics: a **MAJOR** bump signals a breaking claims change (a field removed, renamed, re-typed, re-meaned, or a newly *required* claim a correct verifier must not miss); a **MINOR** bump signals a purely additive, safely-ignorable change (a new optional field). The envelope `v` SHALL bump on any change to the envelope shape or binding mechanism.

#### Scenario: Claims document carries schema_version and moved fields

- **WHEN** an execution attestation is generated
- **THEN** `claims_raw` contains a `MAJOR.MINOR` `schema_version` and the execution claim fields (`repository_url`, `commit_hash`, `script_path`, `script_env_hash`, `security`), and `schema_version` identifies the claims content format independently of the envelope `v`

#### Scenario: Additive change is a minor bump

- **WHEN** a new optional, safely-ignorable claim field is introduced
- **THEN** only the `schema_version` MINOR component increases (the MAJOR component and the envelope `v` are unchanged), and the added field does not affect the binding check

#### Scenario: Breaking change is a major bump

- **WHEN** a claim field is removed, renamed, re-typed, re-meaned, or a newly required claim is introduced
- **THEN** the `schema_version` MAJOR component increases

### Requirement: Digest binding over transmitted bytes

The binding `claims_digest = sha256(claims_raw)` SHALL be computed over the exact bytes transmitted in the response body. `claims_raw` SHALL be transmitted as a single base64-encoded opaque byte string — NOT as a nested/structured JSON object — so that "the transmitted bytes" is unambiguous: the server hashes the pre-encoding bytes, and the verifier hashes the bytes recovered by base64-decoding that field. A verifier SHALL base64-decode `claims_raw`, hash the decoded bytes, compare to `claims_digest`, and only THEN parse the decoded bytes to read fields; it SHALL NOT re-serialize or re-canonicalize the claims document to check the binding, and SHALL NOT hash the base64 text. Digest values SHALL be written with an algorithm prefix (`sha256:<hex>`), and verifiers SHALL select the hash by prefix and reject unknown algorithms.

#### Scenario: Binding checked against decoded bytes

- **WHEN** a verifier receives the base64 `claims_raw` field and the signed `claims_digest`
- **THEN** base64-decoding `claims_raw` and hashing the decoded bytes with the algorithm named by the `sha256:` prefix reproduces `claims_digest` without any canonicalization step, and the verifier reads fields only after this check passes

#### Scenario: claims_raw is an opaque blob, not nested JSON

- **WHEN** the response is assembled
- **THEN** `claims_raw` appears as one base64-encoded string field carrying the exact hashed bytes, and the claims are not embedded as a structured JSON object that a codec could re-serialize differently

#### Scenario: Unknown digest algorithm rejected

- **WHEN** a digest value carries an algorithm prefix other than `sha256:`
- **THEN** the verifier rejects the attestation rather than attempting to interpret it

### Requirement: Strict envelope/claims field partition

Fields carried inline in the envelope SHALL be authoritative there and SHALL NOT be duplicated inside the claims document. `execution_id`, `timestamp`, and `v` live only in the envelope; the claims document carries strictly the complement. A verifier SHALL never have to reconcile two copies of the same field.

#### Scenario: No field appears in both places

- **WHEN** an attestation and its claims document are inspected
- **THEN** `execution_id`, `timestamp`, and `v` appear only in the `user_data` envelope and do not reappear inside `claims_raw`

### Requirement: Verifier binding-check contract is mandatory and fail-closed

A verifier SHALL recompute `sha256(claims_raw)`, compare it to the signed `claims_digest`, reject an unknown `schema_version` or digest algorithm, and only then read claim fields. If `claims_raw` is missing, or its recomputed digest does not equal `claims_digest`, the verifier SHALL read no claim fields and SHALL reject the attestation. Stripping or altering the preimage SHALL NOT yield a trusted-but-empty read.

#### Scenario: Missing preimage rejected

- **WHEN** an attestation is presented without its `claims_raw`
- **THEN** the verifier reads no claim fields and rejects the attestation

#### Scenario: Digest mismatch rejected

- **WHEN** the recomputed `sha256(claims_raw)` does not equal the signed `claims_digest`
- **THEN** the verifier reads no claim fields and rejects the attestation

#### Scenario: Unknown major schema_version rejected before reading

- **WHEN** `claims_raw` carries a `schema_version` whose MAJOR component the verifier does not recognize
- **THEN** the verifier rejects the attestation before interpreting any claim field (even though the binding check may have passed)

#### Scenario: Higher minor schema_version tolerated

- **WHEN** `claims_raw` carries a known MAJOR with a MINOR component higher than the verifier was built for
- **THEN** the verifier reads the fields it knows and ignores the fields it does not, rather than rejecting

### Requirement: Consumers ignore unknown claim fields

A verifier SHALL ignore claim fields it does not recognize rather than failing on their presence, so that additive (minor) schema evolution does not force a lockstep upgrade. This tolerance applies only within a known MAJOR version; an unknown MAJOR (or unknown envelope `v`) is still rejected. A field a correct verifier must not miss SHALL therefore be introduced as a MAJOR bump, never smuggled in as an ignorable minor addition.

#### Scenario: Unknown field ignored within a known major

- **WHEN** a claims document of a known MAJOR contains a field the verifier does not recognize
- **THEN** the verifier reads the recognized fields and ignores the unknown one, without rejecting the attestation

#### Scenario: Load-bearing claim is not shipped as a minor

- **WHEN** a new claim must be checked for the attestation to be trusted
- **THEN** it is introduced via a MAJOR bump so verifiers that do not understand it reject rather than silently ignore it

### Requirement: Claims document travels bundled with the attestation

The claims document (`claims_raw`) SHALL be delivered alongside the attestation in the same response body so that the mandatory binding check can be performed. It is application payload placed inside the already-sealed response body and inherits confidentiality and transport integrity from the existing response-sealing requirement; the attestation binding (TPM signature over `claims_digest`) and transport integrity (AEAD auth tag) remain independent guarantees.

#### Scenario: claims_raw present in the response

- **WHEN** a response includes an attestation document
- **THEN** the same response body includes the corresponding `claims_raw` preimage that hashes to the attestation's `claims_digest`

### Requirement: Inner sub-digest canonicalization retained for external preimages

For sub-digests whose preimage is NOT transmitted in `claims_raw` — specifically `script_env_hash`, `output_digest`, and the reserved `gpu.attestation.report_digest` — canonicalization rules SHALL be retained so the preimage is independently reconstructable. `script_env_hash` SHALL be the SHA-256 hex digest of the canonicalized `script_env` (keys sorted, JSON with no whitespace), or of `{}` when `script_env` is empty. `output_digest` SHALL be the `sha256:`-prefixed digest of the canonical JSON object `{ stdout, stderr, exit_code }` (keys sorted, JSON with no whitespace), with `exit_code` carried as a JSON number, so that the map from `(stdout, stderr, exit_code)` to preimage bytes is injective and cannot be forged by in-band delimiters embedded in `stdout`/`stderr`. Both reuse the same canonicalization rule. This canonicalization applies only to inner sub-digests, not to the outer `claims_digest` binding.

#### Scenario: script_env_hash canonicalization unchanged

- **WHEN** `script_env_hash` is computed for a claims document
- **THEN** it is the SHA-256 hex digest of the canonicalized `script_env` (keys sorted, JSON no whitespace), or of `{}` when `script_env` is empty, independently of how `claims_raw` itself is serialized

#### Scenario: output_digest binds a canonical structured form

- **WHEN** `output_digest` is computed for an output claims document
- **THEN** it is the `sha256:`-prefixed digest of the canonical JSON object `{ stdout, stderr, exit_code }` (keys sorted, JSON no whitespace, `exit_code` as a JSON number), NOT of a delimiter-glued string, so that two distinct `(stdout, stderr, exit_code)` triples can never collide on the same preimage

### Requirement: Reserved nested digest-and-preimage slot

The claims schema SHALL support nesting the digest-and-preimage idiom recursively: a large future preimage (such as a hardware GPU-attestation report) is referenced by an inner digest field rather than inlined. The reserved slot `gpu.attestation.report_digest` SHALL follow the inner-digest pattern (algorithm-prefixed, canonicalized/independent preimage) when populated.

#### Scenario: Nested digest referenced, not inlined

- **WHEN** a future large report is bound into the claims document
- **THEN** it is referenced by an algorithm-prefixed inner digest (e.g. `gpu.attestation.report_digest`) and the report itself is not inlined into `claims_raw`

### Requirement: Unified envelope for execute-time and output-time attestations

Both the execute-time and output-time attestation builders SHALL emit the same envelope shape, differing only in their claims body: execution claims versus output claims. The output claims document SHALL carry a digest of the current `stdout‖stderr‖exit_code` as an inner `output_digest` field.

#### Scenario: Output attestation uses the same envelope

- **WHEN** an output attestation is generated for a polled execution
- **THEN** its `user_data` uses the same `{ v, claims_digest, timestamp, execution_id }` envelope, and its `claims_raw` carries output claims including an `output_digest` over the canonical JSON `{ stdout, stderr, exit_code }` (keys sorted, no whitespace, `exit_code` a JSON number)
