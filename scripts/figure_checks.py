#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run deterministic and optional vision-observation checks over evidence.v1 JSON."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys


SCHEMA_VERSION = "figure-review.v1"
VARIABLE_PAIRS = (
    (("capacity", "容量", "ah", "mAh"), ("voltage", "电压", "v")),
    (("voltage", "电压", "v"), ("current", "电流", "a")),
    (("temperature", "温度", "℃", "°c"), ("voltage", "电压", "v")),
    (("energy", "能量", "wh", "kwh"), ("power", "功率", "w", "kw")),
)
NUMBER_RE = re.compile(r"(?<!\d)-?\d+(?:\.\d+)?")
LOCATION_ANCHOR_RE = re.compile(
    r"\[(?:P\d{4}|T\d{2}|S\d{2}(?:-[A-Za-z0-9]+)?|[HF]\d+)\]"
)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {_as_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(item) for item in value)
    return _clean(value)


def _tokens(text):
    return {
        token.upper()
        for token in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?|[\u4e00-\u9fff]{1,4}", _as_text(text))
    }


def _confidence(value, default="中"):
    if isinstance(value, (int, float)):
        return "高" if value >= 0.85 else "中" if value >= 0.6 else "人工"
    text = _clean(value).lower()
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None and 0 <= numeric <= 1:
        return "高" if numeric >= 0.85 else "中" if numeric >= 0.6 else "人工"
    if text in {"high", "高", "confirmed", "确认"}:
        return "高"
    if text in {"medium", "中", "moderate"}:
        return "中"
    if text in {"low", "人工", "manual", "unknown"}:
        return "人工"
    return default


def _severity(value, default="一般"):
    text = _clean(value).lower()
    aliases = {
        "critical": "严重",
        "严重": "严重",
        "major": "一般",
        "一般": "一般",
        "minor": "建议",
        "建议": "建议",
        "manual": "人工核对项",
        "人工": "人工核对项",
        "人工核对项": "人工核对项",
    }
    return aliases.get(text, default)


def _location(item):
    anchors = item.get("source_anchors") or []
    if anchors:
        return "/".join(str(anchor) for anchor in anchors)
    return item.get("source_anchor") or item.get("location") or "[?]"


def _location_refs(value):
    return LOCATION_ANCHOR_RE.findall(_as_text(value))


def _location_index(evidence):
    index = {}
    for unit in evidence.get("units") or []:
        if unit.get("anchor") and unit.get("location_detail"):
            index[unit["anchor"]] = unit["location_detail"]
    for actual in evidence.get("actual_figures") or []:
        detail = actual.get("location_detail")
        if not detail:
            continue
        if actual.get("source_anchor"):
            index[actual["source_anchor"]] = detail
        if actual.get("id"):
            index[actual["id"]] = detail
    for expected in evidence.get("expected_figures") or []:
        details = expected.get("location_details") or expected.get("location_detail")
        if isinstance(details, dict):
            details = [details]
        for anchor, detail in zip(expected.get("source_anchors") or [], details or []):
            index[anchor] = detail
        for key in ("expected_id", "figure_id", "normalized_figure_id"):
            if expected.get(key) and details:
                index[str(expected[key])] = details[0]
    return index


def _attach_location_details(items, evidence):
    index = _location_index(evidence)
    for item in items:
        if not isinstance(item, dict):
            continue
        details = item.get("location_details") or item.get("location_detail")
        if isinstance(details, dict):
            details = [details]
        details = list(details or [])
        refs = _location_refs(item.get("location"))
        for key in ("actual_id", "expected_id", "figure_id"):
            if item.get(key):
                refs.append(str(item[key]))
        seen = {
            str(detail.get("source_anchor"))
            for detail in details
            if isinstance(detail, dict)
        }
        for ref in refs:
            detail = index.get(ref)
            if not isinstance(detail, dict) or detail.get("source_anchor") in seen:
                continue
            details.append(detail)
            seen.add(detail.get("source_anchor"))
        if details:
            item["location_details"] = details
            if len(details) == 1:
                item["location_detail"] = details[0]


def _expected_text(expected):
    return "；".join(filter(None, [
        expected.get("figure_id"),
        " ".join(expected.get("keywords") or []),
        " ".join(expected.get("excerpts") or []),
        " ".join((expected.get("axis_unit_range_hints") or {}).get("axis") or []),
        " ".join((expected.get("axis_unit_range_hints") or {}).get("units") or []),
        " ".join((expected.get("axis_unit_range_hints") or {}).get("ranges") or []),
    ]))


