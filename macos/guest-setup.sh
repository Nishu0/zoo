#!/bin/sh
# Prepares the macOS base VM for Zoo. Run once inside the guest, in Terminal, as the admin user.
# The dashboard's "Run setup" button types this for you.
#   sh guest-setup.sh "<API public key>" ["<this user's password>"]
set -e
KEY="$1"
PASSWORD="$2"
if [ -z "$KEY" ]; then
    echo "usage: sh guest-setup.sh \"<ssh public key>\" [\"<password>\"]" >&2
    exit 1
fi
if [ -z "$PASSWORD" ]; then
    printf "Password for %s: " "$(whoami)"
    stty -echo
    read -r PASSWORD
    stty echo
    echo
fi
ME="$(whoami)"

echo "$PASSWORD" | sudo -S -v
echo "$ME ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/zoo >/dev/null
sudo chmod 440 /etc/sudoers.d/zoo

# Remote Login, so Zoo can run commands and move files.
sudo launchctl enable system/com.openssh.sshd
sudo launchctl bootstrap system /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
mkdir -p ~/.ssh && chmod 700 ~/.ssh
grep -qxF "$KEY" ~/.ssh/authorized_keys 2>/dev/null || echo "$KEY" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
# The same key for root, so Zoo keeps root in sandboxes whose user it takes admin rights away from (the default).
sudo mkdir -p /var/root/.ssh && sudo chmod 700 /var/root/.ssh
sudo grep -qxF "$KEY" /var/root/.ssh/authorized_keys 2>/dev/null || printf '%s\n' "$KEY" | sudo tee -a /var/root/.ssh/authorized_keys >/dev/null
sudo chmod 600 /var/root/.ssh/authorized_keys
echo "PermitRootLogin prohibit-password" | sudo tee /etc/ssh/sshd_config.d/000-zoo.conf >/dev/null

# Log straight into the desktop on boot and never sleep or lock.
sudo sysadminctl -autologin set -userName "$ME" -password "$PASSWORD"
sudo pmset -a sleep 0 displaysleep 0 disksleep 0
defaults -currentHost write com.apple.screensaver idleTime 0
sudo defaults write /Library/Preferences/com.apple.screensaver loginWindowIdleTime 0
sysadminctl -screenLock off -password "$PASSWORD" 2>/dev/null || true

mkdir -p "$HOME/.zoo/bin"
cat > "$HOME/.zoo/bin/reidentify" <<'ZOO_REIDENTIFY'
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
ZOO_REIDENTIFY
chmod 700 "$HOME/.zoo/bin/reidentify"

echo "done. Grant Accessibility to /bin/zsh; Zoo verifies the guest connection and grant before marking the base ready."
