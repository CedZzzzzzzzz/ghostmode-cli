from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import Config, load_config
from .errors import GhostModeError, GuardViolation
from .guards import TestResult, ensure_safe_path, prepare_test_command, run_tests
from .inspect import Finding, inspect_project
from .llm import OllamaClient
from .parser import extract_enclosing_function, language_for_path, parse_trace, truncate_log
from .patcher import FilePatcher, extract_clean_code, matches_target_function, validate_code
from .prompts import build_inspection_prompt, build_prompt, build_regression_test_prompt

app = typer.Typer(add_completion=False, help="Air-gapped, local test repair.")
console = Console()


def repair_loop(
    command: str,
    config: Config,
    dry_run: bool,
    yes: bool,
    allow_test_edits: bool,
    root: Path,
    client: OllamaClient | None = None,
    runner: Callable[[str, int], TestResult] = run_tests,
) -> int:
    llm = client or OllamaClient(config)
    test_command = prepare_test_command(command, root)
    if test_command != command:
        console.print(f"Using non-watch Vitest command: {test_command}")
    first = runner(test_command, config.timeout)
    if first.returncode == 0:
        console.print(Panel("All tests passing", style="green"))
        return 0
    llm.health_check()
    patcher = FilePatcher()
    output = first.output
    previous: list[str] = []
    backed_up_files: list[Path] = []
    started = time.monotonic()
    try:
        for attempt_number in range(config.max_retries):
            if time.monotonic() - started > config.max_minutes * 60:
                break
            trace = parse_trace(output)
            if trace is None:
                console.print("Could not identify a repair target from the test output.")
                break
            target = ensure_safe_path(Path(trace.failed_file), root, allow_test_edits)
            language = language_for_path(target)
            if language is None:
                console.print(f"Unsupported repair language: {target.suffix}")
                break
            span = extract_enclosing_function(str(target), trace.line_number)
            if span is None:
                console.print("Could not find the enclosing function to patch.")
                break
            generated = extract_clean_code(llm.generate(build_prompt(trace, span.source, truncate_log(output), previous)))
            if not validate_code(generated, target.suffix) or not matches_target_function(generated, span.name, target.suffix):
                previous.append(generated)
                continue
            original = target.read_text(encoding="utf-8", errors="replace")
            lines = original.splitlines(keepends=True)
            preview = "".join(lines[:span.start_line - 1]) + generated + "\n" + "".join(lines[span.end_line:])
            console.print(patcher.diff(original, preview))
            if dry_run:
                return 1
            if not yes and not typer.confirm("Apply this patch?"):
                for patched_file in backed_up_files:
                    patcher.rollback(patched_file)
                return 1
            if target not in backed_up_files:
                patcher.backup(target)
                backed_up_files.append(target)
            patcher.apply_function_patch(target, span, generated)
            result = runner(test_command, config.timeout)
            if result.returncode == 0:
                for patched_file in backed_up_files:
                    patcher.cleanup(patched_file)
                console.print(Panel("Fix Verified & Applied Successfully!", style="green"))
                return 0
            if result.output == output:
                break
            output = result.output
            previous.append(generated)
    except BaseException:
        for patched_file in backed_up_files:
            patcher.rollback(patched_file)
        raise
    for patched_file in backed_up_files:
        patcher.rollback(patched_file)
    console.print(Panel("Unable to verify a repair; original file restored.", style="red"))
    return 1


