from __future__ import annotations

from .parser import TraceInfo

SYSTEM_PROMPT = "You are an air-gapped automated code repair agent. Output only the exact replacement requested by the user prompt. When asked for a function, output only that complete function. Do not include markdown, explanations, imports, or unrelated code."
FEW_SHOT = "Buggy: def add(a, b):\n    return a - b\nFixed: def add(a, b):\n    return a + b"


def build_prompt(trace: TraceInfo, function: str, log: str, previous: list[str] | None = None) -> str:
    attempts = "\n".join(f"Previous patch failed: {item[:300]}" for item in (previous or []))
    return (
        f"{FEW_SHOT}\n\nError: {trace.error_type}: {trace.error_message}\n"
        f"Failing line: {trace.line_number}\n\nFunction to replace:\n{function}\n"
        f"\nTest output:\n{log}\n{attempts}\nReturn only the corrected function."
    )


def build_inspection_prompt(finding: str, impact: str, suggestion: str, function: str) -> str:
    return (
        f"Inspection finding: {finding}\nPotential impact: {impact}\nSuggested review: {suggestion}\n"
        f"\nFunction to replace:\n{function}\n\n"
        "Return exactly one complete replacement for this function. Do not include imports, "
        "other functions, module-level code, markdown, or explanations. The surrounding file "
        "already keeps its imports."
    )


def build_regression_test_prompt(
    finding: str,
    impact: str,
    suggestion: str,
    source_path: str,
    test_path: str,
    framework: str,
    function: str,
    test_example: str,
) -> str:
    return (
        f"Inspection finding: {finding}\nPotential impact: {impact}\nSuggested review: {suggestion}\n"
        f"Source file: {source_path}\nNew test file: {test_path}\nTest framework: {framework}\n"
        f"\nFunction under test:\n{function}\n\n"
        "Create one complete regression test file. It must fail with the shown buggy function and "
        "pass only after the intended behavior is fixed. Use the stated framework. Return only test code, "
        "without markdown or explanations. Import and reuse existing application code and test fixtures; "
        "do not redefine application models, routes, or source functions.\n\n"
        f"Existing test example to follow:\n{test_example}"
    )
