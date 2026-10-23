#!/usr/bin/env bash
# Collect pasted source before executing it. The quoted payload avoids interactive
# history expansion; a child Bash keeps exits/options/traps out of the login shell.
# Only read consumes the payload; -c leaves terminal stdin available for prompts.
(
IFS= read -r -d '' tweetnook_install_source <<'TWEETNOOK_INSTALL_SCRIPT' || :
# Install the published TweetNook package in a private, pip-compatible environment.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: bash install.sh [options]

Install TweetNook on macOS or Linux without changing system Python or shell profiles.
Linux with running systemd defaults to a system-wide service (requests sudo).
macOS and systems without systemd default to manual startup.
These defaults also apply without a terminal. Use --no-service for manual startup.
With a terminal, choose startup mode and add-ons interactively. Manual mode does not
start a server. The recommended choice is always option 1; automated tagging defaults
to no. Review it before installation begins.
Explicit --service / --no-service flags skip all prompts.

Run the installer again to update, reinstall, or add add-ons. An existing installation
keeps its startup mode, paths, add-ons, archive, and settings; without a terminal it
is updated. Stop manually started TweetNook processes first. A background service is
stopped and restarted automatically.

Options:
  --service              Install and start the system-wide Linux service (default with systemd).
  --no-service           Install for manual startup; do not prompt or start a server.
  --automated-tagging    Include the optional Gemini tagging dependencies.
  --legacy-migration     Include support for migrating older LanceDB archives.
  --access local|network Who can open the Web app: local (this computer only, 127.0.0.1)
                         or network (other devices on your network, 0.0.0.0).
                         Default: network for the Linux service, local otherwise.
                         Updates keep the saved setting unless this is given.
  --reinstall            Reinstall the installed version of an existing installation
                         instead of updating it.
  --version VERSION      Install an exact published version (default: latest stable).
                         An existing installation is updated to that version.
  --python PYTHON        Python version or executable for a new environment (default: 3.12).
  --install-dir PATH     Absolute runtime directory (service: /opt/tweetnook;
                         personal: $XDG_DATA_HOME/tweetnook-runtime or ~/.local/share/tweetnook-runtime).
  --bin-dir PATH         Absolute launcher directory (service: /usr/local/bin;
                         personal: ~/.local/bin).
  --details              Show file locations and maintenance commands after installation.
  -h, --help             Show this help and exit.

Requires curl or wget only when uv must be downloaded. Missing Python is downloaded
by uv. The environment includes pip for TweetNook's existing service updater.
Existing archives/configuration are kept in their normal locations, outside the runtime.
Run service installation as root or allow sudo. It creates a dedicated tweetnook
account, with data in /var/lib/tweetnook, config in /etc/tweetnook, and cache in
/var/cache/tweetnook. Personal/manual installation must run as a regular user.
The installer checks the service and its Web app before showing a setup address.
Failures show the relevant error and retain a private diagnostic log.
A service unit this installer did not create is never replaced; use its own updater.
HELP
}

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
say() { printf '  %s\n' "$*"; }
quote_command() { printf '  '; printf '%q ' "$@"; printf '\n'; }
styled() {
    if [[ -t 1 && ${TERM:-dumb} != dumb && ! ${NO_COLOR+x} ]]; then
        printf '  \033[%sm%s\033[0m\n' "$1" "$2"
    else say "$2"; fi
}
heading() { printf '\n'; styled 1 "$1"; printf '\n'; }
cancel() { printf '\n'; say 'Installation cancelled. No changes made.'; exit 0; }
ask() {
    local prompt=$1 default=$2 hint=$3 answer allowed
    shift 3
    while :; do
        read -r -p "  $prompt " answer || cancel
        case "$answer" in q|quit|Q|QUIT) cancel ;; esac
        answer=${answer:-$default}
        for allowed in "$@"; do
            if [[ $answer == "$allowed" ]]; then choice=$answer; return; fi
        done
        say "$hint"
    done
}
step_active=false
start_step() {
    step_label=$1
    step_active=true
    if [[ -n ${log_file:-} ]]; then step_log_start=$(( $(wc -l < "$log_file") + 1 )); fi
    if [[ -t 1 && ${TERM:-dumb} != dumb ]]; then printf '  \302\267 %s\342\200\246' "$step_label"; fi
}
finish_step() {
    $step_active || return 0
    if [[ -t 1 && ${TERM:-dumb} != dumb ]]; then printf '\r\033[2K'; fi
    if [[ ${1:-0} == 0 ]]; then styled 32 $'\342\234\223 '"$step_label"
    else styled 31 "! $step_label"; fi
    step_active=false
}

