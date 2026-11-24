import json
from pathlib import Path

import pytest

from zeus.cli import main
from zeus.models import NONE_OF_THE_ABOVE, Incident
from zeus.providers import MockProvider
from zeus.workspace import GuardrailError, PermissionDenied, RationExceeded, Workspace

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "incident_blocking.json"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def incident():
    return Incident.from_dict(json.loads(EXAMPLE.read_text()))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ws(tmp_path, clock):
    return Workspace(tmp_path, MockProvider(), suggestions_per_window=2, window_seconds=600, clock=clock)


def test_viewer_cannot_analyze(ws, incident):
    viewer = ws.login("v", "viewer")
    with pytest.raises(PermissionDenied):
        ws.analyze(viewer, incident)
    assert ws.audit.entries()[-1]["event"] == "denied"


def test_suggestions_are_rationed_then_refill(ws, incident, clock):
    eng = ws.login("jb", "engineer")
    ws.analyze(eng, incident)
    ws.analyze(eng, incident)
    with pytest.raises(RationExceeded):
        ws.analyze(eng, incident)
    clock.now += 601
    ws.analyze(eng, incident)
    assert ws.rationer.remaining("jb") == 1


def test_ration_is_per_user(ws, incident):
    a, b = ws.login("a", "engineer"), ws.login("b", "engineer")
    ws.analyze(a, incident)
    ws.analyze(a, incident)
    ws.analyze(b, incident)  # b still has budget


def test_close_requires_attempt_per_hypothesis(ws, incident):
    eng = ws.login("jb", "engineer")
    analysis = ws.analyze(eng, incident)
    assert len(analysis.hypotheses) >= 2
    ws.log_attempt(eng, incident.id, 1, "checked head blocker", "ETL session held X locks", "confirmed")
    with pytest.raises(GuardrailError, match="still open"):
        ws.close(eng, incident.id, "ETL in business hours", "moved schedule")
    for h in analysis.hypotheses[1:]:
        ws.log_attempt(eng, incident.id, h.rank, "checked", "not it", "ruled_out")
    disposition = ws.close(eng, incident.id, "ETL in business hours", "moved schedule back to 02:00")
    assert disposition.closed_by == "jb"
    with pytest.raises(GuardrailError, match="already closed"):
        ws.close(eng, incident.id, "x", "y")


def test_none_of_the_above_allows_close(ws, incident):
    eng = ws.login("jb", "engineer")
    ws.analyze(eng, incident)
    ws.log_attempt(eng, incident.id, NONE_OF_THE_ABOVE, "checked network", "firewall rule change dropped packets", "confirmed")
    ws.close(eng, incident.id, "firewall rule change", "reverted rule")


def test_attempt_validation(ws, incident):
    eng = ws.login("jb", "engineer")
    ws.analyze(eng, incident)
    with pytest.raises(GuardrailError, match="does not exist"):
        ws.log_attempt(eng, incident.id, 99, "x", "y", "ruled_out")
    with pytest.raises(GuardrailError, match="needs both"):
        ws.log_attempt(eng, incident.id, 1, " ", "y", "ruled_out")


def test_closed_incidents_feed_future_analyses(ws, incident):
    eng = ws.login("jb", "engineer")
    ws.analyze(eng, incident)
    ws.log_attempt(eng, incident.id, NONE_OF_THE_ABOVE, "x", "y", "confirmed")
    ws.close(eng, incident.id, "ETL rollup job blocking submissions", "rescheduled job")

    repeat = Incident.from_dict({**json.loads(EXAMPLE.read_text()), "id": "INC-2002"})
    analysis = ws.analyze(eng, repeat)
    assert any("INC-1001" in s for s in analysis.similar_events)


def test_report_and_usage(ws, incident):
    eng = ws.login("jb", "engineer")
    ws.analyze(eng, incident)
    ws.log_attempt(eng, incident.id, 1, "checked blocker", "found ETL", "confirmed")
    report = ws.report(eng, incident.id)
    assert "# Incident INC-1001" in report and "found ETL" in report and "None of the above" in report

    with pytest.raises(PermissionDenied):
        ws.usage(eng)
    usage = ws.usage(ws.login("boss", "lead"))
    assert usage["jb"]["logins"] == 1
    assert usage["jb"]["events"]["analysis"] == 1


def test_cli_round_trip(tmp_path, capsys):
    home = ["--home", str(tmp_path)]
    assert main(home + ["analyze", str(EXAMPLE), "--user", "jb"]) == 0
    assert "Competing hypotheses" in capsys.readouterr().out
    assert main(home + ["close", "INC-1001", "--root-cause", "x", "--resolution", "y", "--user", "jb"]) == 1
    assert "still open" in capsys.readouterr().err
    assert main(home + ["attempt", "INC-1001", "--hypothesis", "0", "--tried", "a", "--found", "b",
                        "--outcome", "confirmed", "--user", "jb"]) == 0
    assert main(home + ["close", "INC-1001", "--root-cause", "x", "--resolution", "y", "--user", "jb"]) == 0
    assert main(home + ["report", "INC-1001", "--user", "jb"]) == 0
    assert "## Resolution" in capsys.readouterr().out

def test_rejects_path_like_incident_id(ws, incident):
    eng = ws.login("demo", "engineer")
    incident.id = "../escape"
    with pytest.raises(GuardrailError, match="invalid incident"):
        ws.analyze(eng, incident)
    with pytest.raises(GuardrailError, match="invalid incident"):
        ws.load_analysis("../escape")


def test_inconclusive_alternative_does_not_allow_close(ws, incident):
    eng = ws.login("demo", "engineer")
    ws.analyze(eng, incident)
    ws.log_attempt(eng, incident.id, 0, "checked", "uncertain", "inconclusive")
    with pytest.raises(GuardrailError, match="still open"):
        ws.close(eng, incident.id, "cause", "resolution")
    with pytest.raises(GuardrailError, match="cannot replace"):
        ws.analyze(eng, incident)


def test_concurrent_budget_reservations(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from zeus.workspace import Rationer
    ration = Rationer(tmp_path / "budget.sqlite3", 3, 600, lambda: 1000.0)
    def reserve(_):
        try:
            ration.consume("demo")
            return True
        except RationExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(reserve, range(12))) == 3
    assert ration.remaining("demo") == 0
