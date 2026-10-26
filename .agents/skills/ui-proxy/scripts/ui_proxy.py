"""Serve this checkout's web UI in front of a running TweetNook server.

The page and /static/ files come from the checkout; every other request (API,
media, event streams) goes to the target server, so the UI runs on that
server's real archive. Page requests are still sent to the target first, so
its authentication decides who may load the page.

Usage, from the repository root:

    uv run python .agents/skills/ui-proxy/scripts/ui_proxy.py --target http://HOST:8000
"""

import argparse
import hashlib
import mimetypes
import re
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

WEB_ROOT = Path(__file__).resolve().parents[4] / "tweetnook" / "web"
STATIC_ROOT = WEB_ROOT / "static"
# Paths the server answers with index.html; keep in sync with tweetnook/web/server.py.
PAGE_PATH = re.compile(r"/(?:post/\d+(?:/quotes)?)?")
STATIC_REFERENCE = re.compile(r'((?:href|src)="/static/([^"?]+))(")')
HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
    b"host",
}
CONDITIONAL_HEADERS = {b"if-none-match", b"if-modified-since"}
LOCAL_HEADERS = {"Cache-Control": "no-store", "X-TweetNook-Preview": "local-ui"}


class NoCookies(DefaultCookiePolicy):
    # The client is shared by every visitor, so it must never hold a session.
    def set_ok(self, cookie, request):
        return False


def forwardable(raw, drop=frozenset()):
    excluded = HOP_HEADERS | drop
    for name, value in raw:
        if name.lower() == b"connection":
            excluded |= {token.strip().lower() for token in value.split(b",")}
    return [(name, value) for name, value in raw if name.lower() not in excluded]


def local_page() -> bytes:
    # Hash each asset into its URL, like the server does, so a browser holding
    # the server's immutable copies still fetches the checkout's files.
    def version(match):
        asset = (STATIC_ROOT / match[2]).resolve()
        if not asset.is_relative_to(STATIC_ROOT) or not asset.is_file():
            return match[0]
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
        return f"{match[1]}?v={digest}{match[3]}"

    return STATIC_REFERENCE.sub(version, (WEB_ROOT / "index.html").read_text()).encode()


def local_static(path: str) -> Response:
    asset = (WEB_ROOT / path.lstrip("/")).resolve()
    if not asset.is_relative_to(STATIC_ROOT) or not asset.is_file():
        return Response("Not found\n", status_code=404, headers=LOCAL_HEADERS)
    media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    return Response(asset.read_bytes(), media_type=media_type, headers=LOCAL_HEADERS)


async def forward(request: Request) -> Response:
    client: httpx.AsyncClient = request.app.state.client
    path = request.url.path
    reading = request.method in {"GET", "HEAD"}
    if reading and path.startswith("/static/"):
        return local_static(path)

    page = reading and PAGE_PATH.fullmatch(path) is not None
    url = request.app.state.target + request.scope["raw_path"].decode("ascii")
    if request.scope["query_string"]:
        url += "?" + request.scope["query_string"].decode("ascii")
    drop = CONDITIONAL_HEADERS if page else frozenset()
    upstream_request = client.build_request(
        request.method,
        url,
        headers=forwardable(request.headers.raw, drop),
        content=request.stream(),
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as error:
        return Response(
            f"Target server unavailable: {error}\n", status_code=502, headers=LOCAL_HEADERS
        )

    if page and upstream.status_code == 200:
        # The target accepted the visitor; keep any session cookie it issued.
        cookies = [(k, v) for k, v in upstream.headers.raw if k.lower() == b"set-cookie"]
        await upstream.aclose()
        response = Response(
            local_page(), media_type="text/html; charset=utf-8", headers=LOCAL_HEADERS
        )
        response.raw_headers.extend(cookies)
        return response

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    response = StreamingResponse(body(), status_code=upstream.status_code)
    response.raw_headers = forwardable(upstream.headers.raw)
    return response


def build_app(target: str) -> Starlette:
    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=5),
            cookies=CookieJar(policy=NoCookies()),
            trust_env=False,
        ) as client:
            app.state.client = client
            app.state.target = target.rstrip("/")
            yield

    methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    return Starlette(routes=[Route("/{path:path}", forward, methods=methods)], lifespan=lifespan)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--target",
        required=True,
        help="Base URL of the running TweetNook server, e.g. http://HOST:8000",
    )
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Address to listen on; use the machine's LAN address for a phone",
    )
    parser.add_argument("--port", type=int, default=8001, help="Port to listen on")
    args = parser.parse_args()
    uvicorn.run(build_app(args.target), host=args.bind, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
