# Plan 0054: Container Working Directory

## Status
Complete

## Goal
Allow durable command steps to select an explicit absolute working directory inside Docker or Podman.

## Tasks
- [x] Add validated optional `working_dir` configuration to `SandboxSpec`.
- [x] Render `--workdir PATH` deterministically after mounts and environment variables and before the image.
- [x] Add optional `--workdir PATH` wiring to `run execute-next`.
- [x] Verify Docker and Podman rendering, CLI composition, and rejection before state mutation.

## Resume Notes
Selected queue issue: #44. `run execute-next` accepts one explicit absolute in-container working directory and composes it with mounts and environment variables. Relative, empty, and whitespace-only paths are rejected before claiming a queued step. No host-directory changes, mount inference, or shell wrapping are supported. Resume with the next prioritized unblocked `agent-ready` GitHub issue.
