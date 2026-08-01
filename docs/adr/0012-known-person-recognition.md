# ADR 0012: Known-Person Recognition and Temporal Confirmation

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M7

## Context

M7 must match tracks to profiles using both a similarity threshold and a best-versus-second-best margin (ARCHITECTURE.md section 8), require temporal confirmation so identity does not flicker, and recognize multiple known people independently. It must not force ambiguous results into a known identity, must not implement candidate enrollment (M8) or active-user selection (M10), and must reuse the initial threshold/margin M5 already exposed rather than reintroducing them.

## Decision

Add a `recognition/` package with four small, independently testable pieces:

- `decision.py`: `RecognitionState` (`UNKNOWN`, `AMBIGUOUS`, `POSSIBLE_MATCH`, `CONFIRMED_MATCH` — quality/liveness rejection stay owned by M4/M14 and are not states here), `ProfileMatch`, `RecognitionDecision`, and a pure `decide()` function implementing exactly the documented rule: `best < threshold → UNKNOWN`; `best ≥ threshold and margin too small → AMBIGUOUS`; otherwise `POSSIBLE_MATCH` (single-observation; only the cache may promote to confirmed).
- `matcher.py`: `find_nearest_profiles()` takes caller-supplied `(profile_id, embedding)` pairs (one profile may appear multiple times, once per stored embedding) and returns each profile's single best similarity, ranked descending. It performs no I/O and knows nothing about SQLite.
- `cache.py`: `RecognitionCache` holds small, bounded, in-memory per-track state — a candidate profile ID, a consistency counter, and timestamps, nothing biometric. A track's identity becomes `CONFIRMED_MATCH` only after `min_consistent_observations` consecutive `POSSIBLE_MATCH` observations for the *same* profile inside `confirmation_window_seconds`. Once confirmed, the track stays confirmed (sticky) through transient `UNKNOWN`/`AMBIGUOUS` observations — this is the flicker-prevention mechanism HERMES requires — and only a different profile accumulating its own full consistent run can replace it. `discard()` must be called when a track ends (M3 lifecycle); nothing expires automatically otherwise.
- `recognizer.py`: `KnownPersonRecognizer` is the only piece that talks to M6. It loads every `ACTIVE` profile's embeddings via `ProfileRepository`, ranks them, decides, and stabilizes through the cache. Loading and decrypting every active profile's every embedding on every call is a known, explicitly documented performance limitation deferred to M15 (batching/caching), not silently accepted as fine.

`create_recognizer()` returns `None` unless both `recognition.enabled` and `embedding.enabled` are true, extending the M1–M6 disabled-by-default factory pattern; recognition cannot function without M5's embedding configuration, so the dependency is enforced rather than assumed.

CLI gains `recognize --image PATH [--track-id N]`, completing the HERMES-required verb for this milestone. It requires the database, detection, quality, alignment, embedding, and recognition config to all be enabled; a single CLI invocation uses one fresh, ephemeral track ID and cache rather than the eventual daemon's continuous per-track state, and is explicitly a debug/smoke tool, not the real service pipeline (M12 owns that).

## Consequences

- Multiple simultaneously visible tracks are recognized independently because cache state is keyed per `track_id`; one track's confirmation has no effect on another's, verified in this session's smoke testing.
- "Unknown people remain unknown" holds structurally: there is no code path that promotes a track to `CONFIRMED_MATCH` without a `POSSIBLE_MATCH` run against a real profile ID.
- The recognizer's active-profile-embedding scan is O(profiles × embeddings) decrypt operations per call; this will not scale to a real deployment and is an explicit M15 follow-up, not a correctness gap.
- M8 owns candidate matching/deduplication reuse of `find_nearest_profiles()`/`decide()` against candidate rather than profile embeddings; M10 owns cross-track, cross-profile active-user selection, which is a different problem from this milestone's single-track identity stabilization.
