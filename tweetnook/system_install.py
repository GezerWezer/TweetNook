"""Entry point used only by the system installer's global launcher.

Personal/pip installations continue to enter the CLI directly. Archive commands
from this launcher always use the system service account and its XDG roots.
"""

from __future__ import annotations

import os
import pwd
import shutil
import sys

SERVICE_USER = "tweetnook"
SERVICE_HOME = "/var/lib/tweetnook"
SERVICE_ENVIRONMENT = {
    "HOME": SERVICE_HOME,
    "USER": SERVICE_USER,
    "LOGNAME": SERVICE_USER,
    "XDG_DATA_HOME": "/var/lib",
    "XDG_CONFIG_HOME": "/etc",
    "XDG_CACHE_HOME": "/var/cache",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
}


def main() -> None:
    arguments = sys.argv[1:]
    try:
        # These commands operate on the installed unit/environment, not an archive.
        # Keep the existing CLI's elevation and recorded-service-path behavior.
        if arguments[:1] in (["service"], ["update"]):
            os.execv(sys.executable, [sys.executable, "-I", "-m", "tweetnook", *arguments])

        account = pwd.getpwnam(SERVICE_USER)
        if account.pw_uid == 0 or account.pw_dir != SERVICE_HOME:
            raise ValueError("The TweetNook system service account has an invalid UID or home.")
        if os.geteuid() not in (0, account.pw_uid):
            sudo = shutil.which("sudo")
            if sudo is None:
                raise ValueError(
                    "Run this command as root or install sudo to use the service account."
                )
            os.execv(
                sudo,
                [sudo, sys.executable, "-I", "-m", "tweetnook.system_install", *arguments],
            )

        # Do not inherit caller credentials, XDG overrides or Python environment
        # settings into the service archive. Retain terminal/locale preferences.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            in {"LANG", "LANGUAGE", "TZ", "TERM", "COLORTERM", "NO_COLOR", "COLUMNS", "LINES"}
            or key.startswith("LC_")
        }
        environment.update(SERVICE_ENVIRONMENT)
        if os.geteuid() == 0:
            os.initgroups(account.pw_name, account.pw_gid)
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
        os.execve(
            sys.executable,
            [sys.executable, "-I", "-m", "tweetnook", *arguments],
            environment,
        )
    except (KeyError, ValueError, OSError) as exc:
        print(f"TweetNook system command failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
