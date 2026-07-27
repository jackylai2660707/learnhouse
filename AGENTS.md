@/root/.codex/RTK.md

# Codex Project Instructions

This repository is maintained with Codex only. Do not initialize or depend on
an external task, memory, workflow, agent, MCP, or hook harness.

Before non-trivial work:

1. Read `docs/CODEX_PROJECT_GUIDE.md`.
2. Read `docs/HANDOFF_2026-07-26.md` while the current dirty-worktree project is
   being stabilized; it records runtime evidence and work that is not in git.
3. Run `rtk git status --short` and preserve all unrelated user changes.
4. Inspect the relevant code and tests before editing.

## Product Direction

- The active product is a Macau primary/secondary school pilot.
- Traditional Chinese is the default for user-facing UI and AI output.
- Keep teacher, student, and administrator flows short and school-friendly.
- Prioritize the assignment loop: accounts/classes -> publish -> submit ->
  system/AI grading -> teacher review -> gradebook/evidence.
- AI should reduce teacher workload, but teachers retain final authority for
  subjective grading.

## Working Rules

- Prefix shell commands with `rtk` as described in `/root/.codex/RTK.md`.
- Do not expose secrets, credentials, student submissions, or personal data in
  logs, tests, documentation, or responses.
- Do not deploy, restart services, mutate production data, commit, or push
  unless the user explicitly asks.
- Keep changes scoped; do not revert or rewrite unrelated dirty-worktree files.
- Update `docs/CODEX_PROJECT_GUIDE.md` when a durable project-wide decision or
  operating rule changes.
