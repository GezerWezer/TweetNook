#!/usr/bin/env bash
# Install the published TweetNook package in a private, pip-compatible environment.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: bash install.sh [options]

Install TweetNook on macOS or Linux without changing system Python or shell profiles.
Linux with running systemd defaults to a system-wide service (requests sudo).
macOS and systems without systemd default to manual startup.
These defaults also apply without a terminal. Use --no-service for manual startup.
With a terminal, choose startup mode interactively. Manual mode does not start a server.

Options:
  --service              Install and start the system-wide Linux service (default with systemd).
  --no-service           Install for manual startup; do not prompt or start a server.
  --automated-tagging    Include the optional Gemini tagging dependencies.
  --legacy-migration     Include support for migrating older LanceDB archives.
  --version VERSION      Install an exact published version (default: latest stable).
  --python PYTHON        Python version or executable for a new environment (default: 3.12).
  --install-dir PATH     Absolute runtime directory (service: /opt/tweetnook;
                         personal: $XDG_DATA_HOME/tweetnook-runtime or ~/.local/share/tweetnook-runtime).
  --bin-dir PATH         Absolute launcher directory (service: /usr/local/bin;
                         personal: ~/.local/bin).
  -h, --help             Show this help and exit.

Requires curl or wget only when uv must be downloaded. Missing Python is downloaded
by uv. The environment includes pip for TweetNook's existing service updater.
Existing archives/configuration are kept in their normal locations, outside the runtime.
Run service installation as root or allow sudo. It creates a dedicated tweetnook
account, with data in /var/lib/tweetnook, config in /etc/tweetnook, and cache in
/var/cache/tweetnook. Personal/manual installation must run as a regular user.
Existing installations keep their paths; use tweetnook update, not this installer.
Stop manual TweetNook processes before rerunning. For an installed service, use its
existing updater instead; this installer does not replace an existing service.
HELP
}

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
quote_command() { printf '  '; printf '%q ' "$@"; printf '\n'; }

mode=ask
mode_option=false
install_dir_option=false
bin_dir_option=false
original_args=("$@")
tagging=false
migration=false
version=
python_request=3.12
install_dir=${XDG_DATA_HOME:-${HOME:-}/.local/share}/tweetnook-runtime
bin_dir=${HOME:-}/.local/bin
while [[ $# -gt 0 ]]; do
    case "$1" in
        --service|--no-service)
            [[ $mode == ask ]] || die 'Choose only one of --service and --no-service.'
            if [[ $1 == --service ]]; then mode=service; else mode=manual; fi
            mode_option=true
            shift ;;
        --automated-tagging) tagging=true; shift ;;
        --legacy-migration) migration=true; shift ;;
        --version|--python|--install-dir|--bin-dir)
            [[ $# -ge 2 && -n $2 && $2 != --* ]] || die "$1 requires a value."
            case "$1" in
                --version) version=$2 ;;
                --python) python_request=$2 ;;
                --install-dir) install_dir=$2; install_dir_option=true ;;
                --bin-dir) bin_dir=$2; bin_dir_option=true ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (see --help)." ;;
    esac
done

