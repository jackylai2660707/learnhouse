# LearnHouse Codex Project Guide

Updated: 2026-07-27

Current implementation handoff and exact dirty-worktree/runtime evidence:
`docs/HANDOFF_2026-07-26.md`.

This is the single durable project guide for Codex. It contains product
direction, architecture, coding contracts, current delivery status, priorities,
and verification commands. Specialized operational evidence remains in the
other ordinary documents under `docs/`.

## 1. Product Direction

LearnHouse is being adapted for a Macau primary/secondary school pilot. The
first release is not a course marketplace, parent portal, or payment product.
Its core value is the daily school loop:

```text
administrator creates accounts/classes
-> teacher publishes work
-> student submits or retries
-> system/AI grades
-> teacher reviews subjective work
-> gradebook and school evidence
```

Product rules:

- Traditional Chinese UI and AI output by default.
- Teachers and students may not be technical; keep choices and steps minimal.
- AI reduces preparation, generation, grading, summarization, and remediation
  work, but never becomes the final authority for subjective grading.
- Preserve a manual fallback whenever an optional AI/provider feature fails.
- Prefer a small reliable workflow over additional settings and task types.
- Protect student privacy and avoid exposing individual data in leadership
  reporting.

## 2. Repository Structure

| Path | Responsibility |
| --- | --- |
| `apps/web` | Next.js frontend, public courses, student flows, teacher/admin dashboard |
| `apps/api` | FastAPI backend, SQLModel models, services, AI, assignments, tests |
| `apps/collab` | Hocuspocus/Yjs collaboration service |
| `apps/cli` | Installation and environment bootstrap CLI |
| `apps/mcp` | Project MCP application code, independent of Codex project configuration |
| `docker` | Startup, diagnostics, backup, restore, systemd/cron examples |
| `docs` | This guide plus production operations and migration evidence |

Backend boundaries:

- `apps/api/src/routers/**`: HTTP request/response and permission entry points.
- `apps/api/src/services/**`: business logic.
- `apps/api/src/db/**`: SQLModel models and stable enums.
- `apps/api/migrations/versions/**`: Alembic schema changes.
- `apps/api/src/tests/**`: pytest coverage mirroring service domains.

Frontend boundaries:

- `apps/web/app/**`: Next.js routes and page composition.
- `apps/web/components/**`: reusable UI and domain components.
- `apps/web/services/**`: typed frontend API clients.
- `apps/web/lib/**`: shared helpers, query keys, validation, and readiness rules.
- `apps/web/locales/**`: user-facing translations.

## 3. Core Product Capabilities

### Accounts and classes

- Administrators can import teachers/students in bulk through CSV.
- Traditional/Simplified/full-width header aliases are supported.
- Import results distinguish created, existing, and invalid rows and can export
  correction CSV files.
- `UserGroup` currently represents the pilot's class/group model.

### Assignment loop

- Teachers can publish assignments to groups/classes.
- Deadlines, late submission, retries, highest/latest score policies, gradebook,
  CSV export, and teacher review status are implemented.
- The teacher gradebook shows only student-actionable assignments by default;
  legacy published work without a valid target class remains in setup summaries
  instead of creating a misleading missing row for every student.
- The quick flow supports simple AI work, question-bank work, and essays.
- Student home shows pending, retryable, awaiting-review, and completed work.
- Objective work is graded server-side; essays receive AI draft feedback and
  remain pending teacher confirmation.
- Remediation practice creates 2-3 follow-up questions without overwriting the
  original submission or score policy.
- Pre-publication quality checks reject missing answers, ambiguous choices,
  unsupported subjectivity, and grade mismatch.

### Question bank and self-tests

- Questions are classified by subject, grade, and unit.
- AI-generated questions can be saved to the bank.
- Self-tests draw from the bank and store teacher-visible attempts.

### Leadership evidence

- Weekly Macau-timezone operational summaries support date, class, course,
  subject, stage, grade, year, and term filters.
- Leadership endpoints and CSV output are aggregate-only.
- Groups below the privacy threshold hide participation and score metrics.
- Reports do not return student names, emails, answers, filenames, raw AI
  prompts, or individual comments.

### Optional integrations

- OpenAI-compatible text/image providers use service abstractions.
- Embeddings are configured independently from chat. The default backend is the
  bundled `embeddings` service (`jinaai/jina-embeddings-v2-base-zh`, 768 dims),
  because the configured chat provider returns 404 for `/embeddings`.
