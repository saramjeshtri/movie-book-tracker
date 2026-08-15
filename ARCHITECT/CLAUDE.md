# Rules for Claude Code on this project

## Models
- Use Sonnet 5 for normal coding tasks.
- Only switch to Opus 5 or Fable 5 when explicitly asked to "audit" or
  "review" code for mistakes — not for regular building.

## Roles
- Sara tells Claude what to build; Claude writes the code.
- Sara tests and reviews before anything is marked done. Do not declare a
  task finished without her confirmation.

## Critical files
- Do NOT change field names, table structure, or endpoint signatures in
  models.py or database.py without checking with Sara first — a teammate's
  branch (feature/external-api-integration) depends on these staying stable.

## Workflow
- Keep changes scoped and sequential — one feature/fix at a time, not
  sweeping rewrites.
- See ARCHITECT/PROJECT_PLAN.md for full project context.

## Git workflow
- Do not commit or push automatically. After making changes, tell Sara what
  was changed and wait for her to review and commit manually.
- When Sara asks you to commit, write a clear commit message describing what
  changed.