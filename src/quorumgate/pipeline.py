"""Pipeline: compose an optional council with a mandatory gate.

This is deliberately thin. ``Council`` enriches, ``AuditGate`` protects;
the pipeline just wires ``council verdict -> generate -> gate`` so the
common shape is one call. Anything fancier belongs in your own code.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .council import Council
from .gate import AuditGate, Fallback
from .types import Attempt, CouncilResult, GateResult


class Pipeline:
    """One item end-to-end: (optional) council round, then gated generation.

    Parameters
    ----------
    generate : ``(attempt, context, council_result) -> output``. When no
        council is configured, ``council_result`` is None.
    gate : the ``AuditGate`` every output must pass.
    council : optional ``Council`` consulted once per ``run``.
    council_prompt : builds the council prompt from the context. Required
        when ``council`` is given.
    fallback : deterministic fallback forwarded to the gate.
    """

    def __init__(
        self,
        generate: Callable[[Attempt, Any, Optional[CouncilResult]], Any],
        gate: AuditGate,
        council: Optional[Council] = None,
        council_prompt: Optional[Callable[[Any], str]] = None,
        fallback: Optional[Fallback] = None,
    ):
        if council is not None and council_prompt is None:
            raise ValueError("council requires council_prompt")
        self.generate = generate
        self.gate = gate
        self.council = council
        self.council_prompt = council_prompt
        self.fallback = fallback

    def run(self, context: Any = None) -> GateResult:
        council_result: Optional[CouncilResult] = None
        if self.council is not None:
            council_result = self.council.convene(self.council_prompt(context))

        def gated_generate(attempt: Attempt, ctx: Any) -> Any:
            return self.generate(attempt, ctx, council_result)

        return self.gate.run(gated_generate, fallback=self.fallback, context=context)
