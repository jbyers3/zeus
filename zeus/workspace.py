"""Guardrails around the engine: who can do what, how often, and what must be recorded.

Zeus never executes anything. The workspace enforces the human side of the
loop:

* Role-based access: only permitted roles can request analyses or close incidents.
* Rationed suggestions: each user gets a fixed number of analyses per time
  window, which limits cost and keeps engineers from outsourcing judgment.
* Forced engagement: an incident cannot be closed until the engineer has
  logged what they tried against every hypothesis (or recorded a
  "none of the above" finding).
* Institutional memory: every closed incident becomes a disposition that
  informs future analyses.
* Audit: every action is written to an append-only log.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audit import AuditLog
from .engine import Zeus
from .memory import DispositionStore, incident_keywords, keywords
from .models import (
    NONE_OF_THE_ABOVE,
    Analysis,
    Attempt,
    Disposition,
    Incident,
    Outcome,
)
from .providers import Provider
from .report import render_report

ROLES: dict[str, set[str]] = {
    "viewer": {"report"},
    "engineer": {"report", "analyze", "attempt", "close"},
    "lead": {"report", "analyze", "attempt", "close", "usage"},
}


class PermissionDenied(PermissionError):
    pass


class RationExceeded(RuntimeError):
    pass


class GuardrailError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    user: str
    role: str


class Rationer:
    """Fixed number of analyses per user per rolling window, persisted to disk."""

    def __init__(self, path: Path, limit: int, window_seconds: int, clock: Callable[[], float]):
        self.path, self.limit, self.window, self.clock = path, limit, window_seconds, clock

    def _load(self) -> dict[str, list[float]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def remaining(self, user: str) -> int:
        now = self.clock()
        recent = [t for t in self._load().get(user, []) if now - t < self.window]
        return max(0, self.limit - len(recent))

    def consume(self, user: str) -> int:
        now = self.clock()
        state = self._load()
        recent = [t for t in state.get(user, []) if now - t < self.window]
        if len(recent) >= self.limit:
            wait = int(self.window - (now - min(recent)))
            raise RationExceeded(
                f"{user} has used all {self.limit} analyses for this window; "
                f"next one frees up in about {max(wait, 1) // 60 or 1} minute(s). "
                "Keep investigating the open hypotheses by hand."
            )
        recent.append(now)
        state[user] = recent
        self.path.write_text(json.dumps(state), encoding="utf-8")
        return self.limit - len(recent)


class Workspace:
    def __init__(
        self,
        root: Path,
        provider: Provider,
        suggestions_per_window: int = 5,
        window_seconds: int = 3600,
        clock: Callable[[], float] = time.time,
    ):
        self.root = Path(root)
        (self.root / "analyses").mkdir(parents=True, exist_ok=True)
        (self.root / "incidents").mkdir(parents=True, exist_ok=True)
        self.audit = AuditLog(self.root / "audit.jsonl")
        self.memory = DispositionStore(self.root / "dispositions.jsonl")
        self.rationer = Rationer(self.root / "ration.json", suggestions_per_window, window_seconds, clock)
        self.zeus = Zeus(provider, memory=self.memory)
        self._attempts_path = self.root / "attempts.jsonl"

    # --- access -----------------------------------------------------------

    def login(self, user: str, role: str) -> Session:
        if role not in ROLES:
            raise PermissionDenied(f"unknown role '{role}' (use one of: {', '.join(ROLES)})")
        self.audit.record("login", user, role=role)
        return Session(user=user, role=role)

    def _require(self, session: Session, permission: str) -> None:
        if permission not in ROLES[session.role]:
            self.audit.record("denied", session.user, role=session.role, permission=permission)
            raise PermissionDenied(f"role '{session.role}' cannot {permission}")

    # --- storage ----------------------------------------------------------

    def _save_analysis(self, analysis: Analysis) -> None:
        path = self.root / "analyses" / f"{analysis.incident_id}.json"
        path.write_text(json.dumps(analysis.to_dict(), indent=2), encoding="utf-8")

    def load_analysis(self, incident_id: str) -> Analysis:
        path = self.root / "analyses" / f"{incident_id}.json"
        if not path.exists():
            raise GuardrailError(f"no analysis found for incident {incident_id}")
        return Analysis.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def load_incident(self, incident_id: str) -> Incident:
        path = self.root / "incidents" / f"{incident_id}.json"
        if not path.exists():
            raise GuardrailError(f"no incident found with id {incident_id}")
        return Incident.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def attempts(self, incident_id: str) -> list[Attempt]:
        if not self._attempts_path.exists():
            return []
        with self._attempts_path.open(encoding="utf-8") as f:
            rows = [Attempt.from_dict(json.loads(line)) for line in f if line.strip()]
        return [a for a in rows if a.incident_id == incident_id]

    def is_closed(self, incident_id: str) -> bool:
        return any(d.incident_id == incident_id for d in self.memory.all())

    # --- actions ----------------------------------------------------------

    def analyze(self, session: Session, incident: Incident) -> Analysis:
        self._require(session, "analyze")
        try:
            remaining = self.rationer.consume(session.user)
        except RationExceeded:
            self.audit.record("rationed", session.user, incident_id=incident.id)
            raise
        (self.root / "incidents" / f"{incident.id}.json").write_text(
            json.dumps(incident.to_dict(), indent=2), encoding="utf-8"
        )
        analysis = self.zeus.analyze(incident)
        self._save_analysis(analysis)
        self.audit.record(
            "analysis",
            session.user,
            incident_id=incident.id,
            model=analysis.model,
            classification=analysis.classification.value,
            hypotheses=len(analysis.hypotheses),
            remaining=remaining,
        )
        return analysis

    def log_attempt(
        self,
        session: Session,
        incident_id: str,
        hypothesis_rank: int,
        action: str,
        result: str,
        outcome: Outcome | str,
    ) -> Attempt:
        self._require(session, "attempt")
        analysis = self.load_analysis(incident_id)
        if hypothesis_rank != NONE_OF_THE_ABOVE and hypothesis_rank not in analysis.ranks():
            raise GuardrailError(
                f"hypothesis {hypothesis_rank} does not exist; use 1-{len(analysis.hypotheses)} "
                f"or {NONE_OF_THE_ABOVE} for none of the above"
            )
        if not action.strip() or not result.strip():
            raise GuardrailError("an attempt needs both what you tried and what you found")
        attempt = Attempt(
            incident_id=incident_id,
            hypothesis_rank=hypothesis_rank,
            user=session.user,
            action=action.strip(),
            result=result.strip(),
            outcome=Outcome(outcome),
        )
        with self._attempts_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(attempt.to_dict()) + "\n")
        self.audit.record(
            "attempt", session.user, incident_id=incident_id,
            hypothesis=hypothesis_rank, outcome=attempt.outcome.value,
        )
        return attempt

    def unaddressed(self, incident_id: str) -> list[int]:
        """Hypothesis ranks with no logged attempt."""
        analysis = self.load_analysis(incident_id)
        tried = {a.hypothesis_rank for a in self.attempts(incident_id)}
        return sorted(analysis.ranks() - tried)

    def close(self, session: Session, incident_id: str, root_cause: str, resolution: str) -> Disposition:
        self._require(session, "close")
        if self.is_closed(incident_id):
            raise GuardrailError(f"incident {incident_id} is already closed")
        attempts = self.attempts(incident_id)
        found_elsewhere = any(a.hypothesis_rank == NONE_OF_THE_ABOVE for a in attempts)
        open_ranks = self.unaddressed(incident_id)
        if open_ranks and not found_elsewhere:
            raise GuardrailError(
                "log what you tried for each hypothesis before closing; still open: "
                + ", ".join(str(r) for r in open_ranks)
                + f" (or log the real cause as hypothesis {NONE_OF_THE_ABOVE}, none of the above)"
            )
        incident = self.load_incident(incident_id)
        disposition = Disposition(
            incident_id=incident_id,
            app=incident.app,
            title=incident.title,
            root_cause=root_cause.strip(),
            resolution=resolution.strip(),
            keywords=list(dict.fromkeys(incident_keywords(incident) + keywords(root_cause))),
            closed_by=session.user,
        )
        self.memory.add(disposition)
        self.audit.record("close", session.user, incident_id=incident_id)
        return disposition

    def report(self, session: Session, incident_id: str) -> str:
        self._require(session, "report")
        incident = self.load_incident(incident_id)
        analysis = self.load_analysis(incident_id)
        disposition = next((d for d in self.memory.all() if d.incident_id == incident_id), None)
        self.audit.record("report", session.user, incident_id=incident_id)
        return render_report(incident, analysis, self.attempts(incident_id), disposition)

    def usage(self, session: Session) -> dict:
        self._require(session, "usage")
        return self.audit.usage()
