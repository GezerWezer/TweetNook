"""Apply a saved configuration snapshot to the running Web services."""

from __future__ import annotations

import threading

from tweetnook.config import AppConfig, load_config
from tweetnook.web.deps import server_state

lifecycle_lock = threading.RLock()


def reload_server_config(
    config: AppConfig | None = None, *, reset_schedule: bool = False
) -> AppConfig:
    """Reload services while keeping the listener and active worker in place.

    Password/session changes remain owned by Setup's authenticated completion flow.
    """
    with lifecycle_lock:
        if config is None:
            config, _ = load_config()
        if store := server_state.get("store"):
            store.apply_config(config)
        server_state["config"] = config
        if supervisor := server_state.get("job_supervisor"):
            supervisor.config = config
        if avatars := server_state.get("avatar_cache_manager"):
            avatars.reload(config)
        if refresher := server_state.get("stats_refresher"):
            refresher.reload(config.web.analytics_refresh_time)
        if scheduler := server_state.get("schedule_manager"):
            scheduler.reload(reset=reset_schedule, config=config)
        return config
