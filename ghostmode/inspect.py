from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SOURCE_SUFFIXES = {".js", ".jsx", ".php", ".py", ".ts", ".tsx"}
SKIPPED_DIRECTORIES = {
    ".git", ".ghostmode", ".next", ".pytest-agent", ".pytest-run", ".venv", "dist", "node_modules", "tests", "venv",
}
MAX_FILES = 1_000
MAX_SOURCE_BYTES = 200_000


@dataclass(frozen=True)
class Finding:
    file: Path
    line: int
    severity: str
    priority: str
    message: str
    impact: str
    suggestion: str


def inspect_project(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    backend_fields: dict[str, tuple[Path, int]] = {}
    frontend_fields: list[tuple[str, str, Path, int]] = []
    files_checked = 0
    for directory, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in SKIPPED_DIRECTORIES]
        for name in files:
            path = Path(directory, name)
            if path.suffix not in SOURCE_SUFFIXES or files_checked >= MAX_FILES:
                continue
            try:
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    continue
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            findings.extend(inspect_source(path, source))
            if path.suffix == ".py":
                backend_fields.update(response_fields(path, source))
            if path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
                frontend_fields.extend(property_fields(path, source))
            files_checked += 1
    findings.extend(field_mismatches(backend_fields, frontend_fields))
    return findings


def inspect_source(path: Path, source: str) -> list[Finding]:
    lines = source.splitlines()
    findings: list[Finding] = []
    if path.suffix == ".py":
        findings.extend(inspect_python(path, lines, source))
    if path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        findings.extend(inspect_javascript(path, lines))
    return findings


def inspect_python(path: Path, lines: list[str], source: str) -> list[Finding]:
    findings: list[Finding] = []
    optional_values = optional_date_values(lines)
    for index, line in enumerate(lines, start=1):
        if re.search(r"\b(?:date|datetime)\.fromisoformat\(", line) and ".get(" in line:
            findings.append(Finding(
                path,
                index,
                "high",
                "P1",
                "Optional request input is passed directly to fromisoformat().",
                "A missing value can crash an API request instead of returning a client error.",
                "Validate the value before parsing it and return a clear client error when it is missing.",
            ))
        parsed = re.search(r"\b(?:date|datetime)\.fromisoformat\(\s*([A-Za-z_]\w*)\s*\)", line)
        if parsed is not None and parsed.group(1) in optional_values:
            findings.append(Finding(
                path,
                index,
                "high",
                "P1",
                "A possibly optional date value is parsed without a visible guard.",
                "A missing value can crash an API request instead of returning a client error.",
                "Check for a value before parsing it and return a clear client error when it is missing.",
            ))
        if re.search(r"\b(?:bounce|bounced)\w*\s*/\s*[\w.]*page_?views?\b", line, re.IGNORECASE):
            findings.append(Finding(
                path,
                index,
                "medium",
                "P2",
                "A bounce metric is divided by page views.",
                "The reported rate can be wrong when the intended denominator is sessions or visitors.",
                "Confirm the metric definition and denominator with a regression test.",
            ))
    if ".join(" in source and "func.count(" in source and ".distinct(" not in source:
        count_line = next(index for index, value in enumerate(lines, start=1) if "func.count(" in value)
        findings.append(Finding(
            path,
            count_line,
            "medium",
            "P2",
            "A joined query counts rows without distinct().",
            "Related rows can inflate analytics or billing totals.",
            "Check whether joined records can duplicate the entity being counted.",
        ))
    return findings