def _actual_text(actual, observation=None):
    fields = [
        actual.get("figure_id"),
        actual.get("kind"),
        " ".join(actual.get("caption_candidates") or []),
        _as_text(actual.get("nearby_text")),
    ]
    if observation:
        fields.append(_as_text(observation))
    return "；".join(filter(None, fields))


def _issue(issue_id, category, severity, confidence, location, expected, actual,
           evidence, problem, recommendation, source_mode="text",
           requires_author_confirmation=False, figure_refs=None):
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "location": location,
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
        "problem": problem,
        "recommendation": recommendation,
        "technical_meaning_changed": False,
        "requires_author_confirmation": requires_author_confirmation,
        "source": "figure_checks.py",
        "source_mode": source_mode,
        "evidence_type": "vision" if source_mode == "vision" else "text",
        "figure_refs": figure_refs or [],
    }


def _range_values(value):
    values = [float(item) for item in NUMBER_RE.findall(_as_text(value))]
    return values[:2]


def _expected_ranges(expected):
    hints = expected.get("axis_unit_range_hints") or {}
    return [_range_values(value) for value in hints.get("ranges") or [] if _range_values(value)]


def _observed_range(observation):
    for key in ("x_range", "time_range", "range", "observed_range"):
        if key in observation:
            values = _range_values(observation[key])
            if len(values) >= 2:
                return values[:2]
    return None


def _known_variable_mismatch(expected_text, observed_text):
    expected_tokens = _tokens(expected_text)
    observed_tokens = _tokens(observed_text)
    for left, right in VARIABLE_PAIRS:
        left_set = {item.upper() for item in left}
        right_set = {item.upper() for item in right}
        if expected_tokens & left_set and observed_tokens & right_set:
            return next(iter(expected_tokens & left_set)), next(iter(observed_tokens & right_set))
        if expected_tokens & right_set and observed_tokens & left_set:
            return next(iter(expected_tokens & right_set)), next(iter(observed_tokens & left_set))
    return None


def _vision_map(observations):
    result = {}
    for item in observations or []:
        if not isinstance(item, dict):
            continue
        for key in (item.get("actual_id"), item.get("figure_id"), item.get("id")):
            if key:
                result[str(key)] = item
    return result


