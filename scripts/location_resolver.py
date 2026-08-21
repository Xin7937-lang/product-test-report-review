#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional DOCX page resolver used by evidence_pipeline.py.

The standard-library extractor cannot know Word's pagination. This module accepts
an explicit page map or, when pywin32 and Microsoft Word are available, resolves
paragraph/table anchors through Word's range page information. All failures are
returned as warnings so callers can preserve deterministic anchor locations.
"""

from __future__ import annotations

import json
import os
import re


SCHEMA_VERSION = "location.v1"
WD_ACTIVE_END_PAGE_NUMBER = 3


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _anchor_key(value):
    text = _clean(value)
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1]
    return text


def _normalize_page(value):
    if isinstance(value, dict):
        value = value.get("page") or value.get("page_number")
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def load_page_map(path):
    """Load {anchor: page} or {pages: {anchor: page}} from JSON."""
    with open(path, encoding="utf-8-sig") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("页码映射 JSON 顶层必须是对象")
    pages = raw.get("pages", raw)
    if not isinstance(pages, dict):
        raise ValueError("页码映射 JSON 的 pages 必须是对象")
    result = {}
    for anchor, value in pages.items():
        page = _normalize_page(value)
        if page is not None:
            result[f"[{_anchor_key(anchor)}]"] = page
    return result


def _resolve_with_page_map(page_map, anchors):
    pages = {}
    for anchor in anchors:
        normalized = f"[{_anchor_key(anchor)}]"
        if normalized in page_map:
            pages[normalized] = page_map[normalized]
    return {
        "pages": pages,
        "method": "page-map",
        "status": "resolved" if pages else "unavailable",
        "warnings": [] if pages else ["页码映射未覆盖当前文档锚点"],
    }


def _resolve_with_word(source_path, paragraph_anchors, table_anchors):
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return {
            "pages": {},
            "method": "unavailable",
            "status": "unavailable",
            "warnings": ["未安装 pywin32，无法通过 Microsoft Word 解析 DOCX 页码"],
        }

    word = None
    document = None
    pages = {}
    warnings = []
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        document = word.Documents.Open(
            os.path.abspath(source_path),
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        paragraphs = [
            paragraph
            for paragraph in document.Paragraphs
            if _clean(getattr(paragraph.Range, "Text", ""))
        ]
        for anchor, paragraph in zip(paragraph_anchors, paragraphs):
            page = _normalize_page(
                paragraph.Range.Information(WD_ACTIVE_END_PAGE_NUMBER)
            )
            if page is not None:
                pages[anchor] = page

        for index, table in enumerate(document.Tables, 1):
            if index > len(table_anchors):
                break
            page = _normalize_page(
                table.Range.Information(WD_ACTIVE_END_PAGE_NUMBER)
            )
            if page is not None:
                pages[table_anchors[index - 1]] = page
    except Exception as exc:  # Optional backend failure is surfaced to evidence.
        warnings.append(f"Microsoft Word 页码解析失败：{exc}")
        pages = {}
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception as exc:
                warnings.append(f"关闭 Word 文档失败：{exc}")
        if word is not None:
            try:
                word.Quit()
            except Exception as exc:
                warnings.append(f"退出 Word 失败：{exc}")

    return {
        "pages": pages,
        "method": "word-com",
        "status": "resolved" if pages else "unavailable",
        "warnings": warnings or ([] if pages else ["Word 未返回可用页码"]),
    }


def resolve_docx_pages(source_path, units, page_map_path=None, use_word=False):
    """Return page mapping metadata without fabricating unresolved pages."""
    anchors = [
        unit.get("anchor")
        for unit in units
        if isinstance(unit, dict) and unit.get("anchor")
    ]
    paragraph_anchors = [
        anchor for anchor in anchors if str(anchor).startswith("[P")
    ]
    table_anchors = [
        anchor for anchor in anchors if str(anchor).startswith("[T")
    ]
    if page_map_path:
        return _resolve_with_page_map(load_page_map(page_map_path), anchors)
    if use_word:
        return _resolve_with_word(source_path, paragraph_anchors, table_anchors)
    return {
        "pages": {},
        "method": "unavailable",
        "status": "unavailable",
        "warnings": ["未启用 DOCX 页码解析；保留段落/表格锚点"],
    }
