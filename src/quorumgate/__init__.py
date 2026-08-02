"""quorumgate -- never ship unverified LLM output.

A zero-dependency reliability layer for LLM pipelines: multi-model councils
with pluggable arbitration, severity-graded audit gates with bounded retries,
and deterministic fallbacks. Bring your own models; the framework provides
the structure.
"""

from .breaker import CircuitBreaker
from .council import (
    Arbiter,
    Council,
    HighestConfidenceArbiter,
    JudgeArbiter,
    MajorityArbiter,
    Seat,
)
from .gate import AuditGate, check
from .jsonx import extract_json
from .pipeline import Pipeline
from .types import (
    Attempt,
    CouncilResult,
    Failure,
    GateError,
    GateResult,
    Opinion,
    Severity,
)

__version__ = "0.1.0"

__all__ = [
    "Arbiter",
    "Attempt",
    "AuditGate",
    "CircuitBreaker",
    "Council",
    "CouncilResult",
    "Failure",
    "GateError",
    "GateResult",
    "HighestConfidenceArbiter",
    "JudgeArbiter",
    "MajorityArbiter",
    "Opinion",
    "Pipeline",
    "Seat",
    "Severity",
    "check",
    "extract_json",
]
