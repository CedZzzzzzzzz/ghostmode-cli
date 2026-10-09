from __future__ import annotations

import random
import threading
import time

import httpx

from .config import Config
from .errors import CircuitOpen, GenerationTimeout, ModelMissing, OllamaUnreachable
from .guards import ensure_loopback
from .prompts import SYSTEM_PROMPT


class OllamaClient:
    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        ensure_loopback(config.host)
        self.config = config
        self.client = client or httpx.Client(timeout=httpx.Timeout(config.read_timeout, connect=3))
        self.failures = 0
        self.last_generation = 0.0
        self.lock = threading.Lock()

    def health_check(self) -> None:
        try:
            self.client.get(self.config.host).raise_for_status()
            tags = self.client.get(f"{self.config.host}/api/tags").json()
        except httpx.HTTPError as error:
            raise OllamaUnreachable("Cannot reach local Ollama at localhost:11434") from error
        names = {model.get("name") for model in tags.get("models", [])}
        if self.config.model not in names:
            raise ModelMissing(f"Model missing. Run: ollama pull {self.config.model}")

    def generate(self, prompt: str) -> str:
        if self.failures >= 3:
            raise CircuitOpen("Ollama circuit is open after three failures")
        with self.lock:
            remaining = self.config.cooldown - (time.monotonic() - self.last_generation)
            if remaining > 0:
                time.sleep(remaining)
            for attempt in range(3):
                try:
                    response = self.client.post(f"{self.config.host}/api/generate", json={
                        "model": self.config.model, "system": SYSTEM_PROMPT, "prompt": prompt,
                        "stream": False, "keep_alive": "10m", "options": {"temperature": 0.1, "num_ctx": self.config.num_ctx, "num_predict": self.config.num_predict},
                    })
                    if response.status_code >= 500:
                        raise httpx.HTTPStatusError("server error", request=response.request, response=response)
                    response.raise_for_status()
                    self.failures = 0
                    self.last_generation = time.monotonic()
                    return str(response.json().get("response", ""))
                except httpx.TimeoutException as error:
                    self.failures += 1
                    if attempt == 2:
                        raise GenerationTimeout("Ollama generation timed out") from error
                except httpx.HTTPError as error:
                    self.failures += 1
                    if attempt == 2:
                        raise OllamaUnreachable("Ollama generation failed") from error
                time.sleep((2 ** attempt) + random.uniform(0, 0.25))
        raise CircuitOpen("Ollama generation did not complete")
