# company-fit-research

Batch-researches a list of companies and scores how each one fits two products: TiDB
(distributed SQL) and drive9 (a filesystem for AI agent sandboxes). Every company is
researched in its own headless Claude Code session with web search and access to the
internal reference docs, and comes back as one validated JSON record: a plain-language
Chinese summary, a fit assessment, an English pitch, and funding and ARR figures with
source URLs. The records are exported to CSV and to an Excel report that the team pastes
into its shared document.

## Folder layout

```
enrich.py              research pipeline: input.csv -> output.jsonl
export.py              output.jsonl -> output.csv
export_sheet.py        output.jsonl -> report.xlsx (needs openpyxl)
prompt_template.md     the research prompt; three context files are pasted into it
repair_prompt.md       the no-tools prompt used to fix format errors
context/
  fit_rubric.md        how fit is judged and scored (pasted into the prompt)
  example.md           one gold-standard output (pasted into the prompt)
  output_schema.json   field-by-field output spec (pasted into the prompt; also drives
                       validation and column order)
  raw/                 internal reference docs the model greps (never committed)
input.example.csv      the input format, with placeholder rows
input.csv              your real list (not committed)
output.jsonl, output.csv, report.xlsx, errors.log     run outputs (not committed)
CLAUDE.md              instructions for Claude Code sessions that maintain this repo
```

## Setup

1. Python 3.9 or newer. Create the venv and install the one dependency:
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
   Only `export_sheet.py` needs it; the other two scripts are standard library.
2. Claude Code CLI installed and logged in with a claude.ai subscription, not an API
   key. `claude auth status` should show `"authMethod": "claude.ai"`, and
   `echo $ANTHROPIC_API_KEY` must print nothing. With an API key set, calls bill the key
   instead of the subscription and the usage-limit handling below does not apply.
3. Put the internal reference docs in `context/raw/` (gitignored). The prompt tells the
   model to grep that folder first.

## Running

1. Prepare `input.csv` with columns `name,website,category,source_list` (see
   `input.example.csv`). `website` may be left empty; the model then finds it.
   `source_list` is `existing` or `new`; `new` rows also get a Chinese "why selected"
   note. `category` is free text that is echoed into the output.
2. Run the research, three companies at a time:
   ```
   python3 enrich.py --workers 3
   ```
   One status line per company shows fit_score, seconds, the 5-hour window reset time,
   and tags: `norm:` (a format variant fixed locally), `repaired:` (fixed by a cheap
   no-tools call), `rerun`, and `WARN:` (a reported ARR whose source is neither the
   company nor a listed outlet; review it). Expect roughly three minutes and about $0.7
   of notional usage per company; 41 companies ran in 33 minutes with three workers.
3. Export:
   ```
   python3 export.py                    # output.csv, UTF-8 with BOM
   .venv/bin/python export_sheet.py     # report.xlsx
   ```
4. Upload `report.xlsx` to Google Drive, open it, then File → Save as Google Sheets.

### Re-running one company, resuming, errors

- `python3 enrich.py --only "Company Name"` re-runs one company (exact name from
  `input.csv`) and overwrites its line.
- Resume is automatic. Companies already in `output.jsonl` are skipped, so running the
  same command again after a stop, a crash, or a usage-limit exit continues where it
  left off. `--limit N` runs only the first N remaining rows.
- `python3 enrich.py --refresh proof-points --workers 3` updates only the proof-point
  wording of existing records (fit_zh, pitch_en, evidence_urls) after the proof-point
  rule changes; one web search per record via `update_prompt.md`, drive9-target rows
  skipped, changes logged as `updated:` in `errors.log`. Add `--only` names to limit it.
- Usage limits are not failures. On a hit, all workers pause until the reset time the
  CLI reports (30 minutes if it reports none), then retry the same company. After four
  waits, or on a weekly limit, the run exits cleanly; rerun it later.
- `errors.log` has one entry per event under a header line
  `===== <timestamp>  <company>  <marker> =====`. Markers: `FAILED` (the company was
  not written; the raw model text is included), `recovered: rerun`,
  `recovered: repair <fields>` (with the patch), and `recovered: patch <fields>` (a
  deterministic fix applied to a stored record).

## Updating the prompt

| To change | Edit |
|---|---|
| how fit is judged or scored | `context/fit_rubric.md` |
| tone, depth, and structure of the output | `context/example.md` |
| a field's meaning, allowed values, or format | `context/output_schema.json`, plus the matching check in `enrich.py` (`validate()` / `normalize()`) |
| role, product facts, proof points, hard rules, research procedure | `prompt_template.md` |
| how format errors are repaired | `repair_prompt.md` |

