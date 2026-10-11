from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse

from tweetnook import fastjson

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
