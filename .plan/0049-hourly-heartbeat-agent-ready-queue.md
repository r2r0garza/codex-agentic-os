# Plan 0049: Hourly Heartbeat Agent-Ready Queue Report

## Status
Complete

## Goal
Make the hourly heartbeat workflow report the live `agent-ready` issue queue instead of
scanning a `.plan/*.md` checkbox format nothing produces anymore.

## Tasks
- [x] Replace the "Show next plan task" step's `.plan/*.md` `- [ ]` scan with a
  `gh issue list` query for open issues labeled `agent-ready` and not `blocked`.
- [x] Add the `issues: read` permission the new step's `gh` call requires.
- [x] Leave `ci.yml` and workflow triggers unchanged.
- [x] Validate the workflow YAML parses and the step's command produces sensible output
  against the current repo/gh state.

## Resume Notes
Selected queue issue: #35. `.github/workflows/hourly-agentic-os.yml`'s heartbeat job now
runs `gh issue list --repo "$GITHUB_REPOSITORY" --state open --label agent-ready --json
number,title,labels` piped through a `--jq` filter that drops any issue also labeled
`blocked` and prints a count plus `#number title` lines, or "No unblocked agent-ready
issues found." when none remain. No `codex_agentic_os` source changed, so the committed
index is unaffected. Resume with the next prioritized unblocked `agent-ready` GitHub
issue.
