from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any

from tomography_session_browser.domain.models import MdocSection
from tomography_session_browser.services.loading_profiler import record_aggregate_phase


SECTION_RE = re.compile(r"^\[ZValue\s*=\s*(\d+)\]\s*$")


def parse_mdoc(path: Path) -> tuple[dict[str, Any], list[MdocSection], list[str]]:
    started = time.perf_counter()
    warnings: list[str] = []
    header: dict[str, Any] = {}
    sections: list[MdocSection] = []
    current: MdocSection | None = None

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        record_aggregate_phase("parse_mdoc", time.perf_counter() - started)
        return {}, [], [f"Could not read MDOC file: {exc}"]

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        match = SECTION_RE.match(line)
        if match:
            current = MdocSection(z_value=int(match.group(1)))
            sections.append(current)
            continue

        if line.startswith("[") and line.endswith("]"):
            header.setdefault("_sections", []).append(line)
            for key, raw_value in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*([^\]]*?)(?=\s+[A-Za-z][A-Za-z0-9_]*\s*=|\s*\]$)", line):
                parsed, value_warnings = parse_mdoc_value(raw_value.strip())
                for warning in value_warnings:
                    warnings.append(f"{path.name}:{line_number} {key}: {warning}")
                header.setdefault(key, parsed)
            continue

        if "=" not in line:
            warnings.append(f"{path.name}:{line_number} unrecognised MDOC line ignored: {line}")
            continue

        key, value = [part.strip() for part in line.split("=", 1)]
        parsed, value_warnings = parse_mdoc_value(value)
        for warning in value_warnings:
            message = f"{path.name}:{line_number} {key}: {warning}"
            if current is None:
                warnings.append(message)
            else:
                current.warnings.append(message)

        target = header if current is None else current.metadata
        target[key] = parsed

    record_aggregate_phase("parse_mdoc", time.perf_counter() - started)
    return header, sections, warnings


def parse_mdoc_value(value: str) -> tuple[Any, list[str]]:
    warnings: list[str] = []
    parts = value.split()
    if not parts:
        return "", warnings

    parsed_parts = []
    converted_any = False
    for part in parts:
        if part.lower() == "nan":
            warnings.append("NaN value omitted from numeric summaries.")
            parsed_parts.append(None)
            converted_any = True
            continue
        try:
            number = float(part)
        except ValueError:
            parsed_parts.append(part)
            continue
        if math.isfinite(number):
            converted_any = True
            parsed_parts.append(int(number) if number.is_integer() else number)
        else:
            warnings.append("Non-finite value omitted from numeric summaries.")
            parsed_parts.append(None)
            converted_any = True

    if len(parsed_parts) == 1:
        return parsed_parts[0], warnings
    if converted_any:
        return parsed_parts, warnings
    return value, warnings


def numeric_values(sections: list[MdocSection], key: str) -> list[float]:
    values: list[float] = []
    for section in sections:
        value = section.metadata.get(key)
        if isinstance(value, int | float):
            values.append(float(value))
    return values
