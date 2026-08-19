#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize model/legacy review findings into the JSON contract consumed by render_review.py."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys


SCHEMA_VERSION = "review-result.v1"
SEVERITY_ALIASES = {
    "critical": "严重",
    "严重": "严重",
    "high": "严重",
    "高": "严重",
    "major": "一般",
    "一般": "一般",
    "medium": "一般",
    "中": "一般",
    "minor": "建议",
    "建议": "建议",
    "low": "建议",
    "低": "建议",
    "manual": "人工核对项",
    "人工": "人工核对项",
    "人工核对": "人工核对项",
    "人工核对项": "人工核对项",
}
CONFIDENCE_ALIASES = {
    "high": "高",
    "高": "高",
    "confirmed": "高",
    "medium": "中",
    "中": "中",
    "moderate": "中",
    "low": "人工",
    "人工": "人工",
    "manual": "人工",
    "unknown": "人工",
}
REQUIRED_ISSUE_FIELDS = (
    "issue_id",
    "category",
    "severity",
    "confidence",
    "location",
    "expected",
    "actual",
    "evidence",
    "recommendation",
)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _text(value).lower() in {"1", "true", "yes", "是", "需要", "需"}


def _severity(value, warnings, path):
    raw = _text(value)
    result = SEVERITY_ALIASES.get(raw.lower(), SEVERITY_ALIASES.get(raw))
    if result:
        return result
    warnings.append(f"{path}.severity 未识别：{raw or '空'}")
    return "人工核对项"


def _confidence(value, warnings, path):
    if isinstance(value, (int, float)):
        return "高" if value >= 0.85 else "中" if value >= 0.6 else "人工"
    raw = _text(value)
    try:
        numeric = float(raw)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None and 0 <= numeric <= 1:
        return "高" if numeric >= 0.85 else "中" if numeric >= 0.6 else "人工"
    result = CONFIDENCE_ALIASES.get(raw.lower(), CONFIDENCE_ALIASES.get(raw))
    if result:
        return result
    warnings.append(f"{path}.confidence 未识别：{raw or '空'}")
    return "人工"


def _first(item, *keys):
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return ""


def _normalize_issue(raw, index, warnings, prefix="issues"):
    item = raw if isinstance(raw, dict) else {"problem": raw}
    path = f"{prefix}[{index}]"
    issue = {
        "issue_id": _first(item, "issue_id", "id") or f"ISSUE-{index:03d}",
        "category": _first(item, "category", "type") or "未分类",
        "severity": _severity(_first(item, "severity", "level"), warnings, path),
        "confidence": _confidence(_first(item, "confidence", "certainty"), warnings, path),
        "location": _first(item, "location", "position") or "[?]",
        "expected": _first(item, "expected", "expectation") or "未提供",
        "actual": _first(item, "actual", "observed") or "未提供",
        "evidence": _first(item, "evidence", "evidence_quote", "evidence_quotes") or "未提供",
        "problem": _first(item, "problem", "description", "issue") or "未提供",
        "recommendation": _first(item, "recommendation", "suggestion", "fix") or "未提供",
        "technical_meaning_changed": _bool(
            _first(item, "technical_meaning_changed", "meaning_changed")
        ),
        "requires_author_confirmation": _bool(
            _first(item, "requires_author_confirmation", "author_confirmation", "needs_confirmation")
        ),
        "source": _first(item, "source", "source_mode") or "review",
        "source_mode": _first(item, "source_mode", "mode") or "hybrid",
        "evidence_type": _first(item, "evidence_type", "type_of_evidence") or "text",
    }
    for key in ("figure_refs", "context_basis", "conservative_recommendation", "notes"):
        if key in item:
            issue[key] = item[key]
    missing = [key for key in REQUIRED_ISSUE_FIELDS if not item.get(key)]
    if missing:
        issue["validation_warnings"] = [f"缺少字段：{', '.join(missing)}"]
        issue["requires_author_confirmation"] = True
        warnings.append(f"{path} 缺少必填字段：{', '.join(missing)}")
    if issue["confidence"] == "人工":
        issue["requires_author_confirmation"] = True
    return issue


def _normalize_language(raw, index, warnings):
    item = raw if isinstance(raw, dict) else {"source_text": raw}
    path = f"language_findings[{index}]"
    severity = _severity(item.get("severity", "建议"), warnings, path)
    confidence = _confidence(item.get("confidence", "中"), warnings, path)
    result = {
        "finding_id": _first(item, "finding_id", "issue_id", "id") or f"LANG-{index:03d}",
        "severity": severity,
        "confidence": confidence,
        "location": _first(item, "location", "position") or "[?]",
        "source_text": _first(item, "source_text", "original", "actual") or "未提供",
        "suggestion": _first(item, "suggestion", "recommended_text", "expected") or "未提供",
        "reason": _first(item, "reason", "problem", "description") or "未提供",
        "context_basis": _first(item, "context_basis", "context", "evidence") or "未提供",
        "technical_meaning_changed": _bool(item.get("technical_meaning_changed")),
        "requires_author_confirmation": _bool(
            _first(item, "requires_author_confirmation", "needs_confirmation")
        ),
        "source": _first(item, "source", "source_mode") or "language-review",
    }
    if confidence == "人工":
        result["requires_author_confirmation"] = True
    return result


