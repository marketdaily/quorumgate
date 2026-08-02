"""A daily-briefing pipeline with a mock multi-model council.

Mirrors a production shape: for each subscriber, a council of models debates
the day's topics, a judge synthesizes a thesis, a generator writes the brief,
and an audit gate guarantees that what ships is verified -- or the
deterministic template ships instead. Run it:

    python examples/daily_brief.py

No API keys needed; the "models" are canned callables. Swap them for real
clients (OpenAI, Anthropic, Gemini, a local server...) by changing only the
``call=`` lambdas.
"""
import json
import random

from quorumgate import (
    AuditGate,
    CircuitBreaker,
    Council,
    JudgeArbiter,
    Pipeline,
    Seat,
    check,
)

TOPICS = ["renewable storage", "edge computing", "vertical farming"]
SUBSCRIBERS = ["alice", "bob", "carol"]


# --- mock model seats -------------------------------------------------------
# Each "model" returns a JSON opinion. One seat is dead (quota exhausted) to
# show the circuit breaker keeping it from slowing down the whole batch.

def optimist_model(prompt: str) -> str:
    return json.dumps({
        "stance": "expand",
        "confidence": 4,
        "thesis": "demand signals keep strengthening",
        "risk": "supply chains stay tight",
    })


def skeptic_model(prompt: str) -> str:
    return json.dumps({
        "stance": "hold",
        "confidence": 3,
        "thesis": "valuations already price in the growth",
        "risk": "a demand miss would reprice everything",
    })


def dead_model(prompt: str) -> str:
    raise RuntimeError("429 quota exceeded for today")


def judge_model(prompt: str) -> str:
    return json.dumps({
        "thesis": "expansion is real but already partly priced in",
        "risk": "watch for the first demand miss",
    })


council = Council(
    seats=[
        Seat("optimist", call=optimist_model),
        Seat("skeptic", call=skeptic_model),
        Seat("flaky", call=dead_model),
    ],
    arbiter=JudgeArbiter(
        call=judge_model,
        build_prompt=lambda ops: (
            "Synthesize one thesis and one risk from these opinions. "
            "Reply as JSON {\"thesis\": ..., \"risk\": ...}:\n"
            + json.dumps([o.content for o in ops])
        ),
    ),
    quorum=2,
    breaker=CircuitBreaker(max_strikes=3, dead_markers=("quota exceeded",)),
    opposed=[("expand", "contract")],
)


# --- audit checks -----------------------------------------------------------

@check("has_all_sections")
def has_all_sections(output, context):
    missing = [s for s in ("## Thesis", "## Risk", "## Action") if s not in output]
    return missing and f"missing sections: {', '.join(missing)}"


@check("no_placeholder_text")
def no_placeholder_text(output, context):
    for token in ("TODO", "N/A", "[insert", "???"):
        if token in output:
            return f"placeholder text found: {token!r}"
    return None


@check("addresses_subscriber")
def addresses_subscriber(output, context):
    return context["subscriber"] not in output and "greeting missing subscriber name"


# --- generation + deterministic fallback ------------------------------------

def generate_brief(attempt, context, council_result):
    """The 'LLM writer'. On retry it would normally switch to a stronger
    model -- here we simulate a flaky writer that sometimes drops a section."""
    verdict = council_result.verdict if council_result and council_result.quorum_met else {}
    thesis = verdict.get("thesis", "no council consensus today")
    risk = verdict.get("risk", "n/a")
    caution = " (council split: keep sizing conservative)" if (
        council_result and council_result.dissent >= 2) else ""
    sections = [
        f"Hello {context['subscriber']}, your brief on {context['topic']}:",
        f"## Thesis\n{thesis}{caution}",
        f"## Risk\n{risk}",
        "## Action\nRe-read last week's numbers before adding exposure.",
    ]
    if attempt.index == 0 and random.random() < 0.5:
        sections.pop(2)  # flaky first draft: drops the Risk section
    return "\n\n".join(sections)


def deterministic_brief(context):
    """No LLM involved: correct by construction, so it can always ship."""
    return "\n\n".join([
        f"Hello {context['subscriber']}, your brief on {context['topic']}:",
        "## Thesis\nAutomated analysis was unavailable; here are the raw facts on file.",
        "## Risk\nWithout fresh analysis, treat any move as higher risk than usual.",
        "## Action\nNo action recommended today.",
    ])


pipeline = Pipeline(
    generate=generate_brief,
    gate=AuditGate(
        [has_all_sections, no_placeholder_text, addresses_subscriber],
        max_retries=1,
        systemic_threshold=3,
        on_systemic=lambda chk, ctx: print(
            f"  [ALERT] check {chk!r} failed for 3 subscribers -- template bug likely"),
    ),
    council=council,
    council_prompt=lambda ctx: f"Debate the 1-2 week outlook for {ctx['topic']}.",
    fallback=deterministic_brief,
)


if __name__ == "__main__":
    random.seed(7)
    for subscriber, topic in zip(SUBSCRIBERS, TOPICS):
        result = pipeline.run({"subscriber": subscriber, "topic": topic})
        print(f"--- {subscriber} | source={result.source} | attempts={result.attempts} "
              f"| verified={result.verified}")
        print(result.output)
        print()
    print("open seats:", council.breaker.open_reasons)
