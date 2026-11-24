"""Bounded context: what the model is allowed to see.

Zeus sends a deliberately limited, redacted view of an incident. Limits keep
cost predictable and stop the model from drowning in irrelevant telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Disposition, Incident
from .redact import redact


@dataclass(frozen=True)
class ContextLimits:
    max_signals: int = 40
    max_changes: int = 10
    max_similar: int = 3
    max_chars: int = 12_000


def build_context(
    incident: Incident,
    similar: list[Disposition],
    limits: ContextLimits = ContextLimits(),
) -> str:
    lines = [
        f"Incident: {incident.id}",
        f"Application: {incident.app}",
        f"Severity: {incident.severity}",
        f"Opened: {incident.opened_at}",
        f"Title: {incident.title}",
    ]
    if incident.description:
        lines.append(f"Description: {incident.description}")

    signals = incident.signals[: limits.max_signals]
    lines.append("\nSignals:")
    lines += [f"- {s.render()}" for s in signals] or ["- (none provided)"]
    if len(incident.signals) > len(signals):
        lines.append(f"- ({len(incident.signals) - len(signals)} more signals omitted by context limit)")

    lines.append("\nRecent changes:")
    lines += [f"- {c}" for c in incident.recent_changes[: limits.max_changes]] or ["- (none recorded)"]

    lines.append("\nMaintenance windows:")
    lines += [f"- {m}" for m in incident.maintenance_windows] or ["- (none scheduled)"]

    lines.append("\nDependencies:")
    lines += [f"- {d}" for d in incident.dependencies] or ["- (none recorded)"]

    lines.append("\nSimilar past incidents (institutional memory):")
    lines += [f"- {d.summary()}" for d in similar[: limits.max_similar]] or ["- (no similar past incidents)"]

    text = redact("\n".join(lines))
    if len(text) > limits.max_chars:
        text = text[: limits.max_chars] + "\n[context truncated at limit]"
    return text
