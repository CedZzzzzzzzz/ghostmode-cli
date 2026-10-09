from __future__ import annotations

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
from .inspect import inspect_project
from .llm import OllamaClient
from .parser import extract_enclosing_function, language_for_path, parse_trace, truncate_log
from .patcher import FilePatcher, extract_clean_code, matches_target_function, validate_code
from .prompts import build_prompt

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
def inspect(path: Path = Path(".")) -> None:
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
