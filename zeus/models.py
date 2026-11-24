"""Core data types for Zeus."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Classification(str, Enum):
    INCIDENT = "incident"
    MAINTENANCE = "maintenance"
    NOISE = "noise"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> "Classification":
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.UNKNOWN


class Outcome(str, Enum):
    CONFIRMED = "confirmed"
    RULED_OUT = "ruled_out"
    INCONCLUSIVE = "inconclusive"


# Rank 0 is reserved for "none of the above": the engineer found a cause
# Zeus did not suggest. It is always available on every analysis.
NONE_OF_THE_ABOVE = 0


@dataclass
class Signal:
    source: str
    name: str
    value: Any
    unit: str | None = None
    timestamp: str | None = None

    def render(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        when = f" @ {self.timestamp}" if self.timestamp else ""
        return f"[{self.source}] {self.name} = {self.value}{unit}{when}"


@dataclass
class Incident:
    id: str
    app: str
    title: str
    severity: str = "unknown"
    opened_at: str = field(default_factory=utcnow)
    description: str = ""
    signals: list[Signal] = field(default_factory=list)
    recent_changes: list[str] = field(default_factory=list)
    maintenance_windows: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Incident":
        for key in ("id", "app", "title"):
            if not data.get(key):
                raise ValueError(f"incident is missing required field '{key}'")
        signals = [Signal(**s) for s in data.get("signals", [])]
        return cls(
            id=str(data["id"]),
            app=str(data["app"]),
            title=str(data["title"]),
            severity=str(data.get("severity", "unknown")),
            opened_at=str(data.get("opened_at") or utcnow()),
            description=str(data.get("description", "")),
            signals=signals,
            recent_changes=list(data.get("recent_changes", [])),
            maintenance_windows=list(data.get("maintenance_windows", [])),
            dependencies=list(data.get("dependencies", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Hypothesis:
    rank: int
    cause: str
    evidence: list[str]
    confidence: float
    next_steps: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    incident_id: str
    classification: Classification
    summary: str
    hypotheses: list[Hypothesis]
    at_risk: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    similar_events: list[str] = field(default_factory=list)
    model: str = "unknown"
    generated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["classification"] = self.classification.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Analysis":
        return cls(
            incident_id=data["incident_id"],
            classification=Classification.parse(data["classification"]),
            summary=data.get("summary", ""),
            hypotheses=[Hypothesis(**h) for h in data.get("hypotheses", [])],
            at_risk=data.get("at_risk", []),
            missing_context=data.get("missing_context", []),
            similar_events=data.get("similar_events", []),
            model=data.get("model", "unknown"),
            generated_at=data.get("generated_at", utcnow()),
        )

    def ranks(self) -> set[int]:
        return {h.rank for h in self.hypotheses}


@dataclass
class Attempt:
    incident_id: str
    hypothesis_rank: int
    user: str
    action: str
    result: str
    outcome: Outcome
    at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Attempt":
        data = dict(data)
        data["outcome"] = Outcome(data["outcome"])
        return cls(**data)


@dataclass
class Disposition:
    incident_id: str
    app: str
    title: str
    root_cause: str
    resolution: str
    keywords: list[str]
    closed_by: str
    closed_at: str = field(default_factory=utcnow)

    def summary(self) -> str:
        return (
            f"{self.incident_id} ({self.app}): {self.title} -> root cause: "
            f"{self.root_cause}; resolved by: {self.resolution}"
        )
