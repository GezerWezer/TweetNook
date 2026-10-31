import math

from fastapi import APIRouter

from tweetnook.web.routes.tweets import _json


def test_tweet_response_serializer_keeps_mixed_fallback_payload_valid(make_web_client):
    router = APIRouter()

    @router.get("/payload")
    def payload():
        return _json({"big": 2**70, "nested": [{1: math.nan, "infinite": math.inf}]})

    response = make_web_client(router).get("/payload")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "big": 1180591620717411303424,
        "nested": [{"1": None, "infinite": None}],
    }
