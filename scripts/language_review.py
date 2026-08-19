#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare context units for model-assisted Chinese/English technical-language review."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys


SCHEMA_VERSION = "language-review-input.v1"
UNIT_KINDS = {"paragraph", "shape", "table", "slide", "notes", "object"}
TECHNICAL_TOKEN_RE = re.compile(
    r"[A-Za-z]{2,}(?:[-_/][A-Za-z0-9]+)*|\d+(?:\.\d+)?\s*[A-Za-z%Ω℃°]+|"
    r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)+"
)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _text_of(unit):
    text = _clean(unit.get("text"))
    if text:
        return text
    rows = unit.get("rows") or []
    return " / ".join(" | ".join(_clean(cell) for cell in row) for row in rows if row)


def _language_mode(text):
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "other"


def _technical_terms(text):
    terms = []
    seen = set()
    for match in TECHNICAL_TOKEN_RE.finditer(text):
        raw = match.group(0).strip()
        candidates = re.findall(r"[A-Za-z]{2,}(?:[-_/][A-Za-z0-9]+)*", raw)
        if not candidates:
            candidates = [raw]
        for token in candidates or [raw]:
            token = token.strip()
            key = token.upper()
            if key in seen:
                continue
            seen.add(key)
            terms.append(token)
    return terms[:20]


def _actuals_by_anchor(evidence):
    result = {}
    for actual in evidence.get("actual_figures") or []:
        anchor = actual.get("source_anchor")
        if anchor:
            result.setdefault(anchor, []).append({
                "id": actual.get("id"),
                "kind": actual.get("kind"),
                "figure_id": actual.get("figure_id"),
                "extracted_path": actual.get("extracted_path"),
                "excluded": actual.get("excluded"),
            })
    return result


def _tables_by_section(evidence):
    result = {}
    for unit in evidence.get("units") or []:
        if unit.get("kind") != "table":
            continue
        section = tuple(unit.get("section_context") or [])
        result.setdefault(section, []).append({
            "anchor": unit.get("anchor"),
            "text": _text_of(unit),
        })
    return result


def build_review_input(evidence):
    if not isinstance(evidence, dict):
        raise ValueError("evidence JSON 顶层必须是对象")

    document = evidence.get("document") or {}
    actuals_by_anchor = _actuals_by_anchor(evidence)
    tables_by_section = _tables_by_section(evidence)
    units = []
    for unit in evidence.get("units") or []:
        if not isinstance(unit, dict) or unit.get("kind") not in UNIT_KINDS:
            continue
        text = _text_of(unit)
        if not text:
            continue
        anchor = unit.get("anchor")
        units.append({
            "context_id": f"LC{len(units) + 1:04d}",
            "anchor": anchor,
            "order": unit.get("order"),
            "kind": unit.get("kind"),
            "section_context": unit.get("section_context") or [],
            "language_mode": _language_mode(text),
            "text": text,
            "previous": unit.get("neighbor_previous"),
            "next": unit.get("neighbor_next"),
            "direct_figure_references": unit.get("direct_figure_references") or [],
            "nearby_figures": actuals_by_anchor.get(anchor, []),
            "related_tables": [
                table for table in tables_by_section.get(
                    tuple(unit.get("section_context") or []), []
                )
                if table.get("anchor") != anchor
            ][:5],
            "caption_candidates": unit.get("caption_candidates") or [],
            "technical_terms": _technical_terms(text),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "document": {
            "source_path": document.get("source_path"),
            "source_name": document.get("source_name"),
            "source_type": document.get("source_type"),
        },
        "review_requirements": [
            "结合章节标题、前后文、关联图表和术语检查，不按孤立句子定性。",
            "检查语病、口语化、工程报告专业性、歧义、术语一致性和中英文对应关系。",
            "不得改变原始数据、补充上下文中没有的技术结论或夸大结论。",
            "上下文不足时输出 requires_author_confirmation=true，不擅自改写技术含义。",
            "每条建议保留原文、位置、问题类型、修改建议、修改原因、上下文依据、置信度。",
        ],
        "units": units,
        "extraction_warnings": evidence.get("extraction_warnings") or [],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="从 evidence.json 准备上下文驱动的中英文技术语言审核输入。"
    )
    parser.add_argument("evidence", help="evidence_pipeline.py 生成的 JSON")
    parser.add_argument(
        "-o",
        "--output",
        help="输出 JSON 路径；默认写入 evidence 同目录的 language-review-input.json",
    )
    args = parser.parse_args(argv)

    evidence_path = os.path.abspath(args.evidence)
    output_path = os.path.abspath(
        args.output
        or os.path.join(os.path.dirname(evidence_path), "language-review-input.json")
    )
    with open(evidence_path, encoding="utf-8-sig") as handle:
        evidence = json.load(handle)
    result = build_review_input(evidence)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"OK: {output_path} (units={len(result['units'])})")


if __name__ == "__main__":
    main()
