"""FastAPI Web server for TweetNook."""

import hashlib
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from rich.console import Console
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from tweetnook.activity_history import cleanup_runs, read_run
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.job_supervisor import JobSupervisor
from tweetnook.reminders import (
    print_archive_migration_report,
    print_pending_archive_enrichment_reminder,
)
from tweetnook.scheduler import ScheduleManager
from tweetnook.storage import open_archive_store
from tweetnook.web.avatar_cache import AvatarCacheManager
from tweetnook.web.deps import WebSessionMiddleware, server_state, verify_credentials
from tweetnook.web.notices import NoticeStore
from tweetnook.web.routes.activity import router as activity_router
from tweetnook.web.routes.automated_tagging import router as automated_tagging_router
from tweetnook.web.routes.avatars import router as avatars_router
from tweetnook.web.routes.config import router as config_router
from tweetnook.web.routes.dev_replay import router as dev_replay_router
from tweetnook.web.routes.notices import router as notices_router
from tweetnook.web.routes.setup import router as setup_router
from tweetnook.web.routes.stats import router as stats_router
from tweetnook.web.routes.storage_stats import router as storage_stats_router
from tweetnook.web.routes.tags import router as tags_router
from tweetnook.web.routes.tweets import router as tweets_router
from tweetnook.web.setup_coordinator import SetupCoordinator
from tweetnook.web.stats_cache import DailyStatsRefresher, web_stats_cache
from tweetnook.web.warmup import ArchiveWarmer


def _build_fts_in_background(store) -> None:
    """Build the FTS index in a daemon thread so the server can start immediately."""
    try:
        store.ensure_fts_index()
    except Exception:
        pass  # FTS is optional; search degrades gracefully without it


_lifecycle_lock = threading.RLock()
_DETAIL_DOCUMENT_PATH = re.compile(r"^/post/\d+(?:/quotes)?$")


def archive_is_ready() -> bool:
    return server_state.get("store") is not None


def attach_archive_store() -> bool:
    with _lifecycle_lock:
        if archive_is_ready():
            return True
        store = open_archive_store(
            server_state["paths"], create=False, config=server_state["config"]
        )
        if store is None:
            return False
        try:
            store.ensure_scalar_indexes()
        except Exception:
            store.close()
            raise
        server_state["store"] = store
        console = Console(stderr=True)
        print_archive_migration_report(console, store)
        print_pending_archive_enrichment_reminder(console, store)
        thread = threading.Thread(target=_build_fts_in_background, args=(store,), daemon=True)
        server_state["fts_thread"] = thread
        thread.start()
        warmer = ArchiveWarmer(store)
        server_state["archive_warmer"] = warmer
        warmer.request()
        web_stats_cache.clear()
        refresher = DailyStatsRefresher(store, server_state["config"].web.analytics_refresh_time)
        server_state["stats_refresher"] = refresher
        refresher.start()
        return True


def start_scheduler_if_ready() -> None:
    with _lifecycle_lock:
        coordinator = server_state.get("setup_coordinator")
        if (
            server_state.get("shutting_down")
            or not archive_is_ready()
            or coordinator is None
            or not coordinator.state.completed
            or server_state.get("schedule_manager") is not None
        ):
            return
        scheduler = ScheduleManager(
            server_state["paths"],
            server_state["config"],
            server_state["job_supervisor"],
            on_job_conflict=_record_schedule_skip,
        )
        server_state["schedule_manager"] = scheduler
        scheduler.start()


def _record_schedule_skip(context: dict[str, object]) -> None:
    notices = server_state.get("notices")
    if notices is None:
        return
    blocker = context.get("active_run_id")
    occurrence = int(float(context.get("scheduled_at") or 0))
    identity = str(blocker) if blocker else str(occurrence)
    notices.add(
        f"schedule-skipped-{identity}",
        "Scheduled sync skipped",
        "TweetNook could not start the scheduled sync because another task was still "
        "running. It will try again at the next scheduled time.",
        severity="warning",
        action="schedule",
    )


def stop_scheduler() -> None:
    if scheduler := server_state.pop("schedule_manager", None):
        scheduler.stop()


def stop_avatar_cache_manager() -> None:
    if manager := server_state.pop("avatar_cache_manager", None):
        manager.stop()


def request_archive_warmup() -> None:
    if warmer := server_state.get("archive_warmer"):
        warmer.request()


