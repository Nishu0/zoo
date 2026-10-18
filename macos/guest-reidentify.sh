#!/bin/sh
set -eu
umask 077
directory="$HOME/.zoo"
label="com.zoo.guest"
plist="$HOME/Library/LaunchAgents/$label.plist"
rm -f "$directory/identity.ready"
temporary=$(mktemp "$directory/guest.env.XXXXXX")
trap 'rm -f "$temporary"' EXIT HUP INT TERM
cat > "$temporary"
/usr/bin/awk '
    {
        separator = index($0, "=")
        key = substr($0, 1, separator - 1)
        value = substr($0, separator + 1)
        if (separator == 0 || value == "" || key !~ /^(ZOO_GUEST_URL|ZOO_GUEST_TOKEN|ZOO_SANDBOX_ID)$/ || seen[key]++) exit 1
    }
    END { if (!seen["ZOO_GUEST_URL"] || !seen["ZOO_GUEST_TOKEN"] || !seen["ZOO_SANDBOX_ID"]) exit 1 }
' "$temporary" || { echo "reidentify requires exactly the three fresh ZOO guest identity fields" >&2; exit 1; }
test -x "$directory/bin/zoo-guest" && test -f "$plist"
uid=$(id -u)
launchctl bootout "gui/$uid/$label"
mv -f "$temporary" "$directory/guest.env"
launchctl bootstrap "gui/$uid" "$plist"
identity=$(/usr/bin/sed -n 's/^ZOO_SANDBOX_ID=//p' "$directory/guest.env")
printf '%s\n' "$identity" > "$directory/identity.ready"
printf 'ZOO_REIDENTIFIED=%s\n' "$identity"
