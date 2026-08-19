#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_review.py — 将规范化审核 JSON 渲染为自包含 HTML（仅标准库）。

输入契约（缺失字段允许）：
  {
    "metadata": {...},
    "summary": {...},
    "issues": [...],
    "language_findings": [...],
    "figure_inventory": [...],
    "trace_artifacts": [... | {...}],
    "checked_items": [... | {...}]
  }

用法：
  python render_review.py <review.json> [-o 输出.review.html] [--title 标题]
  python render_review.py --smoke-test [-o 输出.review.html]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import re
import sys
from urllib.parse import quote

SEVERITY_ORDER = ("critical", "major", "minor", "manual")
SEVERITY_META = {
    "critical": {"label": "严重", "class": "sev-critical"},
    "major": {"label": "一般", "class": "sev-major"},
    "minor": {"label": "建议", "class": "sev-minor"},
    "manual": {"label": "人工核对", "class": "sev-manual"},
}
SEVERITY_ALIASES = {
    "critical": "critical",
    "严重": "critical",
    "high": "critical",
    "高": "critical",
    "major": "major",
    "一般": "major",
    "medium": "major",
    "中": "major",
    "minor": "minor",
    "建议": "minor",
    "low": "minor",
    "低": "minor",
    "manual": "manual",
    "人工核对": "manual",
    "manual_check": "manual",
    "need_manual_check": "manual",
    "requires_manual_check": "manual",
    "需人工核对": "manual",
}
TRUE_WORDS = {"1", "true", "yes", "y", "on", "是", "需要", "需", "有"}
FALSE_WORDS = {"0", "false", "no", "n", "off", "否", "不需要", "无需", "无"}

FIELD_LABELS = {
    "title": "标题",
    "report_name": "报告名称",
    "report_title": "报告标题",
    "report_id": "报告编号",
    "source_file": "源文件",
    "source_type": "源文件类型",
    "reviewer": "审核人",
    "generator": "生成器",
    "generated_at": "生成时间",
    "project": "项目",
    "customer": "客户",
    "overall_assessment": "总体评价",
    "overall_status": "总体状态",
    "conclusion": "结论",
    "notes": "备注",
    "language": "语言",
    "severity_counts": "严重度统计",
    "issue_count": "问题数量",
    "manual_confirmation_count": "需确认数量",
}

ISSUE_FIELDS = (
    ("issue_id", "问题ID / issue_id"),
    ("category", "类别 / category"),
    ("severity", "严重度 / severity"),
    ("confidence", "置信度 / confidence"),
    ("location", "位置 / location"),
    ("expected", "期望 / expected"),
    ("actual", "实际 / actual"),
    ("evidence", "证据 / evidence"),
    ("problem", "问题 / problem"),
    ("recommendation", "建议 / recommendation"),
    ("technical_meaning_changed", "技术含义变化 / technical_meaning_changed"),
    ("requires_author_confirmation", "需作者确认 / requires_author_confirmation"),
    ("source", "来源 / source"),
)

