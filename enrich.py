#!/usr/bin/env python3
"""Batch-research companies: one headless Claude Code call per company.

Reads input.csv, assembles the prompt from prompt_template.md (with the three
context files pasted in and the four row variables filled at the end), runs
`claude -p` once per company, validates the returned JSON against
context/output_schema.json, and appends one line per success to output.jsonl.
Resumes automatically: companies already in output.jsonl are skipped.

Failure handling, in order:
  1. normalize - fix unambiguous format variants in place (shown as "norm:" on the status line)
  2. repair    - for remaining *format* errors, one cheap no-tools call (repair_prompt.md)
                 that returns only the offending fields, merged into the record
  3. rerun     - only if the output was not JSON, keys are missing, the CLI itself failed,
                 or the repair failed; at most one rerun per company

Usage limits (claude.ai subscription): a hit is not a failure. All workers pause until
the reset time the CLI reports (30 min if none), then the company is retried. Server
throttling (not the subscription limit) gets short backoffs instead. A weekly limit, or
a fifth wait in one run, stops the run cleanly; rerunning resumes.

    python3 enrich.py                        # all remaining rows, one at a time
    python3 enrich.py --workers 3 --limit 10 # first 10 remaining rows, 3 in parallel
    python3 enrich.py --only "Cursor"        # re-run one company, overwrite its line
"""
import argparse
import copy
import csv
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
INPUT_CSV = ROOT / "input.csv"
OUTPUT_JSONL = ROOT / "output.jsonl"
ERRORS_LOG = ROOT / "errors.log"
TEMPLATE = ROOT / "prompt_template.md"
REPAIR_TEMPLATE = ROOT / "repair_prompt.md"
UPDATE_TEMPLATE = ROOT / "update_prompt.md"
CONTEXT = ROOT / "context"
SCHEMA = CONTEXT / "output_schema.json"

# placeholder in prompt_template.md -> file whose full contents replace it
PASTE_FILES = {
    "{{paste context/fit_rubric.md here}}": CONTEXT / "fit_rubric.md",
    "{{paste context/example.md here}}": CONTEXT / "example.md",
    "{{paste context/output_schema.json here}}": SCHEMA,
}
SCHEMA_PLACEHOLDER = "{{paste context/output_schema.json here}}"

MODEL = "sonnet"
TOOLS = "WebSearch,WebFetch,Read,Glob,Grep"
CALL_TIMEOUT_SEC = 900      # one research call; a hung CLI must not stall the run
REPAIR_TIMEOUT_SEC = 180    # the no-tools repair call
REFRESH_TIMEOUT_SEC = 420   # a --refresh call: one search, maybe one fetch, a rewrite
REFRESH_TOOLS = "WebSearch,WebFetch"
REFRESH_FIELDS = {"proof-points": ("fit_zh", "pitch_en", "evidence_urls")}  # --refresh kind -> patchable fields
# --refresh kind -> records to skip without a call (drive9 has no customer story beyond Kimi, a lead point)
REFRESH_SKIP = {"proof-points": lambda rec: rec.get("fit_target") == "drive9"}
MAX_EVIDENCE_URLS = 6
WARMUP_SEC = 30             # first call runs alone this long so the prompt cache exists
THROTTLE_SLEEP_SEC = 120    # server-side throttling (not the subscription limit)
THROTTLE_MAX = 3            # short backoffs per call before treating it as a usage limit
FALLBACK_WAIT_SEC = 30 * 60 # usage limit hit but no reset time reported
RESET_MARGIN_SEC = 60       # wake up this long after the reported reset
MAX_WAITS = 4               # usage-limit waits per run; the next hit stops the run
PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")
LIMIT_TEXT = re.compile(r"limit reached|rate limited|usage limit|rate limit", re.I)
CURRENCY_PREFIXES = ("$", "€", "£")  # arr keeps the reporting currency; no conversion
# forward-looking language that must not appear in arr (schema: most recent ACTUAL figure)
FORWARD_RE = re.compile(
    r"\b(projected|projections?|projects|target(?:s|ing|ed)?|forecast(?:s|ed)?|bookings|"
    r"expect(?:s|ed|ing)?|on track|guidance|goal|by (?:the )?end of|by year[- ]end|"
    r"will (?:reach|hit|exceed|be)|aims?|plans? to)\b", re.I)
THROTTLE_TEXT = re.compile(r"not your usage limit|temporarily limiting|overloaded", re.I)

