# Plan 0055: Sandbox Network Opt-In

## Status
Complete

## Goal
Let an operator explicitly enable container network access for a queued command step while preserving the sandbox default of no network.

## Tasks
- [x] Add `run execute-next --network` as an explicit boolean opt-in.
- [x] Map the flag to the existing `SandboxSpec.network_enabled` field.
- [x] Verify omitting `--network` preserves isolated (`--network none`) command construction.
- [x] Verify `--network` composes with image, mount, env, and working-directory options.
- [x] Confirm help text identifies network access as an explicit opt-in.

## Resume Notes
Selected queue issue: #47. `SandboxSpec` and `ContainerSandbox.command()` already
implemented the two network modes; this issue only exposed the existing policy
through `execute-next`. No changes to `sandboxes.py` were required. Resume with the
next prioritized unblocked `agent-ready` GitHub issue.
