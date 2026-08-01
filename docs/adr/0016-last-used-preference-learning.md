# ADR 0016: Last-Used Preference Attribution and Debounce

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M11

## Context

M11 must read and save stable user settings changes with attribution and debounce safeguards, per HERMES.md's rules: exactly one active profile must exist, that profile must have been active for a configured minimum duration, the changed value must remain stable for a debounce period, and the value must not be one the service itself just applied. It must not apply settings (M9) or select the active user (M10).

## Decision

`settings/preference_learning.py`'s `PreferenceLearner` is a small stateful policy implementing exactly HERMES's four attribution rules as sequential gates in `observe()`: no save without `ActiveUserState.ACTIVE` and a real profile ID; no save (and pending state reset) when the observed value matches a recent self-application within a configured tolerance window (M9's `RateLimitedSettingsAdapter.recent_self_applications()` is the evidence source); a fresh or profile-changed value restarts the debounce timer rather than reusing stale timing; and the same already-saved value for the same profile is not redundantly re-saved. A profile switch always restarts debounce — even if the device's raw setting value happens to coincidentally match what was stable under the previous active profile, attribution correctness takes priority over instant persistence.

`settings/last_used_service.py`'s `LastUsedPreferenceService` is the only M11 component that writes to M6: it reads `SettingsAdapter.read_current()`, asks the learner, and on a save persists to `ProfileSettingsRepository.upsert()` plus a durable `SettingsChanged` event (HERMES's required event type) via `RecognitionEventRepository.record()`, in one committed transaction using M8's caller-owned commit/rollback pattern.

`PreferenceLearningConfig` is disabled by default; `settings/factory.create_last_used_preference_service()` follows the established pattern.

## Consequences

- Every scenario from this session's smoke testing passed: no active profile, self-applied echo suppression, debouncing through a rapid multi-step slider drag (never stabilizes long enough to save), stable-change persistence, redundant-unchanged-value suppression, and profile-switch re-attribution (a value stable under profile A is never silently credited to profile B without B's own debounce wait) — verified end-to-end against a real M6 SQLite database with `MockSettingsAdapter` simulating an externally-changed device value.
- `PreferenceLearner` holds only a handful of scalars (pending settings/timestamp/profile ID, last-saved settings/profile ID) — no pixels, embeddings, or unbounded history.
- This module observes settings on demand (`observe()` is called by a caller); it does not itself poll on a timer. Wiring a periodic observation loop is M12's headless-daemon worker-loop job.
- `min_active_duration_seconds` and `debounce_seconds` defaults are unevaluated starting points, same caveat carried by every prior milestone's threshold defaults.