mode=ask
mode_option=false
install_dir_option=false
bin_dir_option=false
original_args=("$@")
tagging=false
migration=false
access=
access_option=false
access_chosen=false
reinstall=false
details=false
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
        --reinstall) reinstall=true; shift ;;
        --details) details=true; shift ;;
        --version|--python|--install-dir|--bin-dir|--access)
            [[ $# -ge 2 && -n $2 && $2 != --* ]] || die "$1 requires a value."
            case "$1" in
                --version) version=$2 ;;
                --python) python_request=$2 ;;
                --install-dir) install_dir=$2; install_dir_option=true ;;
                --bin-dir) bin_dir=$2; bin_dir_option=true ;;
                --access) access=$2; access_option=true ;;
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
case "$access" in ''|local|network) ;; *) die '--access must be local or network.' ;; esac
if [[ -n $version && ! $version =~ ^[0-9][a-zA-Z0-9.!+_-]*$ ]]; then
    die '--version must be a version number, not a package name, URL, or path.'
fi

installer_marker() { cat "$1/.tweetnook-installer" 2>/dev/null || true; }
installed_version_in() {
    [[ ! -L $1/venv && -f $1/venv/pyvenv.cfg && -x $1/venv/bin/python ]] || return 1
    "$1/venv/bin/python" -I -c 'from importlib.metadata import version; print(version("tweetnook"))' 2>/dev/null
}
has_modules() {
    "$1" -I -c 'import importlib.util, sys
try:
    found = all(importlib.util.find_spec(name) for name in sys.argv[1:])
except ModuleNotFoundError:
    found = False
sys.exit(0 if found else 1)' "${@:2}" 2>/dev/null
}

existing=
existing_version=
existing_runtime=
systemd=false
if [[ $os == Linux ]] && command -v systemctl >/dev/null 2>&1; then
    # Also finds disabled or failed units. Only a unit this installer created is
    # updated in place; never silently repoint or adopt another installation.
    unit_state=$(systemctl show tweetnook.service --property=LoadState --value 2>/dev/null || true)
    if [[ -n $unit_state && $unit_state != not-found ]]; then
        existing_runtime=/opt/tweetnook
        if $install_dir_option; then existing_runtime=$install_dir; fi
        if [[ $(installer_marker "$existing_runtime") == tweetnook-system-installer-v1 ]] \
            && existing_version=$(installed_version_in "$existing_runtime"); then
            existing=service
        else
            die 'A tweetnook.service unit already exists that this installer did not create. Use tweetnook update from that installation; do not install a second copy. See README.md for migration guidance.'
        fi
    fi
    if systemctl show --property=Version --value >/dev/null 2>&1; then systemd=true; fi
fi
if [[ -z $existing && $(installer_marker "$install_dir") == tweetnook-installer-v1 ]] \
    && existing_version=$(installed_version_in "$install_dir"); then
    existing=manual
    existing_runtime=$install_dir
fi
existing_tagging=false
existing_migration=false
if [[ -n $existing ]]; then
    if $mode_option && [[ $mode != "$existing" ]]; then
        if [[ $existing == service ]]; then
            die 'TweetNook is already installed as a background service. Rerun without --no-service to update it.'
        fi
        die 'An existing personal installation was found. Rerun without --service to update it; moving it to a service must be done explicitly.'
    fi
    mode=$existing
    # Updates must keep previously installed add-ons and refresh their dependencies.
    if has_modules "$existing_runtime/venv/bin/python" google.genai PIL; then existing_tagging=true; fi
    if has_modules "$existing_runtime/venv/bin/python" lancedb; then existing_migration=true; fi
    if $reinstall && [[ -n $version && $version != "$existing_version" ]]; then
        die "--reinstall keeps the installed version ($existing_version). Use --version without --reinstall to change versions."
    fi
