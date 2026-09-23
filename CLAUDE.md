# company-fit-research — agent instructions

This repo batch-researches companies and scores their fit for TiDB and drive9: one
headless Claude Code call per company in a fresh context, one validated JSON record
out, then CSV and Excel exports. Read this file before changing anything.

## What the pipeline does

`input.csv` (name, website, category, source_list) → `enrich.py` → `output.jsonl`
(one JSON object per company; keys = `context/output_schema.json`) → `export.py`
(`output.csv`) and `export_sheet.py` (`report.xlsx`).

`enrich.py` builds the prompt from `prompt_template.md` by pasting in three files
(`context/fit_rubric.md`, `context/example.md`, `context/output_schema.json`) and
filling the row's four variables at the very end. The prompt is exactly the template
with placeholders expanded; the script never adds instructions of its own.

## Scripts and flags

- `enrich.py` — `--workers N` (parallel calls, default 1), `--limit N` (first N
  remaining rows), `--only "Name"` (re-run one company, overwrite its line; always
  single-threaded). Resumes automatically: companies already in `output.jsonl` are
  skipped. CLI call: `claude -p --output-format stream-json --verbose --model sonnet
  --tools "WebSearch,WebFetch,Read,Glob,Grep" --allowedTools <same> --strict-mcp-config
  --setting-sources user`, prompt on stdin. `--setting-sources user` keeps this file
  out of the research context; do not remove it.
- `export.py` — `output.jsonl` → `output.csv` (UTF-8 BOM, schema key order, rows in
  `input.csv` order).
- `export_sheet.py` — `output.jsonl` → `report.xlsx` (README, 原有/新增 sheets,
  待确认汇总). Needs openpyxl (`requirements.txt`). `SIMILARITY` sets how aggressively
  the last sheet merges needs_confirmation items into themes.

## Rules

- Never edit `context/fit_rubric.md`, `context/example.md`, `context/output_schema.json`
  or `prompt_template.md` unless the user explicitly authorizes that specific change.
  Propose it and wait. When authorized: back up first, exact-match replace, keep schema
  keys and their order unchanged, show a diff.
- The scripts and `repair_prompt.md` may be maintained without authorization; say what
  changed and why.
- `context/example.md` is pasted as "copy this exactly" and wins over the rules when
  they conflict, so every rule change must also be reflected in the example.
- The checks in `enrich.py` (`ENUMS`, date format, n/a when stage is None, arr rules,
  why_selected_zh null/non-null) mirror the schema descriptions; change both together.
- `context/raw/` is internal material: never commit it, never quote it in chat output.
- Do not start a full run unprompted; the user decides when to spend a usage window.

## Failure handling (enrich.py)

1. normalize — unambiguous fixes in place: enum case, Series E..Z → `Series E+`, n/a
   and unknown/none tokens, fit_score "4" / "4.0" / "4/5", month formats, an unknown
   ARR clearing its confidence and source. Tagged `norm:` on the status line; never
   spends a call.
2. repair — remaining *format* errors go to one cheap no-tools call (`--tools ""`)
   built from `repair_prompt.md`. It returns only the offending fields, which are
   merged in and re-validated; a patch that touches other fields is rejected.
3. rerun — only for non-JSON output, missing keys, a CLI-level failure, or a failed
   repair; at most one rerun per company. Final failures land in `errors.log` under a
   `FAILED` header; recovered repairs, reruns and patches under `recovered:` headers.

Usage limits (claude.ai subscription) are not failures: every worker pauses until the
`resetsAt` from the CLI's `rate_limit_event` (30 min if absent), at most 4 waits per
run; a weekly limit or a fifth wait stops the run cleanly and a rerun resumes it.
Server throttling (not the subscription limit) gets short backoffs instead. A soft
`WARN:` tag marks a reported ARR whose source is neither the company nor a listed
outlet; it never fails a row.

## Open items

The CLI's `--json-schema` structured-output flag is not yet evaluated. Chinese
punctuation (half vs full width) is not enforced by the prompt.
