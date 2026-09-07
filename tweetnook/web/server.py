"""FastAPI Web server for TweetNook."""

import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from rich.console import Console

from tweetnook.activity_history import cleanup_runs, read_run
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.job_supervisor import JobSupervisor
from tweetnook.reminders import (
    print_archive_migration_report,
    print_pending_archive_enrichment_reminder,
)
from tweetnook.scheduler import ScheduleManager
from tweetnook.storage import open_archive_store
from tweetnook.web.deps import server_state, verify_credentials
from tweetnook.web.notices import NoticeStore
from tweetnook.web.routes.activity import router as activity_router
from tweetnook.web.routes.automated_tagging import router as automated_tagging_router
from tweetnook.web.routes.avatars import router as avatars_router
from tweetnook.web.routes.config import router as config_router
from tweetnook.web.routes.notices import router as notices_router
from tweetnook.web.routes.setup import router as setup_router
from tweetnook.web.routes.stats import router as stats_router
from tweetnook.web.routes.storage_stats import router as storage_stats_router
from tweetnook.web.routes.tags import router as tags_router
from tweetnook.web.routes.tweets import router as tweets_router
from tweetnook.web.setup_coordinator import SetupCoordinator
from tweetnook.web.stats_cache import web_stats_cache


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
        web_stats_cache.clear()
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


def detach_archive_store() -> None:
    stop_scheduler()
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

            def on_job_start(_run_id: str, _pid: int) -> None:
                import time

                server_state["activity_worker_started_at"] = time.time()

            def on_job_complete(run_id: str, exit_code: int) -> None:
                server_state.pop("activity_worker_started_at", None)
                web_stats_cache.clear()
                coordinator = server_state.get("setup_coordinator")
                run = read_run(paths.data_dir, run_id) or {}
                setup_job = coordinator and run_id in {
                    coordinator.state.archive_init_run_id,
                    coordinator.state.auto_enrich_run_id,
                }
                if exit_code != 0 and run.get("status") != "stopped" and not setup_job:
                    action = (
                        "setup"
                        if coordinator and run_id == coordinator.state.archive_init_run_id
                        else "activity"
                    )
                    notices.add(
                        f"job-{run_id}",
                        run.get("title") or "Background job failed",
                        "The job did not finish successfully. Open Activity for details.",
                        action=action,
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
            or path in {"/api/activity/status", "/api/activity/stop"}
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

app.include_router(automated_tagging_router)

# Mount static assets directory if available
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _serve_web_app() -> FileResponse:
    html_path = Path(__file__).parent / "index.html"
    return FileResponse(html_path)


@app.get("/", response_class=HTMLResponse)
def read_root(_auth: Annotated[bool, Depends(verify_credentials)]):
    return _serve_web_app()


@app.get("/post/{tweet_id}", response_class=HTMLResponse)
def read_post(
    tweet_id: Annotated[str, PathParameter(pattern=r"^\d+$")],
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    return _serve_web_app()


@app.get("/post/{tweet_id}/quotes", response_class=HTMLResponse)
def read_post_quotes(
    tweet_id: Annotated[str, PathParameter(pattern=r"^\d+$")],
    _auth: Annotated[bool, Depends(verify_credentials)],
):
    return _serve_web_app()


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
    return FileResponse(target)


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
    uvicorn.run(app, host=host, port=port, log_level="info")
