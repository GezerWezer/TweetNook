from __future__ import annotations

import json

import httpx
import pytest

from tweetnook import tagging


@pytest.mark.asyncio
@pytest.mark.parametrize("media_mime", [None, "image/jpeg", "video/mp4"])
async def test_token_preflight_uses_supported_developer_api_sdk_request(monkeypatch, media_mime):
    genai = pytest.importorskip("google.genai")
    from google.genai import types

    requests = []

    def count_tokens(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"totalTokens": 123})

    client = genai.Client(
        api_key="synthetic-test-key",
        http_options=types.HttpOptions(
            client_args={"transport": httpx.MockTransport(count_tokens)},
        ),
    )
    monkeypatch.setattr(tagging, "types", types)
    media_uri = "https://generativelanguage.googleapis.com/v1beta/files/test-media"
    interaction_input = [{"type": "text", "text": "A synthetic post"}]
    if media_mime:
        interaction_input.append(
            {
                "type": "video" if media_mime.startswith("video/") else "image",
                "uri": media_uri,
                "mime_type": media_mime,
            }
        )
    try:
        result = await tagging._count_request_tokens(
            client,
            model="gemini-3.6-flash",
            system_prompt="Suggest specific tags",
            interaction_input=interaction_input,
            search_enabled=True,
        )
    finally:
        client.close()

    assert result == (123, True)
    assert len(requests) == 1
    parts = [part for content in requests[0]["contents"] for part in content["parts"]]
    assert [part["text"] for part in parts if "text" in part] == [
        "Suggest specific tags",
        "A synthetic post",
    ]
    # SDK versions emit either ProtoJSON casing; validate the same URI/MIME
    # contract through FileData's aliases instead of requiring one spelling.
    files = [
        types.FileData.model_validate(part["fileData"]) for part in parts if "fileData" in part
    ]
    assert files == (
        [types.FileData(file_uri=media_uri, mime_type=media_mime)] if media_mime else []
    )
    assert "systemInstruction" not in requests[0]
    assert "tools" not in requests[0]
