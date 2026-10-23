#!/usr/bin/env python3
"""Offline, values-free source map. Does not import or call any provider.

The input stays outside Git. Output has section/line references and categories,
never original names, paragraphs, birthdays or third-party details. Counts are
source review units, NOT confirmed atomic memory facts.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re


def preview(source: str):
    section = subsection = ""
    units = []
    for number, raw in enumerate(source.splitlines(), 1):
        line = raw.strip()
        match = re.match(r"## ([一二三四五六七八九十]+)、", line)
        if match:
            section, subsection = match.group(1), ""
            continue
        sub = re.match(r"### (\d+\.\d+)", line)
        if sub:
            subsection = sub.group(1)
            continue
        if not section or not line or line.startswith(("#", "---", ">", "**")):
            continue
        if line.startswith("|") and ("---" in line or any(h in line for h in ("| 项目 |", "| 事项 |", "| 游戏 |", "| 标识 |"))):
            continue
        status, layer, meaning = "PENDING_REVIEW", "L2", "self_report"
        if section in {"十五", "十六", "十七", "十八"}:
            layer = "EXCLUDED"
            status = {"十五": "DEFERRED", "十六": "UNANSWERED", "十七": "UNCONFIRMED_ANALYSIS", "十八": "DOCUMENT_BOUNDARY"}[section]
        elif section in {"十", "十四"}:
            layer, status = "EXCLUDED", "DESIGN_OR_CONSENT_NOT_DATA"
        elif section in {"一", "二", "九"}:
            layer = "L1" if subsection in {"1.1", "1.2", "2.2", "2.7", "9.1", "9.2", "9.4", "9.5"} else "L2"
        if layer != "EXCLUDED" and (section == "十三" or subsection in {"1.4", "3.4", "4.2", "5.1", "7.7"}):
            layer = "L3"
        if section == "十二" or subsection == "7.7":
            meaning = "wish_or_tentative_plan_not_commitment"
        if section == "二":
            meaning = "self_description_not_diagnosis"
        if section in {"六", "七"}:
            sensitivity = "third_party_or_relationship"
        elif subsection == "1.1":
            sensitivity = "identity_fields_require_individual_review"
        else:
            sensitivity = "personal"
        units.append({"unit_id": f"line-{number}", "source_section": section,
                      "source_subsection": subsection, "source_line": number,
                      "source_line_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                      "layer": layer, "confidence": status, "meaning": meaning,
                      "sensitivity": sensitivity, "temporal": "as_of_source_date_requires_review" if layer == "L3" else "review_before_import",
                      "shared_experience": False, "production_import_allowed": False,
                      "deepseek_use_allowed": False, "public_search_allowed": False})
    return {"schema_version": 1, "mode": "PREVIEW_ONLY", "import_status": "PENDING_APPROVAL",
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "counts_are": "source_review_units_not_confirmed_memory_facts",
            "counts": dict(Counter(u["layer"] for u in units)),
            "review_status_counts": dict(Counter(u["confidence"] for u in units)),
            "shared_experience_count": 0, "model_calls": 0, "production_writes": 0,
            "target": "MemoryCompanion 2.3.1 native memory API via reviewed batch adapter",
            "conflicts": ["L1 may contain identifiers requiring per-field review", "L3 duplicated in background sections requires semantic deduplication", "No consent inherited from interview preferences"],
            "rollback": "batch source references; native withdrawal, retain audit; never delete raw archive implicitly",
            "synthetic_example": {"content": "合成用户偏好简短回复", "layer": "L1", "confidence": "APPROVED_SELF_REPORT", "source_ref": "reviewed-batch:example:1", "deepseek_use_allowed": False},
            "units": units}


def write_private(path: Path, report):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("symlink_output_refused")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = preview(args.source.read_text(encoding="utf-8-sig"))
    write_private(args.output, report)
    print(json.dumps({k: report[k] for k in ("mode", "import_status", "counts", "model_calls", "production_writes")}, ensure_ascii=False))
