# ADR 0011: Persistent Profile Database, Encryption, and Retention

- Status: Accepted (amended)
- Date: 2026-08-01
- Decision milestone: M6

> **Addendum (M8):** the per-call auto-commit behavior described for repository write methods below was removed during M8 to allow atomic multi-write promotion transactions. See ADR 0013 for the correction and its rationale.

## Context

M6 must persist profiles and embeddings across restarts, support multiple embeddings per profile, provide profile/embedding/settings/event schema with migrations, and satisfy ADR 0003's requirement that biometric persistence be encrypted at rest with keys separated from the data, support auditable deletion, and have explicit retention limits. HERMES rule 13 blocks any permanent biometric persistence without encryption. No SQLCipher-class native extension is currently a dependency, and CONTRIBUTING.md requires justifying new dependencies.

## Decision

**Migrations.** Use plain SQL migrations over the standard-library `sqlite3` module (`database/schema.py`), not an ORM/Alembic stack, per CODING_STANDARDS/CONTRIBUTING's preference for an existing, dependency-light solution when one is sufficient. A `schema_version` table records applied migrations so restarts apply only what is pending. M6 creates `profiles`, `face_embeddings`, `profile_settings`, and `recognition_events` — exactly the schema ROADMAP.md assigns to M6. `candidates`/`candidate_embeddings` are explicitly deferred to M8; `recognition_events.candidate_id` is a plain column without a foreign key until then.

**Encryption approach.** Rather than whole-database (SQLCipher-style) encryption, M6 encrypts biometric payloads at the field level: only `face_embeddings.encrypted_vector` is ciphertext (AES-256-GCM via the newly added `cryptography` dependency — BSD/Apache-2.0 dual-licensed, the de facto standard Python crypto library, actively maintained). Because SQLite only ever writes the bytes handed to it, WAL and rollback-journal sidecar files never contain plaintext vectors either, without a native encryption extension or new build-time dependency. `metadata_json` and `display_name` are treated as ordinary application data, not biometric payload, consistent with encrypting "permanent embeddings" rather than the entire schema. Representative face-image storage remains disabled (no image bytes are persisted in M6, matching ARCHITECTURE.md's default).

**Key separation.** `database/keys.py`'s `LocalFileKeyProvider` stores a 32-byte key in its own file, separate from the database file, created with owner-only (`0600`) permissions and rejected if found with looser permissions. This satisfies ADR 0003's key/data separation but is not HSM/KMS integration; rotation and disaster recovery remain a documented manual procedure pending a production key-management adapter.

**File hardening.** The database directory is created `0700`; the database file is created/verified `0600` on every open (`database/connection.py`), matching the M1 frame-writer's owner-only pattern.

**Repositories.** `ProfileRepository` provides create/get/list/update (optimistic-concurrency-checked via `optimistic_version`), `disable`, `soft_delete` (sets `retention_expires_at`), `purge_expired`, `merge` (atomic embedding reassignment plus status transition), embedding add/list/get/remove, and encrypted `export_profile`/`import_profile`. A real correctness bug was found and fixed during implementation: AEAD associated data was bound to the owning profile's UUID, so a naive `UPDATE ... SET profile_id` during merge broke authenticated decryption for moved embeddings; merge now decrypts under the source binding and re-encrypts under the target binding inside the same transaction. `ProfileSettingsRepository` and `RecognitionEventRepository` (append-only, sequence-numbered) complete the M6 schema surface. All repositories depend on typed domain values (`database/models.py`), never raw SQL rows, per CODING_STANDARDS.md.

**CLI.** `face-profile profile create|list|show|delete|merge` complete HERMES's required CRUD verbs for this milestone; `serve` remains M12. Every command fails closed with `database_disabled` unless `database.enabled` is explicitly true, following the M1–M5 disabled-by-default pattern.

## Consequences

- `database.enabled` stays `false` in shipped configuration; enabling it is an explicit, documented opt-in.
- Retention/secure-deletion is a normal SQL `DELETE` after `purge_expired`, not a disk-page secure-wipe; this is a documented limitation pending a VACUUM-based or secure-erase retention job.
- Backups must include both the database file and the separate key file; a database backup without the key file cannot decrypt any embedding. This is not yet automated or documented as an operational backup/restore procedure — a known gap for a later milestone or operations update.
- `recognition_events.sequence` is assigned via `MAX(sequence)+1` inside one transaction; concurrent writers (not introduced until M12) could race on this without additional locking, a documented limitation.
- M7 owns the recognition-decision state machine over `ProfileRepository`'s embeddings; M8 owns candidate schema/lifecycle; M9/M11 own applying and attributing `profile_settings` to a real device.