CSS = """
:root { --crit:#c62828; --crit-bg:#fdecea; --maj:#e65100; --maj-bg:#fff3e0;
        --min:#1565c0; --min-bg:#e3f2fd; --man:#6a1b9a; --man-bg:#f3e5f5;
        --ink:#1a1a1a; --muted:#666; --line:#d0d0d0; --zebra:#f7f7f9;
        --panel:#fafafa; --bg:#ffffff; }
* { box-sizing: border-box; }
html, body { background: var(--bg); }
body { font-family: "Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;
       color: var(--ink); max-width: 1320px; margin: 0 auto; padding: 28px 32px 40px;
       line-height: 1.65; font-size: 14px; }
h1 { font-size: 24px; margin: 0 0 10px; border-bottom: 3px solid var(--ink); padding-bottom: 8px; }
h2 { font-size: 18px; margin: 28px 0 12px; border-left: 5px solid #888; padding-left: 10px; }
h3 { font-size: 15px; margin: 20px 0 8px; }
p { margin: 8px 0; }
a { color: #0b57d0; text-decoration: none; }
a:hover { text-decoration: underline; }
.intro { color: var(--muted); margin-bottom: 18px; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }
.empty { color: var(--muted); font-style: italic; }
.table-wrap { overflow-x: auto; margin: 10px 0 18px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; min-width: 720px; }
th, td { border: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }
th { background: #efefef; font-weight: 600; white-space: nowrap; }
tr:nth-child(even) td { background: var(--zebra); }
tr.sev-critical td { background: var(--crit-bg); }
tr.sev-major td { background: var(--maj-bg); }
tr.sev-minor td { background: var(--min-bg); }
tr.sev-manual td { background: var(--man-bg); }
.wide-table table { min-width: 1600px; }
.text { white-space: pre-wrap; word-break: break-word; }
.mono { font-family: Consolas, "Courier New", monospace; }
.muted { color: var(--muted); }
.badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px;
         font-weight: 700; white-space: nowrap; }
.badge.sev-critical { background: var(--crit); color: #fff; }
.badge.sev-major { background: var(--maj); color: #fff; }
.badge.sev-minor { background: var(--min); color: #fff; }
.badge.sev-manual { background: var(--man); color: #fff; }
.badge.sev-none { background: #9e9e9e; color: #fff; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 12px 0 18px; }
.stat { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: var(--panel); }
.stat .k { color: var(--muted); font-size: 12px; }
.stat .v { font-size: 24px; font-weight: 700; margin-top: 4px; }
.sev-critical .v { color: var(--crit); }
.sev-major .v { color: var(--maj); }
.sev-minor .v { color: var(--min); }
.sev-manual .v { color: var(--man); }
details.issue { border: 1px solid var(--line); border-radius: 8px; background: #fff; margin: 10px 0; }
details.issue summary { cursor: pointer; list-style: none; padding: 10px 12px; font-weight: 600; }
details.issue[open] summary { border-bottom: 1px solid var(--line); }
details.issue summary::-webkit-details-marker { display: none; }
.issue-body { padding: 12px; }
.kv { display: grid; grid-template-columns: 220px 1fr; gap: 8px 12px; }
.kv dt { font-weight: 700; margin: 0; }
.kv dd { margin: 0; }
.footer { margin-top: 32px; padding-top: 10px; border-top: 1px solid var(--line); color: #888; font-size: 12px; }
@media print {
  body { max-width: none; padding: 10mm; }
  h2, h3, details.issue { page-break-inside: avoid; }
}
"""