elif $reinstall; then
    die 'TweetNook is not installed here, so there is nothing to reinstall.'
fi
action=install
if [[ -n $existing ]]; then
    action=update
    if $reinstall; then action=reinstall; fi
fi

prompt=false
if [[ -t 0 && -t 1 ]] && ! $mode_option; then prompt=true; fi
trap cancel INT
if [[ ${TWEETNOOK_INSTALL_REVIEWED:-} != 1 ]]; then heading $'TweetNook \302\267 Install'; fi
if $prompt && [[ $action == update ]]; then
    heading "TweetNook $existing_version is already installed"
    if [[ -n $version ]]; then
        styled 1 "1. Install version $version (recommended)"
    else
        styled 1 '1. Update to the latest version (recommended)'
    fi
    say '   Keeps your archive, settings, and add-ons.'
    printf '\n'
    say "2. Reinstall $existing_version"
    say '   Repairs this version. Use it to add an add-on without updating.'
    printf '\n'
    ask 'Choose [1] (q to cancel):' 1 'Choose 1 or 2, or q to cancel.' 1 2
    if [[ $choice == 2 ]]; then action=reinstall; fi
fi
if [[ $mode == ask ]]; then
    mode=manual
    if $systemd; then mode=service; fi
    if $prompt; then
        heading 'How should TweetNook run?'
        if $systemd; then
            styled 1 '1. Background service (recommended)'
            say '   Starts at boot. Keeps running after you disconnect.'
            printf '\n'
            say '2. Manual startup'
        else
            styled 1 '1. Manual startup'
        fi
        say '   Start it yourself when you need it.'
        printf '\n'
        if $systemd; then
            ask 'Choose [1] (q to cancel):' 1 'Choose 1 or 2, or q to cancel.' 1 2
        else
            ask 'Choose [1] (q to cancel):' 1 'Choose 1, or q to cancel.' 1
        fi
        if [[ $choice == 2 ]]; then mode=manual; fi
    fi
fi
if [[ -z $existing ]] && ! $access_option; then
    # New installations default to this computer only, except the Linux service,
    # which is usually set up from another device. Updates keep the saved host.
    default_access=local
    if [[ $mode == service ]]; then default_access=network; fi
    access=$default_access
    if $prompt; then
        heading 'Who can access TweetNook?'
        local_choice=1
        if [[ $default_access == network ]]; then
            local_choice=2
            styled 1 '1. Other devices on your network (recommended)'
            say '   Phones and other computers can connect, subject to your firewall.'
            printf '\n'
            say '2. This computer only'
            say '   Only a browser on this server can connect.'
        else
            styled 1 '1. This computer only (recommended)'
            say '   Only a browser on this computer can connect.'
            printf '\n'
            say '2. Other devices on your network'
            say '   Phones and other computers can connect, subject to your firewall.'
        fi
        printf '\n'
        ask 'Choose [1] (q to cancel):' 1 'Choose 1 or 2, or q to cancel.' 1 2
        if [[ $choice == "$local_choice" ]]; then access=local; else access=network; fi
        # A deliberate choice replaces a host already saved by an earlier archive.
        access_option=true
        access_chosen=true
    fi
fi
if $prompt && ! $tagging && ! $existing_tagging; then
    heading 'Add automated tagging?'
    styled 1 '1. No'
    say '   You can add it later by running the installer again.'
    printf '\n'
    say '2. Yes'
    say '   Suggests tags and describes media with Google Gemini.'
    say '   Requires a Gemini API key.'
    printf '\n'
    ask 'Choose [1] (q to cancel):' 1 'Choose 1 or 2, or q to cancel.' 1 2
    if [[ $choice == 2 ]]; then tagging=true; fi