def _merge_unique(items, key, extra_keys=()):
    result = []
    seen = set()
    for item in items:
        if isinstance(item, dict):
            identity_parts = [item.get(key)]
            identity_parts.extend(item.get(name) for name in extra_keys)
            identity = tuple(identity_parts)
        else:
            identity = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def _load(path):
    with open(path, encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} 顶层必须是对象")
    return value


def _append_artifact(target, label, path, artifact_type):
    if not path:
        return
    target.append({
        "label": label,
        "type": artifact_type,
        "path": os.path.abspath(path),
    })


def normalize(raw, evidence=None, figure_review=None, language_input=None):
    warnings = []
    issues = []
    language_findings = []
    figure_inventory = []
    raw_issues = raw.get("issues") or []
    for index, item in enumerate(raw_issues, 1):
        issues.append(_normalize_issue(item, index, warnings))
    for index, item in enumerate(raw.get("language_findings") or [], 1):
        language_findings.append(_normalize_language(item, index, warnings))
    figure_inventory.extend(raw.get("figure_inventory") or [])

    if figure_review:
        offset = len(issues)
        for index, item in enumerate(figure_review.get("issues") or [], 1):
            issues.append(_normalize_issue(item, offset + index, warnings, "figure_review.issues"))
        figure_inventory.extend(figure_review.get("figure_inventory") or [])

    if language_input and isinstance(language_input.get("language_findings"), list):
        offset = len(language_findings)
        for index, item in enumerate(language_input["language_findings"], 1):
            language_findings.append(_normalize_language(item, offset + index, warnings))

    issues = _merge_unique(issues, "issue_id")
    language_findings = _merge_unique(language_findings, "finding_id")
    figure_inventory = _merge_unique(
        figure_inventory,
        "figure_id",
        extra_keys=("location", "status", "actual_id"),
    )

    counts = {"严重": 0, "一般": 0, "建议": 0, "人工核对项": 0}
    for item in issues + language_findings:
        severity = item.get("severity")
        if severity in counts:
            counts[severity] += 1
    for item in figure_inventory:
        severity = item.get("severity")
        if severity in counts:
            counts[severity] += 1

    metadata = dict(raw.get("metadata") or {})
    if evidence:
        document = evidence.get("document") or {}
        metadata.setdefault("source_file", document.get("source_path"))
        metadata.setdefault("report_name", document.get("source_name"))
        metadata.setdefault("source_type", document.get("source_type"))
        metadata["evidence_schema_version"] = evidence.get("schema_version")

    summary = dict(raw.get("summary") or {})
    summary["severity_counts"] = counts
    summary["issue_count"] = sum(counts.values())
    summary["manual_confirmation_count"] = sum(
        1
        for item in issues + language_findings + figure_inventory
        if _bool(item.get("requires_author_confirmation")) or item.get("severity") == "人工核对项"
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "metadata": metadata,
        "summary": summary,
        "issues": issues,
        "language_findings": language_findings,
        "figure_inventory": figure_inventory,
        "trace_artifacts": list(raw.get("trace_artifacts") or []),
        "checked_items": list(raw.get("checked_items") or []),
        "validation_warnings": warnings,
        "normalized_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    if evidence is not None:
        result["evidence_summary"] = {
            "schema_version": evidence.get("schema_version"),
            "source": (evidence.get("document") or {}).get("source_name"),
            "unit_count": (evidence.get("document") or {}).get("unit_count"),
            "expected_figure_count": (evidence.get("document") or {}).get("expected_figure_count"),
            "actual_figure_count": (evidence.get("document") or {}).get("actual_figure_count"),
            "extraction_warning_count": len(evidence.get("extraction_warnings") or []),
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="归一化审核 JSON，生成 render_review.py 可消费的 review-result.v1。"
    )
    parser.add_argument("raw_review", help="模型/人工填写的原始审核 JSON")
    parser.add_argument("-o", "--output", required=True, help="输出归一化 JSON")
    parser.add_argument("--evidence", help="evidence.v1 JSON")
    parser.add_argument("--figure-review", help="figure-review.v1 JSON")
    parser.add_argument("--language-input", help="可选的语言审核 JSON")
    parser.add_argument("--workpaper", help="加入追溯工件路径")
    args = parser.parse_args(argv)

    raw = _load(args.raw_review)
    evidence = _load(args.evidence) if args.evidence else None
    figure_review = _load(args.figure_review) if args.figure_review else None
    language_input = _load(args.language_input) if args.language_input else None
    result = normalize(raw, evidence, figure_review, language_input)
    _append_artifact(result["trace_artifacts"], "原始审核 JSON", args.raw_review, "raw-review")
    _append_artifact(result["trace_artifacts"], "证据 JSON", args.evidence, "evidence")
    _append_artifact(result["trace_artifacts"], "图表检查 JSON", args.figure_review, "figure-review")
    _append_artifact(result["trace_artifacts"], "语言审核输入", args.language_input, "language-input")
    _append_artifact(result["trace_artifacts"], "工作稿", args.workpaper, "workpaper")

    output_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if result["validation_warnings"]:
        print(f"WARNING: {output_path} (warnings={len(result['validation_warnings'])})")
    else:
        print(f"OK: {output_path} (issues={len(result['issues'])})")


if __name__ == "__main__":
    main()
