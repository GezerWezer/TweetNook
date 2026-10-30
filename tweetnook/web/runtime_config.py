"""Apply a saved configuration snapshot to the running Web services."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from tweetnook.config import (
    AppConfig,
    XDGPaths,
    config_lock,
    config_snapshot_is_current,
    load_config,
    load_config_at,
)
from tweetnook.web.deps import server_state

lifecycle_lock = threading.RLock()


def reload_server_config(
    config: AppConfig | None = None, *, reset_schedule: bool = False
) -> AppConfig:
    """Reload services while keeping the listener and active worker in place.

    Password/session changes remain owned by Setup's authenticated completion flow.
    """
    with config_lock, lifecycle_lock:
        if config is None or not config_snapshot_is_current(config):
            config, _ = load_config()
        from tweetnook.core.dev_replay import configure_replay

        configure_replay(config.replay)
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


def save_server_config(
    paths: XDGPaths, changes: Mapping[str, Any], *, reset_schedule: bool = False
) -> AppConfig:
    """Save, reload and apply in one order across all Web editors."""
    from tweetnook.config import update_config_values

    with config_lock:
        update_config_values(paths, changes)
        config, _ = load_config_at(paths)
        return reload_server_config(config, reset_schedule=reset_schedule)
