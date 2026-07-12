# Plan 0050: Container Environment Variable Passthrough

## Status
Complete

## Goal
Allow explicitly selected environment variables to be passed into command steps executed in Docker or Podman, mirroring the bind-mount precedent in [[0044-container-bind-mounts]].

## Tasks
- [x] Add validated `env` key/value pairs to `SandboxSpec`.
- [x] Render `--env KEY=VALUE` deterministically after mounts and before the image.
- [x] Add repeatable strict `--env KEY=VALUE` parsing to `run execute-next`.
- [x] Verify env rendering, CLI wiring, and rejection without state mutation.

## Resume Notes
Selected queue issue: #40. `run execute-next` accepts repeatable `--env KEY=VALUE` flags and rejects malformed values (missing `=`, empty key, or empty value) before claiming a queued step. No `.env` file loading or host environment passthrough is supported. Resume with the next prioritized unblocked `agent-ready` GitHub issue.
