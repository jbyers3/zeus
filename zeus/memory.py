"""Institutional memory: closed incidents and how they were resolved.

Every closed incident becomes a disposition. When a new incident arrives,
Zeus retrieves the most similar past dispositions and shows them to the model
and the engineer, so the team stops re-solving the same problem.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from .models import Disposition, Incident

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "at",
    "by", "with", "from", "after", "before", "high", "low", "app", "on", "was",
}


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    seen: dict[str, None] = {}
    for w in words:
        if len(w) > 2 and w not in _STOPWORDS:
            seen.setdefault(w, None)
    return list(seen)


def incident_keywords(incident: Incident) -> list[str]:
    parts = [incident.title, incident.description]
    parts += [s.name for s in incident.signals]
    parts += incident.recent_changes
    return keywords(" ".join(parts))


class DispositionStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, disposition: Disposition) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(disposition)) + "\n")

    def all(self) -> list[Disposition]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [Disposition(**json.loads(line)) for line in f if line.strip()]

    def similar(self, incident: Incident, k: int = 3, threshold: float = 0.15) -> list[Disposition]:
        """Rank past dispositions by keyword overlap, with a boost for the same app."""
        query = set(incident_keywords(incident))
        if not query:
            return []
        scored: list[tuple[float, Disposition]] = []
        for d in self.all():
            if d.incident_id == incident.id:
                continue
            past = set(d.keywords)
            union = query | past
            score = len(query & past) / len(union) if union else 0.0
            if d.app == incident.app:
                score += 0.1
            if score >= threshold:
                scored.append((score, d))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [d for _, d in scored[:k]]
