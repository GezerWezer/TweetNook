"""Coordinate Setup's import (including enrichment/media) → verified-auth first sync."""

from __future__ import annotations

import logging
import threading
from typing import Literal

from pydantic import BaseModel, ValidationError

from tweetnook.activity_history import latest_finished_snapshot, new_run_id, read_run
from tweetnook.job_supervisor import JobConflictError
from tweetnook.storage import open_archive_store
from tweetnook.web.notices import atomic_json


class SetupState(BaseModel):
    version: Literal[1] = 1
    completed: bool = False
    initialization_mode: Literal["import", "migration", "empty"] | None = None
    archive_init_run_id: str | None = None
    initialization_result: Literal["complete", "failed", "interrupted"] | None = None
    archive_previously_ready: bool = False
    # Import's Media result, or a standalone media run persisted by an older version.
    media_run_id: str | None = None
    # The first sync runs as ``import enrich``, which also finishes rows FxTwitter couldn't.
    auto_enrich_requested: bool = False
    auto_enrich_run_id: str | None = None
    auth_verified: bool = False
    auth_skipped: bool = False


# Steps after the local import; stopping in one of them still leaves a usable archive.
_FOLLOWUP_STEPS = {"archive-auth", "archive-enrich", "media"}


def _saved_before_enrichment(run) -> bool:
    """Whether an import that didn't exit cleanly still saved everything before enrichment.

    Stopped follow-up work leaves the saved archive browsable and waits for manual resume.
    """

    steps = ((run or {}).get("snapshot") or {}).get("steps") or []
    if not any(
        step.get("key") in _FOLLOWUP_STEPS and step.get("state") in {"active", "complete", "failed"}
        for step in steps
    ):
        return False
    return all(
        step.get("state") in {"complete", "skipped"}
        for step in steps
        if step.get("key") not in _FOLLOWUP_STEPS
    )