# ---- validation rules; these mirror the field descriptions in context/output_schema.json ----
ENUMS = {
    "relationship_type": ("direct", "partner", "both"),
    "fit_target": ("TiDB", "drive9", "both", "weak"),
    "funding_last_stage": ("Seed", "Series A", "Series B", "Series C", "Series D", "Series E+",
                           "Growth", "Acquired", "Public", "None", "unknown"),
    "arr_confidence": ("reported", "estimated", "unknown"),
}
DATE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
NA_WHEN_NO_FUNDING = ("funding_last_amount", "funding_last_date", "funding_total")
NA_VARIANTS = {"n/a", "na", "n.a.", "n.a", "not applicable"}
UNKNOWN_TOKEN_FIELDS = ("funding_last_amount", "funding_last_date", "funding_total", "arr")
NONE_TOKEN_FIELDS = ("funding_source_url", "arr_source_url")
MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july",
               "august", "september", "october", "november", "december")
# soft check only: a 'reported' ARR should come from the company itself or one of these
REPORTED_DOMAINS = (
    "bloomberg.com", "reuters.com", "theinformation.com", "techcrunch.com", "forbes.com",
    "wsj.com", "ft.com", "cnbc.com", "nytimes.com", "axios.com", "fortune.com",
    "businessinsider.com", "sec.gov",
)


# ---------------------------------------------------------------- inputs

def load_rows():
    with INPUT_CSV.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("name", "website", "category", "source_list"):
            r[k] = (r.get(k) or "").strip()
    return [r for r in rows if r["name"]]


def load_schema_keys():
    return list(json.loads(SCHEMA.read_text(encoding="utf-8")).keys())