`context/example.md` is pasted into the prompt with "copy this tone and depth exactly".
When the example and a rule disagree, the model follows the example, so every rule
change must be mirrored in the example. Keep the four `{{company}}`-style variables at
the very end of the template so prompt caching keeps working across companies. Do not
add instructions inside `enrich.py`; the prompt is exactly the template with the three
files pasted in.

## Known limits

- The CLI's `--json-schema` structured-output flag has not been evaluated. It may
  remove most format repairs at the source.
- Chinese punctuation (half- versus full-width) is not enforced; outputs mix both.
- ARR keeps the reporting currency (`$`, `€`, `£`); nothing is converted.
- Funding stages follow the US "Series X" ladder; anything else maps to Growth,
  Acquired, Public, None, or unknown.

## Sourcing new companies

The 30 `new` rows were found by two web-research agents, one per category, using the
prompt below (the category block, the seed list, and the exclusion list were the only
parts that differed). Results were de-duplicated against `input.csv` by name and
domain, filtered to Series A or later, and ranked: reported ARR of at least $50M first
(company statement or a named outlet), then the strongest Series B-or-later companies
by fit. One search per company; `enrich.py` does the deep research afterwards.

```
You are sourcing companies for a BD target list. Today is <DATE>. Use WebSearch (and
WebFetch only if a search result is ambiguous). Budget: a few discovery searches plus
roughly ONE search per candidate to confirm its latest funding round and any ARR signal.
Do not do deep research; a later pipeline does that.

CATEGORY: <one of the two blocks below>

EXCLUDE these companies, already covered (match loosely, e.g. "Cognition (Devin)"
excludes Cognition, Devin, and Windsurf which Cognition acquired): <all names in input.csv>.
Also exclude Manus, products of large corporations that are not standalone funded
companies (Google, Microsoft/GitHub, Amazon, Salesforce, ServiceNow, ByteDance, Wix,
Figma, ...), bootstrapped companies with no priced round (e.g. Zapier), and companies
that have been acquired or absorbed.

HARD FILTER: the company must have raised a priced institutional round of Series A or
later (Series A, B, C, D..., or a named growth round). Seed-only companies are out, even
if large. Prefer private VC-backed companies.

RANKING: Tier A = publicly reported ARR (or annualized revenue / run-rate) of at least
$50M, stated by the company itself or a named outlet (Bloomberg, Reuters, The
Information, TechCrunch, Forbes, WSJ, FT, CNBC, NYT, Axios, Fortune, Business Insider).
Tier B = strongest Series B-or-later companies below that bar or with no public ARR.
Rank Tier A first (by ARR), then Tier B (by stage, amount raised, and strength of fit).

CANDIDATES TO CHECK FIRST (verify each; drop any that fail the filter or the category):
<seed list>. Then add any others you find through discovery searches such as
"<category> startup raises Series B <YEAR>", "<category> ARR <YEAR>".

OUTPUT: return ONLY a JSON array (no prose, no code fences) of 22 candidates ranked best
first. Each element:
{"name": "...", "website": "https://...", "last_round": "Series B",
 "last_round_date": "YYYY-MM", "last_round_amount": "$50M", "total_raised": "~$80M or
 unknown", "arr_signal": "$120M ARR (Jun 2026), reported by TechCrunch" or "not public",
 "arr_url": "URL stating that figure or none", "tier": "A" or "B", "why": "one line:
 what the product does and why it fits the category and made the cut",
 "source_urls": ["1-3 URLs you actually used"]}
Only include a company if a search actually confirmed its round stage. If unsure of a
date, give the year as YYYY-01 and say "approx" in why. Never invent an ARR figure;
"not public" is fine.
```

Category blocks used:

- `app_building` — platforms where a user describes an app in natural language and an
  AI agent writes, runs, and deploys it (Lovable, Bolt, Replit, Vercel v0, Base44,
  Emergent). Autonomous coding-agent products aimed at building software also count
  (Cursor, Cognition/Devin, Augment, Cline, OpenHands, OpenCode).
- `agent_execution` — enterprise AI agent platforms whose agents run workflows or take
  actions across business tools on behalf of companies (Glean, Harvey, Sierra, Decagon,
  Writer, Fin, Clay, Lindy, Gumloop, Relevance AI, Hebbia, Rogo). Vertical agent
  companies count if the agent actually executes work; horizontal workflow platforms
  count. Pure model labs, pure infra, and pure search tools do not.