- PDF course generation and RAG are optional capabilities. PDF builds use the
  durable PostgreSQL job ledger and dedicated worker, expose server-reported
  stages, survive page reloads and leases/retries, and always remain private
  drafts until a teacher reviews and publishes them.
- The bundled `executor` service exposes the Judge0-compatible API used by code
  execution and objective grading. Do not reintroduce the old external Judge0
  URL without reproducing Chinese-source and Node runtime tests.
- `apps/api/config/code-language-capabilities.json` is the machine-readable
  source of truth for raw executor runtimes, API-owned adapters, and browser
  preview ids. API, executor, durable challenge sync, and frontend surfaces
  must consume it and fail closed. SQL id 82 is an API adapter over Python 71
  and always requires an owned `sqlite_db_path`; assignment CODE tasks do not
  expose that adapter because they have no SQLite upload flow.
- Email, image, embedding, executor, and AI providers have readiness/fallback
  behavior and must not record incorrect grades when infrastructure fails.
- RAG retrieval uses a 768-dimensional cosine HNSW index and requires pgvector
  0.8 or newer so filtered org/course searches can use iterative scans. Every
  grounded answer carries contiguous server-assigned citation numbers and a
  deterministic source footer; the UI links only canonical course/activity UUIDs.
- Every RAG entry point resolves course READ access and server-owned course and
  activity scopes before retrieval. Student scopes include only published,
  unlocked content. Reindex and indexed-content ORM writers share a sorted,
  per-course PostgreSQL advisory transaction lock; new raw SQL or bulk writers
  must acquire the same lock explicitly.
- The bundled embedding container is limited to 5 GiB with a 4 GiB reservation.
  Run the read-only bilingual semantic/vector verifier on a schedule; reindexing
  remains a separate explicit write operation.
- A clean Compose checkout keeps the five-service shape: the executor serially
  builds any missing local Python/Node/C/Java sandbox images before becoming
  healthy. Production SMTP still requires a host-side `0600` secret selected by
  `LEARNHOUSE_SMTP_PASSWORD_FILE_HOST`, and the external `caddy_net` must exist
  before the stack is started.
- The existing `oidc-go-sso` is unsuitable as a school identity provider. Do
  not use it as the unified authentication service for LearnHouse or the linked
  school platforms; any SSO work needs a separately authorized school-IdP plan.

## 4. Programming Course Direction

The public site currently contains two substantial beginner courses:

- HTML/CSS/JavaScript interactive web course: 10 chapters, 5 activities each.
- Python beginner course: 10 chapters, 5 activities each.

There are two programming mechanisms:

1. `CODE` assignment tasks integrated with assignments and grading.
2. Course-page coding challenges with visible/hidden tests, submissions, and
   progress.

The pilot now enforces these programming-course guarantees:

1. Student-readable activity, version, chapter, and export payloads never carry
   reference solutions or hidden tests. A solution is retrieved from a
   server-authorized endpoint only after a formal pass of visible and hidden
   tests; teachers require course update access.
2. Coding-challenge Submit, History, Progress, solution unlock, and teacher
   analytics all use the durable challenge submission/progress tables. A
   visible-test Run is never formal evidence.
3. Challenge identifiers are scoped to organization, course, activity, and
   block. Course create/import/AI planning all synchronize durable challenge
   rows, while teacher-only transfer archives preserve private solution/test
   data outside ordinary activity JSON.
4. The editor uses mobile code/details tabs below the desktop breakpoint, core
   controls are translated to Traditional Chinese, and activity navigation has
   accessible names.
5. Teachers can see pass rate, learners not yet passing, average formal
   attempts, and common visible-test failures without receiving hidden inputs,
   expected output, or student source code.
6. Challenge execution has source, test, additional-file, concurrency, SQLite
   ownership, and revision-race checks. Hidden execution details remain
   redacted.
7. Service-side execution uses the bundled cgroup-v2-compatible Docker executor.
   Supported ids are Python 71, JavaScript 63, TypeScript 74, C 50, C++ 54, and
   Java 62. The measured safe concurrency on this 4-core host is 6.
8. HTML/CSS/browser JavaScript uses `LivePreview` in an iframe with
   `sandbox="allow-scripts"` and no `allow-same-origin`; it must never be sent to
   the server executor. Unsupported runtime languages stay visible but disabled
   with the Traditional Chinese `即將支持` label.
