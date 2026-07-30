# ADR 0004: API and WebSocket Security Defaults

- Status: Accepted
- Date: 2026-07-30
- Decision milestone: M12

## Context

Future REST and WebSocket surfaces expose identity, candidate, settings, and operational data. Network exposure changes the threat model materially.

## Decision

Network services are disabled until M12 and bind to loopback by default. A non-loopback deployment must fail closed unless authentication is configured. REST and WebSocket paths receive equivalent authentication, authorization, audit, rate-limit, request-size, and log-redaction controls. Administrative operations use explicit roles.

## Consequences

No early milestone opens a network listener. M12 acceptance requires negative authorization tests and secure deployment documentation, not only endpoint functionality.
