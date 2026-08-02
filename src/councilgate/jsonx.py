"""Tolerant JSON extraction from LLM text output.

Models routinely wrap JSON in markdown fences or surround it with prose.
``extract_json`` strips fences and pulls the outermost ``{...}`` object.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_OPEN = re.compile(r"^```[a-zA-Z0-9_-]*\n?")
_FENCE_CLOSE = re.compile(r"\n?```\s*$")


def extract_json(text: str) -> Any:
    """Extract and parse the first top-level JSON object found in ``text``.

    Raises ``ValueError`` if no JSON object can be found or parsed.
    """
    raw = text.strip()
    raw = _FENCE_OPEN.sub("", raw)
    raw = _FENCE_CLOSE.sub("", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found in text")
    return json.loads(raw[start:end + 1])