def load_existing():
    """All records currently in output.jsonl, in file order."""
    if not OUTPUT_JSONL.exists():
        return []
    records = []
    for n, line in enumerate(OUTPUT_JSONL.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                sys.exit(f"output.jsonl line {n} is not valid JSON ({e}); fix or remove it")
    return records


def expand_template():
    """Research prompt with the three {{paste ...}} placeholders replaced by file contents."""
    prompt = TEMPLATE.read_text(encoding="utf-8")
    for placeholder, path in PASTE_FILES.items():
        if placeholder not in prompt:
            sys.exit(f"placeholder {placeholder!r} not found in prompt_template.md")
        prompt = prompt.replace(placeholder, path.read_text(encoding="utf-8"))
    return prompt


def expand_repair_template():
    """Repair prompt with the schema pasted in; {{record}}, {{fields}}, {{errors}} stay for later."""
    text = REPAIR_TEMPLATE.read_text(encoding="utf-8")
    if SCHEMA_PLACEHOLDER not in text:
        sys.exit(f"placeholder {SCHEMA_PLACEHOLDER!r} not found in repair_prompt.md")
    return text.replace(SCHEMA_PLACEHOLDER, SCHEMA.read_text(encoding="utf-8"))


def expand_update_template():
    """Update prompt with the research prompt's product/proof-point/rules block and the schema
    pasted in; {{company}} and {{record}} stay for later."""
    text = UPDATE_TEMPLATE.read_text(encoding="utf-8")
    research = TEMPLATE.read_text(encoding="utf-8")
    start, end = research.find("# What we sell"), research.find("# Fit rubric")
    if start == -1 or end == -1 or end <= start:
        sys.exit("could not locate the '# What we sell' .. '# Fit rubric' block in prompt_template.md")
    for placeholder, content in (("{{paste template: what we sell}}", research[start:end].strip()),
                                 (SCHEMA_PLACEHOLDER, SCHEMA.read_text(encoding="utf-8"))):
        if placeholder not in text:
            sys.exit(f"placeholder {placeholder!r} not found in update_prompt.md")
        text = text.replace(placeholder, content)
    return text


def fill_row(template, row):
    prompt = (
        template
        .replace("{{company}}", row["name"])
        .replace("{{website}}", row["website"] or "unknown")
        .replace("{{category}}", row["category"])
        .replace("{{source_list}}", row["source_list"])
    )
    leftover = PLACEHOLDER_RE.findall(prompt)
    if leftover:
        sys.exit(f"unexpanded placeholders in prompt: {leftover}")
    return prompt


# ---------------------------------------------------------------- shared run state

def fmt_time(epoch, date=False):
    if not isinstance(epoch, (int, float)):
        return "?"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M" if date else "%H:%M")


def fmt_dur(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


class UsageLimit(Exception):
    """The claude.ai subscription usage limit was hit (not a failure of the company)."""

    def __init__(self, kind, resets_at, message):
        super().__init__(message)
        self.kind = kind            # "five_hour" | "seven_day" | "unknown"
        self.resets_at = resets_at  # epoch seconds or None
        self.message = message


class RunState:
    """Shared between workers: stop/pause signals, wait counter, window info, counters."""

    def __init__(self, max_waits=MAX_WAITS):
        self.lock = threading.Lock()
        self.print_lock = threading.Lock()
        self.stop = threading.Event()
        self.warm = threading.Event()
        self.pause_until = 0.0
        self.waits = 0
        self.max_waits = max_waits
        self.stop_reason = None
        self.windows = {}       # rateLimitType -> resetsAt, from the latest rate_limit_event
        self.limit_hits = []    # (epoch, kind, resets_at, company)
        self.throttles = 0
        self.cost = 0.0
        self.ok = 0
        self.failed = 0

    def say(self, line):
        with self.print_lock:
            print(line, flush=True)

    def note_rate_info(self, info):
        if not info:
            return
        kind, at = info.get("rateLimitType"), info.get("resetsAt")
        if kind and isinstance(at, (int, float)):
            with self.lock:
                self.windows[kind] = at

    def five_hour_label(self):
        at = self.windows.get("five_hour")
        return f"5h resets {fmt_time(at)}" if at else ""


def wait_until(state, deadline):
    """Sleep until `deadline` (epoch) unless the run is stopped. Returns True if it slept through."""
    while not state.stop.is_set():
        remaining = deadline - time.time()
        if remaining <= 0:
            return True
        state.stop.wait(min(remaining, 5))
    return False


# ---------------------------------------------------------------- claude calls

def run_cli(cmd, prompt, timeout):
    return subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
        cwd=ROOT, timeout=timeout,
    )


def research_cmd():
    return [
        "claude", "-p",
        "--output-format", "stream-json", "--verbose",  # stream carries rate_limit_event + result
        "--model", MODEL,
        "--tools", TOOLS,            # only these tools exist in the session
        "--allowedTools", TOOLS,     # ...and they are pre-approved (no prompts in -p mode)
        "--strict-mcp-config",       # no user-level MCP servers in the research context
        "--setting-sources", "user", # do not load this project's CLAUDE.md into the call
    ]


def repair_cmd():
    return [
        "claude", "-p",
        "--output-format", "stream-json", "--verbose",
        "--model", MODEL,
        "--tools", "",               # no tools at all: a repair must not research
        "--strict-mcp-config",
        "--setting-sources", "user",
    ]


def refresh_cmd():
    return [
        "claude", "-p",
        "--output-format", "stream-json", "--verbose",
        "--model", MODEL,
        "--tools", REFRESH_TOOLS,        # web only: no file tools, the record is in the prompt
        "--allowedTools", REFRESH_TOOLS,
        "--strict-mcp-config",
        "--setting-sources", "user",
    ]


def parse_stream(stdout):
    """From stream-json output return (result message or None, last rate_limit_info or None)."""
    result, rate_info = None, None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = m.get("type")
        if t == "rate_limit_event":
            rate_info = m.get("rate_limit_info") or rate_info
        elif t == "result":
            result = m
    return result, rate_info


def classify_limit(result, rate_info, stderr):
    """None when the call did not hit a limit; else ("throttle", None, None, snippet)
    or ("usage", kind, resets_at, snippet)."""
    is_err = result is None or bool(result.get("is_error")) or result.get("subtype") != "success"
    if not is_err:
        return None
    text = ""
    if result:
        text = str(result.get("result") or "") + " " + " ".join(map(str, result.get("errors") or []))
    text += " " + (stderr or "")
    snippet = re.sub(r"\s+", " ", text).strip()[:200]
    status = result.get("api_error_status") if result else None
    info = rate_info or {}
    if status == 529 or THROTTLE_TEXT.search(text):
        return ("throttle", None, None, snippet)
    if status == 429 or LIMIT_TEXT.search(text) or (info.get("status") and info.get("status") != "allowed"):
        return ("usage", info.get("rateLimitType") or "unknown", info.get("resetsAt"), snippet)
    return None


def call(cmd, prompt, timeout, state, what):
    """One CLI call with server-throttle backoff.

    Returns (text, session, cost, cli_err); cli_err is a string when the call itself failed.
    Raises UsageLimit when the subscription limit was hit.
    """
    for n in range(THROTTLE_MAX + 1):
        try:
            proc = run_cli(cmd, prompt, timeout)
        except subprocess.TimeoutExpired:
            return None, "?", 0.0, f"{what} timed out after {timeout}s"
        result, rate_info = parse_stream(proc.stdout)
        state.note_rate_info(rate_info)
        hit = classify_limit(result, rate_info, proc.stderr)
        if hit is None:
            break
        if hit[0] == "throttle" and n < THROTTLE_MAX:
            with state.lock:
                state.throttles += 1
            state.say(f"    server throttling on {what}; retrying in {THROTTLE_SLEEP_SEC}s ({n + 1}/{THROTTLE_MAX}): {hit[3]}")
            if not wait_until(state, time.time() + THROTTLE_SLEEP_SEC):
                return None, "?", 0.0, f"{what} stopped during throttle backoff"
            continue
        kind = hit[1] if hit[0] == "usage" else "unknown"
        raise UsageLimit(kind, hit[2], hit[3])

    cost = float(result.get("total_cost_usd") or 0) if result else 0.0
    session = result.get("session_id", "?") if result else "?"
    tail = f"--- stdout tail ---\n{proc.stdout[-3000:]}\n--- stderr ---\n{proc.stderr[-3000:]}"
    if proc.returncode != 0:
        return None, session, cost, f"{what} exited {proc.returncode}\n{tail}"
    if result is None:
        return None, session, cost, f"{what} produced no result message\n{tail}"
    if result.get("is_error") or result.get("subtype") != "success":
        return None, session, cost, (f"{what} reported subtype={result.get('subtype')!r} is_error={result.get('is_error')} "
                                     f"api_error_status={result.get('api_error_status')} (session {session})\n{json.dumps(result)[:3000]}")
    return result.get("result") or "", session, cost, None


def escape_inner_quotes(s):
    """Escape ASCII double quotes used as punctuation inside JSON string values.

    A quote inside a string counts as its closing quote only when the next non-space
    character is one of , } ] : (what valid JSON allows after a string). Any other
    quote inside a string is content and becomes \\". On valid JSON this is the identity.
    """
    out = []
    in_str = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
        elif c == "\\":
            out.append(c)
            if i + 1 < n:
                out.append(s[i + 1])
                i += 1
        elif c == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j >= n or s[j] in ",}]:":
                in_str = False
                out.append(c)
            else:
                out.append('\\"')
        else:
            out.append(c)
        i += 1
    return "".join(out)


def extract_json(text):
    """Parse the model's text as JSON, tolerating code fences, stray prose, and
    unescaped quotes inside string values."""
    s = text.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    candidates = [s]
    if start != -1 and end > start and (start > 0 or end < len(s) - 1):
        candidates.append(s[start:end + 1])
    last_error = None
    for cand in candidates:
        for attempt in (cand, escape_inner_quotes(cand)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError as e:
                last_error = e
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    raise ValueError(f"model output is not valid JSON ({last_error})")


# ---------------------------------------------------------------- normalize

def parse_month(s):
    """'Sep 2026', 'September 2026', '2026/09', '2026-9', '09/2026', '2026-09-08' -> '2026-09'; else None."""
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})(?:[-/.]\d{1,2})?", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{4})", s)
        if m:
            mo, y = int(m.group(1)), int(m.group(2))
        else:
            m = re.fullmatch(r"([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
            if not m:
                return None
            name, y = m.group(1).lower(), int(m.group(2))
            mo = next((i for i, full in enumerate(MONTH_NAMES, 1) if full.startswith(name)), None)
            if mo is None:
                return None
    if not 1 <= mo <= 12:
        return None
    return f"{y:04d}-{mo:02d}"


def normalize(obj):
    """Fix unambiguous format variants in place. Returns the names of fields changed.

    Only token-like fields are touched (enums, n/a and unknown/none tokens, fit_score,
    funding_last_date) plus whitespace trimming. Anything ambiguous is left for repair.
    """
    changed = []

    def put(key, val):
        cur = obj.get(key)
        if cur != val or type(cur) is not type(val):  # 4.0 == 4 in Python; type matters here
            obj[key] = val
            if key not in changed:
                changed.append(key)

    for k, v in list(obj.items()):
        if isinstance(v, str) and v != v.strip():
            put(k, v.strip())

    for key, allowed in ENUMS.items():
        v = obj.get(key)
        if not isinstance(v, str):
            continue
        s = v
        if key == "funding_last_stage":
            m = re.fullmatch(r"series\s+([a-z])\+?", s, re.I)
            if m:
                letter = m.group(1).upper()
                s = f"Series {letter}" if letter < "E" else "Series E+"
        for a in allowed:
            if s.lower() == a.lower():
                put(key, a)
                break

    for key in NA_WHEN_NO_FUNDING + ("integration_surface",):
        v = obj.get(key)
        if isinstance(v, str) and v.lower() in NA_VARIANTS:
            put(key, "n/a")
    for key in UNKNOWN_TOKEN_FIELDS:
        v = obj.get(key)
        if isinstance(v, str) and v.lower() == "unknown":
            put(key, "unknown")
    for key in NONE_TOKEN_FIELDS:
        v = obj.get(key)
        if isinstance(v, str) and v.lower() == "none":
            put(key, "none")

    if obj.get("arr") == "unknown":  # an unknown ARR has no confidence and no source
        if obj.get("arr_confidence") != "unknown":
            put("arr_confidence", "unknown")
        if obj.get("arr_source_url") != "none":
            put("arr_source_url", "none")

    v = obj.get("fit_score")
    if isinstance(v, str):
        m = re.fullmatch(r"(\d+)(?:\.0+)?(?:\s*/\s*5)?", v)
        if m:
            put("fit_score", int(m.group(1)))
    elif isinstance(v, float) and v.is_integer():
        put("fit_score", int(v))

    v = obj.get("funding_last_date")
    if isinstance(v, str):
        d = parse_month(v)
        if d:
            put("funding_last_date", d)
    return changed


# ---------------------------------------------------------------- validate

class ContentError(ValueError):
    """Unusable without a fresh research call: not an object, or keys missing."""


class FormatError(ValueError):
    """Complete record with field values that violate the schema; repairable."""

    def __init__(self, problems):  # problems: list of (field, message)
        self.fields = sorted({f for f, _ in problems})
        super().__init__("; ".join(m for _, m in problems))


def validate(obj, schema_keys, row):
    if not isinstance(obj, dict):
        raise ContentError("model output is not a JSON object")
    missing = [k for k in schema_keys if k not in obj]
    if missing:
        raise ContentError("missing keys: " + ", ".join(missing))

    problems = []
    for key, allowed in ENUMS.items():
        if obj[key] not in allowed:
            problems.append((key, f"{key}={obj[key]!r} not in {allowed}"))

    score = obj["fit_score"]
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        problems.append(("fit_score", f"fit_score={score!r} is not an integer 1-5"))

    stage, date = obj["funding_last_stage"], obj["funding_last_date"]
    if stage == "None":
        for k in NA_WHEN_NO_FUNDING:
            if obj[k] != "n/a":
                problems.append((k, f"funding_last_stage=None requires {k}='n/a', got {obj[k]!r}"))
    else:
        if not (date == "unknown" or (isinstance(date, str) and DATE_RE.match(date))):
            problems.append(("funding_last_date", f"funding_last_date={date!r} is not YYYY-MM or 'unknown'"))
        for k in NA_WHEN_NO_FUNDING:
            if obj[k] == "n/a":
                problems.append((k, f"{k}='n/a' but funding_last_stage={stage!r}"))

    arr = obj["arr"]
    if not (arr == "unknown" or (isinstance(arr, str) and arr.startswith(CURRENCY_PREFIXES))):
        problems.append(("arr", f"arr={arr!r} must start with a currency sign ($, €, £; keep the reporting currency, no conversion) or be exactly 'unknown'"))
    elif isinstance(arr, str) and FORWARD_RE.search(arr):
        problems.append(("arr", f"arr={arr!r} contains forward-looking language; use the most recent actual figure the record states, else 'unknown'"))
    if arr == "unknown":
        if obj["arr_confidence"] != "unknown":
            problems.append(("arr_confidence", "arr is 'unknown' so arr_confidence must be 'unknown'"))
        if obj["arr_source_url"] != "none":
            problems.append(("arr_source_url", "arr is 'unknown' so arr_source_url must be 'none'"))

    why = obj["why_selected_zh"]
    if row["source_list"] == "existing" and why is not None:
        problems.append(("why_selected_zh", "why_selected_zh must be null when source_list=existing"))
    if row["source_list"] == "new" and not (isinstance(why, str) and why.strip()):
        problems.append(("why_selected_zh", "why_selected_zh must be a non-empty string when source_list=new"))

    if problems:
        raise FormatError(problems)


def host_of(url):
    try:
        host = (urlparse(str(url)).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def in_domain(host, domain):
    return host == domain or host.endswith("." + domain)


def warnings_for(obj, row):
    """Soft checks for human review; they never fail validation."""
    out = []
    if obj.get("arr_confidence") == "reported":
        host = host_of(obj.get("arr_source_url", ""))
        trusted = list(REPORTED_DOMAINS)
        company = host_of(row["website"])
        if company:
            trusted.append(company)
        if not host or not any(in_domain(host, d) for d in trusted):
            out.append(f"arr 'reported' but source is {host or obj.get('arr_source_url')!r}")
    return out


# ---------------------------------------------------------------- repair

def repair(obj, err, repair_template, schema_keys, row, state):
    """One no-tools call that returns only the offending fields; merged into obj.

    Returns (ok, detail, cost): on success detail is the patch text and obj has been
    patched and re-validated; on failure detail says why.
    Raises UsageLimit if the repair call hit the subscription limit.
    """
    prompt = (
        repair_template
        .replace("{{record}}", json.dumps(obj, ensure_ascii=False, indent=2))
        .replace("{{fields}}", ", ".join(err.fields))
        .replace("{{errors}}", str(err))
    )
    text, session, cost, cli_err = call(repair_cmd(), prompt, REPAIR_TIMEOUT_SEC, state, "repair call")
    if cli_err:
        return False, cli_err, cost
    try:
        patch = extract_json(text)
    except (ValueError, json.JSONDecodeError) as e:
        return False, f"repair output is not JSON: {e} (session {session})\n{text}", cost
    if not isinstance(patch, dict):
        return False, f"repair output is not an object (session {session})\n{text}", cost
    extra = sorted(set(patch) - set(err.fields))
    if extra:
        return False, f"repair touched fields it was not asked to: {extra} (session {session})\n{text}", cost
    obj.update(patch)
    normalize(obj)
    try:
        validate(obj, schema_keys, row)
    except ValueError as e:
        return False, f"still invalid after repair: {e} (session {session})\n--- patch ---\n{text}", cost
    return True, text, cost


# ---------------------------------------------------------------- one company

def research(row, prompt, repair_template, schema_keys, state):
    """Run one company. Returns (record, cost, None, info) or (None, cost, error_text, None).

    info = {"attempt": 1|2, "normalized": [fields], "repaired": [fields]}
    Raises UsageLimit (the company is not consumed; the caller requeues it).
    """
    errors = []
    cost = 0.0
    for attempt in (1, 2):
        text, session, call_cost, cli_err = call(research_cmd(), prompt, CALL_TIMEOUT_SEC, state, "claude")
        cost += call_cost
        if cli_err:
            errors.append(f"attempt {attempt}: {cli_err}")
            continue
        try:
            obj = extract_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            errors.append(f"attempt {attempt}: {e} (session {session})\n--- raw model text ---\n{text}")
            continue
        info = {"attempt": attempt, "normalized": [], "repaired": []}
        if isinstance(obj, dict):
            info["normalized"] = normalize(obj)
        try:
            validate(obj, schema_keys, row)
        except ContentError as e:
            errors.append(f"attempt {attempt}: {e} (session {session})\n--- raw model text ---\n{text}")
            continue
        except FormatError as e:
            ok, detail, repair_cost = repair(obj, e, repair_template, schema_keys, row, state)
            cost += repair_cost
            if not ok:
                errors.append(f"attempt {attempt}: {e} (session {session})\n--- repair failed ---\n{detail}\n--- raw model text ---\n{text}")
                continue
            info["repaired"] = e.fields
            log_error(row["name"], f"{e} (session {session})\n--- patch ---\n{detail}",
                      marker="recovered: repair " + ",".join(e.fields))
        if errors:
            log_error(row["name"], "\n\n".join(errors), marker="recovered: rerun")
        obj["company"] = row["name"]  # join key for resume / --only; schema says "as given in input"
        return obj, cost, None, info
    return None, cost, "\n\n".join(errors), None


def refresh_record(row, record, kind, update_template, schema_keys, state):
    """--refresh: one web-enabled call that may patch only REFRESH_FIELDS[kind] of an existing record.

    Returns (record, cost, None, info) on success (record unchanged when the model returns {}),
    or (None, cost, error_text, None). Raises UsageLimit like research().
    """
    fields = REFRESH_FIELDS[kind]
    prompt = (update_template
              .replace("{{company}}", row["name"])
              .replace("{{record}}", json.dumps(record, ensure_ascii=False, indent=2)))
    text, session, cost, cli_err = call(refresh_cmd(), prompt, REFRESH_TIMEOUT_SEC, state, "refresh call")
    if cli_err:
        return None, cost, cli_err, None
    try:
        patch = extract_json(text)
    except (ValueError, json.JSONDecodeError) as e:
        return None, cost, f"refresh output is not JSON: {e} (session {session})\n{text}", None
    if not isinstance(patch, dict):
        return None, cost, f"refresh output is not an object (session {session})\n{text}", None
    info = {"attempt": 1, "normalized": [], "repaired": [], "updated": [], "no_match": False}
    if not patch:
        info["no_match"] = True
        return record, cost, None, info
    extra = sorted(set(patch) - set(fields))
    if extra:
        return None, cost, f"refresh touched fields it was not allowed to: {extra} (session {session})\n{text}", None
    obj = copy.deepcopy(record)
    obj.update(patch)
    if isinstance(obj.get("evidence_urls"), list) and len(obj["evidence_urls"]) > MAX_EVIDENCE_URLS:
        obj["evidence_urls"] = obj["evidence_urls"][:MAX_EVIDENCE_URLS]
    info["normalized"] = normalize(obj)
    try:
        validate(obj, schema_keys, row)
    except ValueError as e:
        return None, cost, f"refreshed record invalid: {e} (session {session})\n--- patch ---\n{text}", None
    if obj["fit_score"] != record["fit_score"]:
        return None, cost, f"refresh changed fit_score (session {session})\n{text}", None
    changed = [f for f in fields if obj.get(f) != record.get(f)]
    if not changed:
        info["no_match"] = True
        return record, cost, None, info
    before = "\n".join(f"{f}: {json.dumps(record.get(f), ensure_ascii=False)}" for f in changed)
    after = "\n".join(f"{f}: {json.dumps(obj.get(f), ensure_ascii=False)}" for f in changed)
    log_error(row["name"], f"(session {session})\n--- before ---\n{before}\n--- after ---\n{after}",
              marker=f"updated: {kind} " + ",".join(changed))
    info["updated"] = changed
    return obj, cost, None, info


# ---------------------------------------------------------------- outputs

def append_record(obj):
    with OUTPUT_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def replace_record(records, obj):
    """--only: overwrite the existing line for this company (or append), rewriting the file."""
    name = obj["company"]
    out, replaced = [], False
    for r in records:
        if r.get("company") == name:
            if not replaced:
                out.append(obj)
                replaced = True
            continue
        out.append(r)
    if not replaced:
        out.append(obj)
    tmp = OUTPUT_JSONL.with_name(OUTPUT_JSONL.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    tmp.replace(OUTPUT_JSONL)
    return out


LOG_LOCK = threading.Lock()


def log_error(name, detail, marker="FAILED"):
    """Append one entry to errors.log. marker is FAILED, or 'recovered: rerun' / 'recovered: repair <fields>'."""
    stamp = datetime.now().isoformat(timespec="seconds")
    with LOG_LOCK, ERRORS_LOG.open("a", encoding="utf-8") as f:
        f.write(f"===== {stamp}  {name}  {marker} =====\n{detail}\n\n")


def status_tags(info, warns):
    tags = []
    if info["normalized"]:
        tags.append("norm: " + ",".join(info["normalized"]))
    if info["repaired"]:
        tags.append("repaired: " + ",".join(info["repaired"]))
    if info["attempt"] > 1:
        tags.append("rerun")
    if info.get("updated"):
        tags.append("updated: " + ",".join(info["updated"]))
    if info.get("no_match"):
        tags.append("no match")
    if warns:
        tags.append("WARN: " + "; ".join(warns))
    return ("  " + "  ".join(tags)) if tags else ""


# ---------------------------------------------------------------- worker pool

class Ctx:
    """Read-only inputs shared by workers, plus the records list for --only (replace mode)."""

    def __init__(self, template, repair_template, schema_keys, total, replace=False, records=None,
                 refresh=None, update_template=None):
        self.template = template
        self.repair_template = repair_template
        self.schema_keys = schema_keys
        self.total = total
        self.replace = replace
        self.records = records or []
        self.refresh = refresh                  # None, or a REFRESH_FIELDS kind
        self.update_template = update_template

    def record_for(self, row):
        return next((r for r in self.records if r.get("company") == row["name"]), None)

    def prompt_for(self, row):
        return fill_row(self.template, row)


def handle_limit(state, hit, company):
    """Called by the worker that hit the usage limit. Sets (or joins) the global pause and waits."""
    now = time.time()
    with state.lock:
        state.limit_hits.append((now, hit.kind, hit.resets_at, company))
        if hit.kind == "seven_day":
            state.stop_reason = (f"weekly usage limit hit on {company}; resets {fmt_time(hit.resets_at, date=True)}. "
                                 "Stopping cleanly; rerun later to resume.")
            state.stop.set()
            return
        if state.pause_until > now:
            deadline, owner, why = state.pause_until, False, ""
        else:
            state.waits += 1
            if state.waits > state.max_waits:
                state.stop_reason = (f"usage limit hit {state.waits} times this run (cap {state.max_waits}). "
                                     "Stopping cleanly; rerun later to resume.")
                state.stop.set()
                return
            if isinstance(hit.resets_at, (int, float)):
                deadline, why = hit.resets_at + RESET_MARGIN_SEC, f"CLI reports reset at {fmt_time(hit.resets_at)}"
            else:
                deadline, why = now + FALLBACK_WAIT_SEC, "no reset time reported, waiting 30 min"
            state.pause_until, owner = deadline, True
            waits = state.waits
    if owner:
        state.say(f"--- usage limit hit on {company} ({hit.kind}); {why}. "
                  f"All workers waiting until {fmt_time(deadline)} (wait {waits}/{state.max_waits}) ---")
    slept = wait_until(state, deadline)
    if owner and slept:
        state.say(f"--- leaving wait at {fmt_time(time.time())}; resuming ---")


def wait_for_pause(state):
    with state.lock:
        deadline = state.pause_until
    if deadline > time.time():
        wait_until(state, deadline)


def worker(wid, state, todo, ctx):
    if wid > 0:
        while not state.warm.is_set() and not state.stop.is_set():
            state.warm.wait(1)
    while not state.stop.is_set():
        wait_for_pause(state)
        if state.stop.is_set():
            break
        with state.lock:
            if not todo:
                break
            row = todo.popleft()
        name = row["name"]
        t0 = time.monotonic()
        try:
            if ctx.refresh:
                with state.lock:
                    current = ctx.record_for(row)
                obj, cost, err, info = refresh_record(row, current, ctx.refresh, ctx.update_template, ctx.schema_keys, state)
            else:
                obj, cost, err, info = research(row, ctx.prompt_for(row), ctx.repair_template, ctx.schema_keys, state)
        except UsageLimit as hit:
            with state.lock:
                todo.appendleft(row)
            handle_limit(state, hit, name)
            continue
        elapsed = time.monotonic() - t0
        with state.lock:
            state.cost += cost
        if obj is None:
            if state.stop.is_set():
                state.say(f"    {name} aborted while stopping (not written; resume picks it up)")
                continue
            with state.lock:
                log_error(name, err)
                state.failed += 1
                idx = state.ok + state.failed
            state.say(f"[{idx}/{ctx.total}] {name}  FAILED  {elapsed:.0f}s  (see errors.log)")
            continue
        with state.lock:
            if ctx.replace:
                if not info.get("no_match"):
                    ctx.records = replace_record(ctx.records, obj)
            else:
                append_record(obj)
            state.ok += 1
            idx = state.ok + state.failed
            window = state.five_hour_label()
        tags = status_tags(info, warnings_for(obj, row))
        window = f"  {window}" if window else ""
        state.say(f"[{idx}/{ctx.total}] {name}  fit_score={obj['fit_score']}  {elapsed:.0f}s{window}{tags}")


def run_pool(rows_todo, ctx, n_workers, state):
    """Run the workers to completion (or until the run is stopped). Handles Ctrl-C."""
    todo = deque(rows_todo)
    n_workers = max(1, min(n_workers, len(todo)))
    if n_workers > 1:
        threading.Timer(WARMUP_SEC, state.warm.set).start()
    else:
        state.warm.set()
    threads = [threading.Thread(target=worker, args=(i, state, todo, ctx), daemon=True) for i in range(n_workers)]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        state.stop.set()
        state.say("\ninterrupted; letting in-flight calls finish (Ctrl-C again to kill)")
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            pass
        return False
    return True


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Research companies via headless Claude Code, one call each.")
    ap.add_argument("--limit", type=int, metavar="N", help="process only the first N remaining rows")
    ap.add_argument("--only", metavar="NAME", action="append",
                    help="re-run this company (exact name from input.csv), overwriting its line; repeatable")
    ap.add_argument("--workers", type=int, default=1, metavar="N", help="parallel research calls (default 1)")
    ap.add_argument("--refresh", choices=sorted(REFRESH_FIELDS), metavar="KIND",
                    help="update only some fields of existing records (kinds: " + ", ".join(sorted(REFRESH_FIELDS)) + ")")
    args = ap.parse_args()

    if not shutil.which("claude"):
        sys.exit("claude CLI not found on PATH")

    rows = load_rows()
    schema_keys = load_schema_keys()
    records = load_existing()
    done = {r.get("company") for r in records}

    if args.refresh:
        wanted = set(args.only) if args.only else None
        todo = [r for r in rows if r["name"] in done and (wanted is None or r["name"] in wanted)]
        if wanted:
            missing = sorted(wanted - {r["name"] for r in todo})
            if missing:
                sys.exit(f"--refresh needs an existing record for each --only name; not in output.jsonl: {missing}")
        skip = REFRESH_SKIP.get(args.refresh)
        if skip:
            by_name = {r.get("company"): r for r in records}
            before = len(todo)
            todo = [r for r in todo if not skip(by_name[r["name"]])]
            if before != len(todo):
                print(f"skipping {before - len(todo)} record(s) that a '{args.refresh}' refresh cannot improve", flush=True)
        if args.limit is not None:
            todo = todo[:max(args.limit, 0)]
        n_workers = max(1, args.workers)
    elif args.only:
        wanted = set(args.only)
        todo = [r for r in rows if r["name"] in wanted]
        missing = sorted(wanted - {r["name"] for r in todo})
        if missing:
            sys.exit(f"--only: not found in input.csv: {missing}")
        n_workers = max(1, args.workers) if len(todo) > 1 else 1
    else:
        todo = [r for r in rows if r["name"] not in done]
        if args.limit is not None:
            todo = todo[:max(args.limit, 0)]
        n_workers = max(1, args.workers)

    mode = f"refresh {args.refresh}" if args.refresh else ("re-run" if args.only else "run")
    print(f"{len(rows)} rows in input.csv, {len(done)} already in output.jsonl, {len(todo)} to {mode}, "
          f"{min(n_workers, max(len(todo), 1))} worker(s)", flush=True)
    ctx = Ctx(expand_template(), expand_repair_template(), schema_keys, len(todo),
              replace=bool(args.only or args.refresh), records=records,
              refresh=args.refresh, update_template=expand_update_template() if args.refresh else None)
    state = RunState()

    t_start = time.monotonic()
    finished = run_pool(todo, ctx, n_workers, state)
    elapsed = time.monotonic() - t_start

    window = state.five_hour_label() or "5h window: no info"
    print(f"done: {state.ok} ok, {state.failed} failed, ${state.cost:.2f} API cost, elapsed {fmt_dur(elapsed)}, "
          f"usage-limit hits: {len(state.limit_hits)}, throttles: {state.throttles}, waits: {state.waits}, {window}", flush=True)
    for when, kind, at, company in state.limit_hits:
        print(f"    hit at {fmt_time(when)} on {company}: {kind}, reset {fmt_time(at, date=True)}", flush=True)
    if state.stop_reason:
        print(f"stopped early: {state.stop_reason}", flush=True)
    if not finished:
        sys.exit(130)


if __name__ == "__main__":
    main()
