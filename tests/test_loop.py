from pathlib import Path

import httpx

from ghostmode.cli import propose_inspection_fix, repair_loop
from ghostmode.config import Config
from ghostmode.guards import TestResult as CommandResult
from ghostmode.inspect import Finding
from ghostmode.llm import OllamaClient


def test_dry_run_does_not_edit_source(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("def broken():\n    return 0\n")
    test = tmp_path / "test_app.py"
    test.write_text("from app import broken\nassert broken() == 1\n")
    original = source.read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:1.5b"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"response": "def broken():\n    return 1"})
        return httpx.Response(200)

    client = OllamaClient(Config(), httpx.Client(transport=httpx.MockTransport(handler)))
    command = f'python -c "raise Exception(\'File \\\"{source}\\\", line 2, in broken\\nAssertionError: bad\')"'
    assert repair_loop(command, Config(max_retries=1), True, True, False, tmp_path, client) == 1
    assert source.read_text() == original


def test_keeps_progress_until_all_failures_are_fixed(tmp_path: Path) -> None:
    source = tmp_path / "calculator.py"
    source.write_text(
        "def divide(a, b):\n    if b != 0:\n        raise ValueError()\n    return a / b\n\n"
        "def power(a, b):\n    return a ^ b\n\n"
        "def average(values):\n    return sum(values) / (len(values) - 1)\n"
    )
    outputs = [
        f'File "{source}", line 2, in divide\nAssertionError: divide',
        f'File "{source}", line 7, in power\nAssertionError: power',
        f'File "{source}", line 10, in average\nAssertionError: average',
        "",
    ]
    responses = iter([
        "def divide(a, b):\n    if b == 0:\n        raise ValueError()\n    return a / b",
        "def power(a, b):\n    return a ** b",
        "def average(values):\n    return sum(values) / len(values)",
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:1.5b"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"response": next(responses)})
        return httpx.Response(200)

    def runner(command: str, timeout: int) -> CommandResult:
        del command, timeout
        output = outputs.pop(0)
        return CommandResult(0 if not output else 1, output)

    client = OllamaClient(Config(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert repair_loop("tests", Config(max_retries=3), False, True, False, tmp_path, client, runner) == 0
    assert "return a ** b" in source.read_text()
    assert "len(values) - 1" not in source.read_text()
    assert not source.with_name("calculator.py.ghost_bak").exists()


def test_rolls_back_all_progress_when_repair_stalls(tmp_path: Path) -> None:
    source = tmp_path / "calculator.py"
    original = "def divide(a, b):\n    return a / b\n"
    source.write_text(original)
    output = f'File "{source}", line 2, in divide\nAssertionError: divide'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:1.5b"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"response": "def divide(a, b):\n    return a / b"})
        return httpx.Response(200)

    def runner(command: str, timeout: int) -> CommandResult:
        del command, timeout
        return CommandResult(1, output)

    client = OllamaClient(Config(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert repair_loop("tests", Config(max_retries=1), False, True, False, tmp_path, client, runner) == 1
    assert source.read_text() == original
    assert not source.with_name("calculator.py.ghost_bak").exists()


def test_propose_applies_inspection_fix_when_existing_tests_pass(tmp_path: Path) -> None:
    source = tmp_path / "analytics.py"
    source.write_text("def divide(total, count):\n    return total / (count - 1)\n")
    finding = Finding(source, 2, "medium", "P2", "Suspicious denominator.", "Wrong totals.", "Review it.")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:1.5b"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"response": "def divide(total, count):\n    return total / count"})
        return httpx.Response(200)

    def runner(command: str, timeout: int) -> CommandResult:
        del command, timeout
        return CommandResult(0, "")

    client = OllamaClient(Config(), httpx.Client(transport=httpx.MockTransport(handler)))
    result = propose_inspection_fix(finding, tmp_path, "tests", Config(), client, lambda: True, runner)
    assert result == 0
    assert source.read_text() == "def divide(total, count):\n    return total / count\n"
    assert not source.with_name("analytics.py.ghost_bak").exists()
