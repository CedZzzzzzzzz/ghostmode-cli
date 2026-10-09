# Architecture

```mermaid
flowchart LR
    T[Trusted test command] --> P[Trace parser]
    P --> L[Local Ollama]
    L --> F[FilePatcher backup and atomic patch]
    F --> T
    T -->|pass| C[cleanup backup]
    T -->|fail| R[rollback]
```

| Module | Responsibility |
| --- | --- |
| `guards` | loopback, paths, subprocess limits |
| `parser` | trace and function extraction |
| `llm` | local Ollama requests only |
| `patcher` | backups and atomic writes |
| `cli` | command interface and orchestration |
