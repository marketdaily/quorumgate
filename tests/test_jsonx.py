import pytest

from councilgate import extract_json


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json():
    text = '```json\n{"stance": "approve", "confidence": 4}\n```'
    assert extract_json(text)["stance"] == "approve"


def test_json_with_surrounding_prose():
    text = 'Sure! Here is the answer:\n{"a": [1, 2]}\nHope that helps.'
    assert extract_json(text) == {"a": [1, 2]}


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json("no structured data here")


def test_malformed_json_raises():
    with pytest.raises(ValueError):
        extract_json('{"a": unquoted}')
