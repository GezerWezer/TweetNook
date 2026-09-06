# Scheduler

[Development guide](README.md) · [Web and Jobs](web-and-jobs.md) ·
[Client and Sync](client-and-sync.md) · [User Web guide](../web-app.md)

`ScheduleManager` calculates the next sync time and asks `JobSupervisor` to
launch it when due. It runs inside the web server and persists the next time
across restarts. Its lifetime is tied to the FastAPI application.

## Configuration model

`ScheduleConfig` validates:

- cadence: `hours`, `daily`, `weekly`, `monthly`;
- every-hours: 1–720;
- `HH:MM` wall-clock time;
- weekday: Monday 0 through Sunday 6;
- day: 1–31;
- `local` or installed IANA timezone;
- random added delay bounds: 0–24 hours, min ≤ max for enabled non-hourly
  randomization.

Default schedule is disabled, daily 03:00 local, random delay 0–2 hours.

## Timezone resolution

Named zones use `zoneinfo.ZoneInfo`. `local` first resolves `/etc/localtime` to a
zoneinfo name; if unavailable it falls back to the process's current local
`tzinfo`, then UTC.

Daily/weekly calculations use `replace()` on a zone-aware local datetime;
monthly constructs a zone-aware datetime directly. Offsets therefore follow
Python ZoneInfo, but there is no explicit project policy for nonexistent or
ambiguous DST wall times (`fold`/gap normalization). Add focused tests before
claiming stronger DST semantics.

## Nominal recurrence

### Hours

```text
next = after + every_hours
```

This is an interval anchored to the current scheduling calculation, not a
wall-clock hour grid. Randomization is ignored.

### Daily

Use today's configured local wall time when strictly after `after`; otherwise
advance one local day.

### Weekly

Compute days to configured weekday, set wall time, and advance seven days if the
candidate is not strictly future.

### Monthly

Clamp configured day to the target month's last day and advance months until
strictly future.

## Random added delay

Daily/weekly/monthly schedules can add a uniform random delay in the configured
window. The algorithm considers whether the previous nominal event's jitter
window still contains a future time, so configuring/restarting during that
window need not skip directly to the next nominal occurrence.

The random offset is not persisted separately; its effect is captured in the
persisted `next_run_at`. That timestamp remains stable across ordinary restart
as long as the schedule fingerprint is unchanged. State also carries the
fingerprint and last-run metadata. After a due occurrence or explicit schedule
edit/reset, a new offset is drawn.

The delay is additive and never schedules before nominal time.

## Persistent state

`schedule-state.json` stores:

- `next_run_at` epoch timestamp;
- serialized configuration fingerprint;
- last run time/result/run ID where available.

Writes go to a PID-specific temporary file then atomically replace the state.
Missing/corrupt/non-object JSON becomes empty state and is recalculated.

The fingerprint is compact sorted JSON for the entire schedule model. Any
schedule field change resets the next occurrence.

## Loop and due handling

A daemon thread waits five seconds between ticks. A tick:

1. returns when disabled;
2. initializes a missing next time;
3. returns before due time;
4. asks JobSupervisor to launch `tweetnook sync` with origin `schedule`;
5. records started/run ID or `skipped: another command was running`;
6. records last time;
7. calculates the next occurrence from the current tick time;
8. persists state.

The launched command is bare sync: bookmarks + likes + follow-ups. Authored-tweet
sync is not included.

Job conflicts are consumed and advance the schedule. Other exceptions escape
`tick()` into the loop's broad suppression; the Web service remains alive and a
due timestamp may be retried on a later tick if state was not advanced.

## Downtime and catch-up

The scheduler exists only while Web is running. On startup, an existing future
timestamp is retained. If state is absent/reset, the next time is calculated
strictly after now. There is no missed-occurrence queue or catch-up loop.

If a persisted timestamp is already due, the first tick tries one launch and
then advances. It does not replay every elapsed interval.

## Reload behavior

The dedicated schedule PUT route:

1. persists every `schedule.*` field;
2. calls `manager.reload(reset=True)`;
3. reloads global config and updates supervisor config;
4. recomputes next state.

Generic `/api/config` persistence does not reload the manager and should not be
used as the runtime schedule control path.

## Status contract

Disabled state reports `configured=false`, no next timestamp, and Setup copy.
Enabled state reports local formatted date, relative duration, description,
last result, effective config, and epoch next time.

“Configured” in this response means an enabled next occurrence exists, not only
that fields are present in TOML.

## Concurrency

Manager state is guarded by an in-process `RLock`. JobSupervisor plus durable
activity/command lock provides cross-process conflict control. There is no
distributed leader election; multiple Web processes pointed at one data
directory are unsupported and could each run a scheduler thread.

## Regression targets

- `tests/test_scheduler.py`
- schedule portions of `tests/web/test_activity_routes.py`
- config validation tests
- Web asset schedule control tests

Tests should inject `now` and explicit random offsets instead of depending on
wall clock or randomness.
