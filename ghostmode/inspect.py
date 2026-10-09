from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SOURCE_SUFFIXES = {".js", ".jsx", ".py", ".ts", ".tsx"}
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
            files_checked += 1
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
            if "fetch(" in nearby and ".catch(" not in nearby and "try" not in nearby:
                findings.append(Finding(
                    path,
                    index,
                    "high",
                    "P1",
                    "Loading is enabled around a request without visible error handling.",
                    "A rejected request can leave the interface stuck in a loading state.",
                    "Handle rejected requests and always clear the loading state.",
                ))
    for index, line in enumerate(lines):
        if "useEffect(" not in line:
            continue
        effect = "\n".join(lines[index:index + 30])
        if "fetch" in effect and re.search(r"},\s*\[\s*\]\s*\)", effect):
            request_line = next(
                (position for position in range(index, min(index + 30, len(lines))) if "fetch" in lines[position]),
                index,
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
    return findings
