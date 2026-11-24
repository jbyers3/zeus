"""Markdown incident report: what Zeus suggested, what people tried, how it ended."""

from __future__ import annotations

from .models import NONE_OF_THE_ABOVE, Analysis, Attempt, Disposition, Incident


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_report(
    incident: Incident,
    analysis: Analysis,
    attempts: list[Attempt],
    disposition: Disposition | None,
) -> str:
    out = [
        f"# Incident {incident.id}: {incident.title}",
        "",
        f"- **Application:** {incident.app}",
        f"- **Severity:** {incident.severity}",
        f"- **Opened:** {incident.opened_at}",
        f"- **Classification:** {analysis.classification.value}",
        f"- **Analyzed by:** {analysis.model} at {analysis.generated_at}",
        f"- **Status:** {'closed' if disposition else 'open'}",
        "",
        "## Summary",
        "",
        analysis.summary or "(no summary)",
        "",
        "## Hypotheses",
        "",
        "| # | Cause | Confidence | Evidence |",
        "| - | ----- | ---------- | -------- |",
    ]
    for h in analysis.hypotheses:
        out.append(f"| {h.rank} | {_cell(h.cause)} | {h.confidence:.0%} | {_cell('; '.join(h.evidence))} |")
    out.append(f"| {NONE_OF_THE_ABOVE} | None of the above | n/a | Cause found outside Zeus's suggestions |")

    out += ["", "## What was tried", ""]
    if attempts:
        out += ["| When | Who | Hypothesis | Tried | Found | Outcome |", "| - | - | - | - | - | - |"]
        for a in attempts:
            out.append(
                f"| {a.at} | {a.user} | {a.hypothesis_rank} | {_cell(a.action)} | "
                f"{_cell(a.result)} | {a.outcome.value} |"
            )
    else:
        out.append("Nothing logged yet.")

    if analysis.missing_context:
        out += ["", "## Missing context", ""] + [f"- {m}" for m in analysis.missing_context]
    if analysis.similar_events:
        out += ["", "## Similar past incidents", ""] + [f"- {s}" for s in analysis.similar_events]

    if disposition:
        out += [
            "",
            "## Resolution",
            "",
            f"- **Root cause:** {disposition.root_cause}",
            f"- **Resolution:** {disposition.resolution}",
            f"- **Closed by:** {disposition.closed_by} at {disposition.closed_at}",
        ]
    return "\n".join(out) + "\n"
