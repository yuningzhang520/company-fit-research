# Gold example — copy this tone and depth exactly

Input: Replit | https://replit.com | app_building | existing

```json
{
  "company": "Replit",
  "website": "https://replit.com",
  "category": "app_building",
  "source_list": "existing",

  "relationship_type": "both",
  "integration_surface": "default DB option for user apps (where Neon / Databricks Lakebase sit today); drive9 mount inside Replit Agent's sandboxes",

  "intro_zh": "Replit 是一个用 AI 造软件的平台：用户用大白话描述想要的应用，它的 Agent 负责写代码、跑起来、部署上线，全程不需要用户自己写代码。现在大部分用户不是程序员。每个做出来的 app 都是独立跑的，自带数据库、自带部署。",

  "fit_headline_zh": "自用 drive9 + 给用户 app 配库，两条线",

  "fit_zh": "这家两种关系都有。合作那条线在 TiDB：Replit 给每个用户的每个 app 配一个数据库，几千万用户就是海量小数据库，现在这个位置是 Neon 和 Databricks Lakebase 在做。TiDB 的切入点是成为它 app 的数据库选项之一，走的是 Manus 那种上百万 tenant 的路。摩擦点是它现在整条线是 Postgres 系，TiDB 是 MySQL 兼容，不是零成本。直接用的那条线在 drive9，而且更顺：Replit 官方说过 Agent 会连续跑一小时以上，做 app 要装依赖、改文件、跑测试、反复试，都发生在临时 sandbox 里，sandbox 一销毁现场就没了。drive9 的 durable workspace 加 LayerFS 正好解决跑长任务、多次尝试、并行分支不互相覆盖这几件事。",

  "fit_target": "both",
  "fit_score": 4,

  "pitch_en": "Replit Agent runs for an hour-plus per task, installing deps, editing files, and running tests inside ephemeral sandboxes. drive9 gives every sandbox a POSIX mount over a durable, server-side workspace, so source, artifacts, and test results survive sandbox teardown. LayerFS lets parallel attempts write into isolated layers over one base and commit only what passes — conflict-aware, so a stale attempt can't overwrite newer work. Agents keep using git, npm, and grep as-is. Separately: per-app databases at your user count is the multi-tenant pattern TiDB already runs for Manus at ~1M tenants — worth a conversation if a MySQL-compatible option for user apps is ever on the table.",

  "why_selected_zh": null,

  "funding_last_stage": "Series D",
  "funding_last_amount": "$400M",
  "funding_last_date": "2026-03",
  "funding_total": "~$900M",
  "funding_source_url": "https://replit.com/blog/replit-raises-400-million-dollars",

  "arr": "$250M (2025)",
  "arr_confidence": "estimated",
  "arr_source_url": "https://www.arr.club/replit",

  "stack_signals": ["Postgres via Neon partnership", "Databricks Lakebase integration (2026)", "usage-based billing for Agent"],
  "needs_confirmation": ["Whether TiDB Cloud offers a Postgres-compatible path relevant to Replit's user apps"],
  "evidence_urls": ["https://replit.com/blog/replit-raises-400-million-dollars", "https://replit.com/news/funding-announcement"],
  "notes": "Lead with drive9 (direct). TiDB is a partnership conversation, slower because of Postgres lock-in."
}
```

Why this example is good:
- relationship_type is decided first and fit_zh opens by naming it.
- intro_zh explains to a non-technical reader in 3 sentences.
- fit_zh separates the partner line and the direct line, names the concrete workload
  pattern for each, cites an approved proof point, and states the friction (Postgres)
  instead of hiding it.
- pitch_en speaks to an engineer, uses drive9's own vocabulary (durable workspace,
  LayerFS, conflict-aware, POSIX mount), leads with the stronger side, and keeps the
  partnership angle to one sentence at the end.
- Numbers carry a source URL; ARR confidence is labeled.