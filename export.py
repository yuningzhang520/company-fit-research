#!/usr/bin/env python3
"""Convert output.jsonl -> output.csv.

Columns follow the key order in context/output_schema.json. Rows follow input.csv order
(companies not found in input.csv go last, in file order). Array fields are joined with
"; ". Written as UTF-8 with BOM so Chinese renders in Excel / Sheets.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT_CSV = ROOT / "input.csv"
OUTPUT_JSONL = ROOT / "output.jsonl"
OUTPUT_CSV = ROOT / "output.csv"
SCHEMA = ROOT / "context" / "output_schema.json"
JOIN = "; "


def cell(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return JOIN.join(cell(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def input_order():
    with INPUT_CSV.open(newline="", encoding="utf-8-sig") as f:
        names = [(r.get("name") or "").strip() for r in csv.DictReader(f)]
    return {name: i for i, name in enumerate(names) if name}


def main():
    if not OUTPUT_JSONL.exists():
        sys.exit("output.jsonl not found; run enrich.py first")
    columns = list(json.loads(SCHEMA.read_text(encoding="utf-8")).keys())
    records = [
        json.loads(line)
        for line in OUTPUT_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    order = input_order()
    records.sort(key=lambda r: order.get(r.get("company"), len(order)))  # stable: unknowns keep file order
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(columns)
        for r in records:
            w.writerow([cell(r.get(c)) for c in columns])
    print(f"wrote {len(records)} rows x {len(columns)} columns to {OUTPUT_CSV.name}")


if __name__ == "__main__":
    main()