def propose_inspection_fix(
    finding: Finding,
    root: Path,
    test_command: str,
    config: Config,
    client: OllamaClient,
    approve: Callable[[], bool],
    runner: Callable[[str, int], TestResult] = run_tests,
    dry_run: bool = False,
) -> int:
    command = prepare_test_command(test_command, root)
    baseline = runner(command, config.timeout)
    target = ensure_safe_path(finding.file, root)
    span = extract_enclosing_function(str(target), finding.line)
    if span is None:
        console.print("Could not find an enclosing function for this finding.", style="red")
        return 1
    client.health_check()
    generated = extract_clean_code(client.generate(
        build_inspection_prompt(finding.message, finding.impact, finding.suggestion, span.source),
    ))
    if not validate_code(generated, target.suffix) or not matches_target_function(generated, span.name, target.suffix):
        console.print("The local model returned an invalid function replacement.", style="red")
        return 1
    original = target.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines(keepends=True)
    preview = "".join(lines[:span.start_line - 1]) + generated + "\n" + "".join(lines[span.end_line:])
    patcher = FilePatcher()
    console.print(patcher.diff(original, preview))
    if dry_run:
        status = "tests currently pass" if baseline.returncode == 0 else "tests currently fail"
        console.print(f"Dry run: proposal was not applied ({status}).")
        return 0
    if not approve():
        console.print("Proposal not applied.")
        return 1
    patcher.backup(target)
    try:
        patcher.apply_function_patch(target, span, generated)
        result = runner(command, config.timeout)
        if result.returncode != 0:
            patcher.rollback(target)
            console.print(Panel("Existing tests failed after the proposal; original file restored.", style="red"))
            return 1
        patcher.cleanup(target)
    except BaseException:
        patcher.rollback(target)
        raise
    message = (
        "Proposal applied and the regression test now passes."
        if baseline.returncode != 0
        else "Proposal applied and existing tests still pass. Add a regression test to verify the intended behavior."
    )
    console.print(Panel(message, style="yellow"))
    return 0


def regression_test_path(root: Path, finding: Finding) -> tuple[Path, str] | None:
    suffix = finding.file.suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", finding.file.stem)
    if suffix == ".py":
        return root / "tests" / f"test_ghostmode_{stem}_{finding.line}.py", "pytest"
    if suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return finding.file.with_name(f"{finding.file.stem}.ghostmode.{finding.line}.test{suffix}"), "Vitest"
    if suffix == ".php":
        class_name = "".join(part.capitalize() for part in stem.split("_"))
        return root / "tests" / "Feature" / f"Ghostmode{class_name}{finding.line}Test.php", "PHPUnit or Laravel"
    return None


def test_example(root: Path, suffix: str) -> str:
    patterns: tuple[str, ...]
    if suffix == ".py":
        patterns = ("tests/conftest.py", "tests/test_*.py", "test_*.py")
    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        patterns = ("src/**/*.test.*", "src/**/*.spec.*", "**/*.test.*")
    else:
        patterns = ("tests/**/*.php",)
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(sorted(root.glob(pattern)))
    for candidate in candidates:
        if candidate.is_file() and "ghostmode" not in candidate.name.lower():
            return candidate.read_text(encoding="utf-8", errors="replace")[:4_000]
    return "No existing test file was found. Use standard project imports and test conventions."


def generate_regression_test(
    finding: Finding,
    root: Path,
    test_command: str,
    config: Config,
    client: OllamaClient,
    approve: Callable[[], bool],
    runner: Callable[[str, int], TestResult] = run_tests,
    dry_run: bool = False,
) -> int:
    command = prepare_test_command(test_command, root)
    if runner(command, config.timeout).returncode != 0:
        console.print("Test command already fails. Fix unrelated failures before generating a regression test.", style="red")
        return 1
    target = ensure_safe_path(finding.file, root)
    span = extract_enclosing_function(str(target), finding.line)
    details = regression_test_path(root, finding)
    if span is None or details is None:
        console.print("Could not determine a supported regression-test target.", style="red")
        return 1
    test_path, framework = details
    test_path = ensure_safe_path(test_path, root, allow_test_edits=True)
    if test_path.exists():
        console.print(f"Regression test already exists: {test_path.relative_to(root)}", style="red")
        return 1
    client.health_check()
    prompt = build_regression_test_prompt(
        finding.message,
        finding.impact,
        finding.suggestion,
        str(target.relative_to(root)),
        str(test_path.relative_to(root)),
        framework,
        span.source,
        test_example(root, test_path.suffix),
    )
    generated = ""
    for _ in range(config.max_retries):
        generated = extract_clean_code(client.generate(prompt))
        if validate_code(generated, test_path.suffix):
            break
        prompt += "\nYour previous response was invalid. Return valid test code only."
    else:
        console.print("The local model returned an invalid regression test after multiple attempts.", style="red")
        return 1
    patcher = FilePatcher()
    console.print(patcher.diff("", generated))
    if dry_run:
        console.print("Dry run: regression test was not created.")
        return 0
    if not approve():
        console.print("Regression test was not created.")
        return 1
    try:
        patcher.apply_fix(test_path, generated)
        result = runner(command, config.timeout)
        if result.returncode == 0:
            test_path.unlink()
            console.print(Panel(
                "The generated test passed against the current code, so it was removed. Review the expected behavior and try again.",
                style="yellow",
            ))
            return 1
    except BaseException:
        test_path.unlink(missing_ok=True)
        raise
    console.print(Panel(
        "Regression test created and it currently fails. Run ghostmode inspect --propose with the same test command to repair it.",
        style="green",
    ))
    return 0


