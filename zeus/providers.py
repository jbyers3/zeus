"""Model providers. Zeus is model-agnostic: anything that turns a prompt into text works.

Real providers use only the standard library, so Zeus has no required
dependencies. The mock provider is deterministic and needs no API key, which
makes it the default for demos and tests.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Protocol


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


def _post_json(url: str, headers: dict[str, str], body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network, HTTP, or decode failure
        raise ProviderError(f"provider request failed: {exc}") from exc


class AnthropicProvider:
    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 2000):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        self.model = model or os.environ.get("ZEUS_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens
        self.name = f"anthropic:{self.model}"

    def complete(self, system: str, user: str) -> str:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        try:
            return "".join(block["text"] for block in data["content"] if block.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"unexpected Anthropic response: {data}") from exc


class OpenAIProvider:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ProviderError("OPENAI_API_KEY is not set")
        self.model = model or os.environ.get("ZEUS_MODEL", "gpt-4o")
        self.name = f"openai:{self.model}"

    def complete(self, system: str, user: str) -> str:
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"authorization": f"Bearer {self.api_key}"},
            {
                "model": self.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected OpenAI response: {data}") from exc


# (keywords, cause, next steps) used by the offline mock provider.
_RULES: list[tuple[tuple[str, ...], str, list[str]]] = [
    (("block", "lock", "deadlock"),
     "Lock contention from a long-running or uncommitted transaction",
     ["Find the head blocker and the statement it is running",
      "Check whether the blocking session belongs to a batch job or a user"]),
    (("cpu",),
     "CPU saturation from an expensive query or a plan regression",
     ["Pull the top queries by CPU for the window",
      "Compare current plans against the last known good plan"]),
    (("page_life", "page life", "ple", "memory", "buffer"),
     "Memory pressure evicting the buffer pool",
     ["Check page life expectancy trend and memory grants pending",
      "Look for a large scan or new workload competing for memory"]),
    (("failover", "replica", "synchron", "redo", "send_queue"),
     "Availability Group replica lag or an unplanned failover",
     ["Check replica synchronization state and send/redo queues",
      "Confirm which replica is primary and when it changed"]),
    (("job", "batch", "etl"),
     "A batch or ETL job overlapping peak traffic",
     ["List jobs running during the window and their durations",
      "Compare against the job's normal schedule and runtime"]),
    (("disk", "latency", "storage", "io_"),
     "Storage latency slowing reads and writes",
     ["Check read/write latency per volume for the window",
      "Confirm whether backups or snapshots were running"]),
    (("deploy", "release", "change", "patch"),
     "A recent change introduced a regression",
     ["Diff behavior before and after the change time",
      "Ask the change owner whether a rollback is safe"]),
]


class MockProvider:
    """Deterministic, offline stand-in for a model. Useful for demos and tests."""

    name = "mock"

    def complete(self, system: str, user: str) -> str:
        lines = user.splitlines()
        evidence_lines = [l[2:] for l in lines if l.startswith("- ") and "(none" not in l]
        lowered = user.lower()
        hypotheses = []
        for keys, cause, steps in _RULES:
            hits = [l for l in evidence_lines if any(k in l.lower() for k in keys)]
            if hits:
                hypotheses.append({
                    "cause": cause,
                    "evidence": hits[:4],
                    "confidence": round(min(0.9, 0.3 + 0.15 * len(hits)), 2),
                    "next_steps": steps,
                })

        in_maintenance = "maintenance windows:\n- (none scheduled)" not in lowered
        if in_maintenance:
            window = re.search(r"maintenance windows:\n- (.+)", user, re.I)
            hypotheses.append({
                "cause": "Expected impact from scheduled maintenance",
                "evidence": [window.group(1) if window else "maintenance window overlaps incident"],
                "confidence": 0.6,
                "next_steps": ["Confirm the maintenance owner and expected end time"],
            })

        if not hypotheses:
            classification = "noise"
        elif in_maintenance and len(hypotheses) == 1:
            classification = "maintenance"
        else:
            classification = "incident"

        return json.dumps({
            "classification": classification,
            "summary": f"Mock analysis found {len(hypotheses)} candidate cause(s).",
            "at_risk": [],
            "hypotheses": hypotheses,
            "missing_context": [] if hypotheses else ["More signals are needed to form hypotheses"],
        })


def get_provider(name: str) -> Provider:
    name = name.lower()
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openai":
        return OpenAIProvider()
    raise ProviderError(f"unknown provider '{name}' (use mock, anthropic, or openai)")
