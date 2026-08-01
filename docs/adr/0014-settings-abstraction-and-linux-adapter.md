# ADR 0014: Settings Abstraction, Rate Limiting, and the Linux Platform Adapter

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M9

## Context

M9 must implement the portable settings interface and mock adapter first, then exactly one explicitly selected operating-system/desktop-environment adapter, with capability/permission detection, safe ranges, rate limits, feedback-loop protection, and rollback/fail-safe behavior. HERMES.md requires selecting the target platform before writing a real adapter, and requires that "recognition tests never modify real device settings." This repository's own development and CI baseline (docs/reports/M2.md) is Debian GNU/Linux; this sandbox has neither an ALSA mixer nor a backlight device, so no real host mutation is possible or attempted here — only dependency-injected fakes exercise the adapter's logic, plus one read-only capability-detection call against the real (absent) tools.

## Decision

**Selected platform.** Linux at the system level: ALSA's `amixer` for volume, the kernel `/sys/class/backlight` interface for brightness. This targets the OS layer rather than a specific desktop-environment daemon (GNOME/KDE settings services), so the same adapter works in the headless/container deployment ARCHITECTURE.md §14 lists as a supported target, where no desktop session exists. A future desktop-environment-specific adapter (e.g., a GNOME/PulseAudio one) remains a documented option, not implemented here.

**Interface extension.** `settings/__init__.py` adds `CapabilityStatus` (`available`/`unsupported`/`permission_denied`) and `AdapterCapabilities`, and extends the `SettingsAdapter` protocol with `capabilities()`. `ApplyResult` gains `errors: tuple[str, ...]` and `rolled_back: bool`, both defaulted so existing `ApplyResult(applied=...)` call sites remain valid. `MockSettingsAdapter` implements `capabilities()` returning `AVAILABLE` for both settings, preserving its role as the always-safe default for every automated test.

**Real adapter (`settings/linux.py`).** All host interaction goes through injectable `CommandRunner` (subprocess boundary) and `BacklightAccessor` (sysfs boundary) protocols, mirroring the `capture_factory`/`backend_factory` dependency-injection pattern M1/M2 established. `LinuxSettingsAdapter.apply()` applies volume and brightness independently; if one succeeds and the other fails, it attempts a best-effort rollback of the value that already changed and reports both the original and any rollback failure in `ApplyResult.errors`/`rolled_back` rather than leaving the host in a silently inconsistent state. Capability detection is routed entirely through the same injected `CommandRunner` (a bug where it initially called `shutil.which()` directly, bypassing dependency injection and making it untestable with fakes, was found and fixed during this session's own smoke testing) and through `BacklightAccessor.is_writable()` (a non-mutating `os.access()` permission probe), so "unsupported" and "permission denied" are always distinguished rather than guessed at.

**Rate limiting and feedback-loop recording (`settings/rate_limit.py`).** `RateLimitedSettingsAdapter` wraps any adapter and rejects (`ApplyResult(applied=False, errors=("rate_limited",))`, not silently drops) calls made before a configured minimum interval has elapsed since the last successful apply — protecting real hardware from rapid repeated writes such as a fast slider drag. Every successful apply is recorded (value, timestamp, correlation ID) in a small bounded ring buffer. This is the *mechanism* ARCHITECTURE.md §11 describes ("when the service applies settings, it records..."); the *policy* of comparing a later observation against these records to decide user-vs-self attribution is explicitly M11's job, not implemented here.

**Configuration and wiring.** `SettingsConfig` gains `backend` (`mock`/`linux`, default `mock`) and `min_apply_interval_seconds`. `settings/factory.py`'s `create_settings_adapter()` is the only M9 factory that never returns `None` — recognition and profile code must always have a safe settings boundary — but only ever selects the real backend when explicitly configured, and always wraps the result in the rate limiter. The CLI's `check`/service path now goes through this factory instead of constructing `MockSettingsAdapter` directly.

## Consequences

- The shipped configuration keeps `settings.backend: mock`; no automated path in this repository can change a real device setting.
- `LinuxSettingsAdapter`'s logic (capability detection, successful apply, partial-failure rollback, unsupported/permission-denied reporting) was fully exercised with fakes in this session; its real subprocess/sysfs code paths were only exercised as read-only capability detection against this environment's actual (absent) tools, which correctly reported `UNSUPPORTED` for both settings. Real-hardware manual verification (safe ranges, rate limits, feedback-loop suppression, rollback) per HERMES's M9 acceptance criteria remains an explicit, undone follow-up — this is not claimed as passed.
- M10/M11 build the active-user and last-used-preference policy on top of `RateLimitedSettingsAdapter.recent_self_applications()`; M9 does not implement attribution or debouncing itself.
