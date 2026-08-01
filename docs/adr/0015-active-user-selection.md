# ADR 0015: Stable Active-User Selection

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M10

## Context

M10 must select at most one profile to control shared device settings from among currently recognized people, using the scoring/hysteresis/cooldown model ARCHITECTURE.md §10 documents, so that only one profile is ever active and brief challengers never cause a switch. It must not decide identity (M7) or apply settings (M9/M11).

## Decision

`presence/active_user.py` defines `PresenceCandidate` (per-track inputs: normalized face size, centre proximity, visible duration, recognition confidence, profile priority — each range-validated), `ActiveUserState` (HERMES's `NONE → CANDIDATE_ACTIVE → ACTIVE → LEAVING → NONE`), and a pure `compute_score()` implementing ARCHITECTURE.md §10's weighted-sum score exactly, with duration and priority each normalized to `[0, 1]` via configured saturation/scale constants rather than left unbounded.

`ActiveUserSelector` is the stateful policy: the current active profile keeps control until a challenger's score exceeds it by `switch_margin`, continuously, for `stability_duration_seconds` — matching "the current active user retains an advantage until a challenger exceeds the switch margin for the configured stability duration" — and even then a switch is refused until `switch_cooldown_seconds` has elapsed since the last switch, an explicit extra safeguard beyond the stability requirement. A candidate that never sustains its lead (a brief appearance) never accumulates enough consecutive time to switch. When the active profile's track disappears from the input entirely, the selector enters `LEAVING` for a configured grace period before clearing to `NONE`, rather than flickering instantly, but a genuinely absent active user does eventually and correctly clear.

Only `CONFIRMED_MATCH` tracks are eligible input, enforced by `presence/builder.py`'s `build_presence_candidate()`, which returns `None` for `UNKNOWN`/`AMBIGUOUS`/`POSSIBLE_MATCH` decisions: unknown people are never eligible to become the active user, since personalization requires a real, confirmed profile ID. The builder performs only geometric normalization (face-box area vs. frame area; box-centre distance vs. frame-centre, normalized by the frame's own diagonal-to-centre distance) and assembly — no scoring or state logic.

A real floating-point precision defect was found and fixed during this session's own smoke testing: comparing `(challenger_score - active_score) < switch_margin` with a strict `<` let a challenger whose true score sat exactly at the configured margin lose by a few ULPs of summation error and never even register as a pending challenger. Fixed with a small (`1e-9`) epsilon tolerance on the margin comparison, per TESTING.md §4's floating-point-tolerance guidance.

`ActiveUserConfig` is disabled by default; `presence/factory.create_active_user_selector()` follows the established disabled-by-default pattern.

## Consequences

- Every acceptance/TESTING.md §7 scenario was exercised in this session: single known user with stable activation delay, brief challenger rejected, active user moving off-centre while remaining best stays active, two known users with a sustained stronger challenger switching after stability+cooldown, a priority profile winning on otherwise-equal geometry (the exact scenario that surfaced the float-precision bug), cooldown blocking an immediate second switch, and an absent active user (no eligible candidates, since unknowns are excluded) correctly transitioning through `LEAVING` to `NONE`.
- `ActiveUserSelector` holds only small per-instance state (a handful of UUIDs and timestamps); it retains no pixels, embeddings, or track history beyond what it needs for the current decision.
- M11 owns applying the active profile's saved settings and learning new preferences from it; M10 only ever reports who, if anyone, is active.
