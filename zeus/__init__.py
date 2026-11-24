"""Zeus: evidence-first incident reasoning. Zeus advises. Engineers decide."""

from .engine import AnalysisError, Zeus, parse_analysis
from .models import Analysis, Attempt, Classification, Disposition, Hypothesis, Incident, Outcome, Signal
from .providers import AnthropicProvider, MockProvider, OpenAIProvider, get_provider
from .workspace import GuardrailError, PermissionDenied, RationExceeded, Workspace

__version__ = "0.1.0"

__all__ = [
    "Analysis", "AnalysisError", "AnthropicProvider", "Attempt", "Classification", "Disposition",
    "GuardrailError", "Hypothesis", "Incident", "MockProvider", "OpenAIProvider", "Outcome",
    "PermissionDenied", "RationExceeded", "Signal", "Workspace", "Zeus", "get_provider", "parse_analysis",
]
