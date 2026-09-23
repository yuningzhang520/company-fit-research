# Fit Rubric

"Fit" means the company's workload — or its users' workload — matches what TiDB or
drive9 is built for. It does NOT mean "they use a database" or "they do AI".
Decide the relationship type first, then answer three questions from evidence,
then score.

## Step 1. Relationship type — decide this before anything else

- `direct`  — their own product's agents / state would run on TiDB or drive9.
- `partner` — they are a platform; THEIR USERS' agents need TiDB or drive9, and we
              reach those users through them. Typical partners: sandbox providers,
              agent frameworks / SDKs, app builders that provision a DB or storage for
              user apps, marketplaces and integration catalogs.
- `both`    — they consume it themselves and could also expose it to their users.

For `partner`, Q1 and Q2 below are asked about THEIR USERS' workloads, not theirs.
Also name the integration surface: mount target, SDK / plugin, template, marketplace
listing, default DB option for user apps, co-selling.

## Step 2. Three questions

### Q1. What does the state look like? (→ TiDB)
- Is storage provisioned per end-user, per app, or per agent? (tenant explosion)
- Is the workload write-heavy and bursty (agents writing, not humans)?
- Does state need to persist across sessions / runs / handoffs?
- Are they on MySQL or MySQL-compatible today? (Postgres = harder migration, say so)

### Q2. Do agents read/write files inside sandboxes? (→ drive9)
- Do agents clone repos, install deps, edit files, run tests, produce artifacts?
- Do tasks run long (hours), get retried, handed off, or run as parallel attempts?
- Do they need Git semantics (diff, checkpoint, rollback, conflict-aware commit)?
- Do they run sandboxes on E2B / Firecracker / Docker / cloud VMs?

### Q3. What are they on today?
- Look at docs, blog, changelog, job posts, GitHub, integration pages. Name what you find.
- If nothing is public, write "unknown". Do not guess a stack.

## Step 3. fit_target
- `drive9` — Q2 strong, Q1 weak or unknown
- `TiDB`   — Q1 strong, Q2 weak or unknown
- `both`   — Q1 and Q2 both strong
- `weak`   — neither; still fill every field, just be honest

## Step 4. fit_score (1–5)
- **5** — Agent-scale workload confirmed by public evidence (their own or their users'):
  per-agent / per-app state, or long-running sandboxed coding agents, at meaningful
  scale. Clear pain we solve. For `partner`: a visible integration surface already exists.
- **4** — Strong evidence on one side (Q1 or Q2), the other side plausible.
- **3** — Real AI / agent product but state and file patterns are not visible publicly.
  Fit is plausible, not demonstrated.
- **2** — Adjacent (dev tools, infra, data) but no agent-scale state or sandbox story.
- **1** — Pure compute, pure frontend, consumer app with thin state, or publicly
  committed to a competing database with no migration signal.

## Anti-patterns — never count these as fit
- "They store data, so they need a database."
- "They use AI, so they need vector search."
- Company size or funding alone. Money qualifies the account; it is not a fit signal.
- Framing drive9 as replacing or competing with sandbox providers (E2B, Runloop,
  Daytona). They are partners and mount targets.