def _reconfigure_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _is_blank(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
    return None


def _normalize_severity(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value.strip()
    else:
        raw = str(value).strip()
    if not raw:
        return ""
    low = raw.lower()
    if low in SEVERITY_ALIASES:
        return SEVERITY_ALIASES[low]
    if raw in SEVERITY_ALIASES:
        return SEVERITY_ALIASES[raw]
    for key, norm in SEVERITY_ALIASES.items():
        if key and (key in low or key in raw):
            return norm
    return ""


def _severity_label(value) -> str:
    norm = _normalize_severity(value)
    if norm:
        return SEVERITY_META[norm]["label"]
    text = _single_line(value)
    return text or "无"


def _severity_class(value) -> str:
    norm = _normalize_severity(value)
    return SEVERITY_META[norm]["class"] if norm else ""


def _severity_badge(value) -> str:
    cls = _severity_class(value) or "sev-none"
    return '<span class="badge {0}">{1}</span>'.format(cls, _escape(_severity_label(value)))


def _label_for(key: str) -> str:
    return FIELD_LABELS.get(key, key.replace("_", " "))


def _flatten_lines(value):
    if _is_blank(value):
        return []
    if isinstance(value, bool):
        return ["是" if value else "否"]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, str):
        text = value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        return text.split("\n") if text else []
    if isinstance(value, dict):
        lines = []
        for key, val in value.items():
            parts = _flatten_lines(val)
            if not parts:
                continue
            head = _label_for(str(key))
            lines.append(f"{head}: {parts[0]}")
            lines.extend(f"  {line}" for line in parts[1:])
        return lines
    if isinstance(value, (list, tuple)):
        lines = []
        for item in value:
            parts = _flatten_lines(item)
            if not parts:
                continue
            lines.append(f"• {parts[0]}")
            lines.extend(f"  {line}" for line in parts[1:])
        return lines
    return [str(value)]


def _single_line(value) -> str:
    lines = _flatten_lines(value)
    return lines[0] if lines else ""


def _render_text(value, empty="无") -> str:
    lines = _flatten_lines(value)
    if not lines:
        return f'<span class="empty">{_escape(empty)}</span>'
    return '<div class="text">{0}</div>'.format(_escape("\n".join(lines)))


def _render_boolish(value) -> str:
    flag = _to_bool(value)
    if flag is True:
        return '<span class="badge sev-manual">是</span>'
    if flag is False:
        return "否"
    return _render_text(value)


def _render_severity(value) -> str:
    return _severity_badge(value)


def _safe_href(target: str) -> str:
    text = target.strip()
    if not text:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return quote(text, safe=":/?#[]@!$&'()*+,;=%-._~")
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return "file:///" + quote(text.replace("\\", "/"), safe=":/!$&'()*+,;=%-._~")
    if text.startswith("\\\\"):
        return "file://" + quote(text.replace("\\", "/"), safe=":/!$&'()*+,;=%-._~")
    if any(ch in text for ch in (":", "\\", "/")):
        return quote(text.replace("\\", "/"), safe="/!$&'()*+,;=%-._~")
    return ""


def _render_link_cell(value) -> str:
    text = _single_line(value)
    if not text:
        return '<span class="empty">无</span>'
    href = _safe_href(text)
    shown = _escape(text)
    if not href:
        return '<div class="text mono">{0}</div>'.format(shown)
    return (
        '<div class="text"><a class="mono" href="{0}">{1}</a></div>'
        .format(_escape(href), shown)
    )


def _table(headers, rows, classes=None, wide=False):
    if not rows:
        return '<p class="empty">无</p>'
    out = ['<div class="table-wrap{0}"><table><thead><tr>'.format(" wide-table" if wide else "")]
    out.extend(f"<th>{_escape(label)}</th>" for label in headers)
    out.append("</tr></thead><tbody>")
    classes = classes or [""] * len(rows)
    for idx, row in enumerate(rows):
        cls = classes[idx] if idx < len(classes) else ""
        class_attr = f' class="{cls}"' if cls else ""
        out.append(f"<tr{class_attr}>")
        out.extend(f"<td>{cell}</td>" for cell in row)
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _kv_table(mapping) -> str:
    if not isinstance(mapping, dict) or not mapping:
        return '<p class="empty">无</p>'
    rows = []
    for key, value in mapping.items():
        rows.append((_escape(_label_for(str(key))), _render_text(value)))
    return _table(["字段", "内容"], rows)


def _issue_counts(issues):
    counts = {key: 0 for key in SEVERITY_ORDER}
    for issue in issues:
        norm = _normalize_severity(issue.get("severity") if isinstance(issue, dict) else "")
        if norm:
            counts[norm] += 1
    return counts


def _summary_counts(data):
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    raw = summary.get("severity_counts")
    if isinstance(raw, dict):
        counts = {key: 0 for key in SEVERITY_ORDER}
        for key, value in raw.items():
            norm = _normalize_severity(key)
            if not norm:
                continue
            try:
                counts[norm] += int(value)
            except (TypeError, ValueError):
                continue
        if any(counts.values()):
            return counts
    items = []
    for key in ("issues", "language_findings", "figure_inventory"):
        values = data.get(key)
        if isinstance(values, list):
            items.extend(item for item in values if isinstance(item, dict))
    return _issue_counts(items)


def _issue_row(issue, index):
    item = issue if isinstance(issue, dict) else {"problem": issue}
    row = []
    for key, _label in ISSUE_FIELDS:
        value = item.get(key)
        if key == "severity":
            row.append(_render_severity(value))
        elif key in ("technical_meaning_changed", "requires_author_confirmation"):
            row.append(_render_boolish(value))
        else:
            row.append(_render_text(value))
    issue_id = item.get("issue_id") or f"ISSUE-{index:03d}"
    location = _single_line(item.get("location")) or "位置未提供"
    category = _single_line(item.get("category")) or "未分类"
    title = (
        f"{_severity_badge(item.get('severity'))} "
        f"{_escape(str(issue_id))} · {_escape(category)} · {_escape(location)}"
    )
    details = ['<details class="issue {0}"><summary>{1}</summary><div class="issue-body"><dl class="kv">'.format(
        _severity_class(item.get("severity")), title)]
    for key, label in ISSUE_FIELDS:
        value = item.get(key)
        if key == "severity":
            rendered = _render_severity(value)
        elif key in ("technical_meaning_changed", "requires_author_confirmation"):
            rendered = _render_boolish(value)
        else:
            rendered = _render_text(value)
        details.append(f"<dt>{_escape(label)}</dt><dd>{rendered}</dd>")
    details.append("</dl></div></details>")
    return row, details


def _render_issues(issues) -> str:
    if not isinstance(issues, list) or not issues:
        return '<p class="empty">无</p>'
    rows = []
    classes = []
    details = []
    for idx, issue in enumerate(issues, 1):
        row, detail = _issue_row(issue, idx)
        rows.append(row)
        classes.append(_severity_class(issue.get("severity") if isinstance(issue, dict) else ""))
        details.extend(detail)
    table_html = _table([label for _, label in ISSUE_FIELDS], rows, classes=classes, wide=True)
    return table_html + "<h3>逐项详情</h3>" + "".join(details)


def _render_language_findings(findings) -> str:
    if not isinstance(findings, list) or not findings:
        return '<p class="empty">无</p>'
    headers = [
        "ID", "严重度", "置信度", "位置", "原文", "建议写法", "原因",
        "上下文依据", "技术含义变化", "需作者确认", "来源"
    ]
    rows = []
    classes = []
    for idx, raw in enumerate(findings, 1):
        item = raw if isinstance(raw, dict) else {"source_text": raw}
        rows.append([
            _render_text(item.get("finding_id") or item.get("id") or f"LANG-{idx:03d}"),
            _render_severity(item.get("severity")),
            _render_text(item.get("confidence")),
            _render_text(item.get("location")),
            _render_text(item.get("source_text") or item.get("original") or item.get("actual")),
            _render_text(item.get("suggestion") or item.get("suggested_text") or item.get("expected")),
            _render_text(item.get("reason") or item.get("problem") or item.get("note")),
            _render_text(item.get("context_basis") or item.get("context") or item.get("evidence")),
            _render_boolish(item.get("technical_meaning_changed")),
            _render_boolish(item.get("requires_author_confirmation")),
            _render_text(item.get("source")),
        ])
        classes.append(_severity_class(item.get("severity")))
    return _table(headers, rows, classes=classes)


def _render_figure_inventory(items) -> str:
    if not isinstance(items, list) or not items:
        return '<p class="empty">无</p>'
    headers = [
        "图项ID", "位置", "预期 / expected", "实际 / actual", "状态", "备注", "来源"
    ]
    rows = []
    classes = []
    for idx, raw in enumerate(items, 1):
        item = raw if isinstance(raw, dict) else {"actual": raw}
        severity = item.get("severity")
        if not severity and _to_bool(item.get("requires_author_confirmation")) is True:
            severity = "manual"
        rows.append([
            _render_text(item.get("figure_id") or item.get("id") or f"FIG-{idx:03d}"),
            _render_text(item.get("location")),
            _render_text(item.get("expected") or item.get("expected_figure") or item.get("expected_caption")),
            _render_text(item.get("actual") or item.get("actual_figure") or item.get("actual_caption")),
            _render_text(item.get("status") or item.get("result") or item.get("outcome")),
            _render_text(item.get("note") or item.get("notes") or item.get("problem") or item.get("recommendation")),
            _render_text(item.get("source")),
        ])
        classes.append(_severity_class(severity))
    return _table(headers, rows, classes=classes)


def _artifact_rows(section):
    rows = []
    if isinstance(section, dict):
        iterator = section.items()
    elif isinstance(section, list):
        iterator = [(None, item) for item in section]
    else:
        iterator = [(None, section)]
    for group, raw in iterator:
        if isinstance(raw, list):
            for item in raw:
                rows.extend(_artifact_rows({group: item}))
            continue
        item = raw if isinstance(raw, dict) else {"path": raw}
        target = item.get("href") or item.get("url") or item.get("path") or item.get("file")
        label = item.get("label") or item.get("name") or item.get("title") or group or target or "工件"
        rows.append([
            _render_text(label),
            _render_text(item.get("type") or group),
            _render_link_cell(target),
            _render_text(item.get("description") or item.get("note") or item.get("notes")),
        ])
    return rows


def _render_trace_artifacts(section) -> str:
    rows = _artifact_rows(section)
    if not rows:
        return '<p class="empty">无</p>'
    return _table(["工件", "类型/分组", "链接或路径", "说明"], rows)


def _checked_rows(section):
    rows = []
    classes = []
    entries = section.items() if isinstance(section, dict) else enumerate(section or [], 1)
    for key, raw in entries:
        item = raw if isinstance(raw, dict) else {"item": raw}
        severity = item.get("severity")
        if not severity and _to_bool(item.get("requires_author_confirmation")) is True:
            severity = "manual"
        rows.append([
            _render_text(item.get("item") or item.get("check") or item.get("title") or item.get("name") or key),
            _render_text(item.get("result") or item.get("status") or item.get("outcome")),
            _render_text(item.get("note") or item.get("notes") or item.get("comment") or item.get("details")),
            _render_text(item.get("source") or item.get("location")),
            _render_boolish(item.get("requires_author_confirmation")),
        ])
        classes.append(_severity_class(severity))
    return rows, classes


def _render_checked_items(section) -> str:
    rows, classes = _checked_rows(section if section is not None else [])
    if not rows:
        return '<p class="empty">无</p>'
    return _table(["核查项", "结果", "备注", "来源", "需作者确认"], rows, classes=classes)


def _manual_confirmation_rows(data):
    rows = []
    issues = data.get("issues")
    if isinstance(issues, list):
        for idx, raw in enumerate(issues, 1):
            item = raw if isinstance(raw, dict) else {"problem": raw}
            requires = _to_bool(item.get("requires_author_confirmation"))
            manual = _normalize_severity(item.get("severity")) == "manual"
            changed = _to_bool(item.get("technical_meaning_changed"))
            if requires or manual or changed:
                reason = item.get("problem") or item.get("recommendation") or item.get("evidence")
                rows.append([
                    _render_text("问题"),
                    _render_text(item.get("issue_id") or f"ISSUE-{idx:03d}"),
                    _render_text(item.get("location")),
                    _render_text(reason),
                ])
    checked = data.get("checked_items")
    entries = checked.items() if isinstance(checked, dict) else enumerate(checked or [], 1)
    for key, raw in entries:
        item = raw if isinstance(raw, dict) else {"item": raw}
        requires = _to_bool(item.get("requires_author_confirmation"))
        manual = _normalize_severity(item.get("severity")) == "manual"
        if requires or manual:
            rows.append([
                _render_text("核查项"),
                _render_text(item.get("item") or item.get("check") or item.get("title") or item.get("name") or key),
                _render_text(item.get("location") or item.get("source")),
                _render_text(item.get("note") or item.get("notes") or item.get("comment") or item.get("details")),
            ])
    figures = data.get("figure_inventory")
    if isinstance(figures, list):
        for idx, raw in enumerate(figures, 1):
            item = raw if isinstance(raw, dict) else {"actual": raw}
            requires = _to_bool(item.get("requires_author_confirmation"))
            manual = _normalize_severity(item.get("severity")) == "manual"
            if requires or manual:
                rows.append([
                    _render_text("图表盘点"),
                    _render_text(item.get("figure_id") or item.get("id") or f"FIG-{idx:03d}"),
                    _render_text(item.get("location")),
                    _render_text(item.get("note") or item.get("notes") or item.get("problem")),
                ])
    return rows


def _render_manual_confirmation(data) -> str:
    rows = _manual_confirmation_rows(data)
    if not rows:
        return '<p class="empty">无</p>'
    return _table(["来源", "标识", "位置", "需确认原因"], rows)


def _stats_html(data):
    counts = _summary_counts(data)
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    total = sum(counts.values())
    if isinstance(summary.get("issue_count"), int):
        total = summary["issue_count"]
    manual_items = len(_manual_confirmation_rows(data))
    cards = []
    for severity in SEVERITY_ORDER:
        cards.append(
            '<div class="stat {0}"><div class="k">{1}</div><div class="v">{2}</div></div>'.format(
                SEVERITY_META[severity]["class"], SEVERITY_META[severity]["label"], counts[severity]
            )
        )
    cards.append('<div class="stat"><div class="k">问题总数</div><div class="v">{0}</div></div>'.format(total))
    cards.append('<div class="stat"><div class="k">需人工/作者确认</div><div class="v">{0}</div></div>'.format(manual_items))
    return '<div class="stats">{0}</div>'.format("".join(cards))


def _default_title(src_path: str, data: dict, override: str | None) -> str:
    if override:
        return override
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    for key in ("title", "report_title", "report_name", "report_id"):
        text = _single_line(metadata.get(key))
        if text:
            return text
    base = os.path.basename(src_path) if src_path else "review"
    if base.lower().endswith(".review.json"):
        return base[:-12]
    return os.path.splitext(base)[0]


def _default_output_path(src: str) -> str:
    lower = src.lower()
    if lower.endswith(".review.json"):
        return os.path.splitext(src)[0] + ".html"
    if lower.endswith(".json"):
        return src[:-5] + ".review.html"
    return src + ".review.html"


def render_html(data: dict, title: str, source_name: str = "") -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = [
        f"<h1>{_escape(title)}</h1>",
        '<p class="intro">规范化审核结果渲染（JSON → HTML），缺失章节统一显示为“无”，颜色沿用现有严重度约定。</p>',
        "<h2>报告信息</h2>",
        _kv_table(data.get("metadata")),
        "<h2>摘要</h2>",
        _kv_table(data.get("summary")),
        "<h2>证据摘要</h2>",
        _kv_table(data.get("evidence_summary")),
        "<h2>问题统计</h2>",
        _stats_html(data),
        "<h2>问题明细</h2>",
        _render_issues(data.get("issues")),
        "<h2>语言建议</h2>",
        _render_language_findings(data.get("language_findings")),
        "<h2>图表盘点（预期 vs 实际）</h2>",
        _render_figure_inventory(data.get("figure_inventory")),
        "<h2>人工确认项</h2>",
        _render_manual_confirmation(data),
        "<h2>追溯工件</h2>",
        _render_trace_artifacts(data.get("trace_artifacts")),
        "<h2>已核查项</h2>",
        _render_checked_items(data.get("checked_items")),
        '<div class="footer">由 render_review.py 生成 · {0} · 源文件：{1} · AI 辅助审核，结果需工程师复核确认</div>'.format(
            _escape(now), _escape(source_name or "无")
        ),
    ]
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_escape(title)}</title><style>{CSS}</style></head><body>"
        + "".join(body)
        + "</body></html>"
    )


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit("错误：输入 JSON 顶层必须是对象。")
    return data


