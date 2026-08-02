# quorumgate

**Never ship unverified LLM output.**

`quorumgate` is a zero-dependency reliability layer for LLM pipelines. It was
distilled from a production system that emails AI-generated reports to real
subscribers every day, where a hallucinated number or a half-rendered template
is not a bug ticket — it lands in someone's inbox. The rules that system lives
by are the rules this library encodes:

1. **Every output is audited before it ships.** Checks are graded
   HIGH / MED / LOW. HIGH means *do not ship this*.
2. **Failure earns a bounded retry, not a shrug.** The retry sees exactly
   which checks failed, so it can switch to a stronger model or a tighter
   prompt instead of rolling the same dice again.
3. **The last line of defense is deterministic.** When generation cannot pass
   the gate, a template built without any LLM ships instead — degraded, but
   never wrong, and never silent.
4. **Ensembles must degrade gracefully.** A multi-model council is an
   enhancement, not a dependency: dead seats get circuit-broken, quorum
   failures are reported instead of raised, and a wall-clock budget stops a
   sick provider from blowing your deadline.

No LLM SDK is imported anywhere. A "model" is any `Callable[[str], str]` you
provide — OpenAI, Anthropic, Gemini, Ollama, a local server, or a lambda in a
test. The framework supplies structure; you supply models, checks, and
fallbacks.

```
pip install quorumgate-llm      # stdlib only, Python >= 3.9
```

## Quickstart

```python
from quorumgate import AuditGate, check, Severity

@check("no_invented_links")
def no_invented_links(output, context):
    if "http" in output and "http" not in context["source"]:
        return "summary contains a URL that is not in the source"

@check("long_enough")
def long_enough(output, context):
    return len(output.split()) < 15 and "summary under 15 words"

def summarize(attempt, context):
    model = strong_model if attempt.is_retry else cheap_model   # your callables
    return model(f"Summarize:\n{context['source']}")

def first_sentences(context):                # deterministic: never wrong
    return " ".join(context["source"].split(". ")[:2]) + "."

gate = AuditGate([no_invented_links, long_enough], max_retries=1, cooldown=60)
result = gate.run(summarize, fallback=first_sentences, context={"source": text})

result.output    # what ships — always
result.source    # "primary" | "retry" | "soft-pass" | "fallback"
result.verified  # True unless HIGH failures survived (fallback is audited too)
```

With a fallback, `gate.run` **never raises** — the deadline philosophy is
that shipping the deterministic version beats shipping nothing, and both beat
shipping something wrong. Without a fallback, unresolvable HIGH failures
raise `GateError`, because silence is the one thing the gate will not do.

### A multi-model council

```python
import json
from quorumgate import Council, Seat, JudgeArbiter, CircuitBreaker

council = Council(
    seats=[
        Seat("gemini-flash", call=my_gemini),      # each: prompt -> raw text
        Seat("gpt-mini",     call=my_openai),
        Seat("local-qwen",   call=my_ollama),
    ],
    arbiter=JudgeArbiter(                          # an LLM judge synthesizes...
        call=my_judge_model,
        build_prompt=lambda ops: "Synthesize one verdict from:\n"
                                 + json.dumps([o.content for o in ops]),
    ),                                             # ...and if the judge dies,
    quorum=2,                                      # highest-confidence wins
    breaker=CircuitBreaker(max_strikes=3, dead_markers=("quota", "402")),
    time_budget=600,                               # whole batch, wall-clock
    opposed=[("long", "short")],                   # head-on conflict = dissent 2
)

verdict = council.convene("Assess the 1-2 week outlook for ACME.")
verdict.quorum_met   # False -> use your single-model path, don't crash
verdict.dissent      # 0 unanimous / 1 mixed / 2 head-on conflict
verdict.verdict      # the arbiter's synthesis
```

Seats parse their own replies (default: tolerant JSON extraction that strips
markdown fences); arbitration is a strategy object (`JudgeArbiter`,
`MajorityArbiter`, `HighestConfidenceArbiter`, or your own). Disagreement is
a first-class signal: pass `dissent` downstream so a split council produces a
hedged output, not false confidence.

### Composed

```python
from quorumgate import Pipeline

pipeline = Pipeline(
    generate=write_report,          # (attempt, context, council_result) -> str
    gate=gate,
    council=council,
    council_prompt=lambda ctx: f"Debate the outlook for {ctx['topic']}.",
    fallback=deterministic_report,
)
result = pipeline.run({"topic": "solar"})
```

