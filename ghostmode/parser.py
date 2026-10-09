from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript/React",
    ".ts": "TypeScript",
    ".tsx": "TypeScript/React",
    ".php": "PHP",
}


@dataclass(frozen=True)
class TraceInfo:
    failed_file: str
    line_number: int
    error_type: str
    error_message: str
    function_name: str | None
    is_test_file: bool


@dataclass(frozen=True)
class FunctionSpan:
    source: str
    start_line: int
    end_line: int
    name: str


def is_test_file(path: str) -> bool:
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def is_external_frame(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return "site-packages" in normalized or "/lib/python" in normalized or bool(re.search(r"/python\d+(?:\.\d+)?/lib/", normalized))


def language_for_path(file_path: str | Path) -> str | None:
    return SUPPORTED_SUFFIXES.get(Path(file_path).suffix.lower())


def parse_trace(test_output: str) -> TraceInfo | None:
    frames = re.findall(r'File "([^"]+)", line (\d+), in ([^\n]+)', test_output)
    frames += [(path, line, "") for path, line in re.findall(r'File "([^"]+)", line (\d+)', test_output)]
    frames += [(path, line, "") for path, line in re.findall(r"([\w./\\-]+\.py):(\d+):", test_output)]
    suffixes = "|".join(re.escape(item) for item in SUPPORTED_SUFFIXES)
    pattern = rf"([^\s()]+(?:{suffixes})):(\d+)(?::\d+)?"
    frames += [(path, line, "") for path, line in re.findall(pattern, test_output, re.IGNORECASE)]
    frames += [(path, line, "") for path, line in re.findall(r"in\s+([^\s]+\.php)\s+on line\s+(\d+)", test_output, re.IGNORECASE)]
    usable = [
        frame for frame in frames
        if language_for_path(frame[0]) is not None and not is_external_frame(frame[0])
    ]
    if not usable:
        return None
    chosen = next((frame for frame in reversed(usable) if not is_test_file(frame[0])), usable[-1])
    errors = re.findall(r"(?:E\s+)?([A-Za-z_][\w.]*(?:Error|Exception|Failure|Exit))(?::\s*(.*))?", test_output)
    error_type, message = errors[-1] if errors else ("TestFailure", "")
    if is_test_file(chosen[0]):
        resolved_target = resolve_test_target(Path(chosen[0]), int(chosen[1]))
        if resolved_target is not None:
            file_path, target_line, function_name = resolved_target
            return TraceInfo(file_path, target_line, error_type, message.strip(), function_name, False)
    return TraceInfo(chosen[0], int(chosen[1]), error_type, message.strip(), chosen[2] or None, is_test_file(chosen[0]))


def extract_code_context(file_path: str, line_number: int, window: int = 25) -> str:
    try:
        lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start, end = max(0, line_number - window - 1), min(len(lines), line_number + window)
    return "\n".join(f"{index + 1:>4}: {lines[index]}" for index in range(start, end))


def extract_enclosing_function(file_path: str, line_number: int) -> FunctionSpan | None:
    if Path(file_path).suffix.lower() != ".py":
        return extract_braced_function(file_path, line_number)
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except OSError:
        return None
    except SyntaxError:
        return extract_indented_python_function(source, line_number)
    matches: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.end_lineno is not None
            and node.lineno <= line_number <= node.end_lineno
        ):
            matches.append(node)
    if not matches:
        return None
    node = max(matches, key=lambda item: item.lineno)
    if node.end_lineno is None:
        return None
    lines = source.splitlines(keepends=True)
    return FunctionSpan("".join(lines[node.lineno - 1:node.end_lineno]), node.lineno, node.end_lineno, node.name)


def extract_indented_python_function(source: str, line_number: int) -> FunctionSpan | None:
    lines = source.splitlines(keepends=True)
    declaration = re.compile(r"^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
    start = -1
    name = ""
    indent = 0
    for index in range(min(line_number - 1, len(lines) - 1), -1, -1):
        match = declaration.match(lines[index])
        if match is not None:
            start, name, indent = index, match.group(2), len(match.group(1))
            break
    if start < 0:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].strip() and len(lines[index]) - len(lines[index].lstrip()) <= indent:
            end = index
            break
    return FunctionSpan("".join(lines[start:end]), start + 1, end, name)


def resolve_test_target(test_file: Path, line_number: int) -> tuple[str, int, str] | None:
    try:
        lines = test_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if line_number < 1 or line_number > len(lines):
        return None
    call = re.search(r"\b([A-Za-z_]\w*)\s*\(", lines[line_number - 1])
    if call is None:
        return None
    function_name = call.group(1)
    for line in lines:
        imported = re.match(r"\s*from\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+import\s+(.+)", line)
        if imported is None:
            continue
        names = {name.strip().split(" as ")[0] for name in imported.group(2).split(",")}
        if function_name not in names:
            continue
        module_file = test_file.parent.joinpath(*imported.group(1).split(".")).with_suffix(".py")
        span = find_function_by_name(module_file, function_name)
        if span is not None:
            return str(module_file), span.start_line, function_name
    return None


def find_function_by_name(file_path: Path, function_name: str) -> FunctionSpan | None:
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name and node.end_lineno is not None:
            lines = source.splitlines(keepends=True)
            return FunctionSpan("".join(lines[node.lineno - 1:node.end_lineno]), node.lineno, node.end_lineno, node.name)
    return None


def extract_braced_function(file_path: str, line_number: int) -> FunctionSpan | None:
    try:
        lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return None
    suffix = Path(file_path).suffix.lower()
    if suffix == ".php":
        declaration = re.compile(r"\bfunction\s+([A-Za-z_]\w*)")
    else:
        declaration = re.compile(r"(?:\bfunction\s+|\b(?:const|let|var)\s+)([A-Za-z_$][\w$]*)")
    start = -1
    name = ""
    for index in range(min(line_number - 1, len(lines) - 1), -1, -1):
        match = declaration.search(lines[index])
        if match:
            start, name = index, match.group(1)
            break
    if start < 0:
        return None
    depth = 0
    opened = False
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        opened = opened or "{" in lines[index]
        if opened and depth == 0:
            if start <= line_number - 1 <= index:
                return FunctionSpan("".join(lines[start:index + 1]), start + 1, index + 1, name)
            return None
    return None


def truncate_log(output: str, lines: int = 30, characters: int = 2000) -> str:
    return "\n".join(output.splitlines()[-lines:])[-characters:]
