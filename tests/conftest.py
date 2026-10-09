from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ghostmode.config import Config
from ghostmode.llm import OllamaClient


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    source = tmp_path / "app.py"
    source.write_text("def value() -> int:\n    return 0\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_llm() -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:1.5b"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"response": "def value() -> int:\n    return 1"})
        return httpx.Response(200)

    return OllamaClient(Config(), httpx.Client(transport=httpx.MockTransport(handler)))
