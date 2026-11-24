"""Command-line interface.

    zeus analyze examples/incident_blocking.json --user jb
    zeus attempt INC-1001 --hypothesis 1 --tried "checked head blocker" --found "ETL job held locks" --outcome confirmed --user jb
    zeus close INC-1001 --root-cause "ETL job ran into business hours" --resolution "moved job window" --user jb
    zeus report INC-1001 --user jb
    zeus usage --user lead1 --role lead
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .engine import likelihood_label
from .models import NONE_OF_THE_ABOVE, Analysis, Incident, Outcome
from .providers import ProviderError, get_provider
from .workspace import GuardrailError, PermissionDenied, RationExceeded, Workspace


def _print_analysis(analysis: Analysis, remaining: int) -> None:
    print(f"\nIncident {analysis.incident_id}  [{analysis.classification.value}]  via {analysis.model}")
    if analysis.summary:
        print(f"\n{analysis.summary}")
    print("\nCompeting hypotheses:")
    for h in analysis.hypotheses:
        print(f"\n  {h.rank}. {h.cause}  (model likelihood: {likelihood_label(h.confidence)})")
        for e in h.evidence:
            print(f"       evidence: {e}")
        for s in h.next_steps:
            print(f"       next: {s}")
    print(f"\n  {NONE_OF_THE_ABOVE}. None of the above (log the real cause if it is something else)")
    if analysis.missing_context:
        print("\nWould help to collect:")
        for m in analysis.missing_context:
            print(f"  - {m}")
    if analysis.similar_events:
        print("\nSimilar past incidents:")
        for s in analysis.similar_events:
            print(f"  - {s}")
    print(f"\nAnalyses left this window: {remaining}")
    print("Log what you try against each hypothesis with `zeus attempt` before closing.\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zeus", description="Evidence-first incident reasoning. Engineers decide.")
    parser.add_argument("--home", default=os.environ.get("ZEUS_HOME", ".zeus"), help="state directory (default .zeus)")
    parser.add_argument("--provider", default=os.environ.get("ZEUS_PROVIDER", "mock"),
                        help="mock, anthropic, or openai (default mock)")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_identity(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--user", required=True)
        p.add_argument("--role", default="engineer", choices=["viewer", "engineer", "lead"])
        return p

    p = with_identity(sub.add_parser("analyze", help="analyze an incident JSON file"))
    p.add_argument("incident_file", type=Path)
    p.add_argument("--json", action="store_true", help="print the raw analysis as JSON")

    p = with_identity(sub.add_parser("attempt", help="log what you tried against a hypothesis"))
    p.add_argument("incident_id")
    p.add_argument("--hypothesis", type=int, required=True, help=f"rank, or {NONE_OF_THE_ABOVE} for none of the above")
    p.add_argument("--tried", required=True)
    p.add_argument("--found", required=True)
    p.add_argument("--outcome", required=True, choices=[o.value for o in Outcome])

    p = with_identity(sub.add_parser("close", help="close an incident and record its disposition"))
    p.add_argument("incident_id")
    p.add_argument("--root-cause", required=True)
    p.add_argument("--resolution", required=True)

    p = with_identity(sub.add_parser("report", help="print a markdown incident report"))
    p.add_argument("incident_id")

    with_identity(sub.add_parser("usage", help="per-user activity summary (lead only)"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ws = Workspace(Path(args.home), get_provider(args.provider))
        session = ws.login(args.user, args.role)

        if args.command == "analyze":
            incident = Incident.from_dict(json.loads(args.incident_file.read_text(encoding="utf-8")))
            analysis = ws.analyze(session, incident)
            if args.json:
                print(json.dumps(analysis.to_dict(), indent=2))
            else:
                _print_analysis(analysis, ws.rationer.remaining(session.user))
        elif args.command == "attempt":
            ws.log_attempt(session, args.incident_id, args.hypothesis, args.tried, args.found, args.outcome)
            left = ws.unaddressed(args.incident_id)
            print("Logged." + (f" Hypotheses with nothing tried yet: {', '.join(map(str, left))}" if left else ""))
        elif args.command == "close":
            d = ws.close(session, args.incident_id, args.root_cause, args.resolution)
            print(f"Closed {d.incident_id}. Added to institutional memory.")
        elif args.command == "report":
            print(ws.report(session, args.incident_id))
        elif args.command == "usage":
            print(json.dumps(ws.usage(session), indent=2))
        return 0
    except (GuardrailError, PermissionDenied, RationExceeded, ProviderError, ValueError, FileNotFoundError) as exc:
        print(f"zeus: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
