# Plan 0077: Persist Provider Context References

## Status
Complete

## Goal
Let a provider-message step durably declare an ordered list of earlier steps in the
same run as explicit context references, validate the declaration before mutation,
and expose only the referenced step ids through read-only inspection.

## Tasks
- [x] Add ordered context references to the durable `RunStep` model and payload.
- [x] Validate provider-only, known, same-run, earlier-step references before append.
- [x] Add repeatable `run add-step --context-step` CLI support and inspection output.
- [x] Preserve references across lifecycle rewrites and explicit retry attempts.
- [x] Add focused persistence, validation, redaction, and retry tests.
- [x] Run the full suite, rebuild/check the index, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #78. Dispatch-time eligibility/output resolution
and provider-native multi-message payload mapping remain out of scope for #79 and
#80 respectively.

Implementation complete. `RunStep.context_step_ids` reloads as an ordered tuple;
the CLI exposes repeatable `--context-step`, and inspection shows ids only. Focused
runtime/CLI/state tests, the full suite, fresh-process CLI/SQLite UAT, index rebuild
and check, and `git diff --check` all pass.