fi
needs_sudo=false
if [[ $mode == service ]]; then
    $systemd || die '--service requires Linux with running systemd. Use --no-service for manual startup.'
    for candidate in "$install_dir" "${XDG_DATA_HOME:-${HOME:-}/.local/share}/tweetnook-runtime"; do
        if [[ -f $candidate/.tweetnook-installer && $(cat "$candidate/.tweetnook-installer") == tweetnook-installer-v1 ]]; then
            die 'An existing personal installation was found. Keep its current paths and use tweetnook update; migration must be explicit.'
        fi
    done
    if [[ $(id -u) != 0 ]]; then
        # Never turn an existing personal install into a different, empty archive.
        [[ $existing == service || -z $(command -v tweetnook || true) ]] || die 'TweetNook is already on PATH. Keep using that installation; do not install a second copy.'
        command -v sudo >/dev/null 2>&1 || die 'Run service installation as root or install sudo.'
        [[ -f $0 ]] || die 'Download install.sh to a file before requesting sudo.'
        needs_sudo=true
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

if [[ ${TWEETNOOK_INSTALL_REVIEWED:-} != 1 ]]; then
    release=${version:-latest stable}
    startup='Manual startup'
    if [[ $mode == service ]]; then startup='Background service'; fi
    case "$action" in
        install) heading 'Ready to install'; say "$startup"$' \302\267 '"$release" ;;
        update) heading 'Ready to update'; say "$startup"$' \302\267 '"$existing_version"$' \342\206\222 '"$release" ;;
        reinstall) heading 'Ready to reinstall'; say "$startup"$' \302\267 '"$existing_version" ;;
    esac
    if [[ $mode == service ]]; then
        if [[ -n $existing ]]; then say 'The service stops briefly and restarts.'
        else say 'Uses a dedicated tweetnook account.'; fi
        say 'Administrator access required.'
    elif [[ -n $existing ]]; then
        say 'Keeps your archive and settings. Stop TweetNook before continuing.'
    else
        say 'Uses your regular account. No administrator access needed.'
    fi
    case "$access" in
        local) say 'Access: this computer only' ;;
        network) say 'Access: other devices on your network' ;;
    esac
    if $existing_tagging; then say 'Add-on: Gemini automated tagging (installed)'
    elif $tagging; then say 'Add-on: Gemini automated tagging'; fi
    if $existing_migration; then say 'Add-on: legacy archive migration (installed)'
    elif $migration; then say 'Add-on: legacy archive migration'; fi
    if $prompt; then
        printf '\n'
        ask 'Continue with installation? [Y/n]:' y 'Enter y to continue or n to cancel.' y Y yes YES n N no NO
        case "$choice" in n|N|no|NO) cancel ;; esac
    fi
fi
if $needs_sudo; then
    $mode_option || original_args+=(--service)
    if $tagging; then original_args+=(--automated-tagging); fi
    if $access_chosen; then original_args+=(--access "$access"); fi
    if [[ $action == reinstall ]]; then original_args+=(--reinstall); fi
    say 'Requesting administrator access...'
    # This marker only avoids repeating the reviewed UI; explicit flags already
    # skip prompts. The elevated process repeats every installation safety check.
    exec sudo env TWEETNOOK_INSTALL_REVIEWED=1 bash "$0" "${original_args[@]}"
fi

