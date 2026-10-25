# Troubleshooting

Start with Jobs and Settings > Activity logs. The current run and its log usually tell you which part needs attention. Keep any report free of X cookies, API keys, passwords, and private post content.

## Opening TweetNook

On the server, try its printed address or `http://localhost:8000`. Check the appropriate startup mode with `tweetnook web status` or `tweetnook service status`. For a foreground copy, the terminal running `tweetnook serve` must stay open.

If the local address works but another device can't connect, use the server's network IP on that device. Check that both devices can reach the same network, the firewall allows the port, and the configured host permits network access. After changing the host or port, restart TweetNook.

If a port is already in use, stop the old TweetNook process or choose another port. Starting a second copy on the same port won't replace the first.

### The server exits before the page opens

Startup errors can happen before Jobs or Activity logs are available. A personal background server discards its terminal output, so run it in the foreground to see the error. Use the same Python environment and file locations as usual:

```bash
tweetnook web stop
tweetnook serve
```

For the installer's Linux service, inspect its recent output first. If access is denied, run the command as root or with sudo:

```bash
journalctl -u tweetnook.service -n 100 --no-pager
```

If you need to run that installation in the foreground, stop the service first to free its port:

```bash
tweetnook service stop
tweetnook serve
```

The installer's global command uses the service archive. For a service you created yourself, use its Python environment, account and file locations. After diagnosing the error, press `Ctrl+C` and return to your usual mode with `tweetnook web start` or `tweetnook service start`.

### Forgotten web password

Set a replacement from the terminal using the same installation:

```bash
tweetnook web set-password
```

Restart the foreground server, personal background process, or Linux service afterward. The [startup commands](maintenance.md#starting-and-stopping) cover each mode.

## X connection

Sign in to x.com in your browser, copy fresh `auth_token` and `ct0` values, and update Settings > Setup. Check the numeric user ID too if likes or your own tweets aren't working. Cookie values need to be copied in full.

```bash
tweetnook auth check
```

For direct Python/pip processes, credential environment variables take priority over saved settings. The installer's global Linux command uses the service credentials; see [credentials](maintenance.md#credentials). Keep the archive and the live connection on the same X account.

## Slow syncs

The first run can have a lot to collect, and conversation or media work may continue after the new posts have arrived. Look at the active stage in Jobs. Give rate-limit waits time to finish, and let the run stop before starting another archive-changing job.

You can stop and resume without losing posts already saved. For a short terminal run, use a page limit or skip an individual follow-up task; see [syncing](syncing.md). If the machine runs out of space, free enough room for both the archive and media before continuing.

## Missing posts and details

An [official archive](importing.md) can bring in older history that live sync doesn't reach. After importing, connect the owning account and use `tweetnook import enrich` to continue completion and fill collection gaps.

Use the individual Jobs tasks for missing conversations, Articles, media, or link previews.

| State | Next step |
| --- | --- |
| Media or link previews still pending after a stop | Rerun that task to continue. |
| Media or link previews marked failed | Resolve the error, then enable retry failures or use `media download --retry-failed` / `unfurl --retry-failed`. Normal syncs skip these failures. |
| Media incomplete after importing without X | Use Download media; it can fetch saved public URLs without X credentials. |
| Imported posts need account-only details | Connect X, then run Enrich archive. That command and Sync now require credentials. |

Saved text, media, and Articles remain visible with a warning when a post is deleted or becomes unavailable on X. This also applies to saved quotes and reposts. A post placeholder means there is no saved content to show. An availability check can revisit posts whose recorded reason allows retries.

## Scheduling

The host must be awake and TweetNook must stay running. Check that Sync automatically is enabled, your collection choices were saved, and the next run and timezone are what you expect. Random delay can move a run later than its nominal time.

Another active job can prevent a scheduled sync from starting. Notifications and Activity logs record those events. A scheduled run isn't a reason to start another copy of the server.

## Tags and search

Try All collections, reduce the query, and check `is:hidden`. Use `tag:` for tag names and `description:` for generated descriptions. Search only covers posts already in the archive.

For automated tagging, check that the add-on is installed and enabled, the API key works, and the model supports the options you chose. A test run helps you review one archived post. Reaching quota or a spending limit leaves the remaining work for a later run.

## Archive location

The installer's global Linux command targets the service archive automatically. Direct Python/pip processes use their own account and `XDG_*` overrides, so they can open a different archive. Check [file locations](maintenance.md#file-locations) when counts or posts differ between launches.

## Getting help

Include your TweetNook version (`tweetnook --version`), operating system, installation/startup mode, what you tried, and the relevant part of the run log. If a command failed, include the command with private values removed. Report it in the project's [issue tracker](https://github.com/gezerwezer/tweetnook/issues).

For archive checks, backups, and updates, see [maintenance](maintenance.md).
