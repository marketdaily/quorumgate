"""Gated summarization: retry with a stronger model, else refuse to guess.

The gate contract in one file: the cheap model tries first; HIGH failures
send the retry to the stronger model (the ``Attempt`` object tells you it is
a retry and why); if even that fails, a deterministic extract ships. Run it:

    python examples/summarize.py
"""
from councilgate import AuditGate, Severity, check

ARTICLE = (
    "The city council approved the riverside redevelopment plan on Tuesday. "
    "Construction of the first phase, a public promenade, begins in March. "
    "Funding comes from a bond measure passed last year. Local businesses "
    "voiced concerns about parking during construction."
)


# --- two mock models: cheap first, strong on retry --------------------------

def cheap_model(prompt: str) -> str:
    # Cuts corners: too short, and invents a URL the source never contained.
    return "Riverside plan approved. Details at https://example.com/plan."


def strong_model(prompt: str) -> str:
    return (
        "The city council approved the riverside redevelopment plan; the "
        "first phase, a public promenade funded by last year's bond measure, "
        "starts construction in March, while local businesses worry about "
        "parking during the work."
    )


# --- checks -----------------------------------------------------------------

@check("no_invented_links")
def no_invented_links(output, context):
    if "http" in output and "http" not in context["source"]:
        return "summary contains a URL that is not in the source"
    return None


@check("long_enough")
def long_enough(output, context):
    return len(output.split()) < 15 and "summary under 15 words"


@check("mentions_funding", severity=Severity.MED)
def mentions_funding(output, context):
    return "bond" not in output.lower() and "funding source omitted (nice to have)"


def summarize(attempt, context):
    model = strong_model if attempt.is_retry else cheap_model
    if attempt.is_retry:
        failed = ", ".join(f.check for f in attempt.failures)
        print(f"  retry with strong model (previous failures: {failed})")
    return model(f"Summarize in 2 sentences:\n{context['source']}")


def first_sentences(context):
    """Deterministic fallback: the lead sentences, verbatim. Never wrong."""
    return " ".join(context["source"].split(". ")[:2]) + "."


if __name__ == "__main__":
    gate = AuditGate([no_invented_links, long_enough, mentions_funding],
                     max_retries=1)
    result = gate.run(summarize, fallback=first_sentences,
                      context={"source": ARTICLE})
    print(f"source={result.source} attempts={result.attempts} verified={result.verified}")
    for f in result.failures:
        print(f"  note: {f}")
    print(result.output)