log_file=$(mktemp "${TMPDIR:-/tmp}/tweetnook-install.XXXXXX")
# A failing shell function can enter EXIT while its log redirection is active.
# Retain the real streams so diagnostics and the failed row remain visible.
exec 3>&1 4>&2
lock_dir=
download_file=
launcher_temp=
service_attempted=false
service_stopped=false
cleanup() {
    local status=$?
    exec 1>&3 2>&4 3>&- 4>&-
    finish_step "$status"
    if [[ $status != 0 ]]; then
        heading 'Installation needs attention' >&2
        if [[ -s $log_file ]]; then
            sed -n "${step_log_start:-1},\$p" "$log_file" | tail -n 12 >&2
        fi
        if $service_stopped; then
            if systemctl start tweetnook.service >> "$log_file" 2>&1; then
                printf '\n  The background service was started again.\n' >&2
            else
                printf '\n  The background service could not be started again.\n' >&2
            fi
        fi
        printf '\n  Diagnostic log:\n  %s\n' "$log_file" >&2
        if $service_attempted; then
            printf '  Check the service:\n' >&2
            quote_command "$launcher" service status >&2
            printf '  View the error log:\n  journalctl -u tweetnook -n 50 --no-pager\n' >&2
        fi
    else rm -f "$log_file"; fi
    [[ -z $download_file ]] || rm -f "$download_file"
    [[ -z $launcher_temp ]] || rm -f "$launcher_temp"
    [[ -z $lock_dir ]] || rmdir "$lock_dir" || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

heading 'Installing'
start_step 'Check requirements'
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
mkdir "$install_dir/.install-lock" 2>/dev/null || die "Another installer may be running. If it was interrupted, remove the empty $install_dir/.install-lock directory before retrying."
lock_dir=$install_dir/.install-lock
finish_step

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

prepare_environment() {
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
}
start_step 'Prepare Python environment'
prepare_environment >> "$log_file" 2>&1
finish_step

requirement=tweetnook
extras=
if $tagging || $existing_tagging; then extras=automated-tagging; fi
if $migration || $existing_migration; then extras=${extras:+$extras,}legacy-migration; fi
if [[ -n $extras ]]; then requirement="${requirement}[$extras]"; fi
package=$requirement
if [[ -n $version ]]; then package="$package==$version"; fi

check_stopped() {
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
}

pip_install() { "$python" -I -m pip --isolated install --disable-pip-version-check "$@"; }
read_version() {
    "$python" -I -c 'from importlib.metadata import version; print(version("tweetnook"))' 2>/dev/null || true
}
install_package() {
    installed_version=$(read_version)
    if [[ -n $existing ]]; then
        # Any background service was stopped above; a remaining process would keep
        # old code loaded while its files change underneath it.
        check_stopped
        if [[ $action == reinstall ]]; then
            say "Reinstalling tweetnook==$installed_version ..."
            # Repair TweetNook's own files, then add dependencies for any new add-on.
            pip_install --force-reinstall --no-deps "tweetnook==$installed_version"
            pip_install "$requirement==$installed_version"
        else
            say "Updating to $package ..."
            pip_install --upgrade "$package"
        fi
    else
        # An interrupted fresh installation resumes without changing its version.
        if [[ -n $installed_version && -n $version && $installed_version != "$version" ]]; then
            die "TweetNook $installed_version is already installed here. Rerun without --version, or stop it and update it with this installer."
        fi
        if [[ -n $installed_version && ( -n $extras || $mode == service ) ]]; then check_stopped; fi
        if [[ -z $installed_version || -n $extras ]]; then
            say "Installing $package ... Stop any manually started TweetNook processes before adding extras."
            pip_install "$package"
        fi
    fi
    "$python" -I -m tweetnook --version
    "$python" -I -m tweetnook --help >/dev/null
    new_version=$(read_version)
}
if [[ $existing == service ]]; then
    start_step 'Stop background service'
    service_stopped=true
    systemctl stop tweetnook.service >> "$log_file" 2>&1
    finish_step
fi
case "$action" in
    update) start_step 'Update TweetNook' ;;
    reinstall) start_step 'Reinstall TweetNook' ;;
    *) start_step 'Install TweetNook' ;;
esac
install_package >> "$log_file" 2>&1
finish_step

setup_service_account() {
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
}

