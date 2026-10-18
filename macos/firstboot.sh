#!/bin/sh
# Runs as root inside a freshly restored base. On failure retain state for a retry.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
umask 077
exec >>/var/log/zoo-firstboot.log 2>&1
state=/var/db/zoo
failure() {
    code=$?
    if [ "$code" -ne 0 ]; then printf 'failed:%s\n' "$code" > "$state/status"; fi
}
trap failure EXIT
if [ -f "$state/done" ]; then exit 0; fi
printf 'configuring\n' > "$state/status"
ME=$(cat "$state/user")
PASSWORD=$(cat "$state/password")
i=0
until dscl . -list /Users >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -lt 60 ] || exit 1; sleep 2
done
if ! id "$ME" >/dev/null 2>&1; then
    sysadminctl -addUser "$ME" -fullName "$ME" -password "$PASSWORD" -admin
fi
# sysadminctl can print a failure while returning zero. Require the account and
# staged password to authenticate before declaring this bootstrap successful.
id "$ME" >/dev/null
/usr/bin/dscl . -authonly "$ME" "$PASSWORD"
createhomedir -c -u "$ME" >/dev/null
/bin/sh "$state/configure-guest.sh" "$ME" "$state/password" "$state/key"
touch "$state/done"
printf 'configured\n' > "$state/status"
rm -f /Library/LaunchDaemons/com.zoo.firstboot.plist "$state/password"
echo 'zoo first boot: guest account, SSH and autologin configured'
shutdown -h now
