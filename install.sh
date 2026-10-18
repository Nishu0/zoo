#!/usr/bin/env bash
# `[ … ] && [ … ] || die` checks: die runs whenever a check fails, which is the intent
# shellcheck disable=SC2015
set -euo pipefail

REPO="chann44/zoo"
KATA_VERSION="4.2.0"
KATA_GO_SHA_AMD64="7dda31ca54b397cbf8165f620d6041872d3c45b7a77383ce0e86f76a06e103d0"
KATA_GO_SHA_ARM64="ae5131fa8d231bd4d2808c9b175d17fe87e1ef42daaa838e4bd80b6d4b6df8dd"
DOCKER_VERSION="29.9.0"
COMPOSE_VERSION="v5.6.0"
K3S_VERSION="v1.37.1+k3s1"
HELM_VERSION="v4.3.0"
TOPOLVM_VERSION="17.3.0"
CERT_MANAGER_VERSION="v1.21.2"
SNAPSHOT_VERSION="v8.6.0"
POSTGRES_IMAGE="postgres:17"
SEAWEED_IMAGE="chrislusf/seaweedfs:4.47"
ZOO_HOME="${ZOO_HOME:-/opt/zoo}"
ZOO_VERSION="${ZOO_VERSION:-}"
NATIVE_VERSION_REQUESTED=0
if [ -n "$ZOO_VERSION" ]; then NATIVE_VERSION_REQUESTED=1; fi
ZOO_REGISTRY="${ZOO_REGISTRY:-docker.io/chann44}"
ADMIN_EMAILS="${ADMIN_EMAILS:-}"
ZOO_DOMAIN="${ZOO_DOMAIN:-}"
RUNTIME=docker
NAMESPACE=zoo
DISK_GB=100
DEVICE=""
BUNDLE=""
WORK=""
NODE=0
REPAIR_MACOS_HELPER=0
NODE_KEY=""
NODE_TOKEN=""
CONTROL_PLANE=""
NODE_USER=""
ADMIN_PASSWORD=""
CHECK_ONLY=0
FAILED=0
IPSW=""
BASE=1
HOST_ADDRESS=""
HAS_KVM=0
DOCS="https://github.com/$REPO"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok    %s\n' "$*"; }
note() { printf '  note  %s\n' "$*"; }
fail() { printf '  fail  %s\n' "$*" >&2; FAILED=1; }

usage() {
    cat <<'USAGE'
Install Zoo on Linux (Kata micro-VMs with KVM, plain Docker containers without), or on a Mac with
Docker Desktop (Linux sandboxes as containers; add this Mac as a server for macOS sandboxes):
  install.sh [--runtime docker|kube] [--version VERSION] [--dir PATH]
             [--admin-email EMAIL] [--domain HOST] [--check]
  install.sh --runtime kube [--device EMPTY_BLOCK_DEVICE | --disk-gb GB]
  install.sh --bundle PATH [--runtime docker|kube]

Bundles contain binaries, charts, manifests and both runtimes' images for one
architecture. PATH accepts a .tar.zst archive or its .parts index. Offline hosts
must already have python3, openssl, zstd, tar, systemd and iptables; kube also
needs LVM2, losetup, truncate and modprobe. Offline HTTPS uses a local certificate
authority. The loop-file storage option is for development, not durable storage.

Add a Linux server (Docker), or a Mac (Docker and native macOS on Apple Silicon):
  install.sh --node --token TOKEN --control-plane URL --key SSH_PUBLIC_KEY
             [--user USER] [--version VERSION] [--ipsw PATH] [--no-base]
Refresh or resume an enrolled Mac with the same setup command (no new token):
  install.sh --node [--control-plane URL] [--user USER] [--version VERSION]
USAGE
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version|--dir|--admin-email|--domain|--runtime|--device|--disk-gb|--bundle|--key|--token|--control-plane|--user|--ipsw)
                [ "$#" -ge 2 ] && [ -n "$2" ] || die "$1 needs a value"
                case "$1" in
                    --version) ZOO_VERSION="${2#v}"; NATIVE_VERSION_REQUESTED=1 ;;
                    --dir) ZOO_HOME="$2" ;;
                    --admin-email) ADMIN_EMAILS="$2" ;;
                    --domain) ZOO_DOMAIN="$2" ;;
                    --runtime) RUNTIME="$2" ;;
                    --device) DEVICE="$2" ;;
                    --disk-gb) DISK_GB="$2" ;;
                    --bundle) BUNDLE="$2" ;;
                    --key) NODE_KEY="$2" ;;
                    --token) NODE_TOKEN="$2" ;;
                    --control-plane) CONTROL_PLANE="${2%/}" ;;
                    --user) NODE_USER="$2" ;;
                    --ipsw) IPSW="$2" ;;
                esac
                shift 2 ;;
            --node) NODE=1; shift ;;
            --repair-macos-helper) REPAIR_MACOS_HELPER=1; NODE=1; shift ;;
            --no-base) BASE=0; shift ;;
            --check) CHECK_ONLY=1; shift ;;
            -h|--help) usage; exit 0 ;;
            *) die "unknown option: $1" ;;
        esac
    done
    case "$RUNTIME" in docker|kube) ;; *) die "--runtime must be docker or kube" ;; esac
    [[ "$ZOO_HOME" =~ ^/[A-Za-z0-9_./-]+$ ]] || die "--dir must be an absolute path without spaces or shell characters"
    [[ "$DISK_GB" =~ ^[0-9]+$ ]] && [ "$DISK_GB" -ge 80 ] || die "--disk-gb must be an integer of at least 80"
    if [ -n "$ZOO_DOMAIN" ]; then
        [[ "$ZOO_DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "--domain must be a hostname"
    fi
    [[ "$ADMIN_EMAILS" != *$'\n'* ]] && [[ "$ADMIN_EMAILS" != *$'\r'* ]] || die "--admin-email must be one line"
    [ -z "$DEVICE" ] || [ "$RUNTIME" = kube ] || die "--device requires --runtime kube"
    if [ "$NODE" = 0 ] && [ "$RUNTIME" = kube ] && [ -n "$ZOO_DOMAIN" ] && [ -z "$BUNDLE" ]; then
        [ -n "$ADMIN_EMAILS" ] || die "--domain on Kubernetes requires --admin-email for ACME"
    fi
    if [ "$REPAIR_MACOS_HELPER" = 1 ]; then
        [ "$(uname -s)" = Darwin ] || die "--repair-macos-helper requires macOS"
        [ -n "$CONTROL_PLANE" ] || die "--repair-macos-helper needs --control-plane"
    fi
    if [ "$NODE" = 1 ]; then
        case "$(uname -s)" in Linux|Darwin) ;; *) die "Windows hosts use windows/install-node.ps1" ;; esac
        if [ "$(uname -s)" = Linux ]; then
            [ -n "$NODE_TOKEN" ] && [ -n "$CONTROL_PLANE" ] && [ -n "$NODE_KEY" ] || die "--node needs --token, --control-plane and the API-provided --key"
        fi
        [ -z "$BUNDLE" ] || die "--bundle installs the Linux control plane; use the API's server join command"
    fi
}

need_disk() {
    local free
    free=$(df -Pk "$1" | awk 'NR==2 {print int($4 / 1048576)}')
    if [ "$free" -ge "$2" ]; then ok "$free GB free on $1"; else fail "$free GB free on $1, needs $2 GB"; fi
}

macos_node_disk() {
    local base="$1/.zoovm/vms/${ZOO_MACOS_BASE:-zoo-macos-base}"
    if [ "$BASE" = 0 ]; then
        need_disk "$1" 1
        note "base VM preparation deferred; a new base needs 80 GB free"
    elif [ -f "$base/installed" ] && [ -f "$base/disk.img" ]; then
        need_disk "$1" 20
        note "reusing the installed macOS base VM; keeping 20 GB free for setup and growth"
    else
        need_disk "$1" 80
    fi
}

need_memory() {
    if [ "$1" -lt "$2" ]; then fail "$1 GB memory, needs $2 GB"
    elif [ "$1" -lt "$3" ]; then note "$1 GB memory; $3 GB or more recommended"
    else ok "$1 GB memory"; fi
}

reach_control_plane() {
    if curl -fsS --max-time 10 -o /dev/null "$CONTROL_PLANE/healthz"; then ok "control plane reachable"
    else fail "can't reach $CONTROL_PLANE"; fi
}

preflight_done() {
    [ "$FAILED" = 0 ] || die "pre-flight checks failed; nothing was installed. See $DOCS"
    if [ "$CHECK_ONLY" = 1 ]; then say "Pre-flight checks passed"; exit 0; fi
}

fetch() {
    mkdir -p "$(dirname "$2")"
    curl -fL --retry 3 --connect-timeout 15 "$1" -o "$2"
}

write_image_compose() {
    # The installer ships an image-only deployment. Build contexts belong to the source checkout.
    python3 - "$1" "$2" <<'PY'
import pathlib, sys
source, destination = map(pathlib.Path, sys.argv[1:])
lines = source.read_text().splitlines(keepends=True)
output = []
inside_build = False
for line in lines:
    if line.startswith("    build:"):
        inside_build = not line.removeprefix("    build:").strip()
        continue
    if inside_build:
        if not line.strip() or len(line) - len(line.lstrip()) > 4:
            continue
        inside_build = False
    output.append(line)
text = "".join(output)
text = text.replace(
    "# Images come from ZOO_REGISTRY at ZOO_VERSION. `docker compose up --build` builds them from this checkout instead.",
    "# Runs published images from ZOO_REGISTRY at ZOO_VERSION; no source checkout is required.",
)
destination.write_text(text)
PY
}

check_sha() {
    printf '%s  %s\n' "$1" "$2" | sha256sum --check --status
}

