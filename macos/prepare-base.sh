#!/bin/bash
# shellcheck disable=SC2329
# User-mode preparation coordinator. The narrowly scoped seed helper alone runs as root.
set -euo pipefail
umask 077
PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin
name=''
guest_user='admin'
ipsw=''
fail() { failure=$*; printf 'zoo base preparation: %s\n' "$*" >&2; exit 1; }
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || fail 'missing option value'
    case "$1" in --name) name=$2 ;; --guest-user) guest_user=$2 ;; --ipsw) ipsw=$2 ;; *) fail 'unknown option' ;; esac
    shift 2
done
[[ "$name" =~ ^zoo-[a-zA-Z0-9][a-zA-Z0-9_-]{0,119}$ ]] || fail 'invalid Zoo base VM name'
[[ "$guest_user" =~ ^[a-zA-Z_][a-zA-Z0-9_-]{0,30}$ ]] && [ "$guest_user" != root ] || fail 'invalid guest user'
[ "$(id -u)" != 0 ] || fail 'run as the enrolled host account, not root'
vm="$HOME/.zoovm/vms/$name"
status="$HOME/.zoovm/preparation/$name"
mkdir -p "$status"
lock="$status/lock"
recovery="$status/recovery"
own_lock=false
phase=starting
failure=''
runner=''
write_status() {
    printf '%s\n' "$2" > "$status/.$1.$$"
    mv -f "$status/.$1.$$" "$status/$1"
}
set_state() { phase=$1; write_status state "$1"; printf 'zoo preparation: %s\n' "$1"; }
cleanup() {
    code=$?
    trap - EXIT
    if [ "$own_lock" = true ]; then
        if [ "$code" -ne 0 ]; then
            write_status error "${failure:-$phase failed (exit $code); see ~/.zoovm/install.log}"
            write_status state failed
            if [ -n "$runner" ]; then /usr/local/bin/zoovm stop "$name" --timeout 10 9>&- >/dev/null 2>&1 || true; fi
        fi
        if [ "$(cat "$lock/pid" 2>/dev/null)" = "$$" ]; then rm -f "$lock/pid"; rmdir "$lock" || true; fi
    fi
    exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# macOS lockf's descriptor form locks our inherited open file description.
# fd 9 stays open in this shell, so the kernel serializes acquisitions/recovery
# and releases ownership even after SIGKILL. Long-lived children must close it.
exec 9>"$status/coordination.lock"
code=0
/usr/bin/lockf -s -t 0 9 2>/dev/null || code=$?
if [ "$code" = 64 ]; then
    # macOS 14's older lockf lacks descriptor mode. Stock Perl's core Fcntl
    # module can lock the same inherited description without a helper process
    # retaining ownership after this coordinator exits.
    code=0
    /usr/bin/perl -MFcntl=:flock -e 'open(my $guard, ">&=9") or exit 73; flock($guard, LOCK_EX | LOCK_NB) or exit 75;' || code=$?
fi
if [ "$code" != 0 ]; then
    [ "$code" = 75 ] && exit 0
    fail 'cannot acquire the preparation coordination lock'
fi
older_than_grace() {
    modified=$(stat -f %m "$1" 2>/dev/null) || modified=$(stat -c %Y "$1" 2>/dev/null) || return 1
    [[ "$modified" =~ ^[0-9]+$ ]] || return 1
    [ "$(( $(date +%s) - modified ))" -ge 60 ]
}
reclaimable() {
    directory=$1
    owner=$(cat "$directory/pid" 2>/dev/null || true)
    if [[ "$owner" =~ ^[0-9]+$ ]] && [ "$owner" -gt 1 ]; then
        if kill -0 "$owner" 2>/dev/null; then return 1; fi
        return 0
    else
        # A fresh missing/malformed PID could be an older runner acquiring its
        # lock. Give both directory and PID file a full minute before recovery.
        older_than_grace "$directory" && { [ ! -e "$directory/pid" ] || older_than_grace "$directory/pid"; }
    fi
}
if [ -d "$recovery" ]; then
    # Legacy versions used a second directory for recovery. Our kernel guard
    # makes an orphaned recovery directory recoverable without another PID lock.
    reclaimable "$recovery" || exit 0
    rm -f "$recovery/pid"
    rmdir "$recovery" || fail 'stale recovery lock contains unexpected files'
fi
if ! mkdir "$lock" 2>/dev/null; then
    reclaimable "$lock" || exit 0
    rm -f "$lock/pid"
    rmdir "$lock" || fail 'stale preparation lock contains unexpected files'
    mkdir "$lock" || fail 'cannot recreate the preparation lock'
fi
printf '%s\n' "$$" > "$lock/pid"
own_lock=true
write_status pid "$$"
set_state starting
rm -f "$status/error"
IFS= read -r key || fail 'supply the control-plane public key on stdin'
[[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+$ ]] || fail 'expected the control-plane Ed25519 public key'
if [ ! -f "$vm/installed" ]; then
    set_state downloading
    if [ -n "$ipsw" ]; then
        [ -f "$ipsw" ] && [ -r "$ipsw" ] || fail 'the selected macOS restore image is not readable'
    fi
    install_base() {
        if [ -n "$ipsw" ]; then /usr/local/bin/zoovm install "$name" --ipsw "$ipsw" 9>&-
        else /usr/local/bin/zoovm install "$name" 9>&-; fi
    }
    install_base 9>&- 2>&1 | while IFS= read -r line; do
        printf '%s\n' "$line"
        case "$line" in installing*) set_state installing ;; esac
    done 9>&-
fi
[ -f "$vm/installed" ] || fail 'macOS restore did not produce an installed base'
/usr/local/bin/zoovm stop "$name" --timeout 45 9>&-
set_state configuring
seed_result=$(exec 9>&-; printf '%s\n' "$key" | sudo -n /usr/local/libexec/zoo-macos/base-prepare seed --name "$name" --guest-user "$guest_user")
printf '%s\n' "$seed_result"
identity=$(cksum "$vm/machine.bin")
completed=$(cat "$status/firstboot-complete" 2>/dev/null || true)
if [ ! -f "$vm/zoo-ready" ] && [ "$completed" != "$identity" ] && [[ "$seed_result" != *ZOO_BASE_CONFIGURED=* ]]; then
    set_state booting
    /usr/local/bin/zoovm run "$name" 9>&- &
    runner=$!
    deadline=$((SECONDS + 1800))
    while kill -0 "$runner" 2>/dev/null; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            /usr/local/bin/zoovm stop "$name" --timeout 10 9>&- || true
            fail 'macOS first boot did not finish within 30 minutes'
        fi
        sleep 2 9>&-
    done
    wait "$runner" || fail 'macOS first boot failed; see ~/.zoovm/install.log'
    runner=''
    write_status firstboot-complete "$identity"
fi
set_state booting
nohup /usr/local/bin/zoovm run "$name" 9>&- < /dev/null &
runner=$!
deadline=$((SECONDS + 600))
while [ "$SECONDS" -lt "$deadline" ]; do
    kill -0 "$runner" 2>/dev/null || fail 'the prepared macOS VM stopped before SSH became available'
    address=$(/usr/local/bin/zoovm ip "$name" 9>&- 2>/dev/null || true)
    if [[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && /usr/bin/nc -z -G 2 "$address" 22 9>&-; then
        set_state verifying
        # The API verifies authenticated guest SSH and tools, then marks and stops
        # the template. A listening port alone never makes the base ready.
        exit 0
    fi
    sleep 2 9>&-
done
fail 'the prepared macOS VM did not expose SSH within 10 minutes'
