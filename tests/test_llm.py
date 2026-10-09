import httpx

from ghostmode.config import Config
from ghostmode.llm import OllamaClient


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