component_manifest() {
    python3 - "$1" "$ZOO_VERSION" "$ARCH" "$ZOO_REGISTRY" "$KATA_VERSION" "$DOCKER_VERSION" "$COMPOSE_VERSION" "$K3S_VERSION" "$HELM_VERSION" "$TOPOLVM_VERSION" "$CERT_MANAGER_VERSION" "$SNAPSHOT_VERSION" <<'PY'
import json, sys
keys = ["version", "arch", "registry", "kata", "docker", "compose", "k3s", "helm", "topolvm", "cert_manager", "snapshot"]
with open(sys.argv[1], "w") as target:
    json.dump(dict(zip(keys, sys.argv[2:], strict=True)), target, indent=2)
    target.write("\n")
PY
}

write_kata_values() {
    cat > "$1" <<EOF
image:
  reference: quay.io/kata-containers/kata-deploy
  tag: "$KATA_VERSION"
imagePullPolicy: IfNotPresent
kubectlImage:
  reference: registry.k8s.io/kubectl
  tag: "${K3S_VERSION%+*}"
k8sDistribution: k3s
nodeSelector:
  kubernetes.io/arch: amd64
shims:
  disableAll: true
  clh:
    enabled: true
    dropIn: |
      [hypervisor.clh]
      shared_fs = "virtio-fs"
      enable_mem_prealloc = false
      reclaim_guest_freed_memory = true
      [factory]
      enable_template = false
    runtimeClass:
      nodeSelector:
        kubernetes.io/arch: amd64
defaultShim:
  amd64: clh
runtimeClasses:
  enabled: true
  createDefault: true
  defaultName: kata
node-feature-discovery:
  enabled: false
EOF
}

write_topolvm_values() {
    cat > "$1" <<'EOF'
cert-manager:
  enabled: false
lvmd:
  deviceClasses:
    - name: zoo-thin
      volume-group: zoo
      default: true
      spare-gb: 1
      type: thin
      thin-pool:
        name: thinpool
        overprovision-ratio: 1.0
storageClasses:
  - name: zoo-local
    storageClass:
      fsType: ext4
      isDefaultClass: true
      volumeBindingMode: WaitForFirstConsumer
      allowVolumeExpansion: true
EOF
}

prepare_components() {
    local root="$1" selection="$2" machine_arch binary_name kata_sha
    mkdir -p "$root/artifacts/$ARCH" "$root/charts" "$root/manifests"
    case "$ARCH" in
        amd64) machine_arch=x86_64; binary_name=k3s; kata_sha="$KATA_GO_SHA_AMD64" ;;
        arm64) machine_arch=aarch64; binary_name=k3s-arm64; kata_sha="$KATA_GO_SHA_ARM64" ;;
        *) die "unsupported architecture $ARCH" ;;
    esac
    if [ "$selection" = docker ] || [ "$selection" = all ]; then
        fetch "https://download.docker.com/linux/static/stable/$machine_arch/docker-$DOCKER_VERSION.tgz" "$root/artifacts/$ARCH/docker.tgz"
        fetch "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-$machine_arch" "$root/artifacts/$ARCH/docker-compose"
        fetch "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-$machine_arch.sha256" "$root/artifacts/$ARCH/compose.sha256"
        check_sha "$(awk '{print $1}' "$root/artifacts/$ARCH/compose.sha256")" "$root/artifacts/$ARCH/docker-compose"
        fetch "https://github.com/kata-containers/kata-containers/releases/download/$KATA_VERSION/kata-go-static-$KATA_VERSION-$ARCH.tar.zst" "$root/artifacts/$ARCH/kata.tar.zst"
        check_sha "$kata_sha" "$root/artifacts/$ARCH/kata.tar.zst"
    fi
    if [ "$selection" = kube ] || [ "$selection" = all ]; then
        fetch "https://get.helm.sh/helm-$HELM_VERSION-linux-$ARCH.tar.gz" "$root/artifacts/$ARCH/helm.tar.gz"
        fetch "https://get.helm.sh/helm-$HELM_VERSION-linux-$ARCH.tar.gz.sha256sum" "$root/artifacts/$ARCH/helm.sha256"
        check_sha "$(awk '{print $1}' "$root/artifacts/$ARCH/helm.sha256")" "$root/artifacts/$ARCH/helm.tar.gz"
        fetch "https://github.com/k3s-io/k3s/releases/download/$K3S_VERSION/$binary_name" "$root/artifacts/$ARCH/k3s"
        fetch "https://github.com/k3s-io/k3s/releases/download/$K3S_VERSION/k3s-airgap-images-$ARCH.tar.zst" "$root/artifacts/$ARCH/k3s-airgap.tar.zst"
        fetch "https://github.com/k3s-io/k3s/releases/download/$K3S_VERSION/sha256sum-$ARCH.txt" "$root/artifacts/$ARCH/k3s.sha256"
        check_sha "$(awk -v name="$binary_name" '$2 == name || $2 == ("*" name) {print $1}' "$root/artifacts/$ARCH/k3s.sha256")" "$root/artifacts/$ARCH/k3s"
        check_sha "$(awk -v name="k3s-airgap-images-$ARCH.tar.zst" '$2 == name || $2 == ("*" name) {print $1}' "$root/artifacts/$ARCH/k3s.sha256")" "$root/artifacts/$ARCH/k3s-airgap.tar.zst"
        fetch "https://raw.githubusercontent.com/k3s-io/k3s/$K3S_VERSION/install.sh" "$root/artifacts/$ARCH/install-k3s.sh"
        fetch "https://github.com/kata-containers/kata-containers/releases/download/$KATA_VERSION/kata-deploy-$KATA_VERSION.tgz" "$root/charts/kata-deploy.tgz"
        check_sha "52a3e9139e810d58867a7676fb38957870dd09e8b2033e54cebdda7c97598e8c" "$root/charts/kata-deploy.tgz"
        fetch "https://github.com/topolvm/topolvm/releases/download/topolvm-chart-v$TOPOLVM_VERSION/topolvm-$TOPOLVM_VERSION.tgz" "$root/charts/topolvm.tgz"
        check_sha "d51eb34db2e676f21a6e5a814bc8cc7ed41cd0f6505fab92956f06155f2d65ac" "$root/charts/topolvm.tgz"
        fetch "https://github.com/cert-manager/cert-manager/releases/download/$CERT_MANAGER_VERSION/cert-manager.yaml" "$root/manifests/cert-manager.yaml"
        for resource in volumesnapshotclasses volumesnapshotcontents volumesnapshots; do
            fetch "https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/$SNAPSHOT_VERSION/client/config/crd/snapshot.storage.k8s.io_$resource.yaml" "$root/manifests/$resource.yaml"
        done
        for resource in rbac-snapshot-controller setup-snapshot-controller; do
            fetch "https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/$SNAPSHOT_VERSION/deploy/kubernetes/snapshot-controller/$resource.yaml" "$root/manifests/$resource.yaml"
        done
        write_kata_values "$root/kata-values.yaml"
        write_topolvm_values "$root/topolvm-values.yaml"
    fi
}

unpack_bundle() {
    local archive="$BUNDLE"
    case "$BUNDLE" in
        *.parts)
            python3 - "$BUNDLE" "$WORK/bundle.tar.zst" <<'PY'
import hashlib, json, pathlib, shutil, sys
index = pathlib.Path(sys.argv[1]).resolve()
parts = json.loads(index.read_text())
with open(sys.argv[2], "wb") as output:
    for part in parts:
        name = part["file"]
        if pathlib.Path(name).name != name:
            raise SystemExit("invalid bundle part filename")
        source = index.parent / name
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != part["sha256"]:
            raise SystemExit(f"checksum mismatch: {name}")
PY
            archive="$WORK/bundle.tar.zst" ;;
        *.tar.zst) ;;
        *) die "--bundle needs a .tar.zst archive or .parts index" ;;
    esac
    python3 - "$archive" <<'PY'
import pathlib, subprocess, sys
entries = subprocess.check_output(["tar", "--zstd", "-tf", sys.argv[1]], text=True).splitlines()
for entry in entries:
    path = pathlib.PurePosixPath(entry)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("unsafe bundle archive path")
PY
    mkdir -p "$WORK/bundle"
    tar --zstd -xf "$archive" -C "$WORK/bundle" --no-same-owner
    PAYLOAD="$WORK/bundle"
    (cd "$PAYLOAD" && sha256sum --check --quiet SHA256SUMS)
    python3 - "$PAYLOAD/bundle.json" "$ARCH" "$ZOO_VERSION" "$KATA_VERSION" "$DOCKER_VERSION" "$COMPOSE_VERSION" "$K3S_VERSION" "$HELM_VERSION" "$TOPOLVM_VERSION" "$CERT_MANAGER_VERSION" "$SNAPSHOT_VERSION" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
keys = ["arch", "version", "kata", "docker", "compose", "k3s", "helm", "topolvm", "cert_manager", "snapshot"]
for key, expected in zip(keys, sys.argv[2:], strict=True):
    if expected and manifest.get(key) != expected:
        raise SystemExit(f"bundle {key}: expected {expected}, got {manifest.get(key)}")
PY
    ZOO_VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PAYLOAD/bundle.json")
    ZOO_REGISTRY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["registry"])' "$PAYLOAD/bundle.json")
}