def detach_archive_store() -> None:
    stop_scheduler()
    if refresher := server_state.pop("stats_refresher", None):
        refresher.stop()
    if warmer := server_state.pop("archive_warmer", None):
        warmer.stop()
    if thread := server_state.pop("fts_thread", None):
        if hasattr(thread, "join"):
            thread.join()
    if store := server_state.pop("store", None):
        web_stats_cache.wait_for_refreshes()
        web_stats_cache.clear()
        store.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    server_state.pop("shutting_down", None)
    try:
        if "paths" in server_state:
            paths = server_state["paths"]
            config = server_state.get("config") or AppConfig()
            server_state["config"] = config
            notices = NoticeStore(paths.notices_file)
            server_state["notices"] = notices
            cleanup_runs(
                paths.data_dir,
                max_runs=config.activity.max_runs,
                retention_days=config.activity.retention_days,
            )
            avatar_cache_manager = AvatarCacheManager(paths, config)
            server_state["avatar_cache_manager"] = avatar_cache_manager
            avatar_cache_manager.start()

            def on_job_start(_run_id: str, _pid: int) -> None:
                import time

                server_state["activity_worker_started_at"] = time.time()

            def on_job_complete(run_id: str, exit_code: int) -> None:
                server_state.pop("activity_worker_started_at", None)
                # Analytics keeps its daily report; Refresh collects one on demand.
                # Jobs change like-order inputs and churn the page cache.
                request_archive_warmup()
                coordinator = server_state.get("setup_coordinator")
                run = read_run(paths.data_dir, run_id) or {}
                setup_job = coordinator and run_id in {
                    coordinator.state.archive_init_run_id,
                    coordinator.state.auto_enrich_run_id,
                }
                if setup_job and (store := server_state.get("store")) is not None:
                    # A new archive should not show its pre-import report until tomorrow.
                    web_stats_cache.start_refresh(store)
                if (
                    (exit_code != 0 or run.get("success") is not True)
                    and run.get("status") != "stopped"
                    and not setup_job
                ):
                    action = (
                        "setup"
                        if coordinator and run_id == coordinator.state.archive_init_run_id
                        else "activity"
                    )
                    summary = str(run.get("summary") or "").strip()
                    notices.add(
                        f"job-{run_id}",
                        run.get("title") or "Background job failed",
                        f"The job did not finish successfully: {summary}"
                        if summary
                        else "The job did not finish successfully. Open its log for details.",
                        action=action,
                        run_id=run_id,
                    )
                if coordinator:
                    coordinator.job_completed(run_id, exit_code)

            supervisor = JobSupervisor(
                paths, config, on_start=on_job_start, on_complete=on_job_complete
            )
            server_state["job_supervisor"] = supervisor
            coordinator = SetupCoordinator(
                paths, server_state, attach_archive_store, start_scheduler_if_ready, notices
            )
            server_state["setup_coordinator"] = coordinator
            coordinator.reconcile()
        yield
    finally:
        server_state["shutting_down"] = True
        if stream := server_state.pop("activity_stream", None):
            await stream.close()
        stop_avatar_cache_manager()
        stop_scheduler()
        if supervisor := server_state.get("job_supervisor"):
            supervisor.shutdown()
        server_state.pop("job_supervisor", None)
        detach_archive_store()


app = FastAPI(title="TweetNook Web UI", lifespan=lifespan)


@app.middleware("http")
async def gate_initial_setup(request: Request, call_next):
    coordinator = server_state.get("setup_coordinator")
    path = request.url.path
    if coordinator is not None:
        safe = (
            path == "/"
            or _DETAIL_DOCUMENT_PATH.fullmatch(path) is not None
            or path.startswith("/static/")
            or path == "/api/setup"
            or path.startswith("/api/setup/")
            or path == "/api/notices"
            or path.startswith("/api/notices/")
            or path in {"/api/activity/status", "/api/activity/events", "/api/activity/stop"}
        )
        if not coordinator.state.completed and not safe:
            return JSONResponse(
                status_code=409,
                content={"detail": {"code": "setup_required", "message": "Complete Setup first."}},
            )
        if (
            coordinator.state.completed
            and not archive_is_ready()
            and path in {"/api/activity/sync", "/api/activity/enrich", "/api/activity/import"}
        ):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "code": "archive_not_ready",
                        "message": "Initialize the archive from Setup first.",
                    }
                },
            )
    return await call_next(request)


# Include route modules
app.include_router(tweets_router)
app.include_router(tags_router)
app.include_router(config_router)
app.include_router(stats_router)
app.include_router(avatars_router)
app.include_router(storage_stats_router)
app.include_router(activity_router)
app.include_router(setup_router)
app.include_router(notices_router)
app.include_router(dev_replay_router)

app.include_router(automated_tagging_router)


