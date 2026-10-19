from __future__ import annotations

import json
import tomllib

import httpx
import pytest
from pydantic import ValidationError

from tests.conftest import make_tweet_detail_response, make_tweet_result
from tweetnook.config import (
    SyncConfig,
    get_config_ui_schema,
    load_config,
    update_config_values,
)
from tweetnook.query_ids import QueryIdStore
from tweetnook.storage import open_archive_store
from tweetnook.threads import expand_threads, walk_linked_statuses


def test_reply_limit_defaults_validation_persistence_and_schema(paths):
    assert SyncConfig().reply_linked_limit == 50
    with pytest.raises(ValidationError):
        SyncConfig(reply_linked_limit=0)
    update_config_values(paths, {"sync.reply_linked_depth": 1, "sync.reply_linked_limit": 3})
    env = {
        "XDG_CONFIG_HOME": str(paths.config_dir.parent),
        "XDG_DATA_HOME": str(paths.data_dir.parent),
        "XDG_CACHE_HOME": str(paths.cache_dir.parent),
    }
    # Read the exact saved model without relying on the host's XDG layout.
    assert (
        SyncConfig.model_validate(
            tomllib.loads(paths.config_file.read_text())["sync"]
        ).reply_linked_limit
        == 3
    )
    env["TWEETNOOK_REPLY_LINKED_LIMIT"] = "7"
    assert load_config(env)[0].sync.reply_linked_limit == 7
    schema = get_config_ui_schema()
    assert "sync.reply_linked_depth" in schema["whitelist"]
    assert "sync.reply_linked_limit" not in schema["whitelist"]
    assert schema["visible_when"]["sync.reply_linked_limit"] == ["sync.reply_linked_depth"]


def seed(store):
    rows = []
    for tweet_id, author in [("100", "1000"), ("101", "1000"), ("102", "2000"), ("103", "3000")]:
        raw = make_tweet_result(tweet_id, "fixture", user_id=author, conversation_id="100")
        rows.append(
            store._record(
                row_key=f"tweet_object:{tweet_id}",
                record_type="tweet_object",
                tweet_id=tweet_id,
                author_id=author,
                conversation_id="100",
                raw_json=json.dumps(raw),
            )
        )
        if tweet_id == "100":
            rows.append(
                store._record(
                    row_key="tweet:bookmark::100",
                    record_type="tweet",
                    tweet_id=tweet_id,
                    collection_type="bookmark",
                    author_id=author,
                    conversation_id="100",
                    raw_json=json.dumps(raw),
                )
            )
    # Reverse insertion order, duplicate URLs, and an author-thread link prove
    # deterministic allocation, deduplication and independent primary traversal.
    for index, (source, target) in enumerate(
        [("103", "500"), ("102", "400"), ("102", "300"), ("102", "300"), ("101", "200")]
    ):
        rows.append(
            store._record(
                row_key=f"url_ref:{index}",
                record_type="url_ref",
                tweet_id=source,
                canonical_url=f"https://x.com/fixture/status/{target}",
            )
        )
    store._merge_records(rows)


def test_reply_limit_is_unique_deterministic_and_keeps_primary_chains(paths):
    store = open_archive_store(paths, create=True)
    seed(store)
    options = dict(max_linked_depth=50, reply_linked_depth=1)
    unlimited = walk_linked_statuses(store, ["100"], **options)
    assert set(unlimited.depths) == {"100", "101", "200", "300", "400", "500"}
    assert unlimited.reply_target_ids == {"300", "400", "500"}
    off = walk_linked_statuses(store, ["100"], max_linked_depth=50, reply_linked_depth=0)
    assert set(off.depths) == {"100", "101", "200"}
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "failure", "incidental_hydration"])
async def test_reply_limit_counts_attempts_and_advances_past_known_targets(
    paths, config, auth_bundle, scenario
):
    store = open_archive_store(paths, create=True)
    seed(store)
    store.close()
    QueryIdStore(paths).save({"TweetDetail": "fixture-query-id"})
    config.sync.reply_linked_depth = 1
    config.sync.reply_linked_limit = 1
    config.sync.detail_max_retries = 0
    config.sync.max_retries = 0
    requested = []

    def handler(request):
        target = json.loads(request.url.params["variables"])["focalTweetId"]
        requested.append(target)
        if target == "300" and scenario == "failure":
            return httpx.Response(500, request=request)
        raw = make_tweet_result(target, "fixture", user_id="1000", conversation_id=target)
        returned = [raw]
        if target == "100" and scenario == "incidental_hydration":
            returned.append(make_tweet_result("300", "incidentally fetched", user_id="2000"))
        return httpx.Response(200, json=make_tweet_detail_response(returned), request=request)

    result = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )
    assert requested == ["100", "200", "400" if scenario == "incidental_hydration" else "300"]
    assert result.failed == int(scenario == "failure")
    requested.clear()
    await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )
    # Failure consumes this run's slot and is retried on the next run. Success
    # leaves room for the next unseen target rather than starving the tail.
    assert (
        requested
        == {"failure": ["300"], "success": ["400"], "incidental_hydration": ["500"]}[scenario]
    )
