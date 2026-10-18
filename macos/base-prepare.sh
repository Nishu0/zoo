#!/bin/bash
# shellcheck disable=SC2015
# Installed root-owned during enrollment. The control plane supplies only its public SSH key.
set -euo pipefail
PATH=/usr/bin:/bin:/usr/sbin:/sbin
ROOT=/usr/local/libexec/zoo-macos
fail() { printf 'zoo base preparation: %s\n' "$*" >&2; exit 1; }
valid_user() { [[ "$1" =~ ^[a-zA-Z_][a-zA-Z0-9_-]{0,30}$ ]]; }
[ "$(id -u)" = 0 ] || fail 'run with sudo'
case "${1:-}" in
    install)
        [ "$#" = 2 ] && valid_user "$2" || fail 'install needs the enrolled host account'
        host_user=$2
        id "$host_user" >/dev/null || fail 'host account does not exist'
        source_dir=$(cd "$(dirname "$0")" && pwd -P)
        [ ! -L "$ROOT" ] || fail 'helper directory cannot be a symlink'
        install -d -o root -g wheel -m 755 "$ROOT"
        staged=''
        trap '[ -z "$staged" ] || rm -f "$staged"' EXIT
        install_atomically() {
            # A running shell may still read its script. Replace its directory
            # entry only after the complete root-owned replacement is ready.
            staged=$(mktemp "$ROOT/.install.XXXXXX")
            install -o root -g wheel -m 755 "$1" "$staged"
            mv -f "$staged" "$2"
            staged=''
        }
        for script in base-prepare firstboot configure-guest guest-reidentify prepare-base; do
            [ -f "$source_dir/$script.sh" ] && [ ! -L "$source_dir/$script.sh" ] || fail "missing $script.sh"
            install_atomically "$source_dir/$script.sh" "$ROOT/$script.sh"
        done
        # A stable extensionless entry point makes the allowed sudo command unambiguous.
        install_atomically "$source_dir/base-prepare.sh" "$ROOT/base-prepare"
        rule=$(mktemp)
        trap 'rm -f "$rule"' EXIT
        printf '%s ALL=(root) NOPASSWD: %s/base-prepare seed *\n' "$host_user" "$ROOT" > "$rule"
        /usr/sbin/visudo -cf "$rule" >/dev/null || fail 'invalid helper sudo rule'
        install -d -o root -g wheel -m 755 /etc/sudoers.d
        install -o root -g wheel -m 440 "$rule" /etc/sudoers.d/zoo-base-prepare
        exit 0 ;;
    seed) shift ;;
    *) fail 'expected install or seed' ;;
esac
host_user=${SUDO_USER:-}
valid_user "$host_user" && [ "$host_user" != root ] || fail 'invoke this helper through sudo from the enrolled account'
name=''
guest_user='admin'
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || fail 'missing option value'
    case "$1" in --name) name=$2 ;; --guest-user) guest_user=$2 ;; *) fail 'unknown option' ;; esac
    shift 2
done
[[ "$name" =~ ^zoo-[a-zA-Z0-9][a-zA-Z0-9_-]{0,119}$ ]] || fail 'invalid Zoo base VM name'
valid_user "$guest_user" && [ "$guest_user" != root ] || fail 'invalid guest user'
host_home=$(/usr/bin/dscl . -read "/Users/$host_user" NFSHomeDirectory | /usr/bin/sed -n 's/^NFSHomeDirectory: //p')
[ -d "$host_home" ] && [ ! -L "$host_home" ] || fail 'invalid host home'
host_uid=$(/usr/bin/id -u "$host_user")
[ "$(/usr/bin/stat -f %u "$host_home")" = "$host_uid" ] || fail 'host account must own its home'
vm="$host_home/.zoovm/vms/$name"
for path in "$host_home/.zoovm" "$host_home/.zoovm/vms" "$vm" "$vm/disk.img"; do
    [ -e "$path" ] && [ ! -L "$path" ] || fail 'VM bundle must exist and contain no symlink paths'
    [ "$(/usr/bin/stat -f %u "$path")" = "$host_uid" ] || fail 'host account must own its VM bundle and disk'
done
[ -f "$vm/disk.img" ] || fail 'VM disk must be a regular file'
[ -f "$vm/installed" ] || fail 'restore macOS before preparing the base'
[ ! -e "$vm/.lease" ] && [ ! -e "$vm/slot-lease.json" ] && [ ! -e "$vm/saved.json" ] || fail 'cannot prepare a saved or leased VM'
if [ -f "$vm/zoo-ready" ]; then printf 'ZOO_BASE_ALREADY_READY=%s\n' "$name"; exit 0; fi
was_seeded=0
if [ -f "$vm/zoo-seeded" ]; then was_seeded=1; fi
if [ -f "$vm/pid" ]; then
    process=$(cat "$vm/pid")
    [[ "$process" =~ ^[0-9]+$ ]] || fail 'invalid VM pid'
    if kill -0 "$process" 2>/dev/null; then
        if [ "$was_seeded" = 1 ]; then printf 'ZOO_BASE_SEEDED_RUNNING=%s\n' "$name"; exit 0; fi
        fail 'stop the base before preparing it'
    fi
