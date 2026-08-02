import pytest

from councilgate import AuditGate, Failure, GateError, Severity, check


@check("must_contain_summary")
def must_contain_summary(output, context):
    return "summary" not in output and "output missing summary section"


@check("too_short", severity=Severity.MED)
def too_short(output, context):
    return len(output) < 20 and "output shorter than 20 chars"


@check("soft_quality", severity=Severity.HIGH)
def soft_quality(output, context):
    return "rich detail" not in output and "quality below bar"


def test_clean_output_ships_first_try():
    gate = AuditGate([must_contain_summary])
    result = gate.run(lambda attempt, ctx: "a fine summary of things")
    assert result.verified and result.source == "primary" and result.attempts == 1


def test_med_failures_ship_without_retry():
    gate = AuditGate([must_contain_summary, too_short])
    result = gate.run(lambda attempt, ctx: "tiny summary")
    assert result.verified
    assert result.source == "primary"
    assert [f.check for f in result.failures] == ["too_short"]


def test_high_failure_retries_then_passes():
    outputs = ["no good section here", "a corrected summary"]

    def generate(attempt, ctx):
        return outputs[attempt.index]

    gate = AuditGate([must_contain_summary])
    result = gate.run(generate)
    assert result.verified and result.source == "retry" and result.attempts == 2


def test_retry_sees_previous_failures():
    seen = []

    def generate(attempt, ctx):
        seen.append((attempt.index, [f.check for f in attempt.failures]))
        return "bad" if attempt.index == 0 else "fixed summary"

    AuditGate([must_contain_summary]).run(generate)
    assert seen[0] == (0, [])
    assert seen[1] == (1, ["must_contain_summary"])


def test_exhausted_retries_trigger_fallback():
    gate = AuditGate([must_contain_summary], max_retries=1)
    result = gate.run(
        lambda attempt, ctx: "never valid",
        fallback=lambda ctx: "deterministic summary template",
    )
    assert result.source == "fallback"
    assert result.verified
    assert result.output == "deterministic summary template"


def test_generate_crash_triggers_fallback():
    def generate(attempt, ctx):
        raise RuntimeError("model unreachable")

    result = AuditGate([must_contain_summary]).run(
        generate, fallback=lambda ctx: "deterministic summary")
    assert result.source == "fallback" and result.verified


def test_no_fallback_raises_gate_error():
    gate = AuditGate([must_contain_summary], max_retries=0)
    with pytest.raises(GateError) as excinfo:
        gate.run(lambda attempt, ctx: "never valid")
    assert any(f.check == "must_contain_summary" for f in excinfo.value.failures)


def test_soft_policy_ships_soft_high_failures():
    gate = AuditGate(
        [must_contain_summary, soft_quality],
        max_retries=0,
        soft_checks={"soft_quality"},
        soft_policy=lambda fails, ctx: ctx == "internal",
    )
    result = gate.run(lambda attempt, ctx: "summary but plain",
                      fallback=lambda ctx: "fallback", context="internal")
    assert result.source == "soft-pass"
    assert result.verified is False

    strict = gate.run(lambda attempt, ctx: "summary but plain",
                      fallback=lambda ctx: "fallback", context="external")
    assert strict.source == "fallback"


def test_hard_high_failure_never_soft_passes():
    gate = AuditGate(
        [must_contain_summary, soft_quality],
        max_retries=0,
        soft_checks={"soft_quality"},
        soft_policy=lambda fails, ctx: True,
    )
    result = gate.run(lambda attempt, ctx: "no good section at all",
                      fallback=lambda ctx: "fallback")
    assert result.source == "fallback"


def test_systemic_escalation_fires_once_at_threshold():
    alerts = []
    gate = AuditGate(
        [must_contain_summary],
        max_retries=0,
        systemic_threshold=3,
        on_systemic=lambda name, ctx: alerts.append((name, ctx)),
    )
    for i in range(5):
        gate.run(lambda attempt, ctx: "always bad",
                 fallback=lambda ctx: "fallback", context=f"item-{i}")
    assert alerts == [("must_contain_summary", "item-2")]


def test_cooldown_waits_before_retry_only():
    naps = []
    gate = AuditGate([must_contain_summary], max_retries=2,
                     cooldown=60, sleep=naps.append)
    gate.run(lambda attempt, ctx: "always bad", fallback=lambda ctx: "fb")
    assert naps == [60, 60]


def test_crashing_check_recorded_not_fatal():
    def broken_check(output, context):
        raise RuntimeError("check bug")

    gate = AuditGate([broken_check])
    result = gate.run(lambda attempt, ctx: "anything")
    assert result.verified  # MED only
    assert any("crashed" in f.check for f in result.failures)


def test_fallback_output_is_still_audited():
    gate = AuditGate([must_contain_summary], max_retries=0)
    result = gate.run(lambda attempt, ctx: "bad",
                      fallback=lambda ctx: "also bad")
    assert result.source == "fallback"
    assert result.verified is False
    assert any(f.check == "must_contain_summary" for f in result.failures)


def test_audit_returns_failures_directly():
    gate = AuditGate([must_contain_summary, too_short])
    fails = gate.audit("short")
    assert {f.check for f in fails} == {"must_contain_summary", "too_short"}
    assert all(isinstance(f, Failure) for f in fails)
