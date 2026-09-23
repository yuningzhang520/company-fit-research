#!/usr/bin/env python3
"""Build report.xlsx from output.jsonl: README, 原有55家, 新增30家, 待确认汇总.

    .venv/bin/python export_sheet.py
"""
import difflib
import json
import re
import sys
from pathlib import Path

try:
    from openpyxl import Workbook
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("openpyxl is required: .venv/bin/pip install openpyxl")

ROOT = Path(__file__).resolve().parent
OUTPUT_JSONL = ROOT / "output.jsonl"
REPORT_XLSX = ROOT / "report.xlsx"
JOIN = "; "
SIMILARITY = 0.5  # needs_confirmation items at or above this ratio are merged into one theme

README_PARAGRAPHS = [
    "研究方法：每家公司单独做一轮公开信息调研（官网与产品文档、融资公告、ARR 报道、招聘与工程博客、GitHub 与集成页），"
    "再对照 TiDB / drive9 的官方产品资料交叉核对契合点。每家公司在独立的上下文里完成，避免互相串扰；输出经过格式校验与一致性修正。",
    "fit_score 量表：5 = 公开证据坐实（agent 规模的 workload 有公开证据，痛点明确）；4 = 一侧证据扎实（TiDB 或 drive9 其中一侧扎实，另一侧合理）；"
    "3 = 有产品但 workload 不可见（契合合理但未被证实）；2 = 相邻领域（开发工具 / 基础设施 / 数据，但没有 agent 规模的状态或 sandbox 故事）；1 = 不是我们的场景。",
    "arr_confidence 三档：reported = 公司自己（博客、新闻稿、财报、创始人或高管公开表态）或指定主流媒体（Bloomberg、Reuters、The Information、TechCrunch、"
    "Forbes、WSJ、FT、CNBC、NYT、Axios、Fortune、Business Insider）明确给出的数字；estimated = 第三方估算（Sacra、arr.club、Getlatka、Tracxn、Crunchbase、"
    "newsletter、个人博客）；unknown = 没有公开数字。ARR 保留来源报告的币种，不做换算；不收录预测或目标值。",
    "融资与 ARR 的每个数字，相邻列都给出来源 URL（融资来源、ARR来源）。unknown 表示没查到，n/a 表示不适用（例如没有外部融资的公司），none 表示没有来源。",
    "待确认（needs_confirmation）= 契合点或 pitch 中提到、但参考资料未确认的 TiDB / drive9 能力主张，对外使用前需内部核实。"
    "「待确认汇总」sheet 已按近似措辞去重合并，并列出出现次数与涉及公司。",
]

# (header, field, column width, wrap text)
COLUMNS = [
    ("公司", "company", 18, False),
    ("网站", "website", 24, False),
    ("类别", "category", 15, False),
    ("关系", "relationship_type", 9, False),
    ("合作切入点", "integration_surface", 36, True),
    ("fit", "fit_score", 5, False),
    ("契合方向", "fit_target", 9, False),
    ("一句话契合点", "fit_headline_zh", 28, True),
    ("中文简介", "intro_zh", 60, True),
    ("契合点", "fit_zh", 60, True),
    ("English pitch", "pitch_en", 60, True),
    ("融资阶段", "funding_last_stage", 11, False),
    ("最近一轮", "funding_last_amount", 10, False),
    ("日期", "funding_last_date", 9, False),
    ("累计融资", "funding_total", 10, False),
    ("融资来源", "funding_source_url", 32, False),
    ("ARR", "arr", 26, True),
    ("可信度", "arr_confidence", 9, False),
    ("ARR来源", "arr_source_url", 32, False),
    ("技术栈信号", "stack_signals", 45, True),
    ("待确认", "needs_confirmation", 45, True),
    ("备注", "notes", 45, True),
]
WHY_COLUMN = ("入选理由", "why_selected_zh", 45, True)
URL_FIELDS = {"website", "funding_source_url", "arr_source_url"}

HEADER_FONT = Font(bold=True)
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
LINK_FONT = Font(color="0563C1", underline="single")
TOP_WRAP = Alignment(vertical="top", wrap_text=True)
TOP = Alignment(vertical="top")


