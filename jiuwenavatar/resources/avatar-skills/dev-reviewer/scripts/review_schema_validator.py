#!/usr/bin/env python3
"""Validate review/result.json before report rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_ENUMS_PATH = _ASSETS / "review_frontmatter_enums.json"

FINDING_BUCKETS = ("must_fix", "should_fix", "nice_to_have")
REQUIRED_TOP_LEVEL = (
    "schema_version",
    "verdict",
    "gate_verdict",
    "verdict_reason",
    "layer_alignment",
    "patch_risk",
    "risk_rating",
    "summary",
    "pass_fail_reasons",
    "findings",
    "security_review",
    "reviewer",
)
REQUIRED_FINDING_FIELDS = ("severity", "category", "location", "issue", "risk", "recommendation")
REQUIRED_SUMMARY_FIELDS = ("change_intent", "scope")
REQUIRED_SECURITY_ITEM_FIELDS = ("category", "status", "evidence")
DRAFT_REVIEWER_VALUES = {"unfilled", "", "draft"}
DRAFT_VERDICT_REASONS = (
    "review has not been completed yet.",
    "initial draft only.",
)


def _load_enums() -> dict[str, list[str]]:
    if not _ENUMS_PATH.is_file():
        return {}
    try:
        payload = json.loads(_ENUMS_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _is_draft_review(review: dict[str, Any]) -> bool:
    reviewer = _norm(review.get("reviewer")).lower()
    if reviewer in DRAFT_REVIEWER_VALUES:
        return True
    reason = _norm(review.get("verdict_reason")).lower()
    return any(marker in reason for marker in DRAFT_VERDICT_REASONS)


def validate_review_result(review: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors; empty list means OK."""
    errors: list[str] = []
    if not review:
        return ["result.json is empty or unreadable."]

    enums = _load_enums()
    enum_verdict = set(enums.get("verdict") or ["PASS", "FAIL"])
    enum_gate = set(enums.get("gate_verdict") or ["PASS", "REWORK", "HOLD"])
    enum_risk = set(enums.get("risk_rating") or ["Low", "Medium", "High", "Unknown"])
    enum_layer = set(enums.get("layer_alignment") or ["PASS", "FAIL"])
    enum_patch = set(enums.get("patch_risk") or ["none", "suspected", "confirmed"])
    enum_severity = set(enums.get("severity") or ["critical", "high", "medium", "low"])
    enum_dimension = set(enums.get("dimension") or [])
    enum_category = set(enums.get("category") or [])
    enum_sec_status = set(enums.get("security_review_status") or ["PASS", "FAIL", "not_applicable"])
    enum_sec_item_cat = set(enums.get("security_item_category") or [])
    enum_sec_item_status = set(enums.get("security_item_status") or ["PASS", "FAIL"])

    for field in REQUIRED_TOP_LEVEL:
        if field not in review:
            errors.append(f"Missing required field: {field}")

    if _is_draft_review(review):
        errors.append("Review is still a draft (reviewer unfilled or verdict_reason indicates incomplete review).")

    verdict = _norm(review.get("verdict")).upper()
    if verdict and verdict not in enum_verdict:
        errors.append(f"Invalid verdict: {verdict!r}")

    gate = _norm(review.get("gate_verdict")).upper()
    if gate and gate not in enum_gate:
        errors.append(f"Invalid gate_verdict: {gate!r}")

    risk = _norm(review.get("risk_rating"))
    if risk and risk not in enum_risk:
        errors.append(f"Invalid risk_rating: {risk!r}")

    layer = _norm(review.get("layer_alignment")).upper()
    if not layer:
        errors.append("layer_alignment is required.")
    elif layer not in enum_layer:
        errors.append(f"Invalid layer_alignment: {layer!r}")

    patch = _norm(review.get("patch_risk")).lower()
    if not patch:
        errors.append("patch_risk is required.")
    elif patch not in enum_patch:
        errors.append(f"Invalid patch_risk: {patch!r}")

    summary = review.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be an object.")
    else:
        for field in REQUIRED_SUMMARY_FIELDS:
            if not _norm(summary.get(field)):
                errors.append(f"summary.{field} is required and must be non-empty.")

    findings = review.get("findings")
    if not isinstance(findings, dict):
        errors.append("findings must be an object.")
    else:
        for bucket in FINDING_BUCKETS:
            items = findings.get(bucket) or []
            if not isinstance(items, list):
                errors.append(f"findings.{bucket} must be a list.")
                continue
            for idx, finding in enumerate(items, start=1):
                prefix = f"findings.{bucket}[{idx}]"
                if not isinstance(finding, dict):
                    errors.append(f"{prefix} must be an object.")
                    continue
                for field in REQUIRED_FINDING_FIELDS:
                    if not _norm(finding.get(field)):
                        errors.append(f"{prefix}.{field} is required.")
                severity = _norm(finding.get("severity")).lower()
                if severity and severity not in enum_severity:
                    errors.append(f"{prefix}.severity invalid: {severity!r}")
                category = _norm(finding.get("category")).lower()
                if category and enum_category and category not in enum_category:
                    errors.append(f"{prefix}.category invalid: {category!r}")
                dimension = _norm(finding.get("dimension"))
                if dimension and enum_dimension and dimension not in enum_dimension:
                    errors.append(f"{prefix}.dimension invalid: {dimension!r}")

    security = review.get("security_review")
    if not isinstance(security, dict):
        errors.append("security_review must be an object.")
    else:
        status = _norm(security.get("status"))
        if status and status not in enum_sec_status:
            errors.append(f"security_review.status invalid: {status!r}")
        items = security.get("items") or []
        if not isinstance(items, list):
            errors.append("security_review.items must be a list.")
        else:
            for idx, item in enumerate(items, start=1):
                prefix = f"security_review.items[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{prefix} must be an object.")
                    continue
                for field in REQUIRED_SECURITY_ITEM_FIELDS:
                    if not _norm(item.get(field)):
                        errors.append(f"{prefix}.{field} is required.")
                cat = _norm(item.get("category")).lower()
                if cat and enum_sec_item_cat and cat not in enum_sec_item_cat:
                    errors.append(f"{prefix}.category invalid: {cat!r}")
                item_status = _norm(item.get("status")).upper()
                if item_status and item_status not in enum_sec_item_status:
                    errors.append(f"{prefix}.status invalid: {item_status!r}")

    pass_fail = review.get("pass_fail_reasons")
    if not isinstance(pass_fail, list) or not pass_fail:
        errors.append("pass_fail_reasons must be a non-empty list.")

    return errors
