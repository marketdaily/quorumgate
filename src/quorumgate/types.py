"""Core value types shared across the framework."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class Severity(str, enum.Enum):
    """How bad a failed check is.

    HIGH  -- the output must not ship as-is; triggers retry, then fallback.
    MED   -- worth recording and alerting on, but the output still ships.
    LOW   -- cosmetic; recorded only.
    """

    HIGH = "high"
    MED = "med"
    LOW = "low"


@dataclass(frozen=True)
class Failure:
    """One failed check on a candidate output."""

    check: str
    severity: Severity = Severity.HIGH
    message: str = ""

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.check}: {self.message}"


@dataclass
class Opinion:
    """One seat's parsed contribution to a council round."""

    seat: str
    content: Any
    stance: Optional[str] = None
    confidence: float = 0.0


@dataclass
class CouncilResult:
    """Outcome of one council round."""

    opinions: List[Opinion] = field(default_factory=list)
    verdict: Any = None
    dissent: int = 0
    quorum_met: bool = False
    budget_exhausted: bool = False
    seat_errors: Dict[str, str] = field(default_factory=dict)

    @property
    def stances(self) -> List[Optional[str]]:
        return [o.stance for o in self.opinions]


@dataclass(frozen=True)
class Attempt:
    """Passed to the generate callable so it can adapt per attempt.

    ``index`` is 0 for the first attempt. On retries, ``failures`` carries the
    failures of the previous attempt so the caller can e.g. switch to a
    stronger model or tighten its prompt.
    """

    index: int = 0
    failures: tuple = ()

    @property
    def is_retry(self) -> bool:
        return self.index > 0


@dataclass
class GateResult:
    """What the gate finally shipped, and how it got there."""

    output: Any
    failures: List[Failure] = field(default_factory=list)
    attempts: int = 1
    source: str = "primary"  # primary | retry | soft-pass | fallback
    verified: bool = True

    @property
    def high_failures(self) -> List[Failure]:
        return [f for f in self.failures if f.severity is Severity.HIGH]


class GateError(RuntimeError):
    """Raised when output fails HIGH checks and no fallback is available."""

    def __init__(self, failures: List[Failure]):
        self.failures = failures
        detail = "; ".join(str(f) for f in failures if f.severity is Severity.HIGH)
        super().__init__(f"output failed HIGH checks and no fallback was provided: {detail}")