def build_figure_review(evidence, observations=None, vision_available=None):
    if not isinstance(evidence, dict):
        raise ValueError("evidence JSON 顶层必须是对象")
    expected = evidence.get("expected_figures") or []
    actuals = evidence.get("actual_figures") or []
    matches = evidence.get("matches") or []
    actual_by_id = {item.get("id"): item for item in actuals if item.get("id")}
    expected_by_id = {item.get("expected_id"): item for item in expected if item.get("expected_id")}
    observations = observations or []
    vision = _vision_map(observations)
    if vision_available is None:
        vision_available = bool(observations)

    issues = []
    inventory = []
    match_by_expected = {item.get("expected_id"): item for item in matches}

    for item in expected:
        expected_id = item.get("expected_id")
        match = match_by_expected.get(expected_id, {})
        actual_ids = match.get("actual_ids") or []
        status = match.get("status") or "unmatched"
        actual = actual_by_id.get(actual_ids[0]) if actual_ids else None
        actual_text = _actual_text(actual, vision.get(actual_ids[0])) if actual else "未发现对应实际图项"
        inventory.append({
            "figure_id": item.get("figure_id") or expected_id,
            "location": _location(item),
            "expected": _expected_text(item),
            "actual": actual_text,
            "status": status,
            "source": (actual or {}).get("extracted_path") or _location(item),
            "location_detail": item.get("location_detail") or item.get("location_details"),
            "expected_evidence": item.get("evidence") or [],
            "actual_ids": actual_ids,
            "requires_author_confirmation": status not in {"matched", "count_satisfied"},
            "severity": "人工核对项" if status not in {"matched", "count_satisfied"} else None,
        })
        if status == "count_shortfall":
            expected_count = item.get("count_hint") or 0
            actual_count = match.get("score") or 0
            issues.append(_issue(
                f"FIG-{len(issues) + 1:03d}",
                "figure_count_shortfall",
                "严重",
                "高",
                _location(item),
                f"应有 {expected_count} 个图项",
                f"实际识别 {actual_count} 个图项",
                item.get("evidence") or [],
                "正文或图表要求的图项数量未满足。",
                "补齐缺失图项，并确认数量要求或正文描述是否有误。",
                figure_refs=[item.get("figure_id") or expected_id],
            ))
        elif status == "unmatched" and item.get("required", True):
            source_types = item.get("source_types") or []
            severity = "严重" if "caption" in source_types or "direct_reference" in source_types else "一般"
            issues.append(_issue(
                f"FIG-{len(issues) + 1:03d}",
                "figure_missing_or_unmatched",
                severity,
                "中",
                _location(item),
                _expected_text(item) or "应存在与正文/目录对应的图项",
                "未匹配到实际图项",
                item.get("evidence") or [],
                "正文、目录或图号关系表明应有图项，但当前证据未匹配到实际图。",
                "检查是否漏图、图号错误、图与正文相隔过远，或补充作者确认。",
                requires_author_confirmation=True,
                figure_refs=[item.get("figure_id") or expected_id],
            ))

    for actual in actuals:
        if actual.get("excluded"):
            status = "excluded"
        elif actual.get("matched_expected_ids"):
            status = "matched"
        else:
            status = "extra_or_unreferenced"
        inventory.append({
            "figure_id": actual.get("figure_id") or actual.get("id"),
            "location": actual.get("source_anchor") or "[?]",
            "expected": "无直接预期项" if status == "extra_or_unreferenced" else "",
            "actual": _actual_text(actual, vision.get(actual.get("id"))),
            "status": status,
            "source": actual.get("extracted_path") or actual.get("source_path"),
            "location_detail": actual.get("location_detail"),
            "actual_id": actual.get("id"),
            "excluded": actual.get("excluded", False),
            "excluded_reason": actual.get("excluded_reason"),
            "requires_author_confirmation": status == "extra_or_unreferenced",
            "severity": "人工核对项" if status == "extra_or_unreferenced" else None,
        })

    by_hash = {}
    for actual in actuals:
        if actual.get("excluded") or not actual.get("sha256"):
            continue
        by_hash.setdefault(actual["sha256"], []).append(actual)
    for sha, group in by_hash.items():
        if len(group) < 2:
            continue
        locations = [item.get("source_anchor") for item in group]
        issues.append(_issue(
            f"FIG-{len(issues) + 1:03d}",
            "figure_exact_duplicate",
            "一般",
            "高",
            "/".join(filter(None, locations)),
            "不同样品/工况应使用各自对应的图像",
            f"媒体哈希 {sha[:12]} 相同，涉及 {len(group)} 个图项",
            [{"anchor": item.get("source_anchor"), "excerpt": item.get("figure_id") or item.get("id")} for item in group],
            "多个图项使用了完全相同的媒体内容，可能存在重复粘贴。",
            "核对样品/工况编号；若确为同一结果，请在报告中说明复用原因。",
            requires_author_confirmation=True,
            figure_refs=[item.get("figure_id") or item.get("id") for item in group],
        ))

    if not vision_available:
        candidates = [item for item in actuals if not item.get("excluded")]
        if candidates:
            issues.append(_issue(
                f"FIG-{len(issues) + 1:03d}",
                "figure_vision_unavailable",
                "人工核对项",
                "人工",
                "/".join(item.get("source_anchor") or item.get("id") for item in candidates),
                "应核对图中变量、坐标轴、单位、范围、图例和趋势",
                "当前未提供可靠视觉观察结果",
                [{"anchor": item.get("source_anchor"), "path": item.get("extracted_path")} for item in candidates],
                "当前运行环境无法可靠读取图像内容，不能确认图文一致性。",
                "请人工核对图片，或切换到支持视觉的模型后重新审核。",
                source_mode="manual",
                requires_author_confirmation=True,
            ))

    for match in matches:
        actual_ids = match.get("actual_ids") or []
        if not actual_ids:
            continue
        expected_item = expected_by_id.get(match.get("expected_id"))
        actual = actual_by_id.get(actual_ids[0])
        observation = vision.get(actual_ids[0]) if actual else None
        if not expected_item or not actual or not observation:
            continue
        expected_text = _expected_text(expected_item)
        observed_text = _as_text(observation)
        mismatch = _known_variable_mismatch(expected_text, observed_text)
        location = actual.get("source_anchor") or _location(expected_item)
        refs = [expected_item.get("figure_id") or expected_item.get("expected_id"), actual.get("id")]
        if mismatch:
            issues.append(_issue(
                f"FIG-{len(issues) + 1:03d}",
                "figure_context_mismatch",
                "严重",
                _confidence(observation.get("confidence"), "中"),
                location,
                expected_text,
                observed_text,
                [
                    {"anchor": source.get("anchor"), "excerpt": source.get("excerpt")}
                    for source in expected_item.get("evidence") or []
                ] + [{"anchor": location, "observation": observation}],
                f"正文/上下文要求变量“{mismatch[0]}”，但视觉观察显示“{mismatch[1]}”。",
                "确认是否错贴图片；必要时替换为与正文变量一致的图。",
                source_mode="vision",
                requires_author_confirmation=True,
                figure_refs=refs,
            ))

        expected_units = {
            unit.upper()
            for unit in ((expected_item.get("axis_unit_range_hints") or {}).get("units") or [])
        }
        observed_units = {unit.upper() for unit in observation.get("units") or []}
        if expected_units and observed_units and not expected_units & observed_units:
            issues.append(_issue(
                f"FIG-{len(issues) + 1:03d}",
                "figure_unit_mismatch",
                "一般",
                _confidence(observation.get("confidence"), "中"),
                location,
                sorted(expected_units),
                sorted(observed_units),
                [{"anchor": location, "observation": observation}],
                "正文/图题要求的单位与图中观察到的单位不一致。",
                "核对单位、数量级和是否进行了明确换算。",
                source_mode="vision",
                requires_author_confirmation=True,
                figure_refs=refs,
            ))

        expected_ranges = _expected_ranges(expected_item)
        observed_range = _observed_range(observation)
        if expected_ranges and observed_range:
            expected_max = max(item[1] for item in expected_ranges if len(item) > 1)
            if observed_range[1] < expected_max:
                issues.append(_issue(
                    f"FIG-{len(issues) + 1:03d}",
                    "figure_data_range_mismatch",
                    "严重",
                    _confidence(observation.get("confidence"), "中"),
                    location,
                    expected_ranges,
                    observed_range,
                    [{"anchor": location, "observation": observation}],
                    "图片数据范围未覆盖正文或测试要求声明的范围。",
                    "替换为完整范围的图，或确认正文中的范围要求是否错误。",
                    source_mode="vision",
                    requires_author_confirmation=True,
                    figure_refs=refs,
                ))

        expected_text_lower = expected_text.lower()
        trend = _as_text(observation.get("trend")).lower()
        trend_pairs = (
            (("下降", "降低", "decrease", "declin", "fall"), ("上升", "增加", "increase", "ris", "grow")),
            (("上升", "增加", "increase", "ris", "grow"), ("下降", "降低", "decrease", "declin", "fall")),
        )
        for expected_words, opposite_words in trend_pairs:
            if any(word in expected_text_lower for word in expected_words) and any(word in trend for word in opposite_words):
                issues.append(_issue(
                    f"FIG-{len(issues) + 1:03d}",
                    "figure_trend_conflict",
                    "一般",
                    _confidence(observation.get("confidence"), "中"),
                    location,
                    expected_text,
                    observation.get("trend"),
                    [{"anchor": location, "observation": observation}],
                    "正文趋势描述与图中观察到的趋势可能不一致。",
                    "复核原始数据、坐标缩放和正文结论；不要仅凭低分辨率图像下定论。",
                    source_mode="vision",
                    requires_author_confirmation=True,
                    figure_refs=refs,
                ))
                break

    _attach_location_details(issues, evidence)
    _attach_location_details(inventory, evidence)

    return {
        "schema_version": SCHEMA_VERSION,
        "vision_mode": "vision" if vision_available else "no-vision",
        "issues": issues,
        "figure_inventory": inventory,
        "observations": observations,
        "expected_figures": expected,
        "actual_figures": actuals,
        "matches": matches,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="对 evidence.v1 执行图表完整性检查，并可合并模型视觉观察结果。"
    )
    parser.add_argument("evidence", help="evidence_pipeline.py 生成的 JSON")
    parser.add_argument("-o", "--output", help="输出 figure-review.json")
    parser.add_argument(
        "--vision-observations",
        help="模型视觉观察 JSON（列表或包含 observations 列表的对象）",
    )
    parser.add_argument(
        "--no-vision",
        action="store_true",
        help="明确声明当前没有可靠视觉能力，生成人工核对项",
    )
    args = parser.parse_args(argv)

    evidence_path = os.path.abspath(args.evidence)
    output_path = os.path.abspath(
        args.output
        or os.path.join(os.path.dirname(evidence_path), "figure-review.json")
    )
    with open(evidence_path, encoding="utf-8-sig") as handle:
        evidence = json.load(handle)
    observations = []
    if args.vision_observations:
        with open(args.vision_observations, encoding="utf-8-sig") as handle:
            raw = json.load(handle)
        observations = raw.get("observations", []) if isinstance(raw, dict) else raw
        if not isinstance(observations, list):
            raise SystemExit("错误：视觉观察 JSON 必须是列表或包含 observations 列表的对象。")
    result = build_figure_review(
        evidence,
        observations=observations,
        vision_available=False if args.no_vision else bool(observations),
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"OK: {output_path} (issues={len(result['issues'])}, figures={len(result['figure_inventory'])})")


if __name__ == "__main__":
    main()