def inspect_javascript(path: Path, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for index, line in enumerate(lines, start=1):
        if "fetch" in line and ".current" in line:
            findings.append(Finding(
                path,
                index,
                "medium",
                "P2",
                "A request uses a mutable ref value.",
                "The interface can show data for a previous selection instead of the current one.",
                "Check that the request uses current UI state rather than a stale previous value.",
            ))
        if "setLoading(true)" in line:
            nearby = "\n".join(lines[index - 1:index + 30])
            if re.search(r"\bfetch\w*\(", nearby) and ".catch(" not in nearby and "try" not in nearby:
                findings.append(Finding(
                    path,
                    index,
                    "high",
                    "P1",
                    "Loading is enabled around a request without visible error handling.",
                    "A rejected request can leave the interface stuck in a loading state.",
                    "Handle rejected requests and always clear the loading state.",
                ))
    calls: dict[str, list[int]] = {}
    for start, end in effect_ranges(lines):
        effect = "\n".join(lines[start:end + 1])
        if "fetch" in effect and re.search(r"},\s*\[\s*\]\s*\)", effect):
            request_line = next(
                (position for position in range(start, end + 1) if "fetch" in lines[position]),
                start,
            )
            findings.append(Finding(
                path,
                request_line + 1,
                "medium",
                "P2",
                "An effect fetches data but has an empty dependency list.",
                "The interface can display stale data after state or prop changes.",
                "Check whether changing state or props should refresh this request.",
            ))
        for offset, effect_line in enumerate(lines[start:end + 1], start=start):
            for match in re.finditer(r"\b(fetch[A-Z][A-Za-z0-9_]*)\s*\(", effect_line):
                calls.setdefault(match.group(1), []).append(offset + 1)
    for name, call_lines in calls.items():
        if len(call_lines) < 2:
            continue
        findings.append(Finding(
            path,
            call_lines[1],
            "medium",
            "P2",
            f"{name}() is called from multiple effects.",
            "The same request can run more than once during a page load or state change.",
            "Confirm the intended effect ownership and add a request-count regression test.",
        ))
    return findings


def optional_date_values(lines: list[str]) -> set[str]:
    values: set[str] = set()
    for line in lines:
        typed = re.finditer(r"\b([A-Za-z_]\w*)\s*:\s*(?:str\s*\|\s*None|Optional\[str\])", line)
        values.update(match.group(1) for match in typed)
        assigned = re.search(r"\b([A-Za-z_]\w*)\s*=.*\.get\(", line)
        if assigned is not None:
            values.add(assigned.group(1))
    return values


def effect_ranges(lines: list[str]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start, line in enumerate(lines):
        if "useEffect(" not in line:
            continue
        end = next(
            (index for index in range(start, min(start + 30, len(lines))) if re.search(r"},\s*\[", lines[index])),
            min(start + 29, len(lines) - 1),
        )
        ranges.append((start, end))
    return ranges


def response_fields(path: Path, source: str) -> dict[str, tuple[Path, int]]:
    fields: dict[str, tuple[Path, int]] = {}
    for index, line in enumerate(source.splitlines(), start=1):
        for match in re.finditer(r"['\"]([a-z][a-z0-9]*_[a-z0-9_]+)['\"]\s*:", line):
            fields[match.group(1)] = (path, index)
    return fields


def property_fields(path: Path, source: str) -> list[tuple[str, str, Path, int]]:
    fields: list[tuple[str, str, Path, int]] = []
    for index, line in enumerate(source.splitlines(), start=1):
        for match in re.finditer(r"\b([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)", line):
            fields.append((match.group(1), match.group(2), path, index))
    return fields


def field_mismatches(
    backend_fields: dict[str, tuple[Path, int]], frontend_fields: list[tuple[str, str, Path, int]],
) -> list[Finding]:
    findings: list[Finding] = []
    for variable, property_name, path, line in frontend_fields:
        field_name = f"{variable}_{property_name}"
        if field_name not in backend_fields:
            continue
        findings.append(Finding(
            path,
            line,
            "medium",
            "P2",
            f"Frontend reads {variable}.{property_name} while the backend exposes {field_name}.",
            "Users can see empty or missing data when the response shape does not match the UI.",
            "Align the API response field and frontend property, then add an integration regression test.",
        ))
    return findings
