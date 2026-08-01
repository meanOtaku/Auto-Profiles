# ADR 0018: Optional Static Dashboard as a Pure API Client

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M13

## Context

M13 must add a dashboard, profile/candidate review, settings editing, and live events, containing no domain logic of its own — UI closure must have no effect on the daemon.

## Decision

`ui/dashboard.html` is one self-contained static HTML/vanilla-JS file (no build step, no new Python or JS framework dependency) that calls the M12 `/api/v1` REST endpoints via `fetch()` for every action (list/approve/reject candidates, list profiles, list events). It holds a bearer token only in page memory (an in-page `<input>`, never `localStorage`), and performs zero recognition, enrollment, or active-user decisions itself — every button click is a direct REST call whose result is simply rendered. `UIConfig.enabled` (default `false`) gates a single `GET /` route in `api/app.py` that serves this static file; disabling it removes the route entirely rather than merely hiding UI elements.

## Consequences

- Verified in this session: with `ui.enabled: true`, `GET /` returns the dashboard HTML; the daemon and its API function identically whether or not any browser is connected, since the page holds no server-side session state.
- WebSocket live-event integration in the dashboard itself was not added given the scope/time available in this milestone; the dashboard's event table is a manual-refresh REST poll of `GET /events`. This is a documented, deliberate scope reduction, not an oversight.
- No new runtime dependency was introduced.