class SetupCoordinator:
    def __init__(self, paths, state, attach, start_scheduler, notices):
        self.paths = paths
        self.server = state
        self.attach = attach
        self.start_scheduler = start_scheduler
        self.notices = notices
        self.lock = threading.RLock()
        try:
            self.state = SetupState.model_validate_json(paths.setup_state_file.read_text())
        except FileNotFoundError:
            self.state = SetupState(completed=paths.database_path.is_file())
            self.save()  # Persist before a worker has a chance to create SQLite.
        except (OSError, ValueError, ValidationError):
            self.state = SetupState()
            notices.add(
                "setup-state",
                "Setup needs attention",
                "Setup state could not be read. Review Setup before continuing.",
                action="setup",
            )

    def save(self):
        atomic_json(self.paths.setup_state_file, self.state.model_dump())

    def _run(self, run_id):
        try:
            return read_run(self.paths.data_dir, run_id) if run_id else None
        except ValueError:
            return None

    def _followup_result(self):
        run = self._run(self.state.auto_enrich_run_id)
        latest = latest_finished_snapshot(
            self.paths.data_dir, titles={"tweetnook sync", "tweetnook import enrich"}
        )
        initialization = self._run(self.state.archive_init_run_id)
        if (
            latest
            and initialization
            and ((latest.get("started_at") or 0) < (initialization.get("started_at") or 0))
        ):
            latest = None
        if latest and (not run or (latest.get("started_at") or 0) >= (run.get("started_at") or 0)):
            return {
                "status": "stopped"
                if latest.get("stopped")
                else ("completed" if latest.get("success") is True else "failed"),
                "success": latest.get("success"),
                "snapshot": latest,
            }
        return run

    def begin(self, mode):
        with self.lock:
            supervisor = self.server["job_supervisor"]
            if self.server.get("shutting_down"):
                raise JobConflictError("The server is stopping.")
            args = (
                ["import", "x-archive", str(self.paths.staged_archive_file), "--defer-followup"]
                if mode == "import"
                else ["migrate"]
            )
            # Persist intent and the exact run ID before spawning; restart cannot lose the chain.
            previous = self.state.model_copy()
            run_id = new_run_id()
            self.state.initialization_mode = mode
            self.state.archive_previously_ready = self.server.get("store") is not None
            self.state.archive_init_run_id = run_id
            self.state.initialization_result = None
            self.state.media_run_id = None
            self.state.auto_enrich_requested = mode == "import"
            self.state.auto_enrich_run_id = None
            self.save()
            try:
                return supervisor.start(
                    kind=mode,
                    cli_args=args,
                    origin="web",
                    title="tweetnook " + " ".join(args[:2]),
                    run_id=run_id,
                )
            except (JobConflictError, OSError):
                self.state = previous
                self.save()
                raise

    def create_empty_archive(self):
        from tweetnook.locking import ProcessLock

        with self.lock:
            probe = ProcessLock(self.paths.command_lock_file)
            probe.acquire()
            try:
                if self.server["job_supervisor"].active:
                    raise JobConflictError("Wait for the active command to finish.")
                if self.paths.database_path.exists():
                    raise JobConflictError(
                        "An archive already exists; keep it or retry its import."
                    )
                store = open_archive_store(self.paths, create=True, config=self.server["config"])
                store.close()
                self.state.initialization_mode = "empty"
                self.state.initialization_result = "complete"
                self.state.archive_init_run_id = None
                self.state.media_run_id = None
                self.state.auto_enrich_requested = False
                self.save()
            finally:
                probe.release()
            self.reconcile()

    def auth_was_verified(self):
        with self.lock:
            self.state.auth_verified = True
            self.state.auth_skipped = False
            self.save()
            self.reconcile()

    def job_completed(self, run_id, exit_code):
        with self.lock:
            if run_id == self.state.archive_init_run_id:
                self._record_initialization(exit_code == 0, self._run(run_id))
            self.reconcile()

    def _record_initialization(self, clean, run, *, failure="failed"):
        saved = clean or (
            self.state.initialization_mode == "import" and _saved_before_enrichment(run)
        )
        self.state.initialization_result = "complete" if saved else failure
        if self.state.initialization_mode == "import":
            # Media belongs to this run even when it failed or never got to start.
            self.state.media_run_id = self.state.archive_init_run_id
        if not clean:
            self.state.auto_enrich_requested = False
        self.save()

    def _initial_followup_failed(self, run):
        return bool(
            self.state.initialization_mode == "import"
            and self.state.initialization_result == "complete"
            and run
            and run.get("status") in {"failed", "interrupted", "stopped"}
        )

    def reconcile(self):
        with self.lock:
            if self.server.get("shutting_down"):
                return
            config = self.server["config"]
            if config.replay.url:
                connected = all(
                    (value or "").strip() for value in (config.auth.auth_token, config.auth.ct0)
                )
                if self.state.auth_verified != connected:
                    self.state.auth_verified = connected
                    self.state.auth_skipped = not connected
                    self.save()
            supervisor = self.server["job_supervisor"]
            run = self._run(self.state.archive_init_run_id)
            init_running = bool(
                self.state.archive_init_run_id
                and supervisor.active_run_id == self.state.archive_init_run_id
            )
            if (
                self.state.archive_init_run_id
                and not init_running
                and self.state.initialization_result is None
            ):
                # A separately surviving worker/command lock wins over stale metadata.
                if supervisor._external_activity_is_running():
                    return
                clean = bool(run and (run.get("exit_code") == 0 or run.get("success") is True))
                self._record_initialization(clean, run, failure="interrupted")
            if self.state.archive_previously_ready or (
                not init_running
                and self.state.initialization_result not in {"failed", "interrupted"}
            ):
                try:
                    if (
                        self.state.initialization_mode == "import"
                        and self.state.initialization_result == "complete"
                        and self.server.get("store") is None
                    ):
                        candidate = open_archive_store(
                            self.paths, create=False, config=self.server["config"]
                        )
                        try:
                            valid = (
                                candidate is not None and candidate.has_completed_archive_import()
                            )
                        finally:
                            if candidate is not None:
                                candidate.close()
                        if not valid:
                            self.state.initialization_result = "failed"
                            self.save()
                    if (
                        self.state.initialization_result != "failed"
                        or self.state.archive_previously_ready
                    ):
                        self.attach()
                except Exception:
                    logging.getLogger(__name__).exception("Could not attach archive storage")
                    self.notices.add(
                        "archive-open",
                        "Archive needs attention",
                        "The archive could not be opened. Check the server log and retry Setup.",
                        action="setup",
                    )
                    return
            store = self.server.get("store")
            if (
                self.state.initialization_mode == "import"
                and self.state.initialization_result == "complete"
            ):
                if store is None or not store.has_completed_archive_import():
                    self.state.initialization_result = "failed"
                    self.save()
            if self.state.initialization_result in {"failed", "interrupted"}:
                if not run or run.get("status") != "stopped":
                    self.notices.add(
                        f"init-{self.state.archive_init_run_id}",
                        "Archive setup needs attention",
                        "Initialization did not finish. Open Setup to retry; "
                        "already imported data is preserved.",
                        action="setup",
                        run_id=self.state.archive_init_run_id,
                    )
            import_ready = (
                store is not None
                and self.state.initialization_mode == "import"
                and self.state.initialization_result == "complete"
            )
            if not init_running and self._initial_followup_failed(run):
                # Also cancel first-sync intent persisted by older versions after failure.
                if self.state.auto_enrich_requested:
                    self.state.auto_enrich_requested = False
                    self.save()
                if run.get("status") != "stopped":
                    self.notices.add(
                        f"import-followup-{self.state.archive_init_run_id}",
                        "Archive preparation needs attention",
                        (run.get("snapshot") or {}).get("summary")
                        or "Imported posts are saved. Open Activity to review the error, "
                        "then run Sync now or Enrich archive to resume.",
                        run_id=self.state.archive_init_run_id,
                    )
            media_run = self._run(self.state.media_run_id)
            if (
                self.state.media_run_id
                and self.state.media_run_id != self.state.archive_init_run_id
                and supervisor.active_run_id != self.state.media_run_id
                and media_run
                and media_run.get("status") in {"failed", "interrupted"}
            ):
                self.notices.add(
                    f"media-{self.state.media_run_id}",
                    "Media download needs attention",
                    "Media download did not finish. Downloaded files are kept; "
                    "open Activity to run Download media again.",
                    run_id=self.state.media_run_id,
                )
            if import_ready and self.state.auto_enrich_requested:
                config = self.server["config"]
                if (
                    self.state.completed
                    and self.state.auth_verified
                    and config.auth.auth_token
                    and config.auth.ct0
                    and not supervisor.active
                ):
                    run_id = new_run_id()
                    self.state.auto_enrich_requested = False
                    self.state.auto_enrich_run_id = run_id
                    self.save()
                    try:
                        supervisor.start(
                            kind="enrich",
                            cli_args=["import", "enrich"],
                            origin="web",
                            title="tweetnook import enrich",
                            run_id=run_id,
                        )
                    except JobConflictError:
                        self.state.auto_enrich_requested = True
                        self.state.auto_enrich_run_id = None
                        self.save()
                    except OSError:
                        self.notices.add(
                            f"enrich-{run_id}",
                            "Enrichment could not start",
                            "Retry enrichment from Activity.",
                        )
            enrich_run = self._followup_result()
            enrich_finishing = bool(
                self.state.auto_enrich_run_id
                and supervisor.active_run_id == self.state.auto_enrich_run_id
            )
            if (
                self.state.auto_enrich_run_id
                and not supervisor.active
                and not enrich_finishing
                and (not enrich_run or enrich_run.get("status") not in {"completed", "stopped"})
            ):
                self.notices.add(
                    f"enrich-{self.state.auto_enrich_run_id}",
                    "Enrichment task needs attention",
                    (enrich_run or {}).get("snapshot", {}).get("summary")
                    or "Enrichment or its automatic sync did not finish. "
                    "Saved progress is preserved. Check disk space and open Activity to resume.",
                    run_id=self.state.auto_enrich_run_id,
                )
            elif enrich_run and enrich_run.get("status") in {"completed", "stopped"}:
                self.notices.dismiss(f"enrich-{self.state.auto_enrich_run_id}")
                # Keep the successful identity so readiness survives browser reloads.
            if enrich_run and enrich_run.get("success") is True:
                self.notices.dismiss(f"import-followup-{self.state.archive_init_run_id}")
            self.start_scheduler()

    def complete_walkthrough(self, *, skip_auth=False):
        with self.lock:
            if not self.state.completed:
                if not self.state.initialization_mode:
                    raise ValueError("Choose an archive initialization option first.")
                if not self.state.auth_verified and not skip_auth:
                    raise ValueError("Connect Twitter/X or explicitly continue without connecting.")
            self.state.auth_skipped = skip_auth and not self.state.auth_verified
            self.state.completed = True
            self.save()

    def pending_jobs(self):
        """Job keys Setup will start on its own, in order, for the Web jobs list."""

        with self.lock:
            if self.state.initialization_mode != "import" or self.state.initialization_result in {
                "failed",
                "interrupted",
            }:
                return []
            if (
                self.state.auto_enrich_requested
                and self.state.completed
                and self.state.auth_verified
            ):
                return ["enrich"]
            return []

    def status(self):
        with self.lock:
            self.reconcile()
            store = self.server.get("store")
            supervisor = self.server["job_supervisor"]
            archive = self.paths.staged_archive_file
            imported = bool(store and store.has_completed_archive_import())
            pending = store.count_incomplete_initial_enrichment() if imported else 0
            running = bool(
                self.state.archive_init_run_id
                and supervisor.active_run_id == self.state.archive_init_run_id
            )
            init_state = (
                "running"
                if running
                else self.state.initialization_result or ("ready" if store else "missing")
            )
            enrichment = "not_applicable"
            initialization = self._run(self.state.archive_init_run_id)
            followup_run = self._followup_result()
            followup_state = "not_started"
            if supervisor.active_kind in {"enrich", "sync"}:
                followup_state = "running"
            elif self.state.auto_enrich_requested:
                followup_state = (
                    "waiting_for_setup"
                    if not self.state.completed
                    else "waiting_for_auth"
                    if not self.state.auth_verified
                    else "waiting_for_worker"
                )
            elif followup_run:
                followup_state = "complete" if followup_run.get("success") is True else "failed"
            elif not running and self._initial_followup_failed(initialization):
                followup_state = "failed"
            followup_failed = followup_state == "failed"
            if self.state.initialization_mode == "import" or imported:
                if running or not imported:
                    enrichment = "waiting_for_import"
                elif supervisor.active_kind == "enrich":
                    enrichment = "running"
                elif followup_failed:
                    enrichment = "failed"
                elif not pending:
                    # Import already enriched everything; the first sync is tracked separately.
                    enrichment = "complete"
                elif self.state.auto_enrich_requested:
                    enrichment = followup_state
                else:
                    run = self._run(self.state.auto_enrich_run_id)
                    enrichment = (
                        "failed"
                        if self.state.auto_enrich_run_id
                        and (not run or run.get("status") != "completed")
                        else "pending"
                    )
            media = "not_applicable"
            if self.state.initialization_mode == "import":
                media_run = self._run(self.state.media_run_id) or initialization
                media_steps = ((media_run or {}).get("snapshot") or {}).get("steps") or []
                media_step = next((step for step in media_steps if step.get("key") == "media"), {})
                # An explicit sync/enrichment can finish the media left by a failed import.
                resumed_media = next(
                    (
                        step
                        for step in ((followup_run or {}).get("snapshot") or {}).get("steps", [])
                        if step.get("key") == "media"
                        and (
                            step.get("state") in {"complete", "failed"}
                            or (
                                step.get("state") == "skipped"
                                and followup_run.get("success") is True
                            )
                        )
                    ),
                    None,
                )
                if resumed_media:
                    media_run, media_step = followup_run, resumed_media
                if running:
                    media = (
                        "running" if media_step.get("state") == "active" else "waiting_for_import"
                    )
                elif media_run:
                    state = media_step.get("state")
                    if state == "complete" or (
                        state in {None, "skipped"} and media_run.get("success") is True
                    ):
                        media = "complete"
                    elif state == "failed":
                        media = "stopped" if media_run.get("status") == "stopped" else "failed"
                    elif (
                        self.state.media_run_id
                        and supervisor.active_run_id == self.state.media_run_id
                    ):
                        media = "running"
                    else:
                        media = "not_started"
                else:
                    media = "not_started"
            return {
                "required": not self.state.completed,
                "completed": self.state.completed,
                "web": {"password_configured": bool(self.server.get("password_hash"))},
                "archive": {
                    "database_exists": self.paths.database_path.is_file(),
                    "ready": store is not None,
                    "uploaded": archive.is_file(),
                    "filename": archive.name if archive.is_file() else None,
                    "size": archive.stat().st_size if archive.is_file() else None,
                    "imported": imported,
                    "official_import_completed": imported,
                    "enriched": imported and pending == 0 and not followup_failed,
                    "details_complete": imported and pending == 0,
                    "browsable": store is not None,
                    "followup_state": followup_state,
                    "all_followups_complete": followup_state == "complete",
                    "pending_enrichment": pending,
                    "enrichment_state": enrichment,
                    "media_state": media,
                    "initialization_state": init_state,
                    "initialization_mode": self.state.initialization_mode,
                    "legacy_migration_available": (
                        self.paths.data_dir / "archive.lancedb"
                    ).exists(),
                    "warnings": [
                        (followup_run or initialization or {}).get("snapshot", {}).get("summary")
                        or "Archive preparation did not finish. Open Activity to review and resume."
                    ]
                    if followup_failed
                    else []
                    if imported
                    else ["Importing your official Twitter/X archive first is recommended."],
                },
            }