def cell_value(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return JOIN.join(cell_value(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def load_records():
    if not OUTPUT_JSONL.exists():
        sys.exit("output.jsonl not found; run enrich.py first")
    return [json.loads(line) for line in OUTPUT_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]


def sort_key(r):
    score = r.get("fit_score")
    return (-(score if isinstance(score, int) else 0), str(r.get("company", "")).lower())


def write_readme(wb):
    ws = wb.active
    ws.title = "README"
    ws.column_dimensions["A"].width = 110
    for i, text in enumerate(README_PARAGRAPHS, 1):
        c = ws.cell(row=i, column=1, value=text)
        c.alignment = TOP_WRAP
        ws.row_dimensions[i].height = 75


def write_table(wb, title, records, columns):
    ws = wb.create_sheet(title)
    for j, (header, _, width, _) in enumerate(columns, 1):
        c = ws.cell(row=1, column=j, value=header)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
        c.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(j)].width = width
    for i, r in enumerate(records, 2):
        for j, (_, field, _, wrap) in enumerate(columns, 1):
            value = cell_value(r.get(field))
            c = ws.cell(row=i, column=j, value=value)
            c.alignment = TOP_WRAP if wrap else TOP
            if field in URL_FIELDS and isinstance(value, str) and value.startswith(("http://", "https://")):
                c.hyperlink = value
                c.font = LINK_FONT
    last_row = max(len(records) + 1, 2)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{last_row}"
    fit_col = get_column_letter([f for _, f, _, _ in columns].index("fit_score") + 1)
    ws.conditional_formatting.add(
        f"{fit_col}2:{fit_col}{last_row}",
        ColorScaleRule(start_type="num", start_value=1, start_color="F8696B",
                       mid_type="num", mid_value=3, mid_color="FFEB84",
                       end_type="num", end_value=5, end_color="63BE7B"),
    )
    return ws


def norm_text(s):
    return re.sub(r"[^0-9a-z一-鿿]+", " ", str(s).lower()).strip()


def cluster_confirmations(records):
    """Group needs_confirmation items by near-identical wording. Returns rows sorted by count."""
    clusters = []  # {"text", "norm", "hits": [company, ...]}
    for r in records:
        for item in r.get("needs_confirmation") or []:
            n = norm_text(item)
            if not n:
                continue
            match = None
            for c in clusters:
                if c["norm"] == n or difflib.SequenceMatcher(None, c["norm"], n).ratio() >= SIMILARITY:
                    match = c
                    break
            if match is None:
                match = {"text": str(item).strip(), "norm": n, "hits": []}
                clusters.append(match)
            match["hits"].append(r.get("company", ""))
    rows = []
    for c in clusters:
        companies = list(dict.fromkeys(c["hits"]))  # unique, first-seen order
        rows.append((c["text"], len(c["hits"]), JOIN.join(companies)))
    rows.sort(key=lambda x: (-x[1], x[0].lower()))
    return rows


def write_confirmations(wb, records):
    ws = wb.create_sheet("待确认汇总")
    headers = [("问题", 90), ("出现次数", 10), ("涉及公司", 50)]
    for j, (h, width) in enumerate(headers, 1):
        c = ws.cell(row=1, column=j, value=h)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
        c.alignment = Alignment(vertical="center", horizontal="center")
        ws.column_dimensions[get_column_letter(j)].width = width
    rows = cluster_confirmations(records)
    for i, (text, count, companies) in enumerate(rows, 2):
        ws.cell(row=i, column=1, value=text).alignment = TOP_WRAP
        ws.cell(row=i, column=2, value=count).alignment = TOP
        ws.cell(row=i, column=3, value=companies).alignment = TOP_WRAP
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:C{max(len(rows) + 1, 2)}"
    return rows


def main():
    records = load_records()
    existing = sorted((r for r in records if r.get("source_list") == "existing"), key=sort_key)
    new = sorted((r for r in records if r.get("source_list") == "new"), key=sort_key)
    new_columns = list(COLUMNS)
    new_columns.insert([f for _, f, _, _ in COLUMNS].index("fit_score") + 1, WHY_COLUMN)

    wb = Workbook()
    write_readme(wb)
    write_table(wb, "原有55家", existing, COLUMNS)
    write_table(wb, "新增30家", new, new_columns)
    confirmations = write_confirmations(wb, records)
    wb.save(REPORT_XLSX)
    raw_items = sum(len(r.get("needs_confirmation") or []) for r in records)
    print(f"wrote {REPORT_XLSX.name}: README ({len(README_PARAGRAPHS)} paragraphs), "
          f"原有55家 ({len(existing)} rows), 新增30家 ({len(new)} rows), "
          f"待确认汇总 ({len(confirmations)} items from {raw_items} raw entries)")


if __name__ == "__main__":
    main()