def _write_text(path: str, text: str):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _smoke_sample():
    return {
        "metadata": {
            "report_name": "示例报告 <script>alert(1)</script>",
            "report_id": "DV-001|A",
            "source_file": r"C:\reports\demo.review.json",
        },
        "summary": {
            "overall_assessment": "存在 1 项严重问题\n另有人工确认项。",
            "conclusion": "建议复核后再发布。",
        },
        "issues": [
            {
                "issue_id": "ISSUE-001",
                "category": "结论一致性",
                "severity": "critical",
                "confidence": "0.98",
                "location": "[S03]\n汇总页",
                "expected": "结论与判定表一致",
                "actual": "判定表写“不合格”，结论写“全部合格”",
                "evidence": "原文包含 <b>全部合格</b> | 判定表写不合格",
                "problem": "结论与数据矛盾",
                "recommendation": "逐项复核并更正",
                "technical_meaning_changed": True,
                "requires_author_confirmation": True,
                "source": "normalized-review",
            }
        ],
        "language_findings": [
            {
                "finding_id": "LANG-001",
                "severity": "minor",
                "location": "[P0005]",
                "source_text": "容里",
                "suggestion": "容量",
                "reason": "明显错别字",
                "technical_meaning_changed": False,
                "source": "spell-check",
            }
        ],
        "figure_inventory": [
            {
                "figure_id": "FIG-001",
                "location": "[S08]",
                "expected": "应有循环曲线图",
                "actual": "仅有标题，无图像内容",
                "status": "缺失",
                "note": "需作者补图",
                "severity": "manual",
                "source": r"C:\reports\trace\slide08.png",
                "requires_author_confirmation": True,
            }
        ],
        "trace_artifacts": [
            {
                "label": "工作稿",
                "type": "workpaper",
                "path": r"C:\reports\demo.workpaper.md",
                "description": "定位索引与原文摘录",
            }
        ],
        "checked_items": [
            {
                "item": "图片页人工核对",
                "result": "待确认",
                "details": "含不可机读图片页",
                "location": "[S08]",
                "requires_author_confirmation": True,
            }
        ],
    }