os=$(uname -s)
case "$os" in Darwin|Linux) ;; *) die 'TweetNook currently supports macOS and Linux.' ;; esac
validate_path() {
    [[ $1 == /* && $1 != / && $1 != *[[:cntrl:]]* ]] || die 'Installation paths must be absolute directories without control characters.'
}
$install_dir_option && validate_path "$install_dir"
$bin_dir_option && validate_path "$bin_dir"
if [[ -n $version && ! $version =~ ^[0-9][a-zA-Z0-9.!+_-]*$ ]]; then
    die '--version must be a version number, not a package name, URL, or path.'
fi

systemd=false
if [[ $os == Linux ]] && command -v systemctl >/dev/null 2>&1; then
    # Also finds disabled or failed units. Never silently repoint an older installation.
    unit_state=$(systemctl show tweetnook.service --property=LoadState --value 2>/dev/null || true)
    if [[ -n $unit_state && $unit_state != not-found ]]; then
        die 'A tweetnook.service unit already exists. Use tweetnook update from that installation; do not install a second copy. See README.md for migration guidance.'
    fi
    if systemctl show --property=Version --value >/dev/null 2>&1; then systemd=true; fi
fi
if [[ $mode == ask ]]; then
    mode=manual
    default_choice=1
    if $systemd; then mode=service; default_choice=2; fi
    if [[ -t 0 && -t 1 ]]; then
        say 'How should TweetNook run?'
        if $systemd; then
            say '  1. Install for manual startup'
            say '  2. Install a system-wide background service (default; Linux systemd; root or sudo)'
        else
            say '  1. Install for manual startup (default)'
        fi
        read -r -p "Choose [$default_choice]: " choice || choice=$default_choice
        case "${choice:-$default_choice}" in
            1) mode=manual ;;
            2) $systemd || die 'Automatic startup requires Linux with running systemd.'; mode=service ;;
            *) die 'Choose 1 or 2, or pass --no-service / --service.' ;;
        esac
    fi
fi
if [[ $mode == service ]]; then
    $systemd || die '--service requires Linux with running systemd. Use --no-service for manual startup.'
    for candidate in "$install_dir" "${XDG_DATA_HOME:-${HOME:-}/.local/share}/tweetnook-runtime"; do
        if [[ -f $candidate/.tweetnook-installer && $(cat "$candidate/.tweetnook-installer") == tweetnook-installer-v1 ]]; then
            die 'An existing personal installation was found. Keep its current paths and use tweetnook update; migration must be explicit.'
        fi
    done
    if [[ $(id -u) != 0 ]]; then
        # Never turn an existing personal install into a different, empty archive.
        [[ -z $(command -v tweetnook || true) ]] || die 'TweetNook is already on PATH. Keep using that installation; do not install a second copy.'
        command -v sudo >/dev/null 2>&1 || die 'Run service installation as root or install sudo.'
        [[ -f $0 ]] || die 'Download install.sh to a file before requesting sudo.'
        $mode_option || original_args+=(--service)
        say 'Administrator access is required for system-wide installation; requesting sudo...'
        exec sudo bash "$0" "${original_args[@]}"
    fi
    $install_dir_option || install_dir=/opt/tweetnook
    $bin_dir_option || bin_dir=/usr/local/bin
    PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"
    export PATH
    umask 022
    for command in getent useradd install stat nologin; do
        command -v "$command" >/dev/null 2>&1 || die "System service installation requires $command."
    done
else
    [[ $(id -u) != 0 ]] || die 'Run personal/manual installation as your regular user, without sudo. Root installation requires Linux with systemd and --service.'
    [[ -n ${HOME:-} && $HOME == /* ]] || die 'HOME must be an absolute path.'
fi
validate_path "$install_dir"
validate_path "$bin_dir"

# Normalize the directories before creating launcher text or comparing an existing command.
[[ ! -L $install_dir ]] || die 'The runtime directory must not be a symbolic link.'
mkdir -p "$(dirname "$install_dir")" "$bin_dir"
install_dir="$(cd "$(dirname "$install_dir")" && pwd -P)/$(basename "$install_dir")"
bin_dir=$(cd "$bin_dir" && pwd -P)
if [[ $mode == service ]]; then
    # The updater executes this runtime as root. No other user may be able to
    # replace it or its global launcher through a writable parent directory.
    check_system_directory() {
        local owner permissions
        owner=$(stat -c %u "$1")
        permissions=$(stat -c %a "$1")
        if [[ $owner != 0 ]] || (( (8#$permissions & 022) != 0 )); then
            die "System runtime and command paths must be root-owned and not writable by other users: $1"
        fi
    }
    for directory in "$(dirname "$install_dir")" "$bin_dir"; do
        while :; do
            check_system_directory "$directory"
            [[ $directory != / ]] || break
            directory=$(dirname "$directory")
        done
    done
    if [[ -d $install_dir ]]; then check_system_directory "$install_dir"; fi
fi
venv_dir=$install_dir/venv
python=$venv_dir/bin/python
launcher=$bin_dir/tweetnook
entrypoint=tweetnook
if [[ $mode == service ]]; then entrypoint=tweetnook.system_install; fi
launcher_text=$(printf '#!/usr/bin/env bash\n# Managed by the TweetNook installer.\nexec %q -I -m %s "$@"\n' "$python" "$entrypoint")
if [[ -e $launcher || -L $launcher ]]; then
    [[ -f $launcher && ! -L $launcher && $(cat "$launcher") == "$launcher_text" ]] || die "Refusing to replace an existing command: $launcher. Keep using that installation, or choose another --bin-dir."
fi
existing_command=$(command -v tweetnook || true)
if [[ -n $existing_command && $existing_command != "$launcher" ]]; then
    die "TweetNook is already on PATH at $existing_command. Keep using it, or remove its PATH entry before installing a separate environment. Your archive does not need to move."
fi

marker=$install_dir/.tweetnook-installer
marker_value=tweetnook-installer-v1
if [[ $mode == service ]]; then marker_value=tweetnook-system-installer-v1; fi
if [[ -e $install_dir ]]; then
    [[ -d $install_dir && -O $install_dir && -f $marker && ! -L $marker && $(cat "$marker") == "$marker_value" ]] || die "Refusing to modify an unmanaged runtime directory: $install_dir"
else
    mkdir "$install_dir"
    printf '%s\n' "$marker_value" > "$marker"
fi
lock_dir=$install_dir/.install-lock
mkdir "$lock_dir" 2>/dev/null || die "Another installer may be running. If it was interrupted, remove the empty $lock_dir directory before retrying."
download_file=
launcher_temp=
cleanup() {
    [[ -z $download_file ]] || rm -f "$download_file"
    [[ -z $launcher_temp ]] || rm -f "$launcher_temp"
    rmdir "$lock_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ensure_uv() {
    if [[ -x $install_dir/bootstrap/uv ]]; then
        uv=$install_dir/bootstrap/uv
    elif command -v uv >/dev/null 2>&1; then
        uv=$(command -v uv)
    else
        say 'Downloading uv from astral.sh to prepare the private Python environment...'
        download_file=$(mktemp "${TMPDIR:-/tmp}/tweetnook-uv.XXXXXX")
        if command -v curl >/dev/null 2>&1; then
            curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
                https://astral.sh/uv/install.sh --output "$download_file"
        elif command -v wget >/dev/null 2>&1; then
            wget --https-only -q https://astral.sh/uv/install.sh -O "$download_file"
        else
            die 'Install curl, wget, or uv, then rerun this installer.'
        fi
        UV_UNMANAGED_INSTALL="$install_dir/bootstrap" sh "$download_file"
        uv=$install_dir/bootstrap/uv
    fi
}

[[ ! -L $venv_dir ]] || die 'The private environment must not be a symbolic link.'
if [[ ! -f $venv_dir/pyvenv.cfg || ! -x $python ]]; then
    ensure_uv
    say "Creating the private Python environment in $venv_dir ..."
    # This is a normal venv, not a uv tool environment; pip remains its package manager.
    # Resume an interrupted creation without clearing unrelated diagnostic files.
    UV_PYTHON_INSTALL_DIR="$install_dir/python" "$uv" --no-config venv --no-project \
        --allow-existing --seed --python "$python_request" "$venv_dir"
fi
[[ -f $venv_dir/pyvenv.cfg && -x $python ]] || die "The private environment is incomplete: $venv_dir. Preserve it for diagnosis and choose a new --install-dir to retry."
"$python" -I -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer is required"; assert sys.prefix != sys.base_prefix, "Expected a virtual environment"'
if ! "$python" -I -m pip --version >/dev/null 2>&1; then
    ensure_uv
    "$uv" --no-config pip install --python "$python" pip
fi

package=tweetnook
extras=
if $tagging; then extras=automated-tagging; fi
if $migration; then extras=${extras:+$extras,}legacy-migration; fi
if [[ -n $extras ]]; then package="${package}[$extras]"; fi
if [[ -n $version ]]; then package="$package==$version"; fi

# No --upgrade: reruns keep the installed version and previously installed extras.
# Version changes must use the managed updater or a deliberate stopped manual upgrade.
installed_version=$("$python" -I -c 'from importlib.metadata import version; print(version("tweetnook"))' 2>/dev/null || true)
if [[ -n $installed_version && -n $version && $installed_version != "$version" ]]; then
    die "TweetNook $installed_version is already installed here. Stop it and upgrade explicitly; --version only selects a fresh installation."
fi
if [[ -n $installed_version && ( -n $extras || $mode == service ) ]]; then
    # A foreground server has no PID file. Inspect processes using this environment
    # instead of opening the archive/config just to check whether it is in use.
    "$python" -I - <<'PY'
import os
import subprocess
import sys

result = subprocess.run(
    ["ps", "-axo", "pid=,args="], check=True, capture_output=True, text=True
)
for line in result.stdout.splitlines():
    fields = line.strip().split(None, 1)
    if len(fields) != 2 or fields[0] == str(os.getpid()):
        continue
    command = fields[1]
    if f"{sys.prefix}/bin/" in command and (
        "-m tweetnook" in command or f"{sys.prefix}/bin/tweetnook" in command
    ):
        raise SystemExit(
            f"Stop TweetNook processes in this environment before changing the installation (PID {fields[0]})."
        )
PY
fi
if [[ -z $installed_version || -n $extras ]]; then
    say "Installing $package ... Stop any manually started TweetNook processes before adding extras."
    "$python" -I -m pip --isolated install --disable-pip-version-check "$package"
fi
"$python" -I -m tweetnook --version
"$python" -I -m tweetnook --help >/dev/null

if [[ $mode == service ]]; then
    # Older published packages do not contain the system launcher. Fail before
    # creating accounts or publishing a command rather than installing a broken CLI.
    "$python" -I -c 'import tweetnook.system_install' || die 'This package version does not support system-wide installation.'
    nologin=$(command -v nologin || true)
    [[ -n $nologin ]] || die 'System service installation requires nologin.'
    account=$(getent passwd tweetnook || true)
    if [[ -z $account ]]; then
        useradd --system --user-group --home-dir /var/lib/tweetnook --shell "$nologin" tweetnook
        account=$(getent passwd tweetnook)
    fi
    IFS=: read -r account_name _ account_uid account_gid _ account_home account_shell <<< "$account"
    [[ $account_name == tweetnook && $account_uid =~ ^[0-9]+$ && $account_gid =~ ^[0-9]+$ && $account_home == /var/lib/tweetnook && $account_shell == "$nologin" ]] || die 'An incompatible tweetnook account already exists; it was left unchanged.'
    (( 10#$account_uid > 0 )) || die 'An incompatible tweetnook account already exists (UID 0); it was left unchanged.'
    # Refuse to chown existing unrelated directories or follow state symlinks.
    for directory in /var/lib/tweetnook /etc/tweetnook /var/cache/tweetnook; do
        [[ ! -L $directory ]] || die "The service directory must not be a symbolic link: $directory"
        if [[ -e $directory ]]; then
            [[ -d $directory && $(stat -c %u "$directory") == "$account_uid" ]] || die "Refusing to change ownership of an existing service directory: $directory"
        fi
        install -d -m 750 -o "$account_uid" -g "$account_gid" "$directory"
    done
fi

if [[ ! -e $launcher ]]; then
    launcher_temp=$(mktemp "$bin_dir/.tweetnook.XXXXXX")
    printf '%s\n' "$launcher_text" > "$launcher_temp"
    chmod 755 "$launcher_temp"
    # Hard-link publication is atomic and refuses a command created in the meantime.
    ln "$launcher_temp" "$launcher" || die "Could not create $launcher; an existing command was left untouched."
fi
say "Installed command: $launcher"
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *)
        say 'For this terminal, add its directory to PATH:'
        # The new shell, not the installer, should expand the existing PATH.
        # shellcheck disable=SC2016
        printf '  export PATH=%q:"$PATH"\n' "$bin_dir"
        say 'Add the same line to your shell profile to keep it available in new terminals.' ;;
esac

if [[ $mode == service ]]; then
    "$python" -I -m tweetnook service install --user tweetnook --data-home /var/lib --config-home /etc --cache-home /var/cache
    systemctl is-active --quiet tweetnook.service || die 'The service did not remain active. Run tweetnook service status for details.'
    say 'The service is running as tweetnook. Open http://<server-or-container-IP>:8000 (or your configured address) to complete Setup.'
    say 'Archive: /var/lib/tweetnook; configuration: /etc/tweetnook; cache: /var/cache/tweetnook'
    say 'For future service updates, run:'
    say '  tweetnook update'
    say 'To restart the service, run:'
    say '  tweetnook service restart'
else
    say 'To start TweetNook, run:'
    quote_command "$launcher" serve
    say 'Then open http://localhost:8000 (or the configured address) to complete Setup.'
    say 'For future manual updates, stop TweetNook and run:'
    quote_command "$python" -I -m pip --isolated install --upgrade "$package"
    say 'Restart TweetNook after updating. Back up your archive before upgrading.'
fi
