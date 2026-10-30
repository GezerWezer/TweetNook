import io
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tweetnook.tagging.pricing import load_openrouter_pricing, pricing_cache_path


@pytest.mark.parametrize(
    "content",
    [b"\xff", json.dumps({"fetched_at": "9999-12-31T23:59:59-23:59", "models": {}}).encode()],
)
def test_invalid_pricing_content_reaches_refresh(tmp_path, content):
    pricing_cache_path(tmp_path).write_bytes(content)
    calls = []

    def opener(request, *, timeout):
        calls.append(request.full_url)
        return io.BytesIO(
            json.dumps(
                {
                    "data": [
                        {
                            "id": "google/gemini-test",
                            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                        }
                    ]
                }
            ).encode()
        )

    result = load_openrouter_pricing(
        tmp_path, "gemini-test", now=datetime(2026, 10, 30, tzinfo=UTC), opener=opener
    )
    assert result is not None
    assert result.input_per_token == Decimal("0.000001")
    assert not result.stale
    assert len(calls) == 1
