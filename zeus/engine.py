"""Turn an incident into a validated, ranked analysis."""

from __future__ import annotations

import json
import math
import re

from .context import ContextLimits, build_context
from .memory import DispositionStore
from .models import Analysis, Classification, Hypothesis, Incident
from .prompt import SYSTEM_PROMPT, build_user_prompt
from .providers import Provider


class AnalysisError(ValueError):
    pass


def _extract_json(raw: str) -> dict:
    """Models sometimes wrap JSON in prose or code fences; take the outermost object."""
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise AnalysisError("model response contained no JSON object")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AnalysisError("model response JSON was not an object")
    return data


def _as_str_list(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    return []


def parse_analysis(raw: str, incident_id: str, model: str, max_hypotheses: int = 5) -> Analysis:
    data = _extract_json(raw)
    hypotheses: list[Hypothesis] = []
    items = data.get("hypotheses", [])
    if not isinstance(items, list):
        raise AnalysisError("hypotheses must be an array")
    for item in items:
        if not isinstance(item, dict):
            continue
        cause = item.get("cause", "")
        cause = cause.strip() if isinstance(cause, str) else ""
        evidence = _as_str_list(item.get("evidence"))
        if not cause or not evidence:
            continue  # doctrine: no evidence, no hypothesis
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if not math.isfinite(confidence):
            confidence = 0.0
        hypotheses.append(Hypothesis(
            rank=0,
            cause=cause,
            evidence=evidence,
            confidence=max(0.0, min(1.0, confidence)),
            next_steps=_as_str_list(item.get("next_steps")),
        ))

    hypotheses.sort(key=lambda h: h.confidence, reverse=True)
    hypotheses = hypotheses[:max_hypotheses]
    for i, h in enumerate(hypotheses, start=1):
        h.rank = i

    return Analysis(
        incident_id=incident_id,
        classification=Classification.parse(data.get("classification")),
        summary=str(data.get("summary", "")).strip(),
        hypotheses=hypotheses,
        at_risk=_as_str_list(data.get("at_risk")),
        missing_context=_as_str_list(data.get("missing_context")),
        model=model,
    )


class Zeus:
    def __init__(
        self,
        provider: Provider,
        memory: DispositionStore | None = None,
        limits: ContextLimits = ContextLimits(),
        max_hypotheses: int = 5,
    ):
        self.provider = provider
        self.memory = memory
        self.limits = limits
        self.max_hypotheses = max_hypotheses

    def analyze(self, incident: Incident) -> Analysis:
        similar = self.memory.similar(incident, k=self.limits.max_similar) if self.memory else []
        context = build_context(incident, similar, self.limits)
        raw = self.provider.complete(SYSTEM_PROMPT, build_user_prompt(context))
        analysis = parse_analysis(raw, incident.id, self.provider.name, self.max_hypotheses)
        analysis.similar_events = [d.summary() for d in similar]
        return analysis


def likelihood_label(score: float) -> str:
    """Model-assigned ordinal label, not a calibrated probability."""
    return "high" if score >= 0.75 else "medium" if score >= 0.4 else "low"
