import json

import pytest

from councilgate import (
    AuditGate,
    Council,
    JudgeArbiter,
    Pipeline,
    Seat,
    check,
)


@check("mentions_topic")
def mentions_topic(output, context):
    return context["topic"] not in output and "output never mentions the topic"


def make_council():
    seats = [
        Seat("optimist", call=lambda p: json.dumps(
            {"stance": "grow", "confidence": 4, "point": "adoption is rising"})),
        Seat("skeptic", call=lambda p: json.dumps(
            {"stance": "stall", "confidence": 3, "point": "costs are rising too"})),
    ]
    judge = JudgeArbiter(
        call=lambda p: '{"thesis": "growth with cost pressure"}',
        build_prompt=lambda ops: json.dumps([o.content for o in ops]),
    )
    return Council(seats, arbiter=judge, opposed=[("grow", "stall")])


def test_pipeline_council_feeds_generation():
    def generate(attempt, context, council_result):
        thesis = council_result.verdict["thesis"]
        return f"Report on {context['topic']}: {thesis} (dissent={council_result.dissent})"

    pipeline = Pipeline(
        generate=generate,
        gate=AuditGate([mentions_topic]),
        council=make_council(),
        council_prompt=lambda ctx: f"Assess {ctx['topic']}",
    )
    result = pipeline.run({"topic": "solar"})
    assert result.verified
    assert "growth with cost pressure" in result.output
    assert "dissent=2" in result.output


def test_pipeline_without_council():
    def generate(attempt, context, council_result):
        assert council_result is None
        return f"plain report on {context['topic']}"

    result = Pipeline(generate=generate, gate=AuditGate([mentions_topic])).run(
        {"topic": "wind"})
    assert result.verified


def test_pipeline_falls_back_when_generation_stays_bad():
    pipeline = Pipeline(
        generate=lambda attempt, context, council_result: "off-topic drivel",
        gate=AuditGate([mentions_topic], max_retries=1),
        fallback=lambda ctx: f"template report on {ctx['topic']}",
    )
    result = pipeline.run({"topic": "hydro"})
    assert result.source == "fallback"
    assert result.verified


def test_pipeline_requires_prompt_builder_with_council():
    with pytest.raises(ValueError):
        Pipeline(
            generate=lambda a, c, r: "x",
            gate=AuditGate([]),
            council=make_council(),
        )
