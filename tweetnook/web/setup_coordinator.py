"""Coordinate the narrowly scoped local import → verified-auth enrichment flow."""

from __future__ import annotations

import logging
import threading
from typing import Literal

from pydantic import BaseModel, ValidationError

from tweetnook.activity_history import new_run_id, read_run
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
    auto_enrich_requested: bool = False
    auto_enrich_run_id: str | None = None
    auth_verified: bool = False
    auth_skipped: bool = False


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

    def begin(self, mode):
        with self.lock:
            supervisor = self.server["job_supervisor"]
            if self.server.get("shutting_down"):
                raise JobConflictError("The server is stopping.")
            args = (
                ["import", "x-archive", str(self.paths.staged_archive_file), "--offline"]
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
                self.state.initialization_result = "complete" if exit_code == 0 else "failed"
                self.save()
            self.reconcile()

    def reconcile(self):
        with self.lock:
            if self.server.get("shutting_down"):
                return
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
                successful = run and (run.get("exit_code") == 0 or run.get("success") is True)
                self.state.initialization_result = "complete" if successful else "interrupted"
                self.save()
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
                    )
            if (
                store is not None
                and self.state.initialization_mode == "import"
                and self.state.initialization_result == "complete"
                and self.state.auto_enrich_requested
            ):
                pending = store.count_incomplete_initial_enrichment()
                config = self.server["config"]
                if pending == 0:
                    self.state.auto_enrich_requested = False
                    self.save()
                elif (
                    self.state.auth_verified
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
            enrich_run = self._run(self.state.auto_enrich_run_id)
            if (
                self.state.auto_enrich_run_id
                and not supervisor.active
                and (not enrich_run or enrich_run.get("status") not in {"completed", "stopped"})
            ):
                self.notices.add(
                    f"enrich-{self.state.auto_enrich_run_id}",
                    "Enrichment needs attention",
                    "Enrichment was interrupted or failed. "
                    "Pending tweets can be retried from Activity.",
                )
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
            if self.state.initialization_mode == "import" or imported:
                if running or not imported:
                    enrichment = "waiting_for_import"
                elif supervisor.active_kind == "enrich":
                    enrichment = "running"
                elif not pending:
                    enrichment = "complete"
                elif self.state.auto_enrich_requested:
                    enrichment = (
                        "waiting_for_auth" if not self.state.auth_verified else "waiting_for_worker"
                    )
                else:
                    run = self._run(self.state.auto_enrich_run_id)
                    enrichment = (
                        "failed"
                        if self.state.auto_enrich_run_id
                        and (not run or run.get("status") != "completed")
                        else "pending"
                    )
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
                    "enriched": imported and pending == 0,
                    "pending_enrichment": pending,
                    "enrichment_state": enrichment,
                    "initialization_state": init_state,
                    "initialization_mode": self.state.initialization_mode,
                    "legacy_migration_available": (
                        self.paths.data_dir / "archive.lancedb"
                    ).exists(),
                    "warnings": []
                    if imported
                    else ["Importing your official Twitter/X archive first is recommended."],
                },
            }
