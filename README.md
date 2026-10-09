# GhostMode

**The Air-Gapped, Self-Healing Code Repair Agent for Low-Resource Hardware.**

GhostMode runs a trusted test command, identifies a likely application frame, asks a local Ollama model for a function replacement, applies it with a backup, and retains it only after the tests pass. It never contacts a network target other than loopback Ollama.

## Supported languages

GhostMode supports Python, JavaScript, TypeScript, React (`.jsx`/`.tsx`), PHP/Laravel, and Rust. React, Vite, Next.js, Angular, Laravel, and Cargo projects are verified through the test or build command you provide. Python receives AST syntax validation; other languages use conservative structural validation before their own local test/build tools provide final verification.

## Install and run

```bash
pip install ghostmode-cli
ollama pull qwen2.5-coder:1.5b
ghostmode doctor
```

Run GhostMode from the project you want to repair:

```bash
ghostmode run "pytest -q"
```

For npm projects:

```bash
ghostmode run "npm test"
```

Inspect a project locally for high-signal risks without changing files:

```bash
ghostmode inspect
```

Each finding includes severity, repair priority, and potential impact so developers can review the most harmful risks first.

Use `--dry-run` to review a diff without changing files. GhostMode never edits tests unless `--allow-test-edits` is explicitly passed.
For Vitest projects, `ghostmode run "npm test"` automatically uses single-run mode instead of watch mode.

## Memory budget

| Component | Budget |
| --- | ---: |
| GhostMode Python process | under 100 MB RSS |
| Ollama + 0.5B model | about 1 GB |
| Ollama + 1.5B model | about 2 GB |

## Limitations

Small local models are best at simple, single-function faults. Multi-file, API-wide, and logic-heavy repairs often need human review. Only run trusted test commands: the command is intentionally executed through a shell to support normal test invocations.
