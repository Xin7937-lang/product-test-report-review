#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render evidence.v1 as a locally navigable HTML evidence viewer."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from urllib.parse import quote


def _escape(value):
    return html.escape(str(value or ""), quote=True)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _text_of(unit):
    text = _clean(unit.get("text"))
    if text:
        return text
    rows = unit.get("rows") or []
    return " / ".join(" | ".join(_clean(cell) for cell in row) for row in rows if row)


def anchor_id(anchor):
    token = re.sub(r"[^0-9A-Za-z_-]+", "-", str(anchor or "").strip("[]"))
    return "location-" + (token or "unknown")


def _file_href(path):
    if not path:
        return ""
    text = os.path.abspath(path).replace("\\", "/")
    return "file:///" + quote(text, safe=":/!$&'()*+,;=%-._~")


def _location_display(detail):
    if not isinstance(detail, dict):
        return str(detail or "[?]")
    return detail.get("display") or detail.get("source_anchor") or "[?]"


def _location_block(detail):
    if not isinstance(detail, dict):
        return ""
    lines = [
        f"<div><b>位置：</b>{_escape(_location_display(detail))}</div>",
        f"<div><b>锚点：</b><code>{_escape(detail.get('source_anchor'))}</code></div>",
    ]
    section = detail.get("section_context") or []
    if section:
        lines.append(f"<div><b>章节：</b>{_escape(' / '.join(section))}</div>")
    if detail.get("page_status") == "unavailable":
        lines.append('<div class="muted">DOCX 页码未解析，未猜测页码。</div>')
    if detail.get("position_summary"):
        lines.append(f"<div><b>相对位置：</b>{_escape(detail['position_summary'])}</div>")
    return "".join(lines)


def render_evidence_html(evidence, title=None):
    document = evidence.get("document") or {}
    source_path = document.get("source_path")
    title = title or document.get("source_name") or "审核证据查看器"
    units = evidence.get("units") or []
    actuals = evidence.get("actual_figures") or []
    actual_by_anchor = {}
    for actual in actuals:
        actual_by_anchor.setdefault(actual.get("source_anchor"), []).append(actual)

    unit_sections = []
    for unit in units:
        anchor = unit.get("anchor") or "[?]"
        detail = unit.get("location_detail") or {}
        media = actual_by_anchor.get(anchor, [])
        media_html = []
        for actual in media:
            path = actual.get("extracted_path")
            media_html.append(
                '<li>{0} <code>{1}</code>{2}</li>'.format(
                    _escape(actual.get("kind") or "媒体"),
                    _escape(actual.get("id") or ""),
                    (
                        f' · <a href="{_escape(_file_href(os.path.join(document.get("artifact_dir", ""), path)))}">打开媒体</a>'
                        if path else ""
                    ),
                )
            )
        unit_sections.append(
            '<section id="{id}" class="unit">'
            '<h2>{anchor} · {kind}</h2>'
            '<div class="location">{location}</div>'
            '<pre>{text}</pre>{media}'
            '</section>'.format(
                id=_escape(anchor_id(anchor)),
                anchor=_escape(anchor),
                kind=_escape(unit.get("kind") or ""),
                location=_location_block(detail),
                text=_escape(_text_of(unit)),
                media=(
                    "<h3>关联媒体</h3><ul>" + "".join(media_html) + "</ul>"
                    if media_html else ""
                ),
            )
        )

    warning_html = "".join(
        f"<li>{_escape(item.get('message') if isinstance(item, dict) else item)}</li>"
        for item in evidence.get("extraction_warnings") or []
    )
    source_link = (
        f'<a href="{_escape(_file_href(source_path))}">打开原文件</a>'
        if source_path else "源文件路径未提供"
    )
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: "Microsoft YaHei","Segoe UI",sans-serif; max-width: 1100px;
       margin: 0 auto; padding: 24px; color: #1a1a1a; line-height: 1.6; }}
h1 {{ border-bottom: 3px solid #222; padding-bottom: 8px; }}
h2 {{ margin-bottom: 6px; }}
.unit {{ border: 1px solid #d0d0d0; border-radius: 8px; padding: 12px 16px;
         margin: 16px 0; scroll-margin-top: 16px; }}
.location {{ background: #f7f7f9; padding: 8px 10px; border-radius: 5px; }}
pre {{ white-space: pre-wrap; word-break: break-word; background: #fafafa;
       padding: 10px; border-left: 3px solid #888; }}
.muted {{ color: #666; }}
code {{ font-family: Consolas,"Courier New",monospace; }}
a {{ color: #0b57d0; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style></head><body>
<h1>{title}</h1>
<p><b>源文件：</b>{source_link}</p>
<p><b>格式：</b>{source_type} · <b>证据版本：</b>{schema}</p>
{warnings}
{units}
</body></html>""".format(
        title=_escape(title),
        source_link=source_link,
        source_type=_escape(document.get("source_type") or "unknown"),
        schema=_escape(evidence.get("schema_version") or "unknown"),
        warnings=(
            "<h2>提取警告</h2><ul>" + warning_html + "</ul>"
            if warning_html else ""
        ),
        units="".join(unit_sections) or "<p>无可导航证据单元。</p>",
    )


def write_evidence_html(evidence_path, output_path=None):
    with open(evidence_path, encoding="utf-8-sig") as handle:
        evidence = json.load(handle)
    output_path = output_path or os.path.join(
        os.path.dirname(os.path.abspath(evidence_path)), "evidence.html"
    )
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(render_evidence_html(evidence))
    return os.path.abspath(output_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="将 evidence.v1 渲染为可点击的本地 HTML 证据查看器。")
    parser.add_argument("evidence", help="evidence.json 路径")
    parser.add_argument("-o", "--output", help="输出 HTML 路径；默认同目录 evidence.html")
    args = parser.parse_args(argv)
    output = write_evidence_html(args.evidence, args.output)
    print(f"OK: {output}")


if __name__ == "__main__":
    raise SystemExit(main())