apply_access() {
    [[ -n $access ]] || return 0
    host=0.0.0.0
    if [[ $access == local ]]; then host=127.0.0.1; fi
    # Write as the account that owns the archive. Without an explicit choice,
    # keep a host already saved by an existing archive's configuration.
    "$python" -I - "$mode" "$host" "$access_option" <<'ACCESS'
import os
import sys
import tomllib

from tweetnook.config import resolve_paths, update_config_values

mode, host, explicit = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
if mode == "service":
    import pwd

    from tweetnook.system_install import SERVICE_ENVIRONMENT, SERVICE_HOME, SERVICE_USER

    account = pwd.getpwnam(SERVICE_USER)
    if account.pw_uid == 0 or account.pw_dir != SERVICE_HOME:
        raise SystemExit("The TweetNook service account has an invalid UID or home.")
    os.environ.clear()
    os.environ.update(SERVICE_ENVIRONMENT)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
paths = resolve_paths()
if not explicit:
    try:
        with paths.config_file.open("rb") as handle:
            web = tomllib.load(handle).get("web", {})
    except FileNotFoundError:
        web = {}
    if isinstance(web, dict) and "host" in web:
        raise SystemExit(0)
update_config_values(paths, {"web.host": host})
ACCESS
}

publish_launcher() {
    if [[ ! -e $launcher ]]; then
        launcher_temp=$(mktemp "$bin_dir/.tweetnook.XXXXXX")
        printf '%s\n' "$launcher_text" > "$launcher_temp"
        chmod 755 "$launcher_temp"
        # Hard-link publication is atomic and refuses a command created in the meantime.
        ln "$launcher_temp" "$launcher" || die "Could not create $launcher; an existing command was left untouched."
    fi
}
if [[ $mode == service ]]; then
    start_step 'Set up background service'
    setup_service_account >> "$log_file" 2>&1
    apply_access >> "$log_file" 2>&1
    publish_launcher >> "$log_file" 2>&1
    service_attempted=true
    "$python" -I -m tweetnook service install --user tweetnook --data-home /var/lib --config-home /etc --cache-home /var/cache >> "$log_file" 2>&1
    service_stopped=false
    finish_step
else
    start_step 'Make the tweetnook command available'
    apply_access >> "$log_file" 2>&1
    publish_launcher >> "$log_file" 2>&1
    finish_step
fi

command_name=tweetnook
path_missing=false
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *)
        command_name=$launcher
        path_missing=true ;;
esac

service_web_status() {
    # Self-contained so this installer can also check older published packages.
    # Read configuration as the service user, never from root's personal archive.
    "$python" -I - <<'WEB_STATUS'
import http.client
import ipaddress
import json
import os
import pwd
import subprocess
import sys
import time
import tomllib
from urllib.parse import urlsplit

from tweetnook.cli_service import _service_runtime_state, _verify_service_running
from tweetnook.config import WebConfig, resolve_paths
from tweetnook.system_install import SERVICE_ENVIRONMENT, SERVICE_HOME, SERVICE_USER
from tweetnook.web.address import display_web_url


def wait_for_web(host, port, timeout=20):
    """Check the local application, without HTTP proxies or redirects."""
    probe_host = {"0.0.0.0": "127.0.0.1", "::": "::1", "[::]": "::1"}.get(host, host)
    probe_host = probe_host.removeprefix("[").removesuffix("]")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        connection = http.client.HTTPConnection(probe_host, port, timeout=min(1, remaining))
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read(1024)
            if response.status == 200 and b"<title>TweetNook</title>" in body:
                return
            if response.status == 401 and response.getheader("WWW-Authenticate") == "Basic":
                if json.loads(body) == {"detail": "Incorrect password"}:
                    return
        except (OSError, http.client.HTTPException, ValueError):
            pass
        finally:
            connection.close()
        time.sleep(max(0, min(0.25, deadline - time.monotonic())))
    raise ValueError(
        f"The TweetNook web app did not respond on its configured port ({port}) "
        f"within {timeout} seconds. Check the service logs for the startup error."
    )


def setup_address(host, port):
    url = display_web_url(host, port)
    try:
        local = ipaddress.ip_address(urlsplit(url).hostname).is_loopback
    except ValueError:
        local = urlsplit(url).hostname == "localhost"
    if local and host in {"0.0.0.0", "::", "[::]"}:
        scope = "unknown"
    else:
        scope = "local" if local else "network"
    return scope, url, port


def main():
    account = pwd.getpwnam(SERVICE_USER)
    if account.pw_uid == 0 or account.pw_dir != SERVICE_HOME:
        raise ValueError("The TweetNook service account has an invalid UID or home.")
    os.environ.clear()
    os.environ.update(SERVICE_ENVIRONMENT)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    _verify_service_running()
    config_file = resolve_paths(SERVICE_ENVIRONMENT).config_file
    try:
        with config_file.open("rb") as handle:
            config = WebConfig.model_validate(tomllib.load(handle).get("web", {}))
    except FileNotFoundError:
        # A fresh service may still be importing the app before creating config.
        # Probe the same defaults it will use, without creating files as root.
        config = WebConfig()
    initial = _service_runtime_state()
    wait_for_web(config.host, config.port)
    final = _service_runtime_state()
    if initial != final or final[:2] != ("active", "running") or final[2] <= 0:
        raise ValueError("The TweetNook service stopped or restarted during its web startup check.")
    print(*setup_address(config.host, config.port))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Could not confirm web startup: {error}", file=sys.stderr)
        sys.exit(1)
WEB_STATUS
}

