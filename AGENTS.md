# SpillTheReel execution instructions

## Read first

For implementation, read `docs/LUNA_IMPLEMENTATION_PLAN.md`, then the assigned
task and relevant code. For the next execution order, read
`docs/LUNA_NEXT_IMPLEMENTATION_PLAN.md` and its linked batch review.
`docs/PROJECT_REVIEW_2026-10-16.md` records the baseline.
Read `buildphase.md` for the original sequencing, but use the dated decision
override at its top. `HANDOFF.md` is historical and is not the execution plan.
Existing code and verified tests determine what is implemented; old completion
percentages and comments do not prove a feature works.

## Product decisions confirmed by the owner on 2026-10-16

- Imports deliberately run full media ingestion, because export metadata does
  not supply the transcription needed for recall. Do not restore text-first
  tiering or age-based automatic downgrades from the old PRD/TRD.
- Free bulk-import allowance: **250 once per account**, consisting of **150
  Instagram** and **100 pooled across the other supported apps**.
- Premium: **1,000 total bulk-import items once per account across all supported
  apps**. No monthly reset and no fresh allowance for every uploaded ZIP.
- These are bulk-import limits. Do not silently impose them on individual
  share-sheet saves. Upgrade/refund details proposed in the plan are explicitly
  implementation assumptions, not additional confirmed product decisions.
- Keep Expo/React Native, FastAPI, Supabase Auth/Postgres/Storage, pgvector,
  Cloud Run/Cloud Tasks, and the existing extractor interfaces.

## Required delegation for implementation

The owner requests GPT-6 Luna with specialist subagents. For a multi-file task,
the primary agent must delegate at least one bounded specialist task and retain
integration ownership. Use `.codex/agents/*.toml` in Codex and
`.opencode/agents/*.md` in OpenCode. Do not merely list agent names in a plan.
Use at most three concurrent children; never give two editors ownership of the
same files. For a tiny edit, one primary agent is sufficient.

If the runtime exposes custom agent selection, select the matching profile.
If it only exposes a general spawn tool, read the relevant TOML profile and
pass its `developer_instructions` plus the specific task, owned files, checks,
and return format in the spawn message. Use `gpt-6-luna` where an explicit model
selector is supported; otherwise inherit the parent Luna model. Do not invent
an `agent_type` argument the runtime does not support. Report unavailability
honestly if no spawn tool exists.

| Work | Specialist |
|---|---|
| Routes, jobs, contracts | fastapi-developer |
| Expo UI, navigation, share intents | expo-react-native-expert |
| Schema, RLS, queries, quotas | postgres-pro |
| Terraform and deploy definitions | terraform-engineer |
| GCP topology, IAM, queue capacity | cloud-architect |
| Regression and device scenarios | test-automator |
| Reproducing failures | debugger |
| Retrieval, grounding, processing budgets | llm-architect |
| Summary/answer prompt evaluations | prompt-engineer |
| Independent final review | code-reviewer |
| Tenant isolation, uploads, auth, quota abuse | security-auditor |

Use `code-reviewer` after each completed batch; add `security-auditor` for auth,
quota, database, upload, deletion, or IAM changes. Both reviewers must not edit
or run shell commands. The parent runs requested checks. Codex review profiles
disable shell, but **do not create an independently enforced read-only sandbox**;
they inherit parent permissions. No nested delegation.

## Design work

Use `.agents/skills/impeccable/SKILL.md` for frontend work. This is a native
mobile product; follow native guidance, not the web-only detector. Read
`docs/DESIGN_EXECUTION_BRIEF.md` for the reviewed direction. Preserve the warm
brand; prioritize task completion, readable body text, semantic tokens, accessible
controls, real data, empty/error/loading states, and safe-area/keyboard handling.
Do not apply website/GSAP recipes to React Native screens.

## Working agreements

- Keep changes scoped to the assigned task. Preserve unrelated working changes.
- Use Alembic for this repo's schema history. Read both local Supabase skills
  before database work. Do not rewrite applied migrations or introduce a
  parallel Supabase migration history without an explicit architecture decision.
- Never use production as a test fixture. MCP installation is not authorization
  to deploy, apply migrations to live databases, delete user data, or run paid
  bulk extraction. Local implementation and isolated validation can proceed.
- Keep credentials in local environment/auth storage, never tracked config,
  screenshots, task messages, or reports. Inspect `.env.example`, not secret
  values. Bright Data MCP and the app's ingestion provider are separate clients.
- Parent owns shared manifests, lockfiles, migrations sequencing, contracts,
  and integration. Specialists request changes to these before overlapping work.
- Add regression tests for demonstrated behavior failures. Do not rewrite the
  whole app or upgrade SDKs to make a small test pass.
- Record commands, outcomes, skips, and device/cloud checks still needed.
  Unit tests and TypeScript success do not prove native or cloud behavior.
- Update the task ledger and relevant product docs after each batch. Stop for
  a genuine product decision or external credential; continue independent work.
