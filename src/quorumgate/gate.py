"""Audit gate: never ship unverified LLM output.

The gate runs registered checks against a candidate output. HIGH-severity
failures trigger a bounded retry loop (the generate callable sees the
previous failures, so it can switch to a stronger model or a tighter
prompt), and if the output still cannot pass, a *deterministic fallback* --
output built without an LLM, guaranteed correct by construction -- ships
instead. MED/LOW failures are recorded but do not block.

Two production-hardening extras:

* **soft checks** -- HIGH checks whose failure means "quality below bar",
  not "content is wrong". An optional policy may choose to ship anyway
  (e.g. for an internal audience) rather than degrade to the fallback.
* **systemic escalation** -- when the *same* HIGH check fails across N
  independent ``run`` calls, that is almost never a per-item fluke; it is a
  template or prompt-layer bug. The gate fires ``on_systemic`` exactly once
  per check so a human gets paged before the batch finishes.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional

from .types import Attempt, Failure, GateError, GateResult, Severity

Check = Callable[[Any, Any], Iterable[Failure]]
Generate = Callable[[Attempt, Any], Any]
Fallback = Callable[[Any], Any]


def check(
    name: str,
    severity: Severity = Severity.HIGH,
) -> Callable[[Callable[[Any, Any], Any]], Check]:
    """Decorator that turns a simple predicate into a check.

    The wrapped function receives ``(output, context)`` and may return:
    ``None`` / ``False`` (pass), ``True`` (fail with the check name as
    message), or a string (fail with that message).
    """

    def wrap(fn: Callable[[Any, Any], Any]) -> Check:
        def run_check(output: Any, context: Any) -> List[Failure]:
            verdict = fn(output, context)
            if not verdict:
                return []
            message = verdict if isinstance(verdict, str) else name
            return [Failure(check=name, severity=severity, message=message)]

        run_check.__name__ = name
        return run_check

    return wrap


class AuditGate:
    """Verify -> retry -> deterministic fallback. Never ship unverified.

    Parameters
    ----------
    checks : callables ``(output, context) -> iterable[Failure]``. Use the
        ``@check`` decorator for the common predicate case. A crashing check
        never blocks shipping: its exception is recorded as a MED failure.
    max_retries : how many extra generation attempts HIGH failures earn.
    cooldown : seconds to wait before each retry. Rate-limited free tiers
        are usually per-minute windows; retrying instantly just lands in the
        same window and fails identically.
    soft_checks : names of HIGH checks that gate quality, not correctness.
    soft_policy : ``(high_failures, context) -> bool``; return True to ship
        the last attempt even though soft HIGH checks failed. Only consulted
        when *every* remaining HIGH failure is a soft check.
    systemic_threshold / on_systemic : escalate once when the same HIGH
        check has failed in this many distinct ``run`` calls.
    sleep : injectable for tests.
    """

    def __init__(
        self,
        checks: Iterable[Check],
        *,
        max_retries: int = 1,
        cooldown: float = 0.0,
        soft_checks: Iterable[str] = (),
        soft_policy: Optional[Callable[[List[Failure], Any], bool]] = None,
        systemic_threshold: Optional[int] = None,
        on_systemic: Optional[Callable[[str, Any], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.checks = list(checks)
        self.max_retries = max(0, max_retries)
        self.cooldown = cooldown
        self.soft_checks: FrozenSet[str] = frozenset(soft_checks)
        self.soft_policy = soft_policy
        self.systemic_threshold = systemic_threshold
        self.on_systemic = on_systemic
        self._sleep = sleep
        self._systemic_counts: Dict[str, int] = {}

    # -- auditing ----------------------------------------------------------

    def audit(self, output: Any, context: Any = None) -> List[Failure]:
        """Run all checks; a broken check is reported, never fatal."""
        failures: List[Failure] = []
        for chk in self.checks:
            try:
                failures.extend(chk(output, context))
            except Exception as exc:
                failures.append(Failure(
                    check=f"{getattr(chk, '__name__', 'check')}:crashed",
                    severity=Severity.MED,
                    message=str(exc)[:120],
                ))
        return failures

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _high(failures: List[Failure]) -> List[Failure]:
        return [f for f in failures if f.severity is Severity.HIGH]

    def _note_systemic(self, high_failures: List[Failure], context: Any) -> None:
        if self.systemic_threshold is None or self.on_systemic is None:
            return
        for name in sorted({f.check for f in high_failures}):
            self._systemic_counts[name] = self._systemic_counts.get(name, 0) + 1
            if self._systemic_counts[name] == self.systemic_threshold:
                try:
                    self.on_systemic(name, context)
                except Exception:
                    pass

    def _soft_pass(self, high_failures: List[Failure], context: Any) -> bool:
        if not high_failures or self.soft_policy is None:
            return False
        if not all(f.check in self.soft_checks for f in high_failures):
            return False
        return bool(self.soft_policy(high_failures, context))

    def _ship_fallback(self, fallback: Fallback, context: Any, attempts: int) -> GateResult:
        output = fallback(context)
        failures = self.audit(output, context)
        return GateResult(
            output=output,
            failures=failures,
            attempts=attempts,
            source="fallback",
            verified=not self._high(failures),
        )

    # -- the gate ----------------------------------------------------------

    def run(
        self,
        generate: Generate,
        fallback: Optional[Fallback] = None,
        context: Any = None,
    ) -> GateResult:
        """Generate, audit, retry on HIGH failures, fall back if needed.

        With a fallback, ``run`` never raises: if generation itself crashes
        or HIGH failures survive every retry, the fallback ships (its audit
        result is recorded on the ``GateResult``). Without a fallback,
        unresolvable HIGH failures raise ``GateError`` -- silence is the one
        thing this gate will not do.
        """
        attempts = 0
        failures: List[Failure] = []
        output: Any = None

        for attempt_index in range(self.max_retries + 1):
            attempt = Attempt(index=attempt_index, failures=tuple(failures))
            if attempt.is_retry and self.cooldown > 0:
                self._sleep(self.cooldown)
            try:
                output = generate(attempt, context)
            except Exception as exc:
                if fallback is not None:
                    return self._ship_fallback(fallback, context, attempts + 1)
                raise GateError([Failure(
                    check="generate:crashed", severity=Severity.HIGH,
                    message=str(exc)[:120],
                )]) from exc
            attempts += 1
            failures = self.audit(output, context)
            high = self._high(failures)
            if not high:
                source = "retry" if attempt.is_retry else "primary"
                return GateResult(output=output, failures=failures,
                                  attempts=attempts, source=source, verified=True)
            if attempt_index == 0:
                self._note_systemic(high, context)

        high = self._high(failures)
        if self._soft_pass(high, context):
            return GateResult(output=output, failures=failures,
                              attempts=attempts, source="soft-pass", verified=False)
        if fallback is not None:
            return self._ship_fallback(fallback, context, attempts)
        raise GateError(failures)