Two runnable, key-free demos live in [`examples/`](examples/):
[`daily_brief.py`](examples/daily_brief.py) (council + gate + fallback +
systemic alerting across a subscriber batch) and
[`summarize.py`](examples/summarize.py) (escalate-on-retry summarization).

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │                Pipeline                 │
                    │                                         │
  context ──────►  Council (optional)                         │
                    │  seats (parallel) ──► parse ──► quorum? │
                    │    │ per-seat CircuitBreaker            │
                    │    │ wall-clock budget                  │
                    │    ▼                                    │
                    │  Arbiter (judge / majority / custom)    │
                    │    │ judge fails -> fallback arbiter    │
                    │    ▼ verdict + dissent                  │
                    │                                         │
                    │  generate(attempt, ctx, verdict)        │
                    │    │                                    │
                    │    ▼                                    │
                    │  AuditGate ── checks (HIGH/MED/LOW)     │
                    │    │ HIGH? -> cooldown -> retry         │
                    │    │         (attempt carries failures) │
                    │    │ still HIGH?                        │
                    │    │   ├─ all soft + policy ok -> ship  │
                    │    │   ├─ fallback() -> audit -> ship   │
                    │    │   └─ no fallback -> GateError      │
                    │    ▼                                    │
                    │  GateResult(output, source, verified)   │
                    └─────────────────────────────────────────┘
```

### Production extras you will eventually want

- **Soft HIGH checks** — some checks gate *quality* ("reasoning too
  shallow"), not *correctness* ("price is fabricated"). Mark them
  `soft_checks` and supply a `soft_policy` to decide when a below-bar-but-
  not-wrong output may ship (e.g. to an internal audience) instead of
  degrading to the fallback.
- **Systemic escalation** — when the same HIGH check fails across
  `systemic_threshold` independent `run()` calls, that is not per-item bad
  luck; it is a template or prompt bug hitting your whole batch. `on_systemic`
  fires exactly once per check so a human is paged *before* the batch
  finishes, not after.
- **Retry cooldown** — free-tier rate limits are usually per-minute windows.
  An instant retry lands in the same window and fails the same way;
  `cooldown=60` makes the retry actually mean something.
- **Circuit breaker dead-markers** — "quota exceeded" and "payment required"
  are not transient. Substring markers open the seat on the first strike
  instead of paying for three.

## What this is not

`quorumgate` is a **reliability layer**, not an orchestration framework.

| | LangChain / LlamaIndex / DSPy | quorumgate |
|---|---|---|
| Core question | *How do I chain LLM calls together?* | *How do I make sure a bad LLM output never reaches a user?* |
| LLM clients | Bundled integrations | None — you inject callables |
| Dependencies | Many | Zero (stdlib only) |
| Prompting | Templates, optimizers | Your problem, on purpose |
| Failure model | Exceptions / callbacks | Graded checks → retry → deterministic fallback, never silent |
| Ensembles | Chains/graphs of calls | Council with quorum, circuit breaking, budget, dissent signal |

Use both if you like: build your chain in anything, then put its final output
behind an `AuditGate`. The gate does not care who generated the text.

Similarly, this is not a guardrails DSL — there is no YAML, no built-in
toxicity classifier, no schema language. A check is a Python function over
`(output, context)`, because in practice the checks that save you are
domain-specific ones nobody could have shipped in a library.

## API surface

| Object | Role |
|---|---|
| `Seat(name, call, parse?)` | One model voice: prompt → raw text → parsed opinion |
| `Council(seats, arbiter, quorum, breaker, time_budget, opposed)` | Parallel collection + arbitration + graceful decay |
| `Arbiter` / `JudgeArbiter` / `MajorityArbiter` / `HighestConfidenceArbiter` | Pluggable arbitration strategies |
| `CircuitBreaker(max_strikes, dead_markers)` | Skip dead providers for the rest of a run |
| `@check(name, severity)` | Predicate → graded check |
| `AuditGate(checks, max_retries, cooldown, soft_checks, soft_policy, systemic_threshold, on_systemic)` | Verify → retry → fallback; never ship unverified |
| `Pipeline(generate, gate, council?, fallback?)` | The common composition in one call |
| `extract_json(text)` | Tolerant JSON-from-LLM-text helper |
| `GateResult` / `CouncilResult` / `Failure` / `Attempt` / `Severity` | Typed results end to end |

## Testing

```
pip install -e ".[dev]"
pytest
```

The suite covers council arbitration (majority, judge, judge-death fallback,
quorum, dissent, budget exhaustion), gate behavior (retry, fallback, soft
policy, systemic escalation, crashing checks), and the circuit breaker —
all with plain callables, no network.

## License

MIT © Delvin Chang