@app.command()
def run(
    test_cmd: Annotated[str, typer.Argument(help="Trusted command used to run tests")],
    model: str | None = None, max_retries: int | None = None, timeout: int | None = None,
    dry_run: bool = False, yes: bool = False, allow_test_edits: bool = False,
    config: Path | None = None, verbose: bool = False, json_output: bool = typer.Option(False, "--json"),
) -> None:
    del verbose, json_output
    settings = load_config(config).with_overrides(model=model, max_retries=max_retries, timeout=timeout)
    try:
        raise typer.Exit(repair_loop(test_cmd, settings, dry_run, yes, allow_test_edits, Path.cwd()))
    except GuardViolation as error:
        console.print(f"Safety check failed: {error}", style="red")
        raise typer.Exit(3) from error
    except GhostModeError as error:
        console.print(str(error), style="red")
        raise typer.Exit(2) from error


@app.command()
def doctor(model: str | None = None, config: Path | None = None) -> None:
    settings = load_config(config).with_overrides(model=model)
    try:
        OllamaClient(settings).health_check()
    except GhostModeError as error:
        console.print(f"[red]FAIL[/red] {error}")
        raise typer.Exit(2) from error
    console.print("[green]OK[/green] Python and local Ollama model are ready")


@app.command()
def inspect(
    path: Path = Path("."),
    propose: bool = False,
    generate_test: bool = typer.Option(False, "--generate-test"),
    test_cmd: str | None = typer.Option(None, "--test-cmd"),
    model: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    config: Path | None = None,
) -> None:
    root = path.resolve()
    if not root.is_dir():
        console.print(f"Directory not found: {root}", style="red")
        raise typer.Exit(1)
    findings = inspect_project(root)
    if not findings:
        console.print(Panel("No high-signal risks found.", style="green"))
        return
    table = Table(title="Local inspection findings")
    table.add_column("Severity")
    table.add_column("Priority")
    table.add_column("Location")
    table.add_column("Finding")
    table.add_column("Potential impact")
    table.add_column("Review")
    for finding in findings:
        location = f"{finding.file.relative_to(root)}:{finding.line}"
        table.add_row(
            finding.severity.upper(),
            finding.priority,
            location,
            finding.message,
            finding.impact,
            finding.suggestion,
        )
    console.print(table)
    console.print("Inspection does not modify files. Add a regression test before using ghostmode run to repair a finding.")
    if not propose and not generate_test:
        return
    if test_cmd is None:
        console.print("--propose and --generate-test require --test-cmd for verification.", style="red")
        raise typer.Exit(2)
    if propose and generate_test:
        console.print("Use either --propose or --generate-test, not both.", style="red")
        raise typer.Exit(2)
    priority = {"P1": 0, "P2": 1}
    finding = min(findings, key=lambda item: priority.get(item.priority, 2))
    settings = load_config(config).with_overrides(model=model)
    client = OllamaClient(settings)
    approved = lambda: yes or typer.confirm("Apply this proposal?")
    if generate_test:
        raise typer.Exit(generate_regression_test(
            finding,
            root,
            test_cmd,
            settings,
            client,
            approved,
            dry_run=dry_run,
        ))
    raise typer.Exit(propose_inspection_fix(
        finding,
        root,
        test_cmd,
        settings,
        client,
        approved,
        dry_run=dry_run,
    ))
