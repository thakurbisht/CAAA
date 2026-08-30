"""
Language model access.

Two constraints shaped this module.

The project has no runtime dependencies, and that is not incidental — a
control whose findings cannot be reproduced without resolving a dependency
tree is harder to defend, not easier. So HTTP is done with `urllib` from the
standard library rather than an SDK.

The second is that this layer must be optional. Every number this project
reports is produced by deterministic code in phases 1 to 4. If the model is
unreachable, misconfigured, or returns nonsense, the pipeline must still
produce its full output with the interpretation layer simply absent. A missing
narrative is a cosmetic loss; a missing finding is not.

Backends:
  ollama     — a local model over http://localhost:11434
  anthropic  — the Messages API, key from ANTHROPIC_API_KEY
  none       — no model; deterministic fallbacks are used throughout
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Response:
    text: str
    ok: bool
    backend: str
    error: str | None = None


class LLMClient:

    def __init__(self, backend: str = "auto", *, model: str | None = None,
                 host: str = "http://localhost:11434", timeout: int = 120):
        self.host = host
        self.timeout = timeout
        self.backend = self._resolve(backend)
        self.model = model or self._default_model()
        self.calls = 0
        self.failures = 0

    def _resolve(self, backend: str) -> str:
        if backend != "auto":
            return backend
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if self._ollama_alive():
            return "ollama"
        return "none"

    def _default_model(self) -> str:
        return {"anthropic": "claude-sonnet-4-5",
                "ollama": "mistral-nemo:12b"}.get(self.backend, "")

    def _ollama_alive(self) -> bool:
        try:
            urllib.request.urlopen(f"{self.host}/api/tags", timeout=2)
            return True
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self.backend != "none"

    # ------------------------------------------------------------------

    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1500) -> Response:
        if self.backend == "none":
            return Response("", False, "none", "no backend configured")

        self.calls += 1
        try:
            if self.backend == "ollama":
                text = self._ollama(prompt, system, max_tokens)
            elif self.backend == "anthropic":
                text = self._anthropic(prompt, system, max_tokens)
            else:
                return Response("", False, self.backend,
                                f"unknown backend '{self.backend}'")
            return Response(text, True, self.backend)
        except Exception as e:
            self.failures += 1
            return Response("", False, self.backend, f"{type(e).__name__}: {e}")

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def _ollama(self, prompt: str, system: str, max_tokens: int) -> str:
        body = self._post(f"{self.host}/api/generate", {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            # Temperature is pinned low because this layer paraphrases facts
            # that have already been established. Variation here is not
            # creativity, it is drift between two runs over the same estate.
            "options": {"temperature": 0.1, "num_predict": max_tokens},
        }, {})
        return body.get("response", "")

    def _anthropic(self, prompt: str, system: str, max_tokens: int) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        body = self._post("https://api.anthropic.com/v1/messages", {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        parts = [b.get("text", "") for b in body.get("content", [])
                 if b.get("type") == "text"]
        return "\n".join(parts)

    def summary(self) -> dict:
        return {"backend": self.backend, "model": self.model,
                "calls": self.calls, "failures": self.failures}


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------

class ScriptedClient(LLMClient):
    """A client that returns prepared responses.

    Used to test the validators against failure modes that a real model
    produces only occasionally and unpredictably — dropping findings from a
    clustering, inventing identifiers, contradicting a catalogue risk rating.
    Waiting for a live model to make each of those mistakes is not a test
    strategy; constructing them is.
    """

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.backend = "scripted"
        self.model = "scripted"
        self.calls = 0
        self.failures = 0

    @property
    def available(self) -> bool:
        return True

    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1500) -> Response:
        self.calls += 1
        if not self.responses:
            return Response("", False, "scripted", "no scripted response left")
        return Response(self.responses.pop(0), True, "scripted")