fi
IFS= read -r key || fail 'supply the control-plane public key on stdin'
[[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+$ ]] || fail 'expected the control-plane Ed25519 public key'
bootstrap_key=''
IFS= read -r bootstrap_key || true
if [ -n "$bootstrap_key" ]; then
    [[ "$bootstrap_key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+$ ]] || fail 'invalid optional bootstrap public key'
fi
# Read no scripts, paths, private keys or passwords from the caller.
work=$(mktemp -d)
dev=''
mounted=0
unmount_guest_volume() { /usr/sbin/diskutil unmount "$work/data" >/dev/null 2>&1; }
detach_guest_disk() { /usr/bin/hdiutil detach "$dev" >/dev/null 2>&1; }
close_guest_disk() {
    if [ "$mounted" = 1 ] && unmount_guest_volume; then mounted=0; fi
    if [ -n "$dev" ]; then
        if detach_guest_disk; then dev=''; mounted=0
        else
            printf 'zoo base preparation: could not detach the guest disk; it remains attached. Do not boot the VM until it is detached.\n' >&2
            return 1
        fi
    fi
    return 0
}
cleanup() {
    original=$?
    trap - EXIT
    if ! close_guest_disk && [ "$original" = 0 ]; then original=1; fi
    # Never recurse into a busy/nonempty mountpoint after a cleanup failure.
    rmdir "$work/data" "$work" >/dev/null 2>&1 || true
    exit "$original"
}
finish_seed() {
    close_guest_disk
    printf '%s=%s\n' "$1" "$name"
}
trap cleanup EXIT
mkdir "$work/data"
dev=$(/usr/bin/hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount "$vm/disk.img" | awk 'NR==1 {print $1}')
[ -n "$dev" ] || fail 'could not attach the restored disk'
container=$(/usr/sbin/diskutil list "$dev" | awk '$2 == "Apple_APFS" {for (i = 3; i < NF; i++) if ($i == "Container") print $(i + 1)}' | head -n1)
[ -n "$container" ] || fail 'no restored APFS container'
data=$(/usr/sbin/diskutil apfs list "$container" | awk '/\(Data\)/ {print $(NF - 1)}' | head -n1)
[ -n "$data" ] || fail 'no restored Data volume'
/usr/sbin/diskutil mount -mountPoint "$work/data" "$data" >/dev/null
mounted=1
/usr/sbin/diskutil enableOwnership "$work/data" >/dev/null
mnt="$work/data"
if [ "$was_seeded" = 1 ] && [ -f "$mnt/private/var/db/zoo/done" ]; then
    finish_seed ZOO_BASE_CONFIGURED
    exit 0
fi
# Refuse symlinks in guest paths that this privileged helper writes.
for relative in private private/var private/var/db private/var/db/zoo Library Library/LaunchDaemons; do
    [ ! -L "$mnt/$relative" ] || fail 'unexpected symlink in restored guest'
    if [ -e "$mnt/$relative" ]; then [ -d "$mnt/$relative" ] || fail 'unexpected non-directory in restored guest'
    else mkdir "$mnt/$relative"; fi
done
db="$mnt/private/var/db"
install_guest_payload() {
for script in firstboot configure-guest guest-reidentify; do
    install -o root -g wheel -m 700 "$ROOT/$script.sh" "$db/zoo/$script.sh"
done
cat > "$mnt/Library/LaunchDaemons/com.zoo.firstboot.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.zoo.firstboot</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>/var/db/zoo/firstboot.sh</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
PLIST
chown root:wheel "$mnt/Library/LaunchDaemons/com.zoo.firstboot.plist"
chmod 644 "$mnt/Library/LaunchDaemons/com.zoo.firstboot.plist"
}
if [ "$was_seeded" = 1 ]; then
    # Refresh only our fixed scripts after a failed/interrupted first boot. Keep
    # staged user, password and both public identities intact for the retry.
    for target in "$db/zoo/firstboot.sh" "$db/zoo/configure-guest.sh" "$db/zoo/guest-reidentify.sh" "$mnt/Library/LaunchDaemons/com.zoo.firstboot.plist"; do
        [ ! -L "$target" ] || fail 'unexpected symlink in guest preparation files'
    done
    install_guest_payload
    finish_seed ZOO_BASE_SEEDED
    exit 0
fi
[ ! -e "$db/.AppleSetupDone" ] || cmp -s "$ROOT/firstboot.sh" "$db/zoo/firstboot.sh" || fail 'existing guest setup will not be overwritten'
for target in "$db/.AppleSetupDone" "$db/zoo/key" "$db/zoo/user" "$db/zoo/password" "$db/zoo/firstboot.sh" "$db/zoo/configure-guest.sh" "$db/zoo/guest-reidentify.sh" "$mnt/Library/LaunchDaemons/com.zoo.firstboot.plist"; do
    [ ! -L "$target" ] || fail 'unexpected symlink in guest preparation files'
done
umask 077
password=$(/usr/bin/openssl rand -hex 18)
printf '%s\n' "$password" | /usr/bin/sudo -u "$host_user" -H /bin/sh -c 'umask 077; cat > "$1"' sh "$vm/password"
printf '%s\n' "$key" > "$db/zoo/key"
if [ -n "$bootstrap_key" ]; then printf '%s\n' "$bootstrap_key" >> "$db/zoo/key"; fi
printf '%s\n' "$guest_user" > "$db/zoo/user"
printf '%s\n' "$password" > "$db/zoo/password"
install_guest_payload
chown -R root:wheel "$db/zoo"
chmod 700 "$db/zoo"
chmod 600 "$db/zoo/key" "$db/zoo/user" "$db/zoo/password"
touch "$db/.AppleSetupDone"
printf 'seeded\n' | /usr/bin/sudo -u "$host_user" -H /bin/sh -c 'umask 077; cat > "$1"' sh "$vm/zoo-seeded"
finish_seed ZOO_BASE_SEEDED
