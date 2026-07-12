# Repository Agent Guide

## Environment

- Activate `.venv` from the repository root before Python commands.
- Use the environment’s `python3`, `pip3`, `pytest`, and `codex-agentic-os`.
- Never install into system Python or use `--break-system-packages`.

## Project records

- GitHub milestones define ordered vertical sprints. The lowest-numbered open milestone is active.
- GitHub issues assigned to the active milestone are the execution queue.
- `.plan/` records implementation structure; `.decisions/` records durable rationale.
- Read only records relevant to the selected issue and affected architecture.
- `MEMORY.md` is the repository-local scheduled-run handoff and retains at most five runs.

## Repository index

- Treat committed `.code-index/` artifacts as derived orientation evidence.
- Start with `.code-index/manifest.json` and prefer `codex-agentic-os index explain <qualified-name>` for indexed symbols.
- Check freshness before relying on the index, inspect source for missing or unresolved relationships, and rebuild the index when tracked source changes.

## Verification and documentation

- Add focused tests for changed behavior and run the full suite when proportionate to risk.
- Run `codex-agentic-os index check` and `git diff --check` before completion.
- Read or update README only for user-facing setup or orientation changes.
- Read or update DEVELOPMENT only for installation, testing, runtime usage, scaffolding, or index-workflow changes.
- Avoid documentation churn that does not reflect a behavior, contract, or workflow change.

## Change discipline

- Preserve unrelated user changes and avoid destructive Git operations.
- Keep each implementation run to one focused issue.
- Do not create work outside the active milestone merely to maintain queue depth.