9. Reference solutions and challenge tests can be audited through
   `apps/api/scripts/repair_challenge_content.py --audit`. Passing the audit
   proves solution/test consistency, not pedagogical quality.
10. `Python Judge Test Course` remains non-public and unpublished in production.
11. The reviewed rewrite manifest owns the 40 JavaScript and 11 Python legacy
    fixed-output challenges. Each is now stdin-driven with 2-4 hidden tests;
    reruns must first pass manifest identity/checksum and real Node/Python gates.
12. Student assignment CODE payloads contain neither reference solutions nor
    hidden test/check bodies. They may contain only visible checks plus a
    server-owned hidden-test count; formal grading always uses the DB-owned
    complete suite, while teachers retain private authoring fields.
13. Run and Submit are mutually exclusive through a synchronous coordinator,
    including keyboard shortcuts. Learner scratch tests are Run-only and never
    become formal progress, grading, or solution-unlock evidence.

Treat each guarantee as a regression boundary before changing programming
content, import/export, activity serialization, course permissions, executor
runtimes, or live-preview isolation.

Keep the pilot focused on Python and HTML/CSS/JavaScript. C/C++/Java/TypeScript
are available, but broad advanced-language expansion is not a current priority.

## 5. Backend Contracts

### Database and migrations

- Use SQLModel `select(...)` with async sessions for service queries.
- Keep organization/resource authorization near user-visible data access.
- Avoid N+1 queries on gradebook, assignment queue, and workbench pages.
- A schema change requires model, migration, service/payload, frontend type,
  and test updates together.
- Keep enum values stable once frontend/backend contracts depend on them.
- Never trust client-submitted grades for server-verifiable task types.

Production startup order is fixed:

```text
preflight -> advisory lock -> database classification/migration
-> API/web/collab/nginx processes
```

- Empty databases bootstrap current metadata and reconcile to the audited head.
- Versioned databases upgrade normally.
- Legacy databases with tables but no marker require an explicit verified
  baseline; never guess or blindly stamp head.
- Migration failure must prevent service startup.

### Errors and fallbacks

- User-visible pilot errors should be actionable Traditional Chinese.
- Never return stack traces, raw provider responses, credentials, or secret
  endpoint details.
- AI/provider failures should use typed stable codes and manual fallbacks.
- Infrastructure failure during code grading must leave work retryable or
  teacher-reviewable rather than recording an incorrect grade.
- Async login bookkeeping must be awaited before returning a response.

### Logging and observability

- Use module-level Python loggers.
- Production access logs are single-line JSON stdout events with only safe
  request metadata.
- Do not log query strings, request bodies, authorization/cookie headers,
  prompts, source code, student answers, recipients, or provider bodies.
- API and web Sentry keep default PII disabled and share recursive redaction.
- Liveness is dependency-free; readiness checks PostgreSQL, Redis, migration
  state, and core configuration. Optional providers may report degraded without
  turning core readiness into a failure.

## 6. Frontend Contracts

- Prefer page composition in `app/**` and reusable domain behavior in
  `components/**` or `lib/**`.
- Use shared service clients and query keys rather than local fetch shapes.
- Preserve organization loading and authorization boundaries before rendering
  role-restricted pages.
- Use Traditional Chinese for pilot-facing labels, validation, empty states,
  and provider errors.
- Keep one obvious primary action; hide advanced settings when possible.
- Verify keyboard access, focus, overflow, and 360-390px mobile layouts.
- Backend payload changes require corresponding TypeScript types and consumer
  updates; avoid scattered `any` casts.

## 7. Security and Privacy

- Never place API keys, passwords, tokens, DSNs, database URLs, or real personal
  data in code, migrations, fixtures, docs, logs, screenshots, or commits.
- Production SMTP passwords are loaded from a read-only `0600` mounted secret
  file; do not place them in tracked configuration or container image layers.
- Maintain org scoping and RBAC across routers and services.
- State-changing browser requests retain CSRF/origin protections.
- Backups are private, checksummed, atomic, and verified in isolated temporary
  PostgreSQL; never test restore directly against production.
- External content/image/file handling must enforce size limits, safe formats,
  traversal protection, HTTPS/SSRF boundaries, and sanitized errors.

