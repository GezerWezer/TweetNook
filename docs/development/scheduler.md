# Scheduler

`ScheduleManager` belongs to the running web service. It submits work through `JobSupervisor`, so scheduled commands use the same lock, activity history, logs and progress as manually started jobs.

## Readiness

The manager starts after an archive attaches and Setup is complete. It runs while the server process is alive; it isn't an operating-system timer and doesn't wake a sleeping machine. Enabling a schedule in a config file alone won't create a separate daemon.

## Sync cadence

| Cadence | Calculation |
| --- | --- |
| `hours` | Add `every_hours` to the scheduling reference time. |
| `daily` | Find the next configured local time. |
| `weekly` | Find the next configured weekday/time; Monday is 0. |
| `monthly` | Use the configured day/time, clamping to the month's last day. |

Times resolve through `schedule.timezone`, either `local` or an IANA zone. The manager uses timezone-aware UTC instants for stored execution times. A non-hourly schedule can add a random delay between its configured minimum and maximum. The persisted next run keeps the same chosen occurrence across normal reloads.

When saving or restoring a randomized schedule, consider whether the current occurrence is already consumed and whether part of its random window is still ahead. Don't reroll an already chosen occurrence on every poll. Hourly cadence ignores the random-delay settings.

## Conflicts

A scheduled sync that encounters another archive-changing task is normally recorded as skipped and tries at its next scheduled time, with a notice linking to the event. If an availability check is running, the sync waits and retries after fifteen minutes. It doesn't start a second writer or change the running job's arguments.

Availability checks have their own `check_next_run_at` and fingerprint. They default to disabled, weekly on Monday at 04:00 in the schedule timezone, without random delay. Daily and monthly frequencies are also supported; monthly dates clamp at month end. Checks require FxTwitter. A conflict delays a check retry by fifteen minutes. Keep the check's due state separate from the normal sync occurrence so one cannot consume the other.

Context checks are separately opt-in, with a default 60-day target cycle. The runner scales each batch by frequency (1, 7 or 31 days); saved and retryable unavailable posts are checked every run. Quotes returned with saved posts are processed even when context checks are off.

## Configuration changes

Schedule updates validate cadence, time, timezone and random-window order before persisting. The manager reloads the relevant settings and updates the next run. Collection choices travel with the scheduled sync and should match the choices displayed in Setup and Settings.

`schedule-state.json` stores scheduler state under the data directory. It isn't a replacement for the config or activity history. A restart should preserve valid pending work without replaying a completed occurrence solely because the process changed.

## Restarts and timezones

A valid future `next_run_at` survives an ordinary restart. If that persisted timestamp is overdue, one launch is attempted and the schedule advances; there is no queue containing every missed occurrence. Missing/corrupt state is recalculated after now. State uses temporary-file replacement and a compact fingerprint of schedule inputs.

`local` first tries to resolve `/etc/localtime` to a ZoneInfo name, then falls back to current local tzinfo or UTC. Python's timezone behavior determines ambiguous/nonexistent wall times; the project does not define a stronger DST fold/gap policy. Add tests before promising one.

The manager's lock is in-process. There is no distributed leader election, so running several server processes against one archive can create competing scheduler threads. Dedicated schedule APIs reload/reset the manager. Every successful generic config save calls the shared runtime reload with `reset=False`. Changed schedule inputs recalculate the next occurrence; an unchanged sync schedule retains its chosen time. See [applying changes](configuration-and-paths.md#applying-changes).
