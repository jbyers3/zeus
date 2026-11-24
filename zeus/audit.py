"""Append-only audit trail of everything people do with Zeus."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .models import utcnow


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, user: str, **fields: Any) -> None:
        entry = {"at": utcnow(), "event": event, "user": user, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def usage(self) -> dict[str, dict[str, Any]]:
        """Per-user login count, last login, and event counts."""
        summary: dict[str, dict[str, Any]] = defaultdict(lambda: {"logins": 0, "last_login": None, "events": Counter()})
        for e in self.entries():
            u = summary[e["user"]]
            u["events"][e["event"]] += 1
            if e["event"] == "login":
                u["logins"] += 1
                u["last_login"] = e["at"]
        return {user: {**data, "events": dict(data["events"])} for user, data in summary.items()}
