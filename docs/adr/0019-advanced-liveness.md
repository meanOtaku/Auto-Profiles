# ADR 0019: Advanced Liveness — Passive Heuristics, Active Challenge, and Depth/IR Stub

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M14

## Context

M14 must build on M8's minimum temporal spoof/replay checks with measured passive anti-spoofing, an optional active challenge, and optional depth/IR support, such that failed liveness can never create a permanent profile. No labeled printed-photo/replay/live dataset is available in this environment, so "measured" and "standard test set" claims from HERMES/ROADMAP cannot be honestly satisfied with real accuracy evidence; this ADR documents exactly what was and was not achieved.

## Decision

**Passive heuristics (`liveness/passive.py`).** `HeuristicPassiveLivenessEvaluator` scores one aligned crop on two classical, unevaluated signal-processing proxies: an FFT-based high-frequency spectral-energy ratio (screens/prints often show a different frequency distribution from natural skin texture) and a specular-highlight spatial-spread proxy (flat prints/screens tend toward more uniform reflectance than a curved, textured live face). Both are explicitly documented as heuristics, not a trained anti-spoof model — no such model could be responsibly sourced, vetted for license/provenance (per ADR 0002), and evaluated within this session, so none is claimed. `create_passive_liveness_evaluator()` follows the disabled-by-default factory pattern.

**Active challenge (`liveness/active_challenge.py`).** `verify_turn_challenge()` is a minimal, deterministic verifier over a caller-supplied sequence of signed yaw proxies (reusing `enrollment/frontality.py`'s asymmetry geometry, signed rather than absolute): the sequence must start near-frontal and later reach the requested direction beyond a configured magnitude, so a pre-turned static replay presented from the first frame is rejected. It does not capture frames or orchestrate a challenge UI — that workflow remains a future daemon/UI concern; this module only judges a supplied sequence.

**Depth/IR (`liveness/depth.py`).** `DepthIRSignal` is an interface-only `Protocol` stub (`is_available()`, `liveness_hint()`). No real backend is implemented — no depth or IR hardware exists in this environment — satisfying HERMES's "optional" framing and the "minimal typed interface" carve-out without fabricating hardware support.

**Enforcement — the safety-critical property.** "Failed liveness cannot create a permanent profile" is enforced at two independent points, matching this session's established dual-gating pattern (duplicate-profile checking in M8):

1. `enrollment/qualification.py`'s `evaluate_candidate_qualification()` gained a `liveness_ok: bool = True` parameter (default preserves M8-only behavior); `enrollment/manager.py`'s `CandidateManager` tracks a *sticky* per-track liveness-failed flag — one failed sample permanently blocks that track's candidate from ever reaching `READY_FOR_REVIEW`, even if later samples pass. When a candidate does become ready, the manager records `liveness_passed: true` in the candidate's `metadata_json` (a new `CandidateRepository.set_metadata_flag()` method) — the only place liveness evidence can be persisted, since candidate embeddings/aligned crops are not retained after promotion.
2. `enrollment/promotion.py`'s `CandidatePromoter` gained a `require_liveness` constructor flag (wired to `config.liveness.enabled` in `enrollment/factory.py`); `promote()` re-checks the candidate's recorded `liveness_passed` metadata flag before promoting and rejects (deleting the candidate's embeddings, same as a duplicate-profile rejection) if it is not explicitly `true` — defense-in-depth against a hypothetical future caller reaching `READY_FOR_REVIEW` through some path that bypassed step 1.

**Pipeline wiring.** `api/worker.py`'s `PipelineWorker` gained an optional `passive_liveness_evaluator`; when configured, it evaluates every aligned crop before calling `CandidateManager.observe_unknown(..., liveness_passed=...)`. `api/app.py` constructs it from `config.liveness` alongside the rest of the worker's stages.

## Consequences

- Verified in this session: a candidate whose every sample fails liveness never leaves `COLLECTING` and is correctly refused at promotion (`CandidateNotReadyError`); a candidate with one failed sample among otherwise-passing samples is still sticky-blocked; a candidate whose samples all pass liveness reaches `READY_FOR_REVIEW` and promotes normally; and a simulated bypass (a candidate marked ready without the recorded flag) is caught and rejected by the promotion-time defense-in-depth check, with its embeddings deleted.
- HERMES/ROADMAP's "static photo attacks are flagged in the standard test set" and "documented rate" language is **not** satisfied with real accuracy evidence — there is no standard test set in this environment, and none is fabricated. The passive evaluator's own smoke test in `docs/reports/M14.md` is a code-path self-test on synthetic images only.
- A trained passive anti-spoof model, real active-challenge frame capture/orchestration, and any real depth/IR backend remain explicit, honestly recorded gaps for a future milestone or dedicated follow-up.