version_summary() {
    case "$action" in
        update)
            if [[ $new_version == "$existing_version" ]]; then say "Already up to date ($new_version)."
            else say "Updated $existing_version"$' \342\206\222 '"$new_version"; fi
            printf '\n' ;;
        reinstall) say "Reinstalled $new_version."; printf '\n' ;;
    esac
}
finish_setup=' to finish setup'
if [[ -n $existing ]]; then finish_setup=; fi
if [[ $mode == service ]]; then
    start_step 'Check web server'
    systemctl is-active --quiet tweetnook.service >> "$log_file" 2>&1 || die 'The service did not remain active.'
    web_status=$(service_web_status 2>> "$log_file")
    read -r web_scope web_url web_port <<< "$web_status"
    case "$web_scope" in
        network|local|unknown) ;;
        *) die 'The web startup check returned an invalid address.' ;;
    esac
    finish_step
    heading 'TweetNook is running'
    version_summary
    case "$web_scope" in
        network)
            say "Open in your browser$finish_setup:"
            printf '\n'
            styled '1;36' "  $web_url" ;;
        local)
            say "Open in a browser on this server$finish_setup:"
            printf '\n'
            styled '1;36' "  $web_url"
            printf '\n'
            say 'The configured address accepts connections from this server only.' ;;
        unknown)
            say "The server's network address could not be determined."
            say 'To find its IP address, run on the server:'
            styled 36 '  hostname -I'
            say "Open that IP address on port $web_port from your browser."
            say "On the server itself: $web_url" ;;
    esac
    printf '\n'
    say 'Starts automatically when the server boots.'
else
    case "$action" in
        update) heading 'TweetNook is updated' ;;
        reinstall) heading 'TweetNook is reinstalled' ;;
        *) heading 'TweetNook is installed' ;;
    esac
    version_summary
    say 'Start it when you are ready:'
    quote_command "$command_name" serve
    printf '\n'
    say "Then open the address it shows$finish_setup."
    say 'Keep that terminal open while using TweetNook.'
fi

if $path_missing; then
    printf '\n'
    say 'To use the short tweetnook command, add its directory to PATH:'
    # The user's shell, not the installer, should expand the existing PATH.
    # shellcheck disable=SC2016
    printf '  export PATH=%q:"$PATH"\n' "$bin_dir"
fi
if $details; then
    heading 'Installation details'
    say "Application  $install_dir"
    say "Command      $launcher"
    "$python" -I -m tweetnook --version
    if [[ $mode == service ]]; then
        say 'Archive      /var/lib/tweetnook'
        say 'Settings     /etc/tweetnook'
        say 'Cache        /var/cache/tweetnook'
        printf '\n'
        say 'Status:'; quote_command "$command_name" service status
        say 'Restart:'; quote_command "$command_name" service restart
        say 'Update:'; quote_command "$command_name" update
    else
        say 'To update or add add-ons, stop TweetNook and run this installer again.'
    fi
fi
printf '\n'
TWEETNOOK_INSTALL_SCRIPT
exec bash +H -c "$tweetnook_install_source" "${BASH_SOURCE[0]:-}" "$@"
)