class TextGZipMiddleware:
    """Gzip JSON, documents, and scripts/styles; pass media and avatars through.

    Images and video are already compressed, and compressing their (possibly
    ranged) file responses would only cost CPU and break byte ranges.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024, compresslevel=5)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _compressible_path(scope.get("path", "")):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def _compressible_path(path: str) -> bool:
    if path == "/api/activity/events":
        return False
    if path.startswith(("/media/", "/api/avatar/")):
        return False
    if path.startswith("/api/dev/replay/x/") and ".twimg.com/" in path:
        return False
    if path.startswith("/static/"):
        return path.endswith((".js", ".css", ".html", ".json", ".svg", ".txt"))
    return True


app.add_middleware(TextGZipMiddleware)
app.add_middleware(WebSessionMiddleware)

WEB_ROOT = Path(__file__).parent
static_dir = WEB_ROOT / "static"
_STATIC_REFERENCE = re.compile(r'((?:href|src)="/static/([^"?]+))(")')
_IMMUTABLE = "public, max-age=31536000, immutable"
_index_lock = threading.Lock()
_index_cache: tuple[tuple[tuple[Path, int], ...], bytes, str, dict[str, str]] | None = None


def _versioned_index() -> tuple[bytes, str, dict[str, str]]:
    """Return index.html with content-hashed asset URLs, its ETag, and asset versions.

    Rebuilt whenever the document or a referenced asset changes on disk, so an
    upgrade or edit never serves stale cached scripts or styles.
    """
    global _index_cache
    with _index_lock:
        cached = _index_cache
        if cached is not None and _unchanged(cached[0]):
            return cached[1], cached[2], cached[3]
        html_path = WEB_ROOT / "index.html"
        stamps = [(html_path, html_path.stat().st_mtime_ns)]
        versions: dict[str, str] = {}

        def version(match: re.Match[str]) -> str:
            asset = match.group(2)
            path = static_dir / asset
            try:
                stamp = path.stat().st_mtime_ns
                digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            except OSError:
                return match.group(0)
            stamps.append((path, stamp))
            versions[asset] = digest
            return f"{match.group(1)}?v={digest}{match.group(3)}"

        body = _STATIC_REFERENCE.sub(version, html_path.read_text(encoding="utf-8")).encode()
        etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
        _index_cache = (tuple(stamps), body, etag, versions)
        return body, etag, versions


def _unchanged(stamps: tuple[tuple[Path, int], ...]) -> bool:
    try:
        return all(path.stat().st_mtime_ns == stamp for path, stamp in stamps)
    except OSError:
        return False


class VersionedStaticFiles(StaticFiles):
    """Cache content-hashed asset URLs for a year; revalidate all others."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in {200, 304}:
            query = scope.get("query_string", b"").decode("latin-1")
            current = _index_cache[3].get(path) if _index_cache is not None else None
            versioned = current is not None and f"v={current}" in query.split("&")
            response.headers["Cache-Control"] = _IMMUTABLE if versioned else "no-cache"
        return response


# Mount static assets directory if available
if static_dir.exists():
    app.mount("/static", VersionedStaticFiles(directory=static_dir), name="static")


def _serve_web_app(request: Request) -> Response:
    body, etag, _versions = _versioned_index()
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return HTMLResponse(body, headers=headers)


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request, _auth: Annotated[bool, Depends(verify_credentials)]):
    return _serve_web_app(request)


@app.get("/post/{tweet_id}", response_class=HTMLResponse)
def read_post(
    request: Request,
    tweet_id: Annotated[str, PathParameter(pattern=r"^\d+$")],
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    return _serve_web_app(request)


@app.get("/post/{tweet_id}/quotes", response_class=HTMLResponse)
def read_post_quotes(
    request: Request,
    tweet_id: Annotated[str, PathParameter(pattern=r"^\d+$")],
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    return _serve_web_app(request)


@app.get("/media/{media_path:path}", response_class=FileResponse)
def read_media(
    media_path: str,
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    """Serve archive media only after auth and never follow paths outside media_dir."""
    paths = server_state.get("paths")
    if paths is None:
        raise HTTPException(status_code=404, detail="Media file not found")

    media_dir = paths.media_dir.resolve()
    target = (media_dir / media_path).resolve()
    if not target.is_relative_to(media_dir) or not target.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    # Downloaded media keeps its path for life; avoid a revalidation per image.
    return FileResponse(target, headers={"Cache-Control": "private, max-age=2592000"})


def run_server(
    config: AppConfig | None,
    paths: XDGPaths,
    host: str,
    port: int,
    password_hash: str | None,
) -> None:
    import uvicorn

    server_state["config"] = config
    server_state["paths"] = paths
    server_state["password_hash"] = password_hash
    # Persistent SSE clients must not keep Uvicorn waiting forever before it
    # enters lifespan shutdown and stops the worker (up to another 70 seconds).
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_graceful_shutdown=5)