prepare_payload() {
    if [ -n "$BUNDLE" ]; then unpack_bundle; return; fi
    if [ -z "$ZOO_VERSION" ]; then
        ZOO_VERSION=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest")
        ZOO_VERSION="${ZOO_VERSION##*/v}"
    fi
    [[ "$ZOO_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die "invalid Zoo release version"
    PAYLOAD="$WORK/payload"
    mkdir -p "$PAYLOAD"
    fetch "https://github.com/$REPO/archive/refs/tags/v$ZOO_VERSION.tar.gz" "$WORK/zoo-source.tar.gz"
    tar -xzf "$WORK/zoo-source.tar.gz" -C "$PAYLOAD" --strip-components=1
    prepare_components "$PAYLOAD" "$RUNTIME"
    stamp_chart "$PAYLOAD/deploy/helm/zoo/Chart.yaml"
}

stamp_chart() {
    python3 - "$1" "$ZOO_VERSION" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
lines = path.read_text().splitlines()
path.write_text("\n".join(f'{line.split(":", 1)[0]}: "{sys.argv[2]}"' if line.startswith(("version:", "appVersion:")) else line for line in lines) + "\n")
PY
}

require_tools() {
    local tool
    for tool in "$@"; do command -v "$tool" >/dev/null || die "missing host tool $tool; install it before running the installer"; done
}

linux_tools() {
    if [ -z "$BUNDLE" ]; then
        command -v apt-get >/dev/null || die "online bootstrap supports Debian/Ubuntu; prepare the documented host tools and use --bundle elsewhere"
        apt-get update
        apt-get install -y --no-install-recommends curl ca-certificates python3 openssl zstd xz-utils iptables iproute2
        if [ "$RUNTIME" = kube ]; then
            apt-get install -y --no-install-recommends lvm2 thin-provisioning-tools util-linux
        fi
    fi
    require_tools python3 openssl zstd tar sha256sum systemctl iptables ip
    python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'
    if [ "$RUNTIME" = kube ]; then require_tools pvcreate vgcreate vgchange vgs pvs lvcreate lvs losetup truncate modprobe wipefs lsblk blockdev; fi
}

write_config() {
    local host web api cors database_host object_endpoint gateway_peers
    host=${HOST_ADDRESS:-$(ip -4 -o addr show scope global | awk 'NR==1 {split($4, address, "/"); print address[1]}')}
    [ -n "$host" ] || die "could not determine this host's address"
    if [ "$RUNTIME" = docker ]; then
        web="http://$host:3000"; api="http://$host:8000"
        if [ -n "$ZOO_DOMAIN" ]; then web="https://$ZOO_DOMAIN"; api="$web/api"; fi
        # the dashboard by address and, on this machine, by localhost
        cors="$web"
        if [ -z "$ZOO_DOMAIN" ]; then cors="$web,http://localhost:3000,http://127.0.0.1:3000"; fi
        database_host=postgres; object_endpoint=http://seaweedfs:8333; gateway_peers=gateway
    else
        WEB_HOST="${ZOO_DOMAIN:-zoo.test}"
        API_HOST="api.$WEB_HOST"
        web="http://$WEB_HOST"; api="http://$API_HOST"
        if [ -n "$ZOO_DOMAIN" ]; then web="https://$WEB_HOST"; api="https://$API_HOST"; fi
        cors="$web"
        database_host=zoo-postgres; object_endpoint=http://zoo-object-store:8333
        gateway_peers="zoo-gateway-peers.$NAMESPACE.svc.cluster.local"
    fi
    [ ! -e "$ZOO_HOME/.env" ] || die "$ZOO_HOME/.env already exists; use Compose or Helm to upgrade"
    umask 077
    JWT_SECRET="$(openssl rand -hex 48)" ZOO_SECRETS_KEY="$(openssl rand -hex 48)" POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
    AWS_ACCESS_KEY_ID="$(openssl rand -hex 12)" AWS_SECRET_ACCESS_KEY="$(openssl rand -hex 32)" \
    ZOO_VERSION="$ZOO_VERSION" ZOO_REGISTRY="$ZOO_REGISTRY" ZOO_RUNTIME="$RUNTIME" \
    ADMIN_EMAILS="$ADMIN_EMAILS" ZOO_DOMAIN="$ZOO_DOMAIN" ZOO_API_URL="$api" CORS_ORIGINS="$cors" \
    ZOO_PUBLIC_IP="$host" ZOO_NAMESPACE="$NAMESPACE-sandboxes" ZOO_STORAGE_CLASS=zoo-local ZOO_SNAPSHOT_CLASS=zoo-snapshots \
    ZOO_GATEWAY_PEERS="$gateway_peers" ZOO_S3_ENDPOINT="$object_endpoint" DATABASE_HOST="$database_host" \
    python3 - "$PAYLOAD/.env.example" "$ZOO_HOME/.env" <<'PY'
import os, re, sys
keys = ["JWT_SECRET", "ZOO_SECRETS_KEY", "POSTGRES_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "ZOO_VERSION", "ZOO_REGISTRY", "ZOO_RUNTIME", "ADMIN_EMAILS", "ZOO_DOMAIN", "ZOO_API_URL", "CORS_ORIGINS", "ZOO_PUBLIC_IP", "ZOO_NAMESPACE", "ZOO_STORAGE_CLASS", "ZOO_SNAPSHOT_CLASS", "ZOO_GATEWAY_PEERS", "ZOO_S3_ENDPOINT"]
values = {key: os.environ[key] for key in keys}
values["DATABASE_URL"] = f'postgresql://zoo:{values["POSTGRES_PASSWORD"]}@{os.environ["DATABASE_HOST"]}:5432/zoo'
values["ZOO_OBJECT_STORE"] = "s3://zoo"
values["COMPOSE_PROFILES"] = "domain" if values["ZOO_DOMAIN"] else ""
seen = set()
with open(sys.argv[2], "x") as output:
    for line in open(sys.argv[1]):
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match and match[1] in values:
            seen.add(match[1])
            output.write(f"{match[1]}={values[match[1]]}\n")
        else:
            output.write(line)
    for key in values.keys() - seen:
        output.write(f"{key}={values[key]}\n")
PY
}

config_value() {
    python3 - "$ZOO_HOME/.env" "$1" <<'PY'
import sys
prefix = sys.argv[2] + "="
for line in open(sys.argv[1]):
    if line.startswith(prefix):
        print(line[len(prefix):].rstrip("\n"))
        break
else:
    raise SystemExit(f"missing configuration {sys.argv[2]}")
PY
}

enable_ksm() {
    [ -w /sys/kernel/mm/ksm/run ] || die "this kernel does not expose writable KSM; enable CONFIG_KSM"
    printf '1\n' > /sys/kernel/mm/ksm/run
    cat > /etc/systemd/system/zoo-ksm.service <<'EOF'
[Unit]
Description=Enable KSM for Zoo sandboxes
After=local-fs.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c "echo 1 > /sys/kernel/mm/ksm/run"
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now zoo-ksm.service
}

write_docker_ingress() {
    if [ -n "$ZOO_DOMAIN" ]; then
        local tls=""
        if [ -n "$ADMIN_EMAILS" ]; then tls="tls ${ADMIN_EMAILS%%,*}"; fi
        if [ -n "$BUNDLE" ]; then tls="tls internal"; note "Offline HTTPS requires Caddy's local CA to be trusted by browsers and macOS/Windows VM guests"; fi
        cat > "$ZOO_HOME/Caddyfile" <<EOF
$ZOO_DOMAIN {
    $tls
    encode gzip
    handle /api/guest/connect {
        uri strip_prefix /api
        reverse_proxy gateway:8001
    }
    handle_path /api/* {
        reverse_proxy api:8000
    }
    handle {
        reverse_proxy web:3000
    }
}
EOF
    else
        cat > "$ZOO_HOME/Caddyfile" <<'EOF'
:80 {
    handle /guest/connect {
        reverse_proxy gateway:8001
    }
    handle {
        reverse_proxy api:8000
    }
}
EOF
        cat > "$ZOO_HOME/compose.override.yml" <<'EOF'
services:
  api:
    ports: !reset []
  caddy:
    profiles: !reset []
    ports: !override ["8000:80"]
    depends_on:
      - gateway
EOF
    fi
}

install_docker() {
    local artifacts="$PAYLOAD/artifacts/$ARCH"
    local -a compose_files=(-f "$ZOO_HOME/compose.yml")
    if ! command -v docker >/dev/null; then
        mkdir -p "$WORK/docker" /usr/local/bin
        tar -xzf "$artifacts/docker.tgz" -C "$WORK/docker"
        install -m 755 "$WORK/docker/docker/"* /usr/local/bin/
        cat > /etc/systemd/system/docker.service <<'EOF'
[Unit]
Description=Docker Engine
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd
Delegate=yes
Restart=always
LimitNOFILE=infinity
TasksMax=infinity
[Install]
WantedBy=multi-user.target
EOF
    fi
    mkdir -p /usr/local/lib/docker/cli-plugins
    install -m 755 "$artifacts/docker-compose" /usr/local/lib/docker/cli-plugins/docker-compose
    if [ "$HAS_KVM" = 1 ]; then install_kata; else systemctl daemon-reload; systemctl enable docker; systemctl restart docker; fi
    cp "$PAYLOAD/compose.yml" "$PAYLOAD/Caddyfile" "$ZOO_HOME/"
    write_image_compose "$ZOO_HOME/compose.yml" "$ZOO_HOME/compose.yml"
    write_docker_ingress
    if [ -z "$ZOO_DOMAIN" ]; then compose_files+=(-f "$ZOO_HOME/compose.override.yml"); fi
    if [ -n "$BUNDLE" ]; then docker load -i "$PAYLOAD/images/$ARCH/docker.tar"
    else docker compose --env-file "$ZOO_HOME/.env" "${compose_files[@]}" -p zoo pull; fi
    docker compose --env-file "$ZOO_HOME/.env" "${compose_files[@]}" -p zoo up -d --no-build --pull never --wait --wait-timeout 600
}

# Kata runs each Linux sandbox in a micro-VM; without KVM, Docker runs them as plain containers (server/docker.py)
install_kata() {
    local artifacts="$PAYLOAD/artifacts/$ARCH"
    tar --zstd -xf "$artifacts/kata.tar.zst" -C /
    ln -sf /opt/kata/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
    ln -sf /opt/kata/bin/kata-runtime /usr/local/bin/kata-runtime
    [ -f /opt/kata/share/defaults/kata-containers/configuration-qemu.toml ] || die "Kata package has no QEMU configuration"
    mkdir -p /etc/kata-containers /etc/docker
    cp /opt/kata/share/defaults/kata-containers/configuration-qemu.toml /etc/kata-containers/configuration.toml
    python3 - <<'PY'
import json, os, pathlib
path = pathlib.Path("/etc/docker/daemon.json")
config = json.loads(path.read_text()) if path.exists() else {}
config.setdefault("runtimes", {})["io.containerd.kata.v2"] = {"runtimeType": "/opt/kata/bin/containerd-shim-kata-v2"}
temporary = path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(config, indent=2) + "\n")
os.replace(temporary, path)
PY
    systemctl daemon-reload
    systemctl enable docker
    systemctl restart docker
    docker info --format '{{json .Runtimes}}' | python3 -c 'import json,sys; assert "io.containerd.kata.v2" in json.load(sys.stdin), "Docker does not list the Kata runtime"'
    /opt/kata/bin/kata-runtime check
}

prepare_volume_group() {
    local device="$DEVICE"
    ! vgs zoo >/dev/null 2>&1 || die "volume group zoo already exists"
    if [ -z "$device" ]; then
        [ ! -e "$ZOO_HOME/topolvm.img" ] || die "$ZOO_HOME/topolvm.img already exists"
        truncate -s "${DISK_GB}G" "$ZOO_HOME/topolvm.img"
        device=$(losetup --find --show "$ZOO_HOME/topolvm.img")
        cat > "$ZOO_HOME/topolvm-loop.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [ -z "\$(losetup -j '$ZOO_HOME/topolvm.img')" ]; then
    losetup --find '$ZOO_HOME/topolvm.img'
fi
vgchange -ay zoo
EOF
        chmod 755 "$ZOO_HOME/topolvm-loop.sh"
        cat > /etc/systemd/system/zoo-topolvm.service <<EOF
[Unit]
Description=Attach Zoo TopoLVM loop storage
After=local-fs.target
Before=k3s.service
[Service]
Type=oneshot
ExecStart=$ZOO_HOME/topolvm-loop.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
        mkdir -p /etc/systemd/system/k3s.service.d
        printf '[Unit]\nRequires=zoo-topolvm.service\nAfter=zoo-topolvm.service\n' > /etc/systemd/system/k3s.service.d/zoo-storage.conf
    fi
    [ -b "$device" ] || die "--device must name a block device"
    [ "$(blockdev --getsize64 "$device")" -ge 85899345920 ] || die "$device needs at least 80 GiB for Zoo and sandbox storage"
    [ -z "$(lsblk -nro MOUNTPOINTS "$device" | tr -d '[:space:]')" ] || die "$device is mounted"
    [ -z "$(wipefs --no-act --noheadings "$device")" ] || die "$device has existing signatures; supply an empty device"
    pvcreate "$device"
    vgcreate zoo "$device"
    lvcreate --type thin-pool --extents 90%FREE --name thinpool zoo
    if [ -z "$DEVICE" ]; then systemctl daemon-reload; systemctl enable --now zoo-topolvm.service; fi
}

write_bootstrap() {
    cat > "$1" <<EOF
apiVersion: v1
kind: Service
metadata: {name: zoo-postgres, namespace: $NAMESPACE}
spec:
  selector: {app: zoo-postgres}
  ports: [{port: 5432}]
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: zoo-postgres, namespace: $NAMESPACE}
spec:
  serviceName: zoo-postgres
  replicas: 1
  selector: {matchLabels: {app: zoo-postgres}}
  template:
    metadata: {labels: {app: zoo-postgres}}
    spec:
      containers:
        - name: postgres
          image: $POSTGRES_IMAGE
          imagePullPolicy: IfNotPresent
          env:
            - {name: POSTGRES_USER, value: zoo}
            - {name: POSTGRES_DB, value: zoo}
            - {name: PGDATA, value: /var/lib/postgresql/data/pgdata}
            - name: POSTGRES_PASSWORD
              valueFrom: {secretKeyRef: {name: zoo-config, key: POSTGRES_PASSWORD}}
          ports: [{containerPort: 5432}]
          readinessProbe:
            exec: {command: [pg_isready, -U, zoo]}
          volumeMounts: [{name: data, mountPath: /var/lib/postgresql/data}]
  volumeClaimTemplates:
    - metadata: {name: data}
      spec:
        accessModes: [ReadWriteOnce]
        storageClassName: zoo-local
        resources: {requests: {storage: 10Gi}}
---
apiVersion: v1
kind: Service
metadata: {name: zoo-object-store, namespace: $NAMESPACE}
spec:
  selector: {app: zoo-object-store}
  ports: [{name: s3, port: 8333}, {name: master, port: 9333}]
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: zoo-object-store, namespace: $NAMESPACE}
spec:
  serviceName: zoo-object-store
  replicas: 1
  selector: {matchLabels: {app: zoo-object-store}}
  template:
    metadata: {labels: {app: zoo-object-store}}
    spec:
      containers:
        - name: seaweedfs
          image: $SEAWEED_IMAGE
          imagePullPolicy: IfNotPresent
          args: [server, "-dir=/data", "-ip=zoo-object-store", "-ip.bind=0.0.0.0", -s3, "-s3.port=8333", "-s3.config=/etc/seaweedfs/s3.json"]
          ports: [{containerPort: 8333}, {containerPort: 9333}]
          readinessProbe:
            httpGet: {path: /healthz, port: 8333}
          volumeMounts:
            - {name: data, mountPath: /data}
            - {name: credentials, mountPath: /etc/seaweedfs}
      volumes:
        - name: credentials
          secret: {secretName: zoo-s3}
  volumeClaimTemplates:
    - metadata: {name: data}
      spec:
        accessModes: [ReadWriteOnce]
        storageClassName: zoo-local
        resources: {requests: {storage: 20Gi}}
---
apiVersion: batch/v1
kind: Job
metadata: {name: zoo-bucket, namespace: $NAMESPACE}
spec:
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: create-bucket
          image: $SEAWEED_IMAGE
          imagePullPolicy: IfNotPresent
          command: [/bin/sh, -ec]
          args:
            - echo 's3.bucket.create -name zoo' | weed shell -master=zoo-object-store:9333
EOF
}

write_zoo_values() {
    ZOO_INSTALL_NAMESPACE="$NAMESPACE" ZOO_INSTALL_VERSION="$ZOO_VERSION" ZOO_INSTALL_REGISTRY="$ZOO_REGISTRY" \
    ZOO_INSTALL_WEB_HOST="$WEB_HOST" ZOO_INSTALL_API_HOST="$API_HOST" ZOO_INSTALL_TLS="$ZOO_DOMAIN" \
    ZOO_INSTALL_ADMIN="$ADMIN_EMAILS" python3 - "$1" <<'PY'
import json, os, sys
tls = bool(os.environ["ZOO_INSTALL_TLS"])
values = {
    "image": {"registry": os.environ["ZOO_INSTALL_REGISTRY"], "tag": os.environ["ZOO_INSTALL_VERSION"], "pullPolicy": "IfNotPresent"},
    "database": {"mode": "external", "external": {"existingSecret": "zoo-database", "key": "url"}},
    "secrets": {"existingSecret": "zoo-config", "create": False},
    "objectStorage": {"url": "s3://zoo", "endpoint": "http://zoo-object-store:8333", "existingSecret": "zoo-config"},
    "sandboxes": {"namespace": os.environ["ZOO_INSTALL_NAMESPACE"] + "-sandboxes", "storageClass": "zoo-local", "snapshotClass": "zoo-snapshots"},
    "env": {"ADMIN_EMAILS": os.environ["ZOO_INSTALL_ADMIN"]},
    "ingress": {"host": os.environ["ZOO_INSTALL_WEB_HOST"], "apiHost": os.environ["ZOO_INSTALL_API_HOST"], "className": "traefik", "scheme": "https" if tls else "http", "annotations": {"cert-manager.io/cluster-issuer": "zoo"} if tls else {}, "tls": [{"secretName": "zoo-ingress", "hosts": [os.environ["ZOO_INSTALL_WEB_HOST"], os.environ["ZOO_INSTALL_API_HOST"]]}] if tls else []}
}
with open(sys.argv[1], "w") as output:
    json.dump(values, output, indent=2)
    output.write("\n")
PY
}

install_kube() {
    local artifacts="$PAYLOAD/artifacts/$ARCH"
    [ ! -e /etc/rancher/k3s/k3s.yaml ] || die "k3s is already configured; use Helm to operate the existing installation"
    write_kata_values "$ZOO_HOME/kata-values.yaml"
    note "Cloud Hypervisor uses virtio-fs without DAX; VM templating is intentionally disabled because virtio-fs requires shared memory"
    prepare_volume_group
    mkdir -p "$WORK/helm" /usr/local/bin /var/lib/rancher/k3s/agent/images
    tar -xzf "$artifacts/helm.tar.gz" -C "$WORK/helm"
    install -m 755 "$WORK/helm/linux-$ARCH/helm" /usr/local/bin/helm
    install -m 755 "$artifacts/k3s" /usr/local/bin/k3s
    cp "$artifacts/k3s-airgap.tar.zst" /var/lib/rancher/k3s/agent/images/
    if [ -n "$BUNDLE" ]; then cp "$PAYLOAD/images/$ARCH/kube.tar" /var/lib/rancher/k3s/agent/images/zoo-bundle.tar; fi
    INSTALL_K3S_SKIP_DOWNLOAD=true INSTALL_K3S_VERSION="$K3S_VERSION" INSTALL_K3S_EXEC="server --write-kubeconfig-mode 600" \
        sh "$artifacts/install-k3s.sh"
    export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
    kubectl wait --for=condition=Ready node --all --timeout=600s
    kubectl apply -f "$PAYLOAD/manifests/cert-manager.yaml"
    kubectl -n cert-manager rollout status deployment/cert-manager --timeout=300s
    kubectl -n cert-manager rollout status deployment/cert-manager-webhook --timeout=300s
    kubectl -n cert-manager rollout status deployment/cert-manager-cainjector --timeout=300s
    for resource in volumesnapshotclasses volumesnapshotcontents volumesnapshots rbac-snapshot-controller setup-snapshot-controller; do
        kubectl apply -f "$PAYLOAD/manifests/$resource.yaml"
    done
    kubectl -n kube-system rollout status deployment/snapshot-controller --timeout=300s
    helm upgrade --install kata-deploy "$PAYLOAD/charts/kata-deploy.tgz" --namespace kube-system \
        -f "$ZOO_HOME/kata-values.yaml" --wait --timeout 20m
    kubectl -n kube-system rollout status daemonset/kata-deploy --timeout=1200s
    kubectl get runtimeclass kata >/dev/null
    helm upgrade --install topolvm "$PAYLOAD/charts/topolvm.tgz" --namespace topolvm-system --create-namespace \
        -f "$PAYLOAD/topolvm-values.yaml" --wait --timeout 10m
    kubectl apply -f - <<'EOF'
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata: {name: zoo-snapshots}
driver: topolvm.io
deletionPolicy: Delete
EOF
    kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n "$NAMESPACE" create secret generic zoo-config --from-env-file="$ZOO_HOME/.env" --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n "$NAMESPACE" create secret generic zoo-database --from-literal="url=$(config_value DATABASE_URL)" --dry-run=client -o yaml | kubectl apply -f -
    local access_key secret_key
    access_key=$(config_value AWS_ACCESS_KEY_ID); secret_key=$(config_value AWS_SECRET_ACCESS_KEY)
    python3 - "$access_key" "$secret_key" > "$WORK/s3.json" <<'PY'
import json, sys
json.dump({"identities": [{"name": "zoo", "credentials": [{"accessKey": sys.argv[1], "secretKey": sys.argv[2]}], "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}, sys.stdout)
PY
    kubectl -n "$NAMESPACE" create secret generic zoo-s3 --from-file="s3.json=$WORK/s3.json" --dry-run=client -o yaml | kubectl apply -f -
    write_bootstrap "$WORK/bootstrap.yaml"
    kubectl apply -f "$WORK/bootstrap.yaml"
    kubectl -n "$NAMESPACE" rollout status statefulset/zoo-postgres --timeout=300s
    kubectl -n "$NAMESPACE" rollout status statefulset/zoo-object-store --timeout=300s
    kubectl -n "$NAMESPACE" wait --for=condition=complete job/zoo-bucket --timeout=300s
    if [ -n "$ZOO_DOMAIN" ]; then
        if [ -n "$BUNDLE" ]; then
            kubectl apply -f - <<'EOF'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: {name: zoo}
spec: {selfSigned: {}}
EOF
            note "Offline HTTPS requires the certificate to be trusted by browsers and macOS/Windows VM guests; replace it with a trusted certificate"
        else
            [ -n "$ADMIN_EMAILS" ] || die "--domain on Kubernetes requires --admin-email for ACME"
            kubectl apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: {name: zoo}
spec:
  acme:
    email: "${ADMIN_EMAILS%%,*}"
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef: {name: zoo-acme}
    solvers:
      - http01: {ingress: {class: traefik}}
EOF
        fi
    fi
    write_zoo_values "$ZOO_HOME/values-install.json"
    helm upgrade --install zoo "$PAYLOAD/deploy/helm/zoo" --namespace "$NAMESPACE" \
        -f "$ZOO_HOME/values-install.json" --wait --timeout 10m
    if [ -n "$ZOO_DOMAIN" ]; then kubectl -n "$NAMESPACE" wait --for=condition=Ready certificate/zoo-ingress --timeout=600s; fi
    if [ -z "$ZOO_DOMAIN" ]; then note "Point zoo.test and api.zoo.test at $(config_value ZOO_PUBLIC_IP) in DNS reachable by browsers and macOS/Windows VM guests; host-only hosts entries do not configure guest DNS"; fi
}

install_linux() {
    [ "$(uname -s)" = Linux ] || die "the control plane needs Linux; Windows hosts use windows/install-node.ps1"
    if [ -c /dev/kvm ] && [ -r /dev/kvm ] && [ -w /dev/kvm ]; then HAS_KVM=1; fi
    if [ "$HAS_KVM" = 0 ]; then
        [ "$RUNTIME" = docker ] || die "Kubernetes needs /dev/kvm; enable hardware or nested virtualization, or use --runtime docker"
        note "no /dev/kvm: Linux sandboxes will run as plain Docker containers, not Kata micro-VMs. Enable hardware or nested virtualization for VM isolation."
    fi
    case "$(uname -m)" in x86_64|amd64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;; *) die "unsupported CPU architecture" ;; esac
    [ "$RUNTIME" != kube ] || [ "$ARCH" = amd64 ] || die "Kubernetes requires amd64: Kata $KATA_VERSION ships the Cloud Hypervisor clh shim only for amd64; Docker uses QEMU on either architecture"
    [ -d /run/systemd/system ] || die "this installer requires systemd"
    [ -e /sys/kernel/mm/ksm/run ] || die "this kernel needs CONFIG_KSM"
    need_disk / 20
    need_memory $(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1048576 )) 4 8
    if [ "$RUNTIME" = kube ] && [ -z "$DEVICE" ]; then
        local storage_parent="$ZOO_HOME"
        while [ ! -d "$storage_parent" ]; do storage_parent=$(dirname "$storage_parent"); done
        need_disk "$storage_parent" "$DISK_GB"
    fi
    if [ "$RUNTIME" = kube ] && [ -e /etc/rancher/k3s/k3s.yaml ]; then fail "k3s is already configured; operate it with Helm"; fi
    [ ! -e "$ZOO_HOME/.env" ] || fail "$ZOO_HOME/.env already exists; use Compose or Helm to upgrade"
    if [ -n "$BUNDLE" ]; then [ -f "$BUNDLE" ] || fail "bundle $BUNDLE does not exist"; fi
    preflight_done
    linux_tools
    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    prepare_payload
    mkdir -p "$ZOO_HOME"
    write_config
    enable_ksm
    case "$RUNTIME" in docker) install_docker ;; kube) install_kube ;; esac
    say "Zoo $ZOO_VERSION installed with $RUNTIME ($([ "$HAS_KVM" = 1 ] && echo "Kata micro-VMs" || echo "plain containers"))"
    printf 'Dashboard: %s\nConfiguration: %s/.env\n' "$(config_value CORS_ORIGINS | cut -d, -f1)" "$ZOO_HOME"
}

# macOS: the control plane in Docker Desktop. Docker Desktop has no KVM, so Linux sandboxes run as plain containers
# inside its VM; macOS sandboxes run on this Mac once it is added as a server.
install_desktop() {
    local user_home
    NODE_USER=${NODE_USER:-${SUDO_USER:-}}
    if [ -z "$NODE_USER" ] || [ "$NODE_USER" = root ]; then die "run this with sudo from your own account, or pass --user"; fi
    [ "$RUNTIME" = docker ] && [ -z "$BUNDLE" ] || die "on a Mac Zoo runs in Docker Desktop; --runtime kube and --bundle need Linux"
    user_home=$(dscl . -read "/Users/$NODE_USER" NFSHomeDirectory | sed 's/^NFSHomeDirectory:[[:space:]]*//')
    [ "$ZOO_HOME" != /opt/zoo ] || ZOO_HOME="$user_home/zoo"
    say "Checking this Mac"
    if command -v docker >/dev/null; then ok "Docker CLI"; else fail "install Docker Desktop (https://www.docker.com/products/docker-desktop/), start it, then run this again"; fi
    if command -v docker >/dev/null && as_user docker info >/dev/null 2>&1; then ok "Docker Desktop is running"; else fail "start Docker Desktop, then run this again"; fi
    if command -v docker >/dev/null && as_user docker compose version >/dev/null 2>&1; then ok "Docker Compose"; else fail "Docker Compose is missing; update Docker Desktop"; fi
    if python3 -c 'import sys; assert sys.version_info >= (3, 9)' 2>/dev/null; then ok "python3"; else fail "python3 is missing; run xcode-select --install"; fi
    need_disk "$user_home" 20
    need_memory $(( $(sysctl -n hw.memsize) / 1073741824 )) 8 16
    # another Zoo in this Docker (a source checkout's `docker compose up`, say) shares the zoo project's volumes and
    # the zoo-egress container name, and its database has other credentials
    local resume=0 other
    if [ -e "$ZOO_HOME/.env" ]; then resume=1; note "$ZOO_HOME/.env exists; carrying on with that installation"
    elif command -v docker >/dev/null; then
        other=$( { as_user docker ps -aq --filter name='^zoo-egress$'; as_user docker volume ls -q --filter name='^zoo_pg-data$'; } 2>/dev/null)
        if [ -n "$other" ]; then
            fail "Docker already has another Zoo (container zoo-egress or volume zoo_pg-data). Stop it with: docker compose -p zoo down && docker rm -f zoo-egress. Its database stays in volume zoo_pg-data; remove that volume (docker volume rm zoo_pg-data) to install fresh"
        else ok "no other Zoo in Docker"; fi
    fi
    preflight_done

    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    if [ -z "$ZOO_VERSION" ]; then
        ZOO_VERSION=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest")
        ZOO_VERSION="${ZOO_VERSION##*/v}"
    fi
    [[ "$ZOO_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die "invalid Zoo release version"
    PAYLOAD="$WORK/payload"
    mkdir -p "$PAYLOAD" "$ZOO_HOME"
    curl -fsSL --retry 3 "https://github.com/$REPO/archive/refs/tags/v$ZOO_VERSION.tar.gz" -o "$WORK/zoo-source.tar.gz"
    tar -xzf "$WORK/zoo-source.tar.gz" -C "$PAYLOAD" --strip-components=1
    # the LAN address, so other servers and this Mac's VMs can reach the API
    HOST_ADDRESS=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)
    [ "$resume" = 1 ] || write_config
    cp "$PAYLOAD/compose.yml" "$PAYLOAD/Caddyfile" "$ZOO_HOME/"
    write_image_compose "$ZOO_HOME/compose.yml" "$ZOO_HOME/compose.yml"
    write_docker_ingress
    chown -R "$NODE_USER" "$ZOO_HOME"
    local -a compose=(docker compose --project-directory "$ZOO_HOME" --env-file "$ZOO_HOME/.env" -f "$ZOO_HOME/compose.yml")
    if [ -z "$ZOO_DOMAIN" ]; then compose+=(-f "$ZOO_HOME/compose.override.yml"); fi
    compose+=(-p zoo)
    say "Starting Zoo $ZOO_VERSION in Docker Desktop"
    as_user "${compose[@]}" pull
    as_user "${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 600
    say "Zoo $ZOO_VERSION is running (Linux sandboxes as plain containers: Docker Desktop has no KVM)"
    printf '\n  Dashboard:     %s\n  Configuration: %s/.env\n  Manage it:     cd %s && docker compose -p zoo ps\n\n' \
        "$(config_value CORS_ORIGINS | cut -d, -f1)" "$ZOO_HOME" "$ZOO_HOME"
    if [ -z "$ADMIN_EMAILS" ]; then
        note "no --admin-email, so this Mac isn't added for macOS sandboxes; sign up, then use Servers > Add server > Mac"
        return
    fi
    # join this Mac for macOS sandboxes, as Servers > Add server > Mac would (server/bootstrap.py)
    local joined
    joined=$(as_user "${compose[@]}" exec -T api uv run --no-sync python -m server.bootstrap \
        --email "${ADMIN_EMAILS%%,*}" --name "$(scutil --get ComputerName 2>/dev/null || hostname -s)") \
        || die "couldn't get a join token from Zoo; add this Mac from Servers > Add server > Mac"
    # the JSON is its last line; anything logged before it is skipped
    eval "$(printf '%s\n' "$joined" | tail -n 1 | python3 -c '
import json, shlex, sys
j = json.load(sys.stdin)
for name, key in (("NODE_TOKEN", "token"), ("NODE_KEY", "key"), ("CONTROL_PLANE", "control_plane"), ("ADMIN_PASSWORD", "password")):
    print(name + "=" + shlex.quote(j[key] or str()))')"
    if [ -n "$ADMIN_PASSWORD" ]; then
        say "Admin account: ${ADMIN_EMAILS%%,*} with password $ADMIN_PASSWORD (shown once; change it after signing in)"
    else
        say "Admin account: ${ADMIN_EMAILS%%,*} already exists; sign in with its password"
    fi
    say "Adding this Mac for macOS sandboxes"
}

authorize() { # USER HOME GROUP
    [ -n "$NODE_KEY" ] || die "--node needs --key, the control plane's SSH public key (copy the command from Add server)"
    mkdir -p "$2/.ssh"
    touch "$2/.ssh/authorized_keys"
    grep -qxF "$NODE_KEY" "$2/.ssh/authorized_keys" || printf '%s\n' "$NODE_KEY" >> "$2/.ssh/authorized_keys"
    chown -R "$1:$3" "$2/.ssh"
    chmod 700 "$2/.ssh"
    chmod 600 "$2/.ssh/authorized_keys"
}

# zoo-node joins with the token and installs its platform service.
join_node() { # USER
    local bin os arch
    os=$(uname -s | tr '[:upper:]' '[:lower:]')
    case "$(uname -m)" in arm64|aarch64) arch=arm64 ;; x86_64|amd64) arch=amd64 ;; *) die "unsupported node architecture: $(uname -m)" ;; esac
    bin=$(mktemp)
    curl -fsSL --retry 3 "$CONTROL_PLANE/v1/nodes/download/$os/$arch" -o "$bin" || die "couldn't download zoo-node from $CONTROL_PLANE"
    chmod 755 "$bin"
    chown "$1" "$bin"
    mkdir -p /usr/local/bin
    if [ "$os" = linux ]; then
        "$bin" join "$NODE_TOKEN" --service --ssh-user "$1" --dir /var/lib/zoo-node || die "zoo-node couldn't join; the token may be used or expired"
        ln -sf /var/lib/zoo-node/bin/zoo-node /usr/local/bin/zoo-node
    else
        as_user "$bin" join "$NODE_TOKEN" --service --ssh-user "$1" || die "zoo-node couldn't join; the token may be used or expired"
        ln -sf "$(dscl . -read "/Users/$1" NFSHomeDirectory | sed 's/^NFSHomeDirectory:[[:space:]]*//')/.zoo-node/bin/zoo-node" /usr/local/bin/zoo-node
    fi
    rm -f "$bin"
    say "This machine joined the control plane"
    cat <<EOF

  Enabled runtimes appear on its Servers page (Linux desktop, browser and code need a Linux Docker engine).

  zoo-node dials out to the control plane, so no inbound port is needed. The server shows up under Servers.
  Logs: journalctl -u zoo-node -f (Linux), or tail ~$1/.zoo-node/node.log (Mac)
EOF
}

# A remote Linux node needs Docker and SSH, but none of the control-plane services.
install_linux_node() {
    command -v systemctl >/dev/null && [ -d /run/systemd/system ] || die "Linux nodes require systemd"
    command -v docker >/dev/null || die "install Docker Engine and start its Linux engine before adding this server"
    [ "$(docker info --format '{{.OSType}}' 2>/dev/null)" = linux ] || die "Docker must be running a Linux engine accessible to root"
    NODE_USER=${NODE_USER:-root}
    [[ "$NODE_USER" =~ ^[A-Za-z_][A-Za-z0-9_.-]*[$]?$ ]] || die "invalid SSH user"
    local node_home node_group
    node_home=$(getent passwd "$NODE_USER" | cut -d: -f6)
    [ -n "$node_home" ] && [ -d "$node_home" ] || die "--user must name an existing account with a home directory"
    node_group=$(id -gn "$NODE_USER")
    reach_control_plane
    preflight_done
    if ! command -v sshd >/dev/null && [ ! -x /usr/sbin/sshd ]; then
        if command -v apt-get >/dev/null; then apt-get update && apt-get install -y openssh-server
        elif command -v dnf >/dev/null; then dnf install -y openssh-server
        elif command -v yum >/dev/null; then yum install -y openssh-server
        else die "install openssh-server, then run this command again"; fi
    fi
    ssh-keygen -A
    if [ "$NODE_USER" = root ]; then
        mkdir -p /etc/ssh/sshd_config.d
        printf 'PermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/00-zoo-node.conf
    else
        getent group docker >/dev/null || die "the SSH account needs access to Docker; configure the docker group first"
        usermod -aG docker "$NODE_USER"
    fi
    authorize "$NODE_USER" "$node_home" "$node_group"
    /usr/sbin/sshd -t || die "invalid SSH configuration"
    if systemctl cat ssh.service >/dev/null 2>&1; then systemctl enable --now ssh.service; systemctl restart ssh.service
    else systemctl enable --now sshd.service; systemctl restart sshd.service; fi
    join_node "$NODE_USER"
}

# macOS: a node for macOS sandboxes

as_user() { sudo -u "$NODE_USER" -H "$@"; }

# A valid enrollment is reused: helper updates never spend another join token.
macos_existing_node() { # HOME
    local directory="$1/.zoo-node"
    [ -s "$directory/config.json" ] && [ -s "$directory/node.key" ] && [ -s "$directory/node.crt" ] && [ -s "$directory/ca.crt" ] || return 1
    python3 - "$directory/config.json" <<'PY'
import json, sys
try:
    config = json.load(open(sys.argv[1]))
    assert isinstance(config, dict)
    assert all(isinstance(config.get(key), str) and config[key] for key in ("api", "node_id", "server_id"))
    assert isinstance(config.get("endpoints"), list) and config["endpoints"]
    assert all(isinstance(endpoint, str) and endpoint for endpoint in config["endpoints"])
except (OSError, ValueError, AssertionError):
    sys.exit(1)
PY
}

macos_node_identity() { # HOME
    local stored cached
    if macos_existing_node "$1"; then
        stored=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["api"].rstrip("/"))' "$1/.zoo-node/config.json")
        [ -n "$CONTROL_PLANE" ] || CONTROL_PLANE=$stored
        [ "$CONTROL_PLANE" = "$stored" ] || die "this account is already enrolled with $stored; use that control plane's setup command"
        if [ -z "$NODE_KEY" ] && [ -s "$1/.zoo-node/control-plane.pub" ]; then
            cached=$(cat "$1/.zoo-node/control-plane.pub")
            if [[ "$cached" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+$ ]]; then NODE_KEY=$cached; fi
        fi
        if [ -z "$NODE_KEY" ]; then
            NODE_KEY=$(curl -fsSL --retry 3 "$CONTROL_PLANE/v1/nodes/bootstrap/ssh-key") \
                || die "couldn't obtain this control plane's SSH public key; copy its Add server setup command"
        fi
    else
        [ -n "$NODE_TOKEN" ] && [ -n "$CONTROL_PLANE" ] && [ -n "$NODE_KEY" ] \
            || die "a new Mac needs the complete one-time setup command from Servers > Add server"
    fi
    [[ "$CONTROL_PLANE" =~ ^https?://[^[:space:]]+$ ]] || die "--control-plane must be an HTTP or HTTPS URL"
    [[ "$NODE_KEY" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+$ ]] || die "the control-plane SSH public key is invalid"
}

# Authenticate possession of the stored key before reusing this enrollment.
# A supplied token independently pins the intended authority, including over HTTP.
macos_enrollment_status() { # HOME; 0 current, 1 reenroll, 2 unavailable/invalid
    ZOO_ENROLLMENT_TOKEN="$NODE_TOKEN" python3 - "$1/.zoo-node" "$CONTROL_PLANE" <<'PYENROLL'
import base64, hashlib, json, os, re, secrets, subprocess, sys, time, urllib.request
from pathlib import Path
directory, api = Path(sys.argv[1]), sys.argv[2].rstrip("/")
try:
    config = json.loads((directory / "config.json").read_text())
    if config["api"].rstrip("/") != api:
        raise ValueError("different control plane")
    token = os.environ.get("ZOO_ENROLLMENT_TOKEN", "")
    if token:
        if not token.startswith("zn1."):
            raise ValueError("invalid token")
        encoded = token[4:]
        claim = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        if (claim.get("u", "").rstrip("/") != api or not claim.get("s")
                or not re.fullmatch(r"[a-fA-F0-9]{64}", claim.get("f", ""))):
            raise ValueError("invalid token binding")
        authority = subprocess.run(["openssl", "x509", "-in", str(directory / "ca.crt"), "-outform", "DER"],
                                   check=True, capture_output=True).stdout
        if hashlib.sha256(authority).hexdigest() != claim["f"].lower():
            sys.exit(1)
    timestamp, nonce = int(time.time()), secrets.token_hex(32)
    message = f"zoo-node-enrollment-v1\n{timestamp}\n{nonce}\n".encode()
    signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(directory / "node.key")],
                               input=message, check=True, capture_output=True).stdout
    payload = {"certificate": (directory / "node.crt").read_text(), "timestamp": timestamp,
               "nonce": nonce, "signature": base64.b64encode(signature).decode()}
    request = urllib.request.Request(api + "/v1/nodes/enrollment/status", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    # Enrollment proof must never follow a redirect to another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        result = json.loads(response.read(65536))
    if result.get("status") == "current":
        if result.get("node_id") != config["node_id"] or result.get("server_id") != config["server_id"]:
            raise ValueError("identity mismatch")
        sys.exit(0)
    if result.get("status") == "reenroll" and result.get("reason") in {
            "authority_changed", "expired", "registration_missing", "registration_replaced"}:
        sys.exit(1)
except Exception:
    pass
sys.exit(2)
PYENROLL
}

resume_macos_node_service() { # HOME [RESTART=0]
    local directory="$1/.zoo-node" agent="$1/Library/LaunchAgents/dev.zoo.node.plist" uid domain bin
    bin="$directory/bin/zoo-node"
    if [ ! -x "$bin" ]; then
        local temporary arch
        case "$(uname -m)" in arm64) arch=arm64 ;; *) arch=amd64 ;; esac
        temporary=$(mktemp) || return 1
        curl -fsSL --retry 3 "$CONTROL_PLANE/v1/nodes/download/darwin/$arch" -o "$temporary" \
            || return 1
        install -d -o "$NODE_USER" -g staff -m 700 "$directory/bin" || return 1
        install -o "$NODE_USER" -g staff -m 755 "$temporary" "$bin" || return 1
        rm -f "$temporary"
    fi
    mkdir -p "$(dirname "$agent")" || return 1
    [ -d /usr/local/bin ] || mkdir -p /usr/local/bin || return 1
    # Keep the service definition current without replacing its credentials.
    python3 - "$bin" "$directory" "$agent" <<'PY' || return 1
import plistlib, sys
binary, directory, target = sys.argv[1:]
value = {"Label": "dev.zoo.node", "ProgramArguments": [binary, "run", "--dir", directory],
 "EnvironmentVariables": {"PATH": "/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
 "RunAtLoad": True, "KeepAlive": True,
 "StandardOutPath": directory + "/node.log", "StandardErrorPath": directory + "/node.log"}
with open(target, "wb") as output:
    plistlib.dump(value, output)
PY
    chown "$NODE_USER:staff" "$(dirname "$agent")" "$agent" || return 1
    chmod 644 "$agent" || return 1
    if [ "$(readlink /usr/local/bin/zoo-node 2>/dev/null || true)" != "$bin" ]; then
        ln -sf "$bin" /usr/local/bin/zoo-node || return 1
    fi
    uid=$(id -u "$NODE_USER") || return 1
    if [ "${2:-0}" = 1 ]; then
        for domain in "gui/$uid" "user/$uid"; do
            as_user launchctl bootout "$domain/dev.zoo.node" >/dev/null 2>&1 || true
        done
    fi
    for domain in "gui/$uid" "user/$uid"; do
        if as_user launchctl print "$domain/dev.zoo.node" >/dev/null 2>&1 \
            || as_user launchctl bootstrap "$domain" "$agent" >/dev/null 2>&1; then
            ok "zoo-node service is enabled"
            break
        fi
    done
    as_user launchctl print "gui/$uid/dev.zoo.node" >/dev/null 2>&1 \
        || as_user launchctl print "user/$uid/dev.zoo.node" >/dev/null 2>&1 \
        || return 1
}

# Stage a token-pinned join before stopping or replacing the old identity. A
# failed join leaves the running node untouched; a failed restart rolls it back.
reenroll_macos_node() ( # HOME; subshell owns transaction cleanup
    # Subshell variables survive into EXIT; Bash drops function locals first.
    enroll_home="$1" enroll_directory="$1/.zoo-node" enroll_stopped=0 enroll_promoted=0 enroll_committed=0
    enroll_staged=$(as_user mktemp -d "$enroll_home/.zoo-node-enroll.XXXXXXXX") || exit 1
    enroll_backup="$enroll_staged.previous"
    # shellcheck disable=SC2329 # Invoked by the EXIT trap.
    cleanup_enrollment() {
        local result=$?
        trap - EXIT
        if [ "$enroll_promoted" = 1 ] && [ "$enroll_committed" = 0 ]; then
            for enroll_domain in "gui/$enroll_uid" "user/$enroll_uid"; do
                as_user launchctl bootout "$enroll_domain/dev.zoo.node" >/dev/null 2>&1 || true
            done
            rm -rf "$enroll_directory"
            mv "$enroll_backup" "$enroll_directory"
            resume_macos_node_service "$enroll_home" 1 >/dev/null 2>&1 || true
        elif [ -d "$enroll_backup" ] && [ ! -e "$enroll_directory" ]; then
            mv "$enroll_backup" "$enroll_directory"
            resume_macos_node_service "$enroll_home" 1 >/dev/null 2>&1 || true
        elif [ "$enroll_stopped" = 1 ] && [ "$enroll_committed" = 0 ]; then
            resume_macos_node_service "$enroll_home" 1 >/dev/null 2>&1 || true
        fi
        rm -rf "$enroll_staged"
        if [ "$enroll_committed" = 1 ]; then rm -rf "$enroll_backup"; fi
        exit "$result"
    }
    trap cleanup_enrollment EXIT
    case "$(uname -m)" in arm64) enroll_arch=arm64 ;; *) enroll_arch=amd64 ;; esac
    as_user mkdir -p "$enroll_staged/bin" || exit 1
    curl -fsSL --retry 3 "$CONTROL_PLANE/v1/nodes/download/darwin/$enroll_arch" -o "$enroll_staged/bin/zoo-node" || exit 1
    chown "$NODE_USER:staff" "$enroll_staged/bin/zoo-node" || exit 1
    chmod 755 "$enroll_staged/bin/zoo-node" || exit 1
    # Suppress untrusted remote error text: it may echo submitted credentials.
    as_user "$enroll_staged/bin/zoo-node" join "$NODE_TOKEN" --ssh-user "$NODE_USER" --dir "$enroll_staged" >/dev/null 2>&1 || exit 1
    [ -s "$enroll_staged/config.json" ] && [ -s "$enroll_staged/node.key" ] && [ -s "$enroll_staged/node.crt" ] && [ -s "$enroll_staged/ca.crt" ] || exit 1
    enroll_uid=$(id -u "$NODE_USER") || exit 1
    enroll_stopped=1
    for enroll_domain in "gui/$enroll_uid" "user/$enroll_uid"; do
        as_user launchctl bootout "$enroll_domain/dev.zoo.node" >/dev/null 2>&1 || true
    done
    for enroll_domain in "gui/$enroll_uid" "user/$enroll_uid"; do
        if as_user launchctl print "$enroll_domain/dev.zoo.node" >/dev/null 2>&1; then exit 1; fi
    done
    mv "$enroll_directory" "$enroll_backup" || exit 1
    enroll_promoted=1
    mv "$enroll_staged" "$enroll_directory" || exit 1
    resume_macos_node_service "$enroll_home" 1 || exit 1
    enroll_committed=1
)

