from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse

from tweetnook.core import fastjson

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "web_raw_tweets.json"


@pytest.mark.parametrize(
    "document",
    [
        '{"text": "caf\\u00e9 \\ud83d\\udc26", "n": [1, 2.5, -3e-5], "ok": true, "none": null}',
        '[{"nested": {"deep": ["x", {"y": 18446744073709551615}]}}]',
        "  [1, 2]  ",
        '"plain"',
    ],
)
def test_loads_matches_the_standard_library(document: str) -> None:
    assert fastjson.loads(document) == json.loads(document)
    assert fastjson.loads(document.encode()) == json.loads(document)


def test_loads_falls_back_for_values_only_the_standard_library_accepts() -> None:
    parsed = fastjson.loads('{"big": 1180591620717411303424, "nan": NaN, "inf": -Infinity}')

    assert parsed["big"] == 2**70
    assert math.isnan(parsed["nan"]) and parsed["inf"] == -math.inf


# 2**70 + 1 and its neighbours, which a float can't hold; each is checked against the literal.
WIDE = 1180591620717411303425


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (f'{{"big":{WIDE}}}', {"big": WIDE}),
        (f'{{"big": -{WIDE}}}', {"big": -WIDE}),
        (f"{WIDE}", WIDE),
        (f"[1, {WIDE}]", [1, WIDE]),
        (f'{{"a":{{"b":[{{"c":{WIDE}}}]}}}}', {"a": {"b": [{"c": WIDE}]}}),
        ('{"big":18446744073709551616}', {"big": 18446744073709551616}),
        ('{"big":-9223372036854775809}', {"big": -9223372036854775809}),
    ],
)
def test_loads_keeps_wide_integers_exactly(document: str, expected: object) -> None:
    for form in (document, document.encode()):
        parsed = fastjson.loads(form)
        assert parsed == expected
        assert "float" not in repr(type(parsed))
        assert "e+" not in repr(parsed)


def test_id_strings_and_status_urls_stay_on_the_fast_path(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        pytest.fail("a document without a wide number left the accelerated parser")

    monkeypatch.setattr(fastjson.json, "loads", unexpected)

    parsed = fastjson.loads(
        '{"id_str":"1234567890123456789",'
        '"url":"https://x.com/a/status/1234567890123456789/photo/1","n":123456789012345678}'
    )

    assert parsed["n"] == 123456789012345678


@pytest.mark.parametrize(("value", "error"), [("not json", ValueError), (None, TypeError)])
def test_loads_raises_the_standard_library_errors(value: object, error: type) -> None:
    with pytest.raises(error):
        fastjson.loads(value)  # type: ignore[arg-type]
    with pytest.raises(json.JSONDecodeError):
        fastjson.loads("{")


def test_dumps_renders_like_json_response_for_tweet_payloads() -> None:
    tweets = json.loads(FIXTURE.read_text())["full"]
    payload = {"tweets": tweets, "total": 6, "next_cursor": None, "note": "é 🐦 <b>&"}

    assert fastjson.dumps(payload) == JSONResponse(payload).body


def test_dumps_falls_back_for_values_orjson_cannot_encode() -> None:
    assert fastjson.dumps({"big": 2**70}) == b'{"big":1180591620717411303424}'
    assert fastjson.dumps({1: "a"}) == JSONResponse({1: "a"}).body
    # Documented difference: a non-finite number becomes null instead of an error.
    assert fastjson.dumps({"score": math.nan}) == b'{"score":null}'


@pytest.mark.parametrize("extra", [{"big": 2**70}, {1: "integer key"}])
def test_fallback_keeps_nonfinite_values_null_without_changing_input(extra):
    payload = {"scores": [math.nan, {"positive": math.inf, "negative": -math.inf}], **extra}
    decoded = json.loads(fastjson.dumps(payload))

    assert decoded["scores"] == [None, {"positive": None, "negative": None}]
    if "big" in extra:
        assert decoded["big"] == 1180591620717411303424
    else:
        assert decoded["1"] == "integer key"
    assert math.isnan(payload["scores"][0])
    assert payload["scores"][1]["positive"] == math.inf


def test_fallback_handles_nested_tuples_and_preserves_non_numeric_text():
    payload = {1: (math.nan, {"text": "NaN Infinity", "number": 2.5})}
    assert json.loads(fastjson.dumps(payload)) == {
        "1": [None, {"text": "NaN Infinity", "number": 2.5}]
    }


def test_fallback_still_rejects_cycles_and_unsupported_values():
    cycle = {"big": 2**70}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="Circular reference"):
        fastjson.dumps(cycle)
    with pytest.raises(TypeError):
        fastjson.dumps({"big": 2**70, "unsupported": object()})
