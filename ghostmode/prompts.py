from __future__ import annotations

from .parser import TraceInfo

SYSTEM_PROMPT = "You are an air-gapped automated code repair agent. Given the broken code context and error log, output ONLY the complete corrected replacement file or code block. Do NOT include markdown explanations, chit-chat, or comments outside the code."
FEW_SHOT = "Buggy: def add(a, b):\n    return a - b\nFixed: def add(a, b):\n    return a + b"


def build_prompt(trace: TraceInfo, function: str, log: str, previous: list[str] | None = None) -> str:
    attempts = "\n".join(f"Previous patch failed: {item[:300]}" for item in (previous or []))
    return (
        f"{FEW_SHOT}\n\nError: {trace.error_type}: {trace.error_message}\n"
        f"Failing line: {trace.line_number}\n\nFunction to replace:\n{function}\n"
        f"\nTest output:\n{log}\n{attempts}\nReturn only the corrected function."
    )
