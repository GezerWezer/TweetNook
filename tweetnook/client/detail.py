"""Authenticated TweetDetail requests, shared by every detail-based pass."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from tweetnook.auth import ResolvedAuthBundle
from tweetnook.client.base import build_async_client, request_with_backoff
from tweetnook.client.timelines import build_tweet_detail_url
from tweetnook.config import SyncConfig, XDGPaths
from tweetnook.core.operations import TWEET_DETAIL
from tweetnook.query_ids import QueryIdStore, refresh_query_ids, resolve_query_ids


@dataclass(slots=True)
class TweetDetailRequester:
    """One run's request policy: detail retry settings and one TweetDetail ID refresh.

    The client belongs to the caller, which closes it. A refreshed query ID updates this
    run's mapping, so later requests use it without another refresh.
    """

    client: httpx.AsyncClient
    query_store: QueryIdStore
    query_ids: dict[str, str]
    sync: SyncConfig
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def _url(self, tweet_id: str) -> str:
        return build_tweet_detail_url(self.query_ids[TWEET_DETAIL], tweet_id)

    async def fetch(
        self, tweet_id: str, *, status: Callable[[str], None] | None = None
    ) -> httpx.Response:
        async def refresh_once() -> str:
            refreshed = await refresh_query_ids(
                self.query_store, operations=[TWEET_DETAIL], client=self.client
            )
            self.query_ids.update(refreshed)
            return self._url(tweet_id)

        return await request_with_backoff(
            self.client,
            self._url(tweet_id),
            self.sync,
            max_retries=self.sync.detail_max_retries,
            backoff_base=self.sync.detail_backoff_base,
            refresh_once=refresh_once,
            status=status,
            sleep=self.sleep,
        )


async def open_detail_requester(
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle,
    sync: SyncConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> TweetDetailRequester:
    """Load the TweetDetail query ID (rediscovering a stale one) and open a signed-in client.

    The caller closes ``requester.client``.
    """

    query_store = QueryIdStore(paths)
    query_ids = await resolve_query_ids(query_store, [TWEET_DETAIL], transport=transport)
    client = build_async_client(auth_bundle, timeout=sync.timeout, transport=transport)
    return TweetDetailRequester(client, query_store, query_ids, sync, sleep=sleep)
