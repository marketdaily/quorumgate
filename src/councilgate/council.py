"""Multi-model council: parallel seats, pluggable arbitration, graceful decay.

A *seat* is any callable that takes a prompt and returns text -- the framework
never imports an LLM SDK. Seats are polled (in parallel by default), their raw
text is parsed into opinions, and an *arbiter* produces the final verdict.

Failure philosophy: a council is an enhancement layer, never a hard
dependency. Seats that error are skipped (and circuit-broken if the failure
looks permanent); if fewer than ``quorum`` opinions survive, the result says
so instead of raising; a wall-clock budget lets long batch runs degrade to
"no council" rather than blow their deadline.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

from .breaker import CircuitBreaker
from .jsonx import extract_json
from .types import CouncilResult, Opinion


@dataclass
class Seat:
    """One model voice in the council.

    ``call``   -- prompt in, raw text out (your LLM client, any SDK).
    ``parse``  -- raw text -> opinion content. Defaults to ``extract_json``.
                  Use ``parse=str`` (or any callable) for free-text councils.
    ``stance_of`` / ``confidence_of`` -- optional per-seat extractors that
    override the council-level ones.
    """

    name: str
    call: Callable[[str], str]
    parse: Callable[[str], Any] = field(default=extract_json)
    stance_of: Optional[Callable[[Any], Optional[str]]] = None
    confidence_of: Optional[Callable[[Any], float]] = None


class Arbiter:
    """Strategy that turns a list of opinions into a verdict."""

    def arbitrate(self, opinions: List[Opinion]) -> Any:  # pragma: no cover
        raise NotImplementedError


class HighestConfidenceArbiter(Arbiter):
    """Verdict = content of the most confident opinion."""

    def arbitrate(self, opinions: List[Opinion]) -> Any:
        return max(opinions, key=lambda o: o.confidence).content


class MajorityArbiter(Arbiter):
    """Verdict = the most common stance (ties broken by summed confidence)."""

    def arbitrate(self, opinions: List[Opinion]) -> Any:
        tally: dict = {}
        for o in opinions:
            key = o.stance
            score = tally.setdefault(key, [0, 0.0])
            score[0] += 1
            score[1] += o.confidence
        return max(tally.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]


class JudgeArbiter(Arbiter):
    """An LLM judge synthesizes the final verdict from all opinions.

    ``build_prompt`` turns the opinions into the judge's prompt; ``call`` is
    the judge model; ``parse`` turns its raw reply into the verdict. If the
    judge fails for any reason, ``fallback`` (default: highest confidence
    opinion) decides instead -- the council never dies with its judge.
    """

    def __init__(
        self,
        call: Callable[[str], str],
        build_prompt: Callable[[List[Opinion]], str],
        parse: Callable[[str], Any] = extract_json,
        fallback: Optional[Arbiter] = None,
    ):
        self.call = call
        self.build_prompt = build_prompt
        self.parse = parse
        self.fallback = fallback or HighestConfidenceArbiter()

    def arbitrate(self, opinions: List[Opinion]) -> Any:
        try:
            return self.parse(self.call(self.build_prompt(opinions)))
        except Exception:
            return self.fallback.arbitrate(opinions)


def _default_stance_of(content: Any) -> Optional[str]:
    if isinstance(content, dict):
        v = content.get("stance")
        return None if v is None else str(v)
    return None


def _default_confidence_of(content: Any) -> float:
    if isinstance(content, dict):
        try:
            return float(content.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


class Council:
    """Collect opinions from seats, then arbitrate.

    Parameters
    ----------
    seats : the model voices. At least one.
    arbiter : arbitration strategy; default ``HighestConfidenceArbiter``.
    quorum : minimum surviving opinions for a valid verdict (default 2).
    breaker : shared ``CircuitBreaker``; a fresh one is created if omitted.
    parallel : poll seats concurrently (default True).
    time_budget : optional wall-clock seconds shared by *all* ``convene``
        calls on this instance. Once exhausted, further rounds return
        immediately with ``budget_exhausted=True`` so batch callers can fall
        back to their single-model path instead of missing their deadline.
    stance_of / confidence_of : extract a stance string / confidence float
        from opinion content; defaults read ``content["stance"]`` and
        ``content["confidence"]`` when content is a dict.
    opposed : pairs of stances considered head-on conflicts, e.g.
        ``[("approve", "reject")]``. Dissent is 0 when all stances agree, 2
        when an opposed pair is present, 1 otherwise.
    """

    def __init__(
        self,
        seats: Sequence[Seat],
        arbiter: Optional[Arbiter] = None,
        quorum: int = 2,
        breaker: Optional[CircuitBreaker] = None,
        parallel: bool = True,
        time_budget: Optional[float] = None,
        stance_of: Callable[[Any], Optional[str]] = _default_stance_of,
        confidence_of: Callable[[Any], float] = _default_confidence_of,
        opposed: Iterable[Tuple[str, str]] = (),
        clock: Callable[[], float] = time.monotonic,
    ):
        if not seats:
            raise ValueError("council needs at least one seat")
        self.seats = list(seats)
        self.arbiter = arbiter or HighestConfidenceArbiter()
        self.quorum = max(1, quorum)
        self.breaker = breaker or CircuitBreaker()
        self.parallel = parallel
        self.time_budget = time_budget
        self.stance_of = stance_of
        self.confidence_of = confidence_of
        self.opposed = [frozenset(p) for p in opposed]
        self._clock = clock
        self._deadline: Optional[float] = None

    # -- internals ---------------------------------------------------------

    def _poll_seat(self, seat: Seat, prompt: str):
        if self.breaker.is_open(seat.name):
            return seat.name, None, "circuit open"
        try:
            content = seat.parse(seat.call(prompt))
        except Exception as exc:
            self.breaker.record_failure(seat.name, exc)
            return seat.name, None, str(exc)[:120]
        self.breaker.record_success(seat.name)
        stance_of = seat.stance_of or self.stance_of
        confidence_of = seat.confidence_of or self.confidence_of
        opinion = Opinion(
            seat=seat.name,
            content=content,
            stance=stance_of(content),
            confidence=confidence_of(content),
        )
        return seat.name, opinion, None

    def _dissent(self, opinions: List[Opinion]) -> int:
        stances = {o.stance for o in opinions if o.stance is not None}
        if len(stances) <= 1:
            return 0
        for pair in self.opposed:
            if pair <= stances:
                return 2
        return 1

    # -- public API --------------------------------------------------------

    def convene(self, prompt: str) -> CouncilResult:
        """Run one council round for ``prompt``."""
        if self.time_budget is not None:
            if self._deadline is None:
                self._deadline = self._clock() + self.time_budget
            elif self._clock() > self._deadline:
                return CouncilResult(budget_exhausted=True)

        result = CouncilResult()
        if self.parallel and len(self.seats) > 1:
            with ThreadPoolExecutor(max_workers=len(self.seats)) as pool:
                polled = list(pool.map(lambda s: self._poll_seat(s, prompt), self.seats))
        else:
            polled = [self._poll_seat(s, prompt) for s in self.seats]

        for name, opinion, error in polled:
            if opinion is not None:
                result.opinions.append(opinion)
            elif error is not None:
                result.seat_errors[name] = error

        if len(result.opinions) < self.quorum:
            return result

        result.quorum_met = True
        result.dissent = self._dissent(result.opinions)
        result.verdict = self.arbiter.arbitrate(result.opinions)
        return result
