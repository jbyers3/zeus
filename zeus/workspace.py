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
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audit import AuditLog
from .engine import AnalysisError, Zeus
from .memory import DispositionStore, incident_keywords, keywords
from .models import (
    NONE_OF_THE_ABOVE,
    Analysis,
    Attempt,
    Disposition,
    Incident,
    Outcome,
)
from .providers import Provider, ProviderError
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


def _safe_id(incident_id: str) -> str:
    if not isinstance(incident_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", incident_id):
        raise GuardrailError("invalid incident identifier")
    return incident_id


def _atomic_json(path: Path, data: dict) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".zeus-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Rationer:
    """SQLite-backed per-user rolling window; reservation is transactional."""

    def __init__(self, path: Path, limit: int, window_seconds: int, clock: Callable[[], float]):
        self.path, self.limit, self.window, self.clock = path, limit, window_seconds, clock
        if limit < 1 or window_seconds < 1:
            raise ValueError("ration limit and window must be positive")
        with closing(self._connect()) as db:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS reservations (user TEXT NOT NULL, at REAL NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS reservations_user_at ON reservations(user, at)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def remaining(self, user: str) -> int:
        with closing(self._connect()) as db:
            count = db.execute("SELECT count(*) FROM reservations WHERE user=? AND at>?",
                               (user, self.clock() - self.window)).fetchone()[0]
        return max(0, self.limit - count)

    def consume(self, user: str) -> int:
        with closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                now = self.clock()
                db.execute("DELETE FROM reservations WHERE at<=?", (now - self.window,))
                count, oldest = db.execute("SELECT count(*), min(at) FROM reservations WHERE user=?",
                                           (user,)).fetchone()
                if count >= self.limit:
                    wait = max(1, int(self.window - (now - oldest)))
                    raise RationExceeded(
                        f"{user} has used all {self.limit} analyses for this window; "
                        f"next one frees up in about {wait // 60 or 1} minute(s). "
                        "Keep investigating the open hypotheses by hand."
                    )
                db.execute("INSERT INTO reservations VALUES (?, ?)", (user, now))
                return self.limit - count - 1


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
        self.rationer = Rationer(self.root / "ration.sqlite3", suggestions_per_window, window_seconds, clock)
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
        path = self.root / "analyses" / f"{_safe_id(analysis.incident_id)}.json"
        _atomic_json(path, analysis.to_dict())

    def load_analysis(self, incident_id: str) -> Analysis:
        path = self.root / "analyses" / f"{_safe_id(incident_id)}.json"
        if not path.exists():
            raise GuardrailError(f"no analysis found for incident {incident_id}")
        return Analysis.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def load_incident(self, incident_id: str) -> Incident:
        path = self.root / "incidents" / f"{_safe_id(incident_id)}.json"
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
        _safe_id(incident.id)
        if self.is_closed(incident.id):
            raise GuardrailError("incident is already closed")
        if self.attempts(incident.id):
            raise GuardrailError("cannot replace an analysis after attempts are recorded")
        try:
            remaining = self.rationer.consume(session.user)
        except RationExceeded:
            self.audit.record("rationed", session.user, incident_id=incident.id)
            raise
        try:
            analysis = self.zeus.analyze(incident)
        except (AnalysisError, ProviderError) as exc:
            self.audit.record("analysis_failed", session.user, incident_id=incident.id,
                              error_type=type(exc).__name__)
            raise
        _atomic_json(self.root / "incidents" / f"{incident.id}.json", incident.to_dict())
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
        _safe_id(incident_id)
        if self.is_closed(incident_id):
            raise GuardrailError("incident is already closed")
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
        _safe_id(incident_id)
        if not root_cause.strip() or not resolution.strip():
            raise GuardrailError("root cause and resolution are required")
        if self.is_closed(incident_id):
            raise GuardrailError(f"incident {incident_id} is already closed")
        attempts = self.attempts(incident_id)
        found_elsewhere = any(a.hypothesis_rank == NONE_OF_THE_ABOVE and a.outcome is Outcome.CONFIRMED for a in attempts)
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
