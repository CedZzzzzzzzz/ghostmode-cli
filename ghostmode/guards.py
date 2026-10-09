from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .errors import GuardViolation

MAX_OUTPUT = 200_000
CREATE_NEW_PROCESS_GROUP = 0x00000200


@dataclass(frozen=True)
class TestResult:
    returncode: int
    output: str
    timed_out: bool = False


def ensure_loopback(url: str) -> None:
    host = urlparse(url).hostname
    if host is None:
        raise GuardViolation("Ollama URL has no host")
    if host.lower() == "localhost":
        return
    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror as error:
        raise GuardViolation(f"Cannot resolve Ollama host: {host}") from error
    for address in addresses:
        try:
            if not ipaddress.ip_address(address[4][0]).is_loopback:
                raise GuardViolation("Ollama must use a loopback-only host")
        except ValueError as error:
            raise GuardViolation("Invalid Ollama host") from error


def ensure_safe_path(file_path: Path, root: Path, allow_test_edits: bool = False) -> Path:
    candidate, project = file_path.resolve(), root.resolve()
    try:
        candidate.relative_to(project)
    except ValueError as error:
        raise GuardViolation("Refusing to patch outside the project root") from error
    blocked = {"site-packages", ".venv", "venv", "__pycache__"}
    if any(part in blocked for part in candidate.parts):
        raise GuardViolation("Refusing to patch an environment or generated file")
    name = candidate.name
    if not allow_test_edits and (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"):
        raise GuardViolation("Refusing to patch a test file; use --allow-test-edits")
    return candidate


def prepare_test_command(command: str, root: Path) -> str:
    if not re.fullmatch(r"\s*npm(?:\s+run)?\s+test\s*", command):
        return command
    package_file = root / "package.json"
    try:
        package = json.loads(package_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return command
    scripts = package.get("scripts")
    test_script = scripts.get("test") if isinstance(scripts, dict) else None
    if not isinstance(test_script, str) or "vitest" not in test_script:
        return command
    if re.search(r"(?:^|\s)(?:run|--run)(?:\s|$)", test_script):
        return command
    return f"{command.rstrip()} -- --run"


def run_tests(command: str, timeout: int) -> TestResult:
    if not command.strip():
        raise GuardViolation("A test command is required")
    try:
        if os.name == "nt":
            completed = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout,
                creationflags=CREATE_NEW_PROCESS_GROUP, check=False,
            )
        else:
            completed = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout,
                start_new_session=True, check=False,
            )
        output = (completed.stdout + completed.stderr)[-MAX_OUTPUT:]
        return TestResult(completed.returncode, output)
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr or ""
        output = (stdout + stderr)[-MAX_OUTPUT:]
        return TestResult(1, output, timed_out=True)


def rss_megabytes() -> float | None:
    status = Path("/proc/self/status")
    if status.is_file():
        match = re.search(r"VmRSS:\s+(\d+)", status.read_text(errors="replace"))
        return int(match.group(1)) / 1024 if match else None
    return None
