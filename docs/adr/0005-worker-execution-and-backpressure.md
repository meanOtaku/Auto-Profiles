# ADR 0005: Worker Execution and Backpressure

- Status: Accepted
- Date: 2026-07-30

## Context

Camera capture, inference, persistence, and event delivery have different latency and failure characteristics. Unbounded or implicit background work can leak resources, reorder state, and hide shutdown failures.

## Decision

All background execution must have explicit ownership, bounded queues, documented overflow behavior, cancellation, timeouts, and deterministic shutdown. Synchronous implementations remain preferred until measurement requires concurrency. Durable externally visible events use transactional persistence rather than best-effort detached tasks.

## Consequences

M0 has no detached worker. Later milestones must introduce workers only at measured boundaries and test overload, cancellation, retry, ordering, and shutdown behavior.
