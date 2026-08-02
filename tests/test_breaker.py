from councilgate import CircuitBreaker


def test_opens_after_max_strikes():
    br = CircuitBreaker(max_strikes=3)
    assert br.record_failure("seat", RuntimeError("timeout")) is False
    assert br.record_failure("seat", RuntimeError("timeout")) is False
    assert br.record_failure("seat", RuntimeError("timeout")) is True
    assert br.is_open("seat")


def test_dead_marker_opens_immediately():
    br = CircuitBreaker(max_strikes=3, dead_markers=("quota exceeded", "402"))
    assert br.record_failure("seat", RuntimeError("HTTP 402 Payment Required")) is True
    assert br.is_open("seat")
    assert "402" in br.open_reasons["seat"]


def test_success_resets_strikes():
    br = CircuitBreaker(max_strikes=2)
    br.record_failure("seat", RuntimeError("flake"))
    br.record_success("seat")
    assert br.record_failure("seat", RuntimeError("flake")) is False
    assert not br.is_open("seat")


def test_reset_clears_open_seats():
    br = CircuitBreaker(max_strikes=1)
    br.record_failure("seat", RuntimeError("dead"))
    assert br.is_open("seat")
    br.reset()
    assert not br.is_open("seat")


def test_open_seat_stays_open():
    br = CircuitBreaker(max_strikes=1)
    br.record_failure("seat", RuntimeError("dead"))
    assert br.record_failure("seat", RuntimeError("later")) is True
    assert "dead" in br.open_reasons["seat"]
