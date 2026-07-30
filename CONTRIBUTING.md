# Contributing

## 1. Scope

Contributions must follow the headless-first architecture and the milestone sequence defined in `HERMES.md` and `ROADMAP.md`.

## 2. Before Starting

Read:

1. `HERMES.md`
2. `ARCHITECTURE.md`
3. `CODING_STANDARDS.md`
4. `TESTING.md`
5. `ROADMAP.md`

Check the current milestone and avoid introducing unrelated future functionality.

## 3. Branching

Use one branch per milestone or focused change.

Recommended formats:

```text
milestone/m02-multi-face-detection
feature/profile-merge
fix/camera-reconnect
refactor/settings-adapter
```

Keep pull requests small enough to review.

## 4. Development Workflow

1. Inspect existing code and tests.
2. State the behavior being changed.
3. Add or update tests.
4. Implement the smallest complete change.
5. Run formatting, linting, type checking, and tests.
6. Update documentation and configuration examples.
7. Record manual verification steps.

## 5. Required Checks

Run the repository-defined equivalents of:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
uv run pip-audit
uv run pip-licenses --from=mixed --ignore-packages face-profile-system --fail-on=UNKNOWN --format=plain
uv build
```

When changing a milestone with hardware-facing behavior, also run the documented manual test.

## 6. Pull Request Requirements

Every pull request must contain:

- Purpose
- Related milestone or issue
- Summary of behavior changes
- Files or modules affected
- Tests added or changed
- Commands executed
- Exact test results
- Manual verification steps
- Configuration or migration impact
- Security and privacy impact
- Known limitations

## 7. Commit Messages

Use concise imperative messages.

Examples:

```text
Add video frame source
Reject low-quality enrollment samples
Persist profile settings
Prevent duplicate candidate promotion
```

Avoid vague messages such as `updates`, `changes`, or `fix stuff`.

## 8. Compatibility

Do not break public APIs, configuration keys, stored data, or CLI behavior without:

- A documented reason
- A migration path
- Updated tests
- Updated release notes

## 9. Database Changes

Every schema change must include:

- A migration
- Upgrade test
- Downgrade strategy where practical
- Data-loss assessment
- Updated schema documentation

Never edit a production database manually as part of normal application startup.

## 10. New Dependencies

Before adding a dependency, document:

- Purpose
- Licence
- Maintenance status
- Platform support
- Runtime size or performance impact
- Whether a standard-library or existing dependency solution is sufficient

Model files require the same review, especially for commercial-use licensing.

## 11. Privacy Requirements

Changes involving face images, embeddings, logs, exports, APIs, or remote access must document:

- What biometric or personal data is stored
- Why it is required
- Retention behavior
- Access-control implications
- Deletion behavior

Never include real user biometric data in the repository.

## 12. Test Data

Use:

- Synthetic images
- Publicly licensed test data
- Explicitly consented internal data kept outside version control

Do not commit personal face datasets.

## 13. AI-Agent Contributions

An AI coding agent must:

- Work on one assigned milestone only
- Read all project documents before editing
- Never claim tests were run unless they were actually executed
- Present exact commands and outcomes
- Avoid broad rewrites unless required
- Preserve manually written code and comments unless replacement is justified
- Stop after the assigned milestone

## 14. Review Checklist

A reviewer should verify:

- Architectural boundaries remain intact
- Failure cases are tested
- Unknown enrollment remains conservative
- Real settings are not used in unit tests
- Logs do not expose biometric vectors
- No UI business logic was introduced
- Documentation matches implementation