## 8. Delivery Status

Verified locally or in isolated rehearsal:

- Production preflight and Alembic migration gate.
- Legacy and fresh database migration/restore rehearsal.
- Administrator -> teacher -> student -> grading -> gradebook browser loop.
- Health, CSRF/CORS, structured logging, request IDs, Sentry redaction.
- Backup creation, retention, and strict isolated restore verification. The
  off-site hook remains unconfigured until an operator supplies its destination
  and credentials.
- Bundled executor, AI text/image, local semantic embeddings, PDF/RAG, and
  synthetic email diagnostics.
- Coding-challenge rewrite and executor audit: all 40 JavaScript and 11 Python
  legacy fixed-output challenges were replaced by reviewed stdin-driven
  exercises with 162 total cases and 2-4 hidden tests each. The deterministic
  manifest is identity/checksum guarded and passes real Node/Python gates.
  Stored-solution audit evidence proves suite consistency only, not general
  pedagogical quality; exact runtime apply state belongs in the handoff.
- Browser-preview source consolidation: assignment and course challenges share
  the hardened preview document builder, with safe CSS/JS injection, bounded
  logs, token/source message checks, and `sandbox="allow-scripts"` without
  `allow-same-origin`.
- Durable PDF build jobs: PostgreSQL job state, idempotent create/list/get APIs,
  lease recovery, retry and billing recovery, private staging, upload caps,
  cleanup verification, dedicated worker, and reload-safe frontend polling.
- Real isolated integration evidence: Redis credit reservation/refund/period
  behavior passed 8 tests; PostgreSQL RAG advisory locking passed 9; durable PDF
  claim eligibility/concurrency passed 8. Each verifier removed its temporary
  container and left zero Redis keys or PostgreSQL public tables.
- Browser P1 is complete in production: the authenticated smoke verified
  assignment course-list access, teacher RAG reindex access and persistent
  Traditional-Chinese states, PDF build entry points, canonical Node language
  labels, authenticated Run, disabled anonymous actions, public/student hub
  links, and 360/390px layouts without horizontal overflow.
- Student product hub linking the typing and Tenlingo platforms through their
  live Caddy URLs.
- Weekly leadership evidence and privacy thresholds.
- Final source-side gate on 2026-07-27: API completed with `3,197 passed, 20
  skipped, 0 warnings` in 352.34 seconds; focused assignment/programming/
  migration coverage passed 363 tests. Alembic is a single head
  (`i0j1k2l3m4n5`).
- Course ZIP imports use a real async savepoint per course. Savepoint release
  and rollback are awaited through `async with`, so a failed course cannot
  discard an earlier successful import or mask the original error. The async
  test fixtures mirror real `AsyncSession` semantics and the full suite emits
  no unawaited-coroutine warnings.
- Frontend Node tests passed 68/68, host TypeScript reports zero errors, affected
  pilot paths pass scoped ESLint, and the clean Bun 1.3.14 root Docker
  `frontend-builder` production target completed successfully. The former six
  BoardCanvas/DragHandle package-identity errors were removed by rebuilding the
  host dependency tree from the frozen Bun lockfile.
- The strict production backup `20260727T180112Z` restored successfully in the
  official isolated verifier: 60 tables and current/expected revision
  `i0j1k2l3m4n5`. The original backup was not changed.
- Production is deployed at Alembic `i0j1k2l3m4n5`. The h10 migration repairs
  legacy assignment `learning_objectives` and `target_usergroup_ids` SQL/JSON
  nulls to `[]`, then enforces `NOT NULL` with a JSON `[]` default. All five
  Compose services (app, db, redis, executor, and embeddings) are healthy.
- The production challenge manifest reports 51 considered, 51 already applied,
  and no pending changes. An 81/81 stored challenge audit still proves only
  solution/test consistency; it is not evidence of pedagogical quality.
- The clean-install gates cover executor runtime-image bootstrap and fresh
  PostgreSQL metadata reconciliation. The isolated loopback PostgreSQL
  regression passed after h9/h10, and the executor bootstrap is serial and
  fail-closed.

Important state distinction:

- The worktree remains a large dirty worktree and its unrelated changes must be
  preserved.
- Production has been deployed and verified at the h10 head, but the worktree
  has not been committed or pushed.

Current priorities:

1. Perform final post-deployment verification, then create a non-overwriting
   tracked/untracked snapshot. With explicit Git authorization, review, stage,
   commit, and push the dirty worktree on these eight boundaries: platform/
   security/migrations; assignments/gradebook; challenge/executor/content
   manifest; RAG authorization/index locking/content integrity; durable PDF
   jobs; hub/preview frontend; operations/backup; and durable documentation.
2. Configure an off-site content-backup destination, hook, and credentials,
   then verify the remote copy through the strict isolated restore workflow;
   `offsite_backup_unconfigured` remains the only readiness degradation.
3. Obtain an operator decision and execute the approved credential-rotation
   plan; do not expose or infer credentials while making that decision.

Explicitly deferred:

- Parent portal.
- Course marketplace and payments for the school pilot.
- Free-form autonomous AI agents.
- Large task-type expansion.
- Multi-language internationalization beyond completing Traditional Chinese.
- Complex multi-language Judge expansion.

## 9. Verification Commands

All shell commands use the `rtk` prefix.

API targeted suites, from `apps/api`:

```bash
rtk timeout 300 uv run pytest -q src/tests/services/test_assignments_service.py
rtk timeout 300 uv run pytest -q src/tests/services/test_org_users_service.py
rtk timeout 300 uv run pytest -q src/tests/services/test_question_bank_service.py
rtk timeout 300 uv run pytest -q src/tests/services/test_self_tests_service.py
```

AI and integration work:

```bash
rtk timeout 300 uv run pytest -q \
  src/tests/services/test_ai_assignment_config.py \
  src/tests/services/test_ai_assignment_generation_service.py \
  src/tests/services/test_ai_openai_compatible.py \
  src/tests/services/test_rag_embedding_service.py \
  src/tests/services/test_rag_query_service.py

# List current vector counts without writing.
/app/api/.venv/bin/python scripts/reembed_courses.py --dry-run
```

Programming work:

```bash
rtk timeout 300 uv run pytest -q \
  src/tests/services/test_coding_challenges_service.py \
  src/tests/routers/test_code_submissions_router.py \
  src/tests/services/test_judge0_service.py \
  src/tests/routers/test_code_execution_security.py \
  src/tests/services/test_assignments_code_grading.py

# No AI calls or writes: prove all stored solutions still pass stored tests.
/app/api/.venv/bin/python scripts/repair_challenge_content.py --audit

# Runtime inventory and health.
curl -fsS http://executor:2358/health
curl -fsS http://executor:2358/languages
```

Frontend, from `apps/web`:

```bash
rtk bun install --frozen-lockfile
rtk timeout 300 node --test tests/*.test.mjs
rtk timeout 300 ./node_modules/.bin/tsc -p ./tsconfig.json --noEmit
rtk timeout 300 bun run build
```

Do not run `npx tsc` from the repository root; it resolves the wrong package and
prints `This is not the tsc command you are looking for`.

The canonical frontend package manager is Bun 1.3.14 (recorded in
`apps/web/package.json`, Docker build stages, and CI). CI treats unit tests,
typecheck, and the production build as release gates. Lint currently reports a
large inherited baseline and remains advisory until that baseline is reduced;
the `lint` script itself returns a real non-zero status on violations.

General checks:

```bash
rtk git diff --check
rtk git status --short
```

Run broader tests when the affected surface or risk justifies them. Treat a
passing existing suite as insufficient when the bug represents a missing
contract; add a regression test for that contract.

## 10. Operations and Documentation

Detailed ordinary references:

- `docs/HANDOFF_2026-07-26.md`: exact current dirty-worktree/runtime state,
  evidence, known defects, and next-agent execution order.
- `docs/PRODUCTION_OPERATIONS.md`: startup, migration, health, backup, restore,
  and incident procedures.
- `docs/PRODUCTION_DB_MIGRATION_REHEARSAL_2026-07-12.md`: isolated migration and
  restore evidence.

Working rules:

- Inspect `rtk git status --short` before edits; this repository commonly has a
  large dirty worktree.
- Preserve unrelated user changes and avoid broad refactors during focused work.
- Search before changing shared constants, enum values, payload fields, or
  repeated behavior.
- For cross-layer changes, map database -> service -> router -> frontend client
  -> state -> component and validate the full round trip.
- Do not deploy, restart services, mutate production, commit, or push without
  explicit user authorization.
- Update this guide when a durable project-wide contract or priority changes.
