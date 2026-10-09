import httpx

from ghostmode.config import Config
from ghostmode.llm import OllamaClient
from ghostmode.prompts import build_inspection_prompt


def test_health_and_generation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:1.5b"}]})
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"response": "def x():\n    return 1"})
        return httpx.Response(200)
    client = OllamaClient(Config(), httpx.Client(transport=httpx.MockTransport(handler)))
    client.health_check()
    assert "return 1" in client.generate("fix")


def test_inspection_prompt_requires_one_function_only() -> None:
    prompt = build_inspection_prompt("Missing guard.", "The request can crash.", "Validate input.", "def parse_range():\n    pass\n")
    assert "Do not include imports" in prompt
    assert "exactly one complete replacement" in prompt


def test_regression_test_prompt_uses_existing_test_context() -> None:
    from ghostmode.prompts import build_regression_test_prompt

    prompt = build_regression_test_prompt(
        "Wrong total.",
        "Users see incorrect data.",
        "Add a test.",
        "app/services/analytics.py",
        "tests/test_ghostmode_analytics.py",
        "pytest",
        "def overview():\n    pass\n",
        "def test_existing():\n    assert True\n",
    )
    assert "do not redefine application models" in prompt
    assert "test_existing" in prompt
