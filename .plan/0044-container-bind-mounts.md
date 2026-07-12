# Plan 0044: Container Bind Mounts

## Status
Complete

## Goal
Allow explicitly selected host directories to be exposed to durable command steps executed in Docker or Podman.

## Tasks
- [x] Add validated host/container mount pairs to `SandboxSpec`.
- [x] Render mounts deterministically after resource flags and before the image.
- [x] Add repeatable strict `--mount HOST:CONTAINER` parsing to `run execute-next`.
- [x] Verify mount rendering, CLI wiring, and rejection without state mutation.

## Resume Notes
Selected queue issue: #34. `run execute-next` accepts repeatable explicit bind mounts and rejects malformed values before claiming a queued step. No mount modes, named volumes, or automatic working-directory mounts are supported. Resume with the next prioritized unblocked `agent-ready` GitHub issue.
