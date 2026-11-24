# Zeus

**Evidence-first incident reasoning for on-call engineers. Zeus advises. Engineers decide.**

When an incident fires, Zeus gives the on-call engineer a ranked set of *competing hypotheses*. Each one comes with the evidence behind it, a confidence level, and safe next steps. Zeus never runs commands or makes changes. Its guardrails keep people doing the thinking: suggestions are rationed, every hypothesis has to be investigated and logged, and every closed incident goes into institutional memory for the next one.

```
incident ──► redact + bound context ──► LLM ──► validate ──► ranked hypotheses
                   ▲                                              │
                   │                                              ▼
        similar past incidents ◄── disposition ◄── close ◄── engineer logs
          (institutional memory)                            what they tried
```

## Doctrine

1. **Competing hypotheses.** Zeus gives 2–5 ranked causes, never a single answer. Option 0, *none of the above*, is always available.
2. **Evidence with every recommendation.** A hypothesis without supporting evidence is dropped during validation.
3. **Bounded context.** The model sees a limited, redacted view of the incident. Anything it would need but doesn't have goes under `missing_context`.
4. **Institutional memory.** Closed incidents become dispositions. Similar past dispositions are shown to the model and to the engineer.
5. **Proportional recommendations.** Next steps match the severity, and read-only diagnostics come before changes.
6. **Humans retain authority.** Zeus has no actions it can take. It only produces analysis.

## Guardrails

| Guardrail | What it does |
| --- | --- |
| Role-based access | `viewer` can read reports. `engineer` can analyze, log attempts, and close. `lead` can also see usage. |
| Rationed suggestions | Each user gets a fixed number of analyses per rolling window (default 5 per hour). This keeps cost predictable and stops people from outsourcing their judgment. |
| Forced engagement | An incident can't be closed until something has been logged against every hypothesis, or the real cause has been recorded as *none of the above*. |
| Redaction | Secrets, keys, tokens, emails, IPs, and card/SSN patterns are stripped before anything reaches a model. |
| Audit trail | Every login, analysis, denial, attempt, and close is written to an append-only JSONL log. |

## Quickstart

```bash
pip install -e ".[dev]"

# Analyze the example incident with the offline mock provider (no API key needed)
zeus analyze examples/incident_blocking.json --user jb

# Log what you tried against each hypothesis
zeus attempt INC-1001 --hypothesis 1 --tried "checked head blocker" \
  --found "nightly rollup ETL session holding X locks" --outcome confirmed --user jb

# Or record a cause Zeus didn't suggest
zeus attempt INC-1001 --hypothesis 0 --tried "..." --found "..." --outcome confirmed --user jb

# Close the incident; it goes into institutional memory
zeus close INC-1001 --root-cause "ETL rollup moved into business hours by CHG-2231" \
  --resolution "moved schedule back to 02:00" --user jb

zeus report INC-1001 --user jb           # markdown incident report
zeus usage --user lead1 --role lead      # per-user activity
```

### Using a real model

Zeus has no required dependencies and works with any provider:

```bash
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY
zeus --provider anthropic analyze examples/incident_blocking.json --user jb
```

Set `ZEUS_MODEL` to choose a specific model, and `ZEUS_HOME` to change where state is stored (default `.zeus/`).

## Incident format

```json
{
  "id": "INC-1001",
  "app": "loan-origination",
  "title": "Loan application submissions timing out",
  "severity": "high",
  "signals": [{"source": "sqlserver", "name": "blocked_sessions", "value": 47}],
  "recent_changes": ["CHG-2231: nightly rollup ETL schedule moved to 13:55"],
  "maintenance_windows": [],
  "dependencies": ["loan-db (SQL Server Availability Group)"]
}
```

## Layout

| Module | Responsibility |
| --- | --- |
| `zeus/engine.py` | Builds context, calls the provider, validates and ranks the output |
| `zeus/context.py` | Bounded, redacted incident view |
| `zeus/redact.py` | Secret and personal-data scrubbing |
| `zeus/memory.py` | Disposition store and similar-incident retrieval |
| `zeus/workspace.py` | Roles, rationing, forced engagement, close-out |
| `zeus/audit.py` | Append-only audit log and usage summary |
| `zeus/providers.py` | Anthropic, OpenAI, and offline mock providers |
| `zeus/cli.py` | Command-line interface |

## Tests

```bash
pytest
```

## License

Copyright © 2026 JB Byers. All rights reserved. The source is published so it can be read and evaluated; see [LICENSE](LICENSE).
