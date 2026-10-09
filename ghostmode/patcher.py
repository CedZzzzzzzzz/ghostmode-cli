from __future__ import annotations

import ast
import difflib
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import PatchError
from .parser import FunctionSpan


def validate_python(code: str) -> bool:
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def validate_code(code: str, suffix: str) -> bool:
    if suffix.lower() == ".py":
        return validate_python(code)
    normalized_suffix = suffix.lower()
    if not code.strip() or normalized_suffix not in {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".php"}:
        return False
    declarations = {
        ".php": r"\bfunction\s+[A-Za-z_]\w*",
    }
    declaration = declarations.get(normalized_suffix, r"\bfunction\s+[A-Za-z_$][\w$]*|=>")
    if re.search(declaration, code) is None:
        return False
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    for character in code:
        if character in pairs:
            stack.append(pairs[character])
        elif character in pairs.values() and (not stack or stack.pop() != character):
            return False
    return not stack


def matches_target_function(code: str, function_name: str, suffix: str) -> bool:
    if suffix.lower() == ".py":
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        functions = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        return functions == [function_name]
    if suffix.lower() == ".php":
        names = re.findall(r"\bfunction\s+([A-Za-z_]\w*)", code)
    else:
        names = re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", code)
        flattened_names = [first or second for first, second in names]
        return flattened_names == [function_name]
    return names == [function_name]


def extract_clean_code(llm_output: str) -> str:
    blocks: list[str] = re.findall(r"```[^\n]*\n(.*?)```", llm_output, re.DOTALL)
    candidates = blocks or [llm_output]
    for candidate in candidates:
        cleaned = candidate.strip()
        if cleaned and not cleaned.lower().startswith(("here is", "the fixed", "fixed code")):
            return cleaned + "\n"
    return candidates[0].strip() + "\n"


class FilePatcher:
    def backup(self, file_path: Path) -> Path:
        backup = file_path.with_name(file_path.name + ".ghost_bak")
        if backup.exists():
            raise PatchError(f"Backup already exists: {backup}")
        backup.write_bytes(file_path.read_bytes())
        return backup

    def apply_fix(self, file_path: Path, new_code: str) -> None:
        if not validate_code(new_code, file_path.suffix):
            raise PatchError("Refusing to write structurally invalid code")
        mode = stat.S_IMODE(file_path.stat().st_mode)
        newline = "\r\n" if b"\r\n" in file_path.read_bytes() else "\n"
        payload = new_code.replace("\r\n", "\n").replace("\n", newline).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=".ghostmode-", dir=file_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
            os.chmod(temporary, mode)
            os.replace(temporary, file_path)
        except OSError as error:
            Path(temporary).unlink(missing_ok=True)
            raise PatchError(f"Could not write {file_path}: {error}") from error

    def apply_function_patch(self, file_path: Path, span: FunctionSpan, new_function_src: str) -> None:
        if not validate_code(new_function_src, file_path.suffix):
            raise PatchError("Generated function is structurally invalid")
        original = file_path.read_text(encoding="utf-8", errors="replace")
        lines = original.splitlines(keepends=True)
        indent = lines[span.start_line - 1][:len(lines[span.start_line - 1]) - len(lines[span.start_line - 1].lstrip())]
        replacement = "\n".join(indent + line if line else line for line in new_function_src.strip().splitlines()) + "\n"
        patched = "".join(lines[:span.start_line - 1]) + replacement + "".join(lines[span.end_line:])
        self.apply_fix(file_path, patched)

    def rollback(self, file_path: Path) -> None:
        backup = file_path.with_name(file_path.name + ".ghost_bak")
        if backup.exists():
            os.replace(backup, file_path)

    def cleanup(self, file_path: Path) -> None:
        file_path.with_name(file_path.name + ".ghost_bak").unlink(missing_ok=True)

    def diff(self, original: str, patched: str) -> str:
        return "".join(difflib.unified_diff(original.splitlines(True), patched.splitlines(True), fromfile="before", tofile="after"))

    @contextmanager
    def session(self, file_path: Path) -> Iterator[None]:
        self.backup(file_path)
        try:
            yield
        except BaseException:
            self.rollback(file_path)
            raise
