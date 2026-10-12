# macOS sandboxes

macOS sandboxes are microVMs on Apple Silicon Macs. Each Mac runs `zoovm`, a small helper in this folder built on Apple's Virtualization.framework. The API reaches the Mac over SSH and:

- clones a base VM for each sandbox (APFS clones, so this is instant and uses almost no extra disk);
- boots it headless with the framework's VNC server on `127.0.0.1` of the Mac;
- drives the screen, mouse and keyboard over that VNC server through the SSH tunnel;
- runs shell, file, app and window tools through the guest agent (`zoo-guest`), or over SSH into the guest for a base VM without it.

Because the screen is read and controlled from the host, the guest needs no Screen Recording permission. Window tools need Accessibility (step 4 below).

## Supported hardware and macOS versions

- **Host:** an Apple Silicon Mac (M1 or later; Intel Macs can't run macOS VMs) on macOS 13 or later, with 16 GB of memory (32 GB to run two sandboxes comfortably) and 100 GB free for the base VM and its download.
- **Guest:** the macOS the host supports, up to the host's own version: a Mac on macOS 15 can't install macOS 26 (update the Mac, or pass `--ipsw` with an older restore image). The VNC server zoovm uses is a private Virtualization.framework API that works on hosts from macOS 13 to 26.
- **Limit:** Apple's license allows 2 macOS VMs running at once per Mac, the base VM included. The scheduler counts them: a sandbox created while every Mac is full waits in the queue ("Mac full" on its page) and starts when a VM stops, for up to 2 hours. Create it with `"queue": false` to get a `409 Mac full` error instead.

## Unattended setup

Run the command from **Servers → Add server → Mac** on the Mac, or `zoo node install --macos --key "<the API's public key>"` (the `zoo` CLI from a release). Both run `install.sh --node`, which:

1. checks the Mac, turns on Remote Login and authorizes the API's key;
2. installs zoovm from the release, checking it against the release's `SHA256SUMS` (release builds are signed with a Developer ID and notarized when the repo has the signing secrets; see `.github/workflows/release.yml`);
3. adds the passwordless `pfctl` sudoers rule for network policy;
4. installs the latest macOS into the base VM (about an hour; `--ipsw PATH` for a given version, `--no-base` to skip);
5. prepares the base VM without Setup Assistant: it writes `.AppleSetupDone` and a one-shot LaunchDaemon into the VM's Data volume, which on first boot creates the `admin` user, does what `guest-setup.sh` does and shuts down. A second boot checks SSH, and the base is marked ready with its version. The user's password is kept in `~/.zoovm/vms/zoo-macos-base/password` on the Mac.

What stays manual is the Accessibility grant (step 4 below), which macOS reserves for a person. Without it everything works except window tools, which need it.

The dashboard steps below do the same by hand, and fix a base VM the installer couldn't prepare.

## 1. Build zoovm on the Mac

Requires macOS 13+ on Apple Silicon and the Xcode command line tools.

```bash
git clone <this repo> && cd zoo/macos/zoovm
./build.sh              # installs /usr/local/bin/zoovm; PREFIX=~/.local ./build.sh to install elsewhere
```

## 2. Let the API reach the Mac

- Turn on **System Settings → General → Sharing → Remote Login** on the Mac.
- Put the API's SSH public key in the Mac's `~/.ssh/authorized_keys`.
- Add the Mac's host key to the API's `~/.ssh/known_hosts`, for example with `ssh-keyscan <mac-ip> >> ~/.ssh/known_hosts`.

## 3. Add the Mac and set up the base VM from the dashboard

In **Remote Servers**, choose **macOS** and enter `ssh://you@<mac-ip>` and the Mac's address. The server card gets a **Base VM** panel:

1. **Install macOS** downloads the latest macOS this Mac supports and installs it. Progress shows on the card, and the whole step takes about an hour. The Mac must run at least the macOS version it installs, so update the Mac first if the install fails with "requires a software update".
2. **Start** boots the base VM. **Open screen** shows it live in the browser. Go through Setup Assistant, create a user named `admin` (or set `ZOO_MACOS_USER`), and skip Apple Account, Siri, analytics and FileVault.
3. **Run setup** opens Terminal in the VM and types `guest-setup.sh` with the API's public key. Type the admin password when it asks. The script turns on passwordless sudo, Remote Login with that key (for the admin user and for root), auto-login, and turns off sleep and screen lock. Sandboxes take the admin user's admin rights and sudo away at boot unless created as admin sandboxes, and Zoo uses root's login for its own root commands.
4. In the VM, open **System Settings → Privacy & Security → Accessibility**, click **+**, press Cmd+Shift+G, enter `/bin/zsh` and enable it: the guest agent runs under it, and window tools and `accessibility_tree` need the grant. Install anything else every sandbox should have.
5. **Stop** shuts the base VM down.

Then create sandboxes with type **macOS**. Each one is a clone of the base VM. To change the base later, stop the server's macOS sandboxes and start the base VM again. New sandboxes get the change, and existing ones keep their own disks.

Each base VM has a version, `<macOS version>-<build>-<UTC time it was prepared>`, kept in `~/.zoovm/vms/zoo-macos-base/zoo-ready`. Stopping the base after a change gives it a new one. A sandbox records the version it was cloned from, and its page shows it with how long its last boot took from the stopped base. Clones are APFS clones (`clonefile`), and the API logs each clone's time as `clone_seconds`. The target is under 10 seconds from the stopped base to a booted sandbox.

The same steps work from a terminal on the Mac with `zoovm install zoo-macos-base` and `zoovm run zoo-macos-base`. `run` prints a `vnc://` URL you can open with Screen Sharing.

## Guest agent

When `ZOO_GUEST_REMOTE_URL` is set and the API has the darwin build of `zoo-guest` (`make guest-darwin`, or included in the API image), each boot installs the guest in the VM as a LaunchAgent. Every tool and the Terminal tab go through it; without a connected guest they fail with `guest_unavailable`. The VM must be able to reach that URL. If the API runs on this Mac, use the Mac's address on the VM network, for example `ws://192.168.64.1:8000/guest/connect`. SSH stays only for root commands and for installing the guest.

The guest sends its metrics every 10 seconds, which double as its heartbeat. When a running VM's guest goes quiet and its screen stops answering for a minute, the API restarts the VM. The sandbox's page then says when it did.

## Moving a sandbox to another Mac

Set `ZOO_OBJECT_STORE=s3://<bucket>[/<prefix>]` on the API: AWS S3, or any S3-compatible store with `ZOO_S3_ENDPOINT` (MinIO, R2), with credentials in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. Moving a stopped macOS sandbox works like other moves:
- The source Mac packs the VM into 1 GB parts and uploads them with presigned URLs.
- The target Mac downloads and unpacks them.
- The parts and the source copy are then deleted.

The API never carries the disk. The source Mac needs free space for the packed VM while it uploads.

## zoovm commands

| Command | Does |
| --- | --- |
| `zoovm install <name> [--ipsw P] [--cpu N] [--memory MB] [--disk GB]` | create a VM from an IPSW (latest if omitted) |
| `zoovm clone <source> <name>` | APFS clone with a new machine id and MAC address |
| `zoovm set <name> [--cpu N] [--memory MB]` | resize |
| `zoovm run <name>` | boot headless and serve VNC; runs until the guest shuts down |
| `zoovm stop <name> [--timeout S]` | stop, forcing it after the timeout |
| `zoovm ip <name>` | guest IP from the host's DHCP leases |
| `zoovm vnc <name>` | VNC URL of a running VM |
| `zoovm version <name>` | the macOS version and build it was installed from, as JSON |
| `zoovm list` | JSON list of VMs and their state |
| `zoovm delete <name>` | delete a stopped VM |

VMs live in `~/.zoovm/vms/<name>`.

## Network policy

The Mac enforces each VM's network policy itself, so nothing inside the VM can undo it. The API runs `zoo-guest -egress` on the Mac (from `~/.zoovm/bin`, restarted when the API's build changes). It proxies the VMs' web traffic and DNS, deciding by name, and loads pf rules for the VMs into the `com.apple/zoo` anchor. The pf rules inside the VM stay as a second layer. The Mac's user needs passwordless sudo for `pfctl` only:

