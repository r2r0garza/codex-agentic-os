# Plan 0079: Map Resolved Context into Native Provider Message Payloads

## Status
Complete

## Goal
Send resolved referenced step outputs to every supported provider adapter
family as explicit prior-context messages, ordered before the current
provider step message, while a step with no context references keeps
today's exact single-turn payload.

## Tasks
- [x] Define the provider-neutral message sequence produced from resolved
      context: for each declared reference in order, a `(user, assistant)`
      pair replaying the referenced step's objective and durable output,
      placed after any system instruction and before the current step's
      user message.
- [x] Build that sequence in `execute_next_step` from the already-resolved
      `context_step_ids` on the dispatched step, reusing the existing
      `ChatRequest`/`ChatMessage` transport boundary (no adapter changes
      needed, since all three adapters already map an arbitrary ordered
      `messages` tuple into their native shape).
- [x] Render a command step's referenced output as
      `exit_code=…\nstdout:\n…\nstderr:\n…`; render a provider step's
      referenced output as its persisted response `content`.
- [x] Add offline adapter transport tests (OpenAI-compatible, Anthropic,
      Google) proving multi-turn context payloads and a no-reference
      single-turn regression payload.
- [x] Add a runtime dispatch test proving resolved context (mixing a
      command-step and a provider-step reference) reaches the adapter in
      declared order and the response persists normally.
- [x] Run the full suite, rebuild/check the index, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #80, the final Sprint 11 issue.

Implementation complete. The `(user, assistant)`-pair replay keeps every
adapter family's payload valid, including Anthropic's strict user/assistant
alternation, without any adapter-level changes since `chat.py` already
accepted an arbitrary ordered `ChatMessage` tuple. Focused adapter/runtime
tests, the full suite, a fresh-process CLI/SQLite UAT (create via CLI,
dispatch via a second process using the public `execute_next_step`
injection points, read back via a third CLI process), index rebuild and
check, and `git diff --check` all pass.
