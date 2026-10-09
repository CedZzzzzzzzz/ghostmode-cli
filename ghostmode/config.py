from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    host: str = "http://localhost:11434"
    model: str = "qwen2.5-coder:1.5b"
    max_retries: int = 5
    timeout: int = 120
    read_timeout: int = 120
    num_ctx: int = 2048
    num_predict: int = 512
    cooldown: float = 0.0
    max_minutes: int = 10

    def with_overrides(self, **values: Any) -> Config:
        allowed = {key: value for key, value in values.items() if value is not None}
        if "max_retries" in allowed:
            allowed["max_retries"] = min(int(allowed["max_retries"]), 10)
        return replace(self, **allowed)


def load_config(path: Path | None = None) -> Config:
    values: dict[str, Any] = {}
    config_path = path or Path("ghostmode.toml")
    if config_path.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:
            pass
        else:
            with config_path.open("rb") as handle:
                values.update(tomllib.load(handle).get("ghostmode", {}))
    mappings = {
        "host": ("GHOSTMODE_HOST", str), "model": ("GHOSTMODE_MODEL", str),
        "max_retries": ("GHOSTMODE_MAX_RETRIES", int), "timeout": ("GHOSTMODE_TIMEOUT", int),
        "read_timeout": ("GHOSTMODE_READ_TIMEOUT", int), "num_ctx": ("GHOSTMODE_NUM_CTX", int),
        "num_predict": ("GHOSTMODE_NUM_PREDICT", int), "cooldown": ("GHOSTMODE_COOLDOWN", float),
        "max_minutes": ("GHOSTMODE_MAX_MINUTES", int),
    }
    for key, (name, converter) in mappings.items():
        if name in os.environ:
            values[key] = converter(os.environ[name])
    return Config().with_overrides(**values)