def _run_smoke_test(output_path: str | None):
    html_text = render_html(_smoke_sample(), "render_review smoke test", "built-in sample")
    checks = [
        ("escaped script", "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text and "<script>alert(1)</script>" not in html_text),
        ("severity badge", "badge sev-critical" in html_text),
        ("missing rendering", "无" in render_html({}, "empty", "built-in sample")),
        ("file link", "file:///" in html_text),
    ]
    failed = [name for name, ok in checks if not ok]
    if failed:
        raise SystemExit("SMOKE FAIL: " + ", ".join(failed))
    if output_path:
        _write_text(output_path, html_text)
        print(f"SMOKE OK: {output_path}")
    else:
        print("SMOKE OK")


def _build_parser():
    parser = argparse.ArgumentParser(
        description="将规范化审核 JSON 渲染为自包含 HTML。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "默认输出命名：\n"
            "  demo.review.json -> demo.review.html\n"
            "  demo.json        -> demo.review.html"
        ),
    )
    parser.add_argument("input", nargs="?", help="规范化审核 JSON 路径")
    parser.add_argument("-o", "--output", help="输出 HTML 路径")
    parser.add_argument("--title", help="覆盖页面标题")
    parser.add_argument("--smoke-test", action="store_true", help="运行内置最小冒烟测试")
    return parser


def main(argv=None):
    _reconfigure_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.smoke_test:
        _run_smoke_test(args.output)
        return 0
    if not args.input:
        parser.error("请提供输入 JSON，或使用 --smoke-test。")
    data = _load_json(args.input)
    title = _default_title(args.input, data, args.title)
    output_path = args.output or _default_output_path(args.input)
    page = render_html(data, title, args.input)
    _write_text(output_path, page)
    print(f"OK: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
