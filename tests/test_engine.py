import json

import pytest

from zeus.context import ContextLimits, build_context
from zeus.engine import AnalysisError, Zeus, parse_analysis
from zeus.models import Classification, Incident, Signal
from zeus.providers import MockProvider
from zeus.redact import redact


def _incident(**overrides):
    data = {
        "id": "INC-1",
        "app": "payments",
        "title": "Checkout slow",
        "signals": [{"source": "sql", "name": "blocked_sessions", "value": 12}],
    }
    data.update(overrides)
    return Incident.from_dict(data)


def test_parse_ranks_by_confidence_and_drops_unsupported_hypotheses():
    raw = json.dumps({
        "classification": "incident",
        "summary": "s",
        "hypotheses": [
            {"cause": "low", "evidence": ["a"], "confidence": 0.2},
            {"cause": "no evidence", "evidence": [], "confidence": 0.99},
            {"cause": "high", "evidence": ["b"], "confidence": 0.8},
        ],
    })
    analysis = parse_analysis(raw, "INC-1", "test")
    assert [h.cause for h in analysis.hypotheses] == ["high", "low"]
    assert [h.rank for h in analysis.hypotheses] == [1, 2]


def test_parse_handles_code_fences_and_clamps_confidence():
    raw = "Here you go:\n```json\n" + json.dumps({
        "classification": "MAINTENANCE",
        "hypotheses": [{"cause": "c", "evidence": "single string", "confidence": 7}],
    }) + "\n```"
    analysis = parse_analysis(raw, "INC-1", "test")
    assert analysis.classification is Classification.MAINTENANCE
    assert analysis.hypotheses[0].confidence == 1.0
    assert analysis.hypotheses[0].evidence == ["single string"]


def test_parse_rejects_non_json():
    with pytest.raises(AnalysisError):
        parse_analysis("I think it's the database.", "INC-1", "test")


def test_unknown_classification_falls_back():
    raw = json.dumps({"classification": "catastrophe", "hypotheses": []})
    assert parse_analysis(raw, "INC-1", "t").classification is Classification.UNKNOWN


def test_redact_strips_secrets_and_personal_data():
    text = (
        "conn password=hunter2 user jb@example.com host 10.0.0.5 "
        "key AKIAABCDEFGHIJKLMNOP auth Bearer abcdefghijklmnopqrstuvwxyz"
    )
    cleaned = redact(text)
    for leaked in ("hunter2", "jb@example.com", "10.0.0.5", "AKIAABCDEFGHIJKLMNOP", "abcdefghijklmnop"):
        assert leaked not in cleaned


def test_context_is_bounded_and_redacted():
    incident = _incident(
        description="contact ops@example.com",
        signals=[{"source": "s", "name": f"sig{i}", "value": i} for i in range(100)],
    )
    text = build_context(incident, [], ContextLimits(max_signals=5))
    assert "sig4" in text and "sig5" not in text
    assert "95 more signals omitted" in text
    assert "ops@example.com" not in text


def test_mock_provider_end_to_end():
    analysis = Zeus(MockProvider()).analyze(_incident())
    assert analysis.classification is Classification.INCIDENT
    assert analysis.hypotheses[0].cause.startswith("Lock contention")
    assert all(h.evidence for h in analysis.hypotheses)


def test_mock_provider_classifies_noise_without_signals():
    analysis = Zeus(MockProvider()).analyze(_incident(signals=[]))
    assert analysis.classification is Classification.NOISE
    assert analysis.hypotheses == []
