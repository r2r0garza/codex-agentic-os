# Plan 0052: Provider Defaults Listing CLI

## Status
Complete

## Goal
Expose the committed `DEFAULT_PROVIDER_SPECS` registry through a read-only CLI command so operators can discover provider kinds, default models, endpoints, credential-variable names, and declared capabilities without inspecting Python source.

## Tasks
- [x] Add `codex-agentic-os provider list` printing every `DEFAULT_PROVIDER_SPECS` entry via `ProviderSpec.to_dict()`, preserving registry order.
- [x] Keep credential output limited to environment-variable names; never read or print secret values.
- [x] Perform no network or state-database access for the command.
- [x] Add CLI coverage for ordering, complete field serialization, credential-value absence, and no-network-access.

## Resume Notes
Selected queue issue: #42. Editing provider configuration, endpoint probing, and credential validation remain out of scope. Resume with the next prioritized unblocked `agent-ready` GitHub issue.
