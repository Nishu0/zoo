#!/bin/sh
# Guest-only privileged configuration, shared by the unattended base bootstrap.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
[ "$(id -u)" = 0 ] || exit 1
ME=$1
PASSWORD=$(cat "$2")
KEY=$(cat "$3")
HOME_DIR=$(dscl . -read "/Users/$ME" NFSHomeDirectory | awk '{print $2}')
[ -d "$HOME_DIR" ] || exit 1
# Before the first GUI login there is no usable per-user cfprefsd bootstrap
# context. Write explicit plist files, then give the guest user ownership.
preferences="$HOME_DIR/Library/Preferences"
mkdir -p "$preferences"
chown "$ME:staff" "$HOME_DIR" "$HOME_DIR/Library" "$preferences"
set_preference() {
    file=$1 key=$2 type=$3 value=$4
    /usr/libexec/PlistBuddy -c "Delete :$key" "$file" >/dev/null 2>&1 || true
    /usr/libexec/PlistBuddy -c "Add :$key $type $value" "$file" >/dev/null
}
setup="$preferences/com.apple.SetupAssistant.plist"
for key in DidSeeCloudSetup DidSeeSiriSetup DidSeePrivacy DidSeeScreenTime DidSeeAppearanceSetup \
    DidSeeAccessibility DidSeeActivationLock DidSeeTouchIDSetup DidSeeApplePaySetup DidSeeTermsOfAddress \
    DidSeeLockdownMode DidSeeIntelligence DidSeeWallpaper DidSeeAppStore DidSeeSetupSequence \
    DidSeeSyncSetup DidSeeSyncSetup2 DidSeeiCloudLoginForStorageServices \
    SkipExpressSettingsUpdating SkipFirstLoginOptimization; do
    set_preference "$setup" "$key" bool true
done
version=$(sw_vers -productVersion)
build=$(sw_vers -buildVersion)
# Loginwindow checks these pre-login stamps before honoring individual pane
# flags; without them it launches MiniBuddy with reason 13 on first GUI login.
# Reference: https://lima-vm.io/docs/usage/guests/macos/#suppressing-first-login-setup-screens
for key in LastPreLoginTasksPerformedVersion LastSeenAgeRangeSelectionProductVersion \
    LastSeenCloudProductVersion LastSeenDiagnosticsProductVersion LastSeenSiriProductVersion \
    LastSeenSyncProductVersion LastSeenIntelligenceProductVersion; do
    set_preference "$setup" "$key" string "$version"
done
for key in LastPreLoginTasksPerformedBuild LastSeenBuddyBuildVersion; do
    set_preference "$setup" "$key" string "$build"
done
set_preference "$setup" MiniBuddyLaunchReason integer 0
set_preference "$setup" MiniBuddyShouldLaunchToResumeSetup bool false
set_preference "$setup" MiniBuddyLaunchedPostMigration bool false
chown "$ME:staff" "$setup"
# MiniBuddy also honors this per-user first-login suppression marker. Keep it
# alongside the completed pane/version preferences for newly cloned desktops.
touch "$HOME_DIR/.skipbuddy"
chown "$ME:staff" "$HOME_DIR/.skipbuddy"
# macOS 27 can run MiniBuddy's Apple Account subflow even when every per-user
# pane flag is complete. Apply Apple's SetupAssistant policy at system scope.
# https://developer.apple.com/documentation/devicemanagement/setupassistant
managed_setup=/Library/Preferences/com.apple.SetupAssistant.managed.plist
set_preference "$managed_setup" SkipSetupItems array ''
set_preference "$managed_setup" SkipSetupItems:0 string AppleID
set_preference "$managed_setup" SkipSetupItems:1 string iCloudStorage
set_preference "$managed_setup" SkipCloudSetup bool true
set_preference "$managed_setup" SkipiCloudStorageSetup bool true
# Explicitly establish the bootstrap administrator even if sysadminctl's optional
# group update returned a nonfatal Directory Services warning on first boot.
/usr/sbin/dseditgroup -o edit -a "$ME" -t user admin
# SSH's SACL must keep working when a sandbox later removes administrator rights.
/usr/sbin/dseditgroup -o read com.apple.access_ssh >/dev/null 2>&1 || /usr/sbin/dseditgroup -o create com.apple.access_ssh
/usr/sbin/dseditgroup -o edit -a root -t user com.apple.access_ssh
/usr/sbin/dseditgroup -o edit -a "$ME" -t user com.apple.access_ssh
printf '%s ALL=(ALL) NOPASSWD: ALL\n' "$ME" > /etc/sudoers.d/zoo
chmod 440 /etc/sudoers.d/zoo
/usr/sbin/visudo -cf /etc/sudoers.d/zoo >/dev/null
launchctl enable system/com.openssh.sshd
launchctl bootstrap system /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
for directory in "$HOME_DIR/.ssh" /var/root/.ssh; do
    mkdir -p "$directory"; chmod 700 "$directory"
    printf '%s\n' "$KEY" > "$directory/authorized_keys"; chmod 600 "$directory/authorized_keys"