```sh
echo "$USER ALL=(root) NOPASSWD: /sbin/pfctl" | sudo tee /etc/sudoers.d/zoo-pf
```

## Limits

- Sandboxes run as a standard user by default, so an agent can't undo the VM's own pf rules or app policy. Create an admin sandbox (the **Guest user** option, or `"admin": true`) when the agent needs sudo. A base VM set up before root logins were added keeps its admin user, since Zoo would otherwise lose root there. Run **Run setup** again on the base VM to fix it.
- A VM's pf rules on the Mac match its address. An admin sandbox can change its address to another VM's, so give admin sandboxes the strictest policy on that Mac or a Mac of their own.
- Safari profiles (`Library/Containers/com.apple.Safari`) need Full Disk Access for `/bin/zsh` in the base VM.
- The VNC server is a private Virtualization.framework API. It works on macOS 13 to 26, but Apple could remove it.
- Moving macOS sandboxes between Macs needs object storage (see above).
- Secrets are written to `~/.zoo/env` in the guest and loaded by `execute_command`. GUI apps don't see them.

## Troubleshooting

1. **"requires a software update" when installing macOS.** The IPSW is newer than the Mac's macOS. Update the Mac, or install an older macOS with `--ipsw PATH` (or `zoovm install zoo-macos-base --ipsw PATH`).
2. **The download or install stops partway.** Run the install again: the IPSW download resumes, and a failed install starts over but keeps the download. It needs about 100 GB free. The log is `~/.zoovm/install.log` when started from the dashboard.
3. **The Mac shows offline, or the API can't SSH in.**
   - Remote Login must be on.
   - The API's key must be in the Mac user's `~/.ssh/authorized_keys`.
   - The Mac's host key must be in the API's `known_hosts` (`ssh-keyscan <mac-ip>`).
   - `zoovm` must be on that user's PATH (`/usr/local/bin` or `/opt/homebrew/bin`).
4. **The base VM never opens SSH after unattended setup.** Start it from the server's page and open its screen.
   - If it's at a login window or Setup Assistant, the first-boot script failed: its log is `/var/log/zoo-firstboot.log` in the VM. Finish with the dashboard steps (**Run setup**).
   - If macOS asks to sign in or set up, the unattended skip didn't cover this macOS version: click through, then run **Run setup**.
5. **Window tools fail with an Accessibility error, or network policy fails with a pfctl error.** Grant Accessibility to `/bin/zsh` and `/usr/libexec/sshd-keygen-wrapper` in the base VM (step 4). Sandboxes cloned before the grant keep their own disks, so recreate them. For pfctl, add the sudoers rule under Network policy (the installer adds it).