join_or_resume_macos_node() { # HOME
    local directory="$1/.zoo-node" status
    if ! macos_existing_node "$1"; then
        join_node "$NODE_USER"
    else
        status=0
        macos_enrollment_status "$1" || status=$?
        case "$status" in
            0)
                say "Resuming this Mac's verified Zoo registration"
                resume_macos_node_service "$1" || die "couldn't resume the enrolled zoo-node service"
                ;;
            1)
                [ -n "$NODE_TOKEN" ] || die "this Mac's registration is no longer current; run the new setup command from Servers > Add server"
                say "Refreshing this Mac's Zoo registration"
                reenroll_macos_node "$1" || die "couldn't refresh Zoo enrollment; previous credentials were preserved; copy a fresh Add server setup command"
                ;;
            *) die "couldn't securely verify this Mac's registration; credentials were preserved; check the control plane connection and retry" ;;
        esac
    fi
    printf '%s\n' "$NODE_KEY" > "$directory/control-plane.pub"
    chown "$NODE_USER:staff" "$directory/control-plane.pub"
    chmod 600 "$directory/control-plane.pub"
}

install_macos_runtime() {
    local release temporary entry
    if [ -x /usr/local/bin/zoovm ] && [ "$NATIVE_VERSION_REQUESTED" = 0 ]; then
        ok "keeping the installed native VM runtime (use --version for a release upgrade)"
        return
    fi
    if [ -x /usr/local/bin/zoovm ] && pgrep -f '[z]oovm (run|restore|install)' >/dev/null 2>&1; then
        note "native VMs are active; retaining their runtime until a later setup run"
        return
    fi
    if [ -z "$ZOO_VERSION" ]; then
        ZOO_VERSION=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" | sed 's#.*/tag/v##')
    fi
    [[ "$ZOO_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die "invalid Zoo release version"
    say "Installing native VM runtime $ZOO_VERSION"
    release="https://github.com/$REPO/releases/download/v$ZOO_VERSION"
    temporary=$(mktemp -d)
    curl -fsSL --retry 3 -o "$temporary/zoovm-macos-arm64.tar.gz" "$release/zoovm-macos-arm64.tar.gz"
    curl -fsSL --retry 3 -o "$temporary/SHA256SUMS" "$release/SHA256SUMS"
    (cd "$temporary" && grep ' \./zoovm-macos-arm64\.tar\.gz$' SHA256SUMS | shasum -a 256 -c - >/dev/null) \
        || die "zoovm-macos-arm64.tar.gz doesn't match the release's SHA256SUMS"
    while IFS= read -r entry; do
        [ "$entry" = zoovm ] || die "unexpected entry in native VM runtime archive"
    done < <(tar -tzf "$temporary/zoovm-macos-arm64.tar.gz")
    tar -xzf "$temporary/zoovm-macos-arm64.tar.gz" -C "$temporary"
    [ -f "$temporary/zoovm" ] && [ ! -L "$temporary/zoovm" ] || die "native VM runtime archive has no regular binary"
    # Stage then rename, so an interrupted download/install cannot truncate the runtime.
    mkdir -p /usr/local/bin
    install -o root -g wheel -m 755 "$temporary/zoovm" /usr/local/bin/zoovm.new
    mv -f /usr/local/bin/zoovm.new /usr/local/bin/zoovm
    rm -rf "$temporary"
    if codesign -dv /usr/local/bin/zoovm 2>&1 | grep -q '^Authority=Developer ID Application'; then
        ok "zoovm is signed with a Developer ID and notarized"
    else
        note "zoovm is ad-hoc signed (this release was built without the signing secrets)"
    fi
}

start_macos_preparation() { # HOME
    local name=${ZOO_MACOS_BASE:-zoo-macos-base} user=${ZOO_MACOS_USER:-admin}
    if [ "$BASE" = 0 ]; then
        note "base preparation deferred; creating a macOS sandbox will prepare it automatically"
        return
    fi
    if [ -f "$1/.zoovm/vms/$name/zoo-ready" ] \
        && [ -f "$1/.zoovm/vms/$name/zoo-controller-key" ] \
        && [ "$(cat "$1/.zoovm/vms/$name/zoo-controller-key")" = "$NODE_KEY" ]; then
        ok "macOS template is already prepared"; return
    fi
    say "Starting automatic macOS preparation in the background"
    # Arguments travel as positional values; spaces in restore-image paths are preserved.
    # shellcheck disable=SC2016
    printf '%s\n' "$NODE_KEY" | as_user /bin/bash -c '
        home=$1; name=$2; user=$3; ipsw=$4
        mkdir -p "$home/.zoovm"
        if [ -n "$ipsw" ]; then
            nohup /usr/local/libexec/zoo-macos/prepare-base.sh --name "$name" --guest-user "$user" --ipsw "$ipsw" > "$home/.zoovm/install.log" 2>&1 <&0 &
        else
            nohup /usr/local/libexec/zoo-macos/prepare-base.sh --name "$name" --guest-user "$user" > "$home/.zoovm/install.log" 2>&1 <&0 &
        fi
    ' zoo-prepare "$1" "$name" "$user" "$IPSW"
    ok "host enrolled; track preparation under Remote Servers or create a macOS sandbox now"
}

# Install the API-matched fixed-function helper; no enrollment or VM changes.
install_macos_helper() {
    local helper_dir entries
    helper_dir=$(mktemp -d)
    curl -fsSL --retry 3 "$CONTROL_PLANE/v1/nodes/bootstrap/macos" -o "$helper_dir/bootstrap.tar.gz" \
        || die "couldn't download macOS preparation helpers from $CONTROL_PLANE"
    entries=$(tar -tzf "$helper_dir/bootstrap.tar.gz")
    while IFS= read -r entry; do
        case "$entry" in base-prepare.sh|firstboot.sh|configure-guest.sh|guest-reidentify.sh|prepare-base.sh) ;; *) die "unexpected file in macOS helper archive" ;; esac
    done <<< "$entries"
    tar -xzf "$helper_dir/bootstrap.tar.gz" -C "$helper_dir"
    bash "$helper_dir/base-prepare.sh" install "$NODE_USER"
    rm -rf "$helper_dir"
    ok "automatic macOS base preparation helper installed"
}


