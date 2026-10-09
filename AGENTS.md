# AGENTS.md

Guidance for AI coding agents working on **GhostMode**, an air-gapped, self-healing test-repair CLI. Read this before changing anything.

## Project Overview

GhostMode runs a test command, parses the failure's stack trace, asks a local Ollama model (`qwen2.5-coder:0.5b` by default) to patch the broken function, applies the patch, and re-runs the tests in a closed loop. If the tests pass, the fix is kept; if not, the file is rolled back and the next attempt gets the new failure.

It is built for regulated, low-resource environments: 8GB RAM, dual-core CPU, no GPU, **no network access beyond loopback**.

## Non-Negotiable Constraints

1. **Air-gapped.** The only allowed network target is the local Ollama server (`localhost`, `127.0.0.1`, `::1`). No telemetry, no update checks, no remote URLs, no package downloads at runtime.
2. **Runtime dependencies are exactly:** `typer>=0.12.0`, `rich>=13.7.0`, `httpx>=0.27.0`. Everything else is stdlib. NO LangChain, NO ChromaDB, NO numpy/pandas/requests/pydantic or other new packages. Dev tools (`pytest`, `mypy`, `ruff`) are dev-only.
3. **Memory:** the Python process stays under 100MB RSS. Do not load whole large files into memory, do not accumulate unbounded output, cap captured subprocess output.
4. **Python 3.10+ compatible.** Do not use 3.11+ only features without a guard (for example `tomllib`).
5. **Never leave a user's file corrupted.** Every mutation goes through `FilePatcher` with a backup and guaranteed rollback.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e . -r requirements-dev.txt

# Quality gates (all must pass before you finish a task)
ruff check .
mypy --strict ghostmode
pytest                      # runs tests/ only (demo_project is excluded on purpose)

# Run
python -m ghostmode doctor
python -m ghostmode run "pytest demo_project -q"
python -m ghostmode run "pytest demo_project -q" --dry-run

# Demo
make demo                   # run GhostMode against the buggy demo
make demo-reset             # restore demo_project/auth.py from auth.py.orig
```

## Repository Layout

| Path | Responsibility |
| --- | --- |
| `ghostmode/cli.py` | Typer commands (`run`, `doctor`) and loop orchestration. No business logic. |
| `ghostmode/parser.py` | Stack trace parsing (pytest, unittest) and AST function extraction. Pure functions. |
| `ghostmode/patcher.py` | Backup, atomic write, rollback, cleanup, LLM output cleaning, validation. The only module that writes user files. |
| `ghostmode/llm.py` | Ollama client: health check, generation, retries, throttle, circuit breaker. The only module that touches the network. |
| `ghostmode/guards.py` | Loopback enforcement, path sandbox, RAM checks, subprocess limits, loop safety. |
| `ghostmode/config.py` | `Config` dataclass; precedence: defaults < `ghostmode.toml` < env vars < CLI flags. |
| `ghostmode/prompts.py` | System prompt, few-shot example, prompt builders. |
| `ghostmode/errors.py` | Typed exceptions. |
| `tests/` | Unit and loop tests; Ollama is always mocked. |
| `demo_project/` | Intentionally buggy demo. **Do not "fix" it by hand.** |
| `benchmarks/` | Seeded-bug cases and the fix-rate runner. |

## Architecture Rules

- Keep module responsibilities strict. Network code lives only in `llm.py`; file writes only in `patcher.py`; safety checks only in `guards.py`.
- Use dependency injection: the repair loop receives an LLM client and a test runner so it can be tested with fakes.
- Prefer **function-level patching** (splice the patched function back into the file) over whole-file replacement. Small models truncate large files.
- Always `ast.parse` generated code before writing it. Never write unparseable code to disk.
- The parser should prefer the deepest **non-test, non-stdlib** frame as the repair target.
- Prompts must stay small: send the enclosing function, the error type and message, and a truncated log, not whole files.

## Code Style

- Strict typing: `mypy --strict` must be clean. Annotate all public functions; use `dataclass(frozen=True)` for value objects.
- Docstrings on public functions and classes (one or two lines is fine).
- No bare `except`; catch specific exceptions and raise typed errors from `errors.py`.
- No mutable module-level state.
- Use `pathlib.Path` for paths; use `logging` (not `print`) for diagnostics; use Rich only in `cli.py` for user-facing output.
- Keep functions short and single-purpose. Prefer clarity over cleverness.

## Testing Requirements

- Every new behavior needs a test. Bug fixes need a regression test.
- Tests must run **without Ollama and without network**. Use `httpx.MockTransport` and the fake LLM fixture in `tests/conftest.py`.
- Parser tests use real trace samples in `tests/fixtures/`. Add a fixture when you handle a new trace shape.
- Cover failure paths: timeouts, malformed LLM output, permission errors, interrupted runs, rollback.
- Do not make `pytest` at the repo root collect `demo_project/` (it fails on purpose). `testpaths = ["tests"]` in `pyproject.toml` enforces this.

## Safety Boundaries

**Always**
- Back up before mutating; roll back on any failure, exception, or interrupt.
- Keep patched files inside the project root (resolve symlinks, reject traversal).
- Enforce timeouts on every subprocess and HTTP call.
- Log prompts, responses, diffs, and test results to `.ghostmode/ghostmode.log` for auditability.

**Ask first**
- Adding any dependency, runtime or dev.
- Changing the default model, default timeouts, or the system prompt wording.
- Changing exit codes or CLI flags (they are a public interface).
- Allowing edits to test files (`--allow-test-edits` is opt-in by design).

**Never**
- Add any outbound network call except to loopback Ollama.
- Send file contents anywhere other than the local model.
- Patch files outside the project root, in `site-packages`, `.venv`, or the stdlib.
- Commit `.ghost_bak` files or `.ghostmode/` logs.
- Weaken the loopback guard, path sandbox, or rollback guarantees to make a test pass.
- Use `shell=True` with anything other than the user-supplied test command.

## Exit Codes

`0` fixed or already passing, `1` unable to fix, `2` environment error (Ollama or model missing), `3` guard violation, `130` interrupted.

## Definition of Done

A task is complete only when:

1. `ruff check .`, `mypy --strict ghostmode`, and `pytest` all pass.
2. New behavior has tests; no test requires Ollama or the network.
3. No new dependencies were added without approval.
4. `make demo` still shows the full detect, patch, verify flow (or a graceful failure with a restored file), and `make demo-reset` restores the bug.
5. README or `docs/ARCHITECTURE.md` is updated if behavior, flags, or architecture changed.

## Known Limitations (be honest in docs)

Sub-2B models reliably fix simple single-function bugs and struggle with multi-file or logic-heavy bugs. Do not claim otherwise in the README or demo; report measured numbers from `benchmarks/run_benchmark.py`.