done
chown -R "$ME:staff" "$HOME_DIR/.ssh"
mkdir -p /etc/ssh/sshd_config.d
printf 'PermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/000-zoo.conf
# Prefer Apple's supported account tool. On a freshly created account its SAC
# service can return EINVAL before any GUI login, leaving the credential absent.
sysadminctl -autologin set -userName "$ME" -password "$PASSWORD" -adminUser "$ME" -adminPassword "$PASSWORD" || true
write_autologin_credential() {
    temporary=$(mktemp /etc/.kcpassword.XXXXXX)
    # loginwindow's legacy credential format: NUL-terminated bytes padded to a
    # multiple of twelve, XORed with its repeating eleven-byte key.
    if ! printf '%s\000' "$PASSWORD" | /usr/bin/od -An -v -tu1 | LC_ALL=C /usr/bin/awk '
        function xorbyte(a, b, result, bit) {
            result = 0; bit = 1
            while (a > 0 || b > 0) {
                if ((a % 2) != (b % 2)) result += bit
                a = int(a / 2); b = int(b / 2); bit *= 2
            }
            return result
        }
        BEGIN { split("125 137 82 35 210 188 221 234 163 185 31", key, " "); count = 0 }
        { for (i = 1; i <= NF; i++) bytes[count++] = $i }
        END {
            while (count % 12) bytes[count++] = 0
            for (i = 0; i < count; i++) printf "%c", xorbyte(bytes[i], key[(i % 11) + 1])
        }
    ' > "$temporary"; then rm -f "$temporary"; return 1; fi
    chmod 600 "$temporary"
    chown root:wheel "$temporary"
    mv -f "$temporary" /etc/kcpassword
}
if [ ! -s /etc/kcpassword ]; then
    if /usr/bin/fdesetup isactive >/dev/null 2>&1; then
        echo 'automatic login cannot be configured while FileVault is active' >&2
        exit 1
    fi
    write_autologin_credential
fi
set_preference /Library/Preferences/com.apple.loginwindow.plist autoLoginUser string "$ME"
[ -s /etc/kcpassword ]
[ "$(/usr/libexec/PlistBuddy -c 'Print :autoLoginUser' /Library/Preferences/com.apple.loginwindow.plist)" = "$ME" ]
pmset -a sleep 0 displaysleep 0 disksleep 0
screensaver="$preferences/com.apple.screensaver.plist"
set_preference "$screensaver" idleTime integer 0
chown "$ME:staff" "$screensaver"
for file in "$preferences"/ByHost/com.apple.screensaver.*.plist; do
    [ -f "$file" ] || continue
    set_preference "$file" idleTime integer 0
    chown "$ME:staff" "$file"
done
set_preference /Library/Preferences/com.apple.screensaver.plist loginWindowIdleTime integer 0
sudo -u "$ME" -H sysadminctl -screenLock off -password "$PASSWORD" || true
mkdir -p "$HOME_DIR/.zoo/bin"
install -m 700 /var/db/zoo/guest-reidentify.sh "$HOME_DIR/.zoo/bin/reidentify"
chown -R "$ME:staff" "$HOME_DIR/.zoo"