if [[ "${BASH_SOURCE[0]:-$0}" != "$0" ]]; then return 0; fi
parse_args "$@"
[ "$(id -u)" -eq 0 ] || die "run install.sh as root"

if [ "$(uname -s)" = Linux ] && [ "$NODE" = 1 ]; then
    install_linux_node
    exit 0
fi

if [ "$(uname -s)" = Darwin ]; then
    if [ "$NODE" = 0 ]; then
        install_desktop
        [ -n "$NODE_TOKEN" ] || exit 0
        NODE=1
    fi
    NODE_USER=${NODE_USER:-${SUDO_USER:-}}
    if [ -z "$NODE_USER" ] || [ "$NODE_USER" = root ]; then
        die "run this with sudo from the account that will own the VMs, or pass --user"
    fi
    [[ "$NODE_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,30}$ ]] || die "invalid host account name"
    node_home=$(dscl . -read "/Users/$NODE_USER" NFSHomeDirectory | sed 's/^NFSHomeDirectory:[[:space:]]*//')
    [ -d "$node_home" ] || die "the enrolled account has no home directory"
    macos_node_identity "$node_home"
    if [ "$BASE" = 1 ] && [ -n "$IPSW" ] && [ ! -f "$node_home/.zoovm/vms/${ZOO_MACOS_BASE:-zoo-macos-base}/installed" ]; then
        as_user test -f "$IPSW" && as_user test -r "$IPSW" || die "--ipsw must name a restore image readable by the host account"
    fi
    say "Checking this Mac"
    if [ "$(uname -m)" = arm64 ]; then ok "Apple Silicon"
    else
        ok "Intel Mac: Linux desktop, browser and code through Docker Desktop"
        as_user docker info --format '{{.OSType}}' 2>/dev/null | grep -qx linux || fail "start Docker Desktop in Linux-container mode before adding this Intel Mac"
    fi
    major=$(sw_vers -productVersion | cut -d. -f1)
    if [ "$major" -ge 14 ]; then ok "macOS $(sw_vers -productVersion)"; else fail "macOS $(sw_vers -productVersion), needs 14 or later"; fi
    if [ "$(uname -m)" = arm64 ]; then
        macos_node_disk "$node_home"
        need_memory $(( $(sysctl -n hw.memsize) / 1073741824 )) 16 32
    fi
    reach_control_plane
    preflight_done

    if ! nc -z 127.0.0.1 22 2>/dev/null; then
        say "Turning on Remote Login"
        systemsetup -setremotelogin on >/dev/null 2>&1 \
            || die "turn on Remote Login in System Settings > General > Sharing, then run this again"
    fi
    # SSH sessions get a bare PATH; zoovm lives in /usr/local/bin
    mkdir -p /etc/ssh/sshd_config.d
    printf 'Match User %s\n    SetEnv PATH=/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n' "$NODE_USER" \
        > /etc/ssh/sshd_config.d/100-zoo.conf
    launchctl kickstart -k system/com.openssh.sshd >/dev/null 2>&1 || true
    authorize "$NODE_USER" "$node_home" staff

    if [ "$(uname -m)" != arm64 ]; then
        join_or_resume_macos_node "$node_home"
        exit 0
    fi

    install_macos_runtime
    # The Mac's egress daemon loads each VM's policy into pf.
    mkdir -p /etc/sudoers.d
    printf '%s ALL=(root) NOPASSWD: /sbin/pfctl\n' "$NODE_USER" > /etc/sudoers.d/zoo-pf
    chmod 440 /etc/sudoers.d/zoo-pf
    visudo -cf /etc/sudoers.d/zoo-pf >/dev/null || { rm -f /etc/sudoers.d/zoo-pf; die "couldn't add the pfctl sudoers rule"; }
    install_macos_helper
    join_or_resume_macos_node "$node_home"
    start_macos_preparation "$node_home"
    exit 0
fi

install_linux
