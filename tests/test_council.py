import json

import pytest

from quorumgate import (
    CircuitBreaker,
    Council,
    HighestConfidenceArbiter,
    JudgeArbiter,
    MajorityArbiter,
    Seat,
)


def seat_returning(payload, name="seat"):
    return Seat(name=name, call=lambda p: json.dumps(payload))


def failing_seat(name="dead", message="boom"):
    def call(prompt):
        raise RuntimeError(message)
    return Seat(name=name, call=call)


def test_quorum_not_met():
    council = Council([seat_returning({"stance": "a"}, "s1"), failing_seat("s2")], quorum=2)
    result = council.convene("q")
    assert result.quorum_met is False
    assert result.verdict is None
    assert "s2" in result.seat_errors


def test_majority_arbiter():
    seats = [
        seat_returning({"stance": "approve", "confidence": 2}, "s1"),
        seat_returning({"stance": "approve", "confidence": 3}, "s2"),
        seat_returning({"stance": "reject", "confidence": 5}, "s3"),
    ]
    result = Council(seats, arbiter=MajorityArbiter()).convene("q")
    assert result.quorum_met
    assert result.verdict == "approve"


def test_highest_confidence_arbiter():
    seats = [
        seat_returning({"stance": "a", "confidence": 1, "note": "weak"}, "s1"),
        seat_returning({"stance": "b", "confidence": 5, "note": "strong"}, "s2"),
    ]
    result = Council(seats, arbiter=HighestConfidenceArbiter()).convene("q")
    assert result.verdict["note"] == "strong"


def test_judge_arbiter_synthesizes():
    seats = [
        seat_returning({"stance": "a", "confidence": 2}, "s1"),
        seat_returning({"stance": "b", "confidence": 3}, "s2"),
    ]
    seen_prompts = []

    def judge_call(prompt):
        seen_prompts.append(prompt)
        return '{"final": "synthesized"}'

    judge = JudgeArbiter(
        call=judge_call,
        build_prompt=lambda ops: "opinions: " + json.dumps(
            [o.content for o in ops]),
    )
    result = Council(seats, arbiter=judge).convene("q")
    assert result.verdict == {"final": "synthesized"}
    assert "opinions:" in seen_prompts[0]


def test_judge_failure_falls_back_to_highest_confidence():
    seats = [
        seat_returning({"stance": "a", "confidence": 1}, "s1"),
        seat_returning({"stance": "b", "confidence": 4, "id": "best"}, "s2"),
    ]

    def broken_judge(prompt):
        raise RuntimeError("judge quota exhausted")

    judge = JudgeArbiter(call=broken_judge, build_prompt=lambda ops: "x")
    result = Council(seats, arbiter=judge).convene("q")
    assert result.verdict["id"] == "best"


def test_dissent_levels():
    unanimous = Council(
        [seat_returning({"stance": "a"}, "s1"), seat_returning({"stance": "a"}, "s2")],
        quorum=2)
    assert unanimous.convene("q").dissent == 0

    mild = Council(
        [seat_returning({"stance": "a"}, "s1"), seat_returning({"stance": "n"}, "s2")],
        opposed=[("a", "b")])
    assert mild.convene("q").dissent == 1

    headon = Council(
        [seat_returning({"stance": "a"}, "s1"), seat_returning({"stance": "b"}, "s2")],
        opposed=[("a", "b")])
    assert headon.convene("q").dissent == 2


def test_breaker_skips_open_seat():
    calls = {"n": 0}

    def counting_dead_call(prompt):
        calls["n"] += 1
        raise RuntimeError("quota exceeded")

    breaker = CircuitBreaker(max_strikes=3, dead_markers=("quota exceeded",))
    council = Council(
        [Seat("dead", counting_dead_call),
         seat_returning({"stance": "a"}, "s1"),
         seat_returning({"stance": "a"}, "s2")],
        breaker=breaker, quorum=2)
    council.convene("q1")
    council.convene("q2")
    assert calls["n"] == 1  # second round skipped the open seat
    assert breaker.is_open("dead")


def test_time_budget_exhaustion():
    now = {"t": 0.0}
    seats = [seat_returning({"stance": "a"}, "s1"), seat_returning({"stance": "a"}, "s2")]
    council = Council(seats, time_budget=10.0, clock=lambda: now["t"])
    first = council.convene("q1")
    assert first.quorum_met
    now["t"] = 11.0
    second = council.convene("q2")
    assert second.budget_exhausted is True
    assert second.opinions == []


def test_sequential_mode_matches_parallel():
    seats = [
        seat_returning({"stance": "a", "confidence": 1}, "s1"),
        seat_returning({"stance": "a", "confidence": 2}, "s2"),
    ]
    seq = Council(seats, parallel=False).convene("q")
    par = Council(seats, parallel=True).convene("q")
    assert [o.seat for o in seq.opinions] == [o.seat for o in par.opinions]


def test_free_text_seats_with_custom_parse():
    seats = [
        Seat("s1", call=lambda p: "short summary", parse=str,
             confidence_of=lambda c: float(len(c))),
        Seat("s2", call=lambda p: "a much longer and richer summary", parse=str,
             confidence_of=lambda c: float(len(c))),
    ]
    result = Council(seats, arbiter=HighestConfidenceArbiter()).convene("q")
    assert result.verdict == "a much longer and richer summary"


def test_empty_seats_rejected():
    with pytest.raises(ValueError):
        Council([])
