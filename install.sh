#!/usr/bin/env bash
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
NODE_KEY=""
NODE_TOKEN=""
CONTROL_PLANE=""
NODE_USER=""
CHECK_ONLY=0
FAILED=0
IPSW=""
BASE=1
DOCS="https://github.com/$REPO"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok    %s\n' "$*"; }
note() { printf '  note  %s\n' "$*"; }
fail() { printf '  fail  %s\n' "$*" >&2; FAILED=1; }

usage() {
    cat <<'USAGE'
Install Zoo on a Linux KVM host:
  install.sh [--runtime docker|kube] [--version VERSION] [--dir PATH]
             [--admin-email EMAIL] [--domain HOST] [--check]
  install.sh --runtime kube [--device EMPTY_BLOCK_DEVICE | --disk-gb GB]
  install.sh --bundle PATH [--runtime docker|kube]

Bundles contain binaries, charts, manifests and both runtimes' images for one
architecture. PATH accepts a .tar.zst archive or its .parts index. Offline hosts
must already have python3, openssl, zstd, tar, systemd and iptables; kube also
needs LVM2, losetup, truncate and modprobe. Offline HTTPS uses a local certificate
authority. The loop-file storage option is for development, not durable storage.

Add an Apple Silicon Mac running macOS 14+:
  install.sh --node --token TOKEN --control-plane URL --key SSH_PUBLIC_KEY
             [--user USER] [--version VERSION] [--ipsw PATH] [--no-base]
USAGE
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version|--dir|--admin-email|--domain|--runtime|--device|--disk-gb|--bundle|--key|--token|--control-plane|--user|--ipsw)
                [ "$#" -ge 2 ] && [ -n "$2" ] || die "$1 needs a value"
                case "$1" in
                    --version) ZOO_VERSION="${2#v}" ;;
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
    if [ "$NODE" = 1 ]; then
        [ "$(uname -s)" = Darwin ] || die "--node supports Apple Silicon Macs only"
        [ -n "$NODE_TOKEN" ] && [ -n "$CONTROL_PLANE" ] && [ -n "$NODE_KEY" ] || die "--node needs --token, --control-plane and the API-provided --key"
        [ -z "$BUNDLE" ] || die "--bundle installs the Linux control plane; use the API's Mac join command"
    fi
}

need_disk() {
    local free
    free=$(df -Pk "$1" | awk 'NR==2 {print int($4 / 1048576)}')
    if [ "$free" -ge "$2" ]; then ok "$free GB free on $1"; else fail "$free GB free on $1, needs $2 GB"; fi
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
    local host web api database_host object_endpoint gateway_peers
    host=$(ip -4 -o addr show scope global | awk 'NR==1 {split($4, address, "/"); print address[1]}')
    [ -n "$host" ] || die "could not determine this host's address"
    if [ "$RUNTIME" = docker ]; then
        web="http://$host:3000"; api="http://$host:8000"
        if [ -n "$ZOO_DOMAIN" ]; then web="https://$ZOO_DOMAIN"; api="$web/api"; fi
        database_host=postgres; object_endpoint=http://seaweedfs:8333; gateway_peers=gateway
    else
        WEB_HOST="${ZOO_DOMAIN:-zoo.test}"
        API_HOST="api.$WEB_HOST"
        web="http://$WEB_HOST"; api="http://$API_HOST"
        if [ -n "$ZOO_DOMAIN" ]; then web="https://$WEB_HOST"; api="https://$API_HOST"; fi
        database_host=zoo-postgres; object_endpoint=http://zoo-object-store:8333
        gateway_peers="zoo-gateway-peers.$NAMESPACE.svc.cluster.local"
    fi
    [ ! -e "$ZOO_HOME/.env" ] || die "$ZOO_HOME/.env already exists; use Compose or Helm to upgrade"
    umask 077
    JWT_SECRET="$(openssl rand -hex 48)" ZOO_SECRETS_KEY="$(openssl rand -hex 48)" POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
    AWS_ACCESS_KEY_ID="$(openssl rand -hex 12)" AWS_SECRET_ACCESS_KEY="$(openssl rand -hex 32)" \
    ZOO_VERSION="$ZOO_VERSION" ZOO_REGISTRY="$ZOO_REGISTRY" ZOO_RUNTIME="$RUNTIME" \
    ADMIN_EMAILS="$ADMIN_EMAILS" ZOO_DOMAIN="$ZOO_DOMAIN" ZOO_API_URL="$api" CORS_ORIGINS="$web" \
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
    cp "$PAYLOAD/compose.yml" "$PAYLOAD/Caddyfile" "$ZOO_HOME/"
    write_docker_ingress
    if [ -z "$ZOO_DOMAIN" ]; then compose_files+=(-f "$ZOO_HOME/compose.override.yml"); fi
    if [ -n "$BUNDLE" ]; then docker load -i "$PAYLOAD/images/$ARCH/docker.tar"
    else docker compose --env-file "$ZOO_HOME/.env" "${compose_files[@]}" -p zoo pull; fi
    docker compose --env-file "$ZOO_HOME/.env" "${compose_files[@]}" -p zoo up -d --no-build --pull never --wait --wait-timeout 600
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
    [ -c /dev/kvm ] && [ -r /dev/kvm ] && [ -w /dev/kvm ] || die "/dev/kvm must exist and be readable/writable; enable hardware or nested virtualization"
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
    say "Zoo $ZOO_VERSION installed with $RUNTIME"
    printf 'Dashboard: %s\nConfiguration: %s/.env\n' "$(config_value CORS_ORIGINS)" "$ZOO_HOME"
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

# zoo-node joins with the token and runs as a LaunchAgent of the user that owns the VMs.
join_node() { # USER
    local bin
    bin=$(mktemp)
    curl -fsSL --retry 3 "$CONTROL_PLANE/v1/nodes/download/darwin/arm64" -o "$bin" || die "couldn't download zoo-node from $CONTROL_PLANE"
    chmod 755 "$bin"
    chown "$1" "$bin"
    as_user "$bin" join "$NODE_TOKEN" --service --ssh-user "$1" || die "zoo-node couldn't join; the token may be used or expired"
    ln -sf "$(eval echo "~$1")/.zoo-node/bin/zoo-node" /usr/local/bin/zoo-node
    rm -f "$bin"
    say "This machine joined the control plane"
    cat <<EOF

  It can run: macOS sandboxes

  zoo-node dials out to the control plane, so no inbound port is needed. The server shows up under Servers.
  Its log: tail ~$1/.zoo-node/node.log
EOF
}

# macOS: a node for macOS sandboxes

as_user() { sudo -u "$NODE_USER" -H "$@"; }

# build_base installs macOS into the base VM and prepares it without Setup Assistant: it seeds the VM's Data volume
# with .AppleSetupDone and a one-shot LaunchDaemon (FIRSTBOOT below) that creates the guest user, does what
# guest-setup.sh does and shuts down. A second boot checks that SSH came up, then the VM is marked ready with its
# version, which sandboxes cloned from it record.
build_base() { # HOME
    local name=${ZOO_MACOS_BASE:-zoo-macos-base} user=${ZOO_MACOS_USER:-admin} vm password ip deadline os build
    vm="$1/.zoovm/vms/$name"
    if [ -f "$vm/zoo-ready" ]; then ok "base VM $name is ready ($(cat "$vm/zoo-ready"))"; return; fi
    if ! as_user /usr/local/bin/zoovm get "$name" >/dev/null 2>&1; then
        say "Installing macOS into the base VM $name (downloads Apple's IPSW; about an hour)"
        as_user /usr/local/bin/zoovm install "$name" ${IPSW:+--ipsw "$IPSW"}
    fi
    as_user /usr/local/bin/zoovm stop "$name" >/dev/null 2>&1 || true
    say "Preparing the base VM"
    password=$(openssl rand -hex 12)
    # kept for unlocking System Settings in the VM (the Accessibility grant); Zoo itself signs in with its key
    (umask 077 && printf '%s\n' "$password" > "$vm/password") && chown "$NODE_USER" "$vm/password"
    seed_base "$vm/disk.img" "$user" "$password"
    say "First boot: creating $user and setting up the guest; the VM shuts itself down when done"
    as_user /usr/local/bin/zoovm run "$name" > "$vm/run.log" 2>&1 &
    local pid=$!
    deadline=$((SECONDS + 1800))
    while kill -0 "$pid" 2>/dev/null; do
        if [ $SECONDS -ge $deadline ]; then
            as_user /usr/local/bin/zoovm stop "$name" >/dev/null 2>&1 || true
            die "the base VM's first boot didn't finish in 30 minutes; open it from the server's page to see why"
        fi
        sleep 5
    done
    say "Second boot: checking SSH"
    as_user sh -c "nohup /usr/local/bin/zoovm run '$name' > '$vm/run.log' 2>&1 < /dev/null &"
    deadline=$((SECONDS + 600))
    until ip=$(as_user /usr/local/bin/zoovm ip "$name" 2>/dev/null) && nc -z -G 2 "$ip" 22 2>/dev/null; do
        if [ $SECONDS -ge $deadline ]; then
            die "the base VM never opened SSH. Start it from the server's page and read /var/log/zoo-firstboot.log in it"
        fi
        sleep 5
    done
    as_user /usr/local/bin/zoovm stop "$name" --timeout 60 >/dev/null
    os=$(as_user /usr/local/bin/zoovm version "$name" | sed -n 's/.*"os":"\([^"]*\)".*/\1/p')
    build=$(as_user /usr/local/bin/zoovm version "$name" | sed -n 's/.*"build":"\([^"]*\)".*/\1/p')
    printf '%s-%s-%s\n' "${os:-macos}" "${build:-unknown}" "$(date -u +%Y%m%d%H%M)" | as_user tee "$vm/zoo-ready" >/dev/null
    ok "base VM $name is ready ($(cat "$vm/zoo-ready")); its user $user's password is in $vm/password"
}

seed_base() { # DISK USER PASSWORD
    local dev container data mnt db
    dev=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount "$1" | awk 'NR==1 {print $1}')
    [ -n "$dev" ] || die "couldn't attach $1"
    container=$(diskutil list "$dev" | awk '$2 == "Apple_APFS" {for (i = 3; i < NF; i++) if ($i == "Container") print $(i + 1)}' | head -n1)
    data=$( [ -n "$container" ] && diskutil apfs list "$container" | awk '/\(Data\)/ {print $(NF - 1)}' | head -n1)
    if [ -z "$data" ]; then
        hdiutil detach "$dev" >/dev/null 2>&1 || true
        die "found no Data volume in $1; finish the base VM's setup from the server's page instead"
    fi
    mnt=$(mktemp -d)
    diskutil mount -mountPoint "$mnt" "$data" >/dev/null
    diskutil enableOwnership "$mnt" >/dev/null
    db="$mnt/private/var/db"
    mkdir -p "$db/zoo" "$mnt/Library/LaunchDaemons"
    printf '%s\n' "$NODE_KEY" > "$db/zoo/key"
    printf '%s' "$2" > "$db/zoo/user"
    printf '%s' "$3" > "$db/zoo/password"
    printf '%s\n' "$FIRSTBOOT" > "$db/zoo/firstboot.sh"
    chown -R root:wheel "$db/zoo"
    chmod 700 "$db/zoo"
    chmod 600 "$db/zoo"/*
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
    touch "$db/.AppleSetupDone"
    diskutil unmount "$mnt" >/dev/null || diskutil unmount force "$mnt" >/dev/null
    hdiutil detach "$dev" >/dev/null
    rmdir "$mnt" 2>/dev/null || true
}

# Runs once in the base VM, as root, at its first boot. Its log is /var/log/zoo-firstboot.log in the VM.
read -r -d '' FIRSTBOOT <<'SCRIPT' || true
#!/bin/sh
umask 077
exec >>/var/log/zoo-firstboot.log 2>&1
set -eu
echo "zoo first boot: $(date)"
trap 'rm -f /Library/LaunchDaemons/com.zoo.firstboot.plist /var/db/zoo/password; shutdown -h now' EXIT
ME=$(cat /var/db/zoo/user)
PASSWORD=$(cat /var/db/zoo/password)
KEY=$(cat /var/db/zoo/key)
i=0
until dscl . -list /Users >/dev/null 2>&1 || [ $i -ge 60 ]; do i=$((i + 1)); sleep 2; done
id "$ME" >/dev/null 2>&1 || sysadminctl -addUser "$ME" -fullName "$ME" -password "$PASSWORD" -admin
createhomedir -c -u "$ME" >/dev/null
HOME_DIR=$(dscl . -read "/Users/$ME" NFSHomeDirectory | awk '{print $2}')
# the per-user Setup Assistant screens at first login
for key in DidSeeCloudSetup DidSeeSiriSetup DidSeePrivacy DidSeeScreenTime DidSeeAppearanceSetup \
    DidSeeAccessibility DidSeeActivationLock DidSeeTouchIDSetup DidSeeApplePaySetup DidSeeTermsOfAddress \
    DidSeeLockdownMode DidSeeIntelligence DidSeeWallpaper SkipFirstLoginOptimization; do
    sudo -u "$ME" defaults write com.apple.SetupAssistant "$key" -bool true
done
sudo -u "$ME" defaults write com.apple.SetupAssistant LastSeenCloudProductVersion "$(sw_vers -productVersion)"
sudo -u "$ME" defaults write com.apple.SetupAssistant LastSeenBuddyBuildVersion "$(sw_vers -buildVersion)"
# what guest-setup.sh does
echo "$ME ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/zoo
chmod 440 /etc/sudoers.d/zoo
launchctl enable system/com.openssh.sshd
launchctl bootstrap system /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
for dir in "$HOME_DIR/.ssh" /var/root/.ssh; do
    mkdir -p "$dir" && chmod 700 "$dir"
    printf '%s\n' "$KEY" > "$dir/authorized_keys" && chmod 600 "$dir/authorized_keys"
done
chown -R "$ME:staff" "$HOME_DIR/.ssh"
echo "PermitRootLogin prohibit-password" > /etc/ssh/sshd_config.d/000-zoo.conf
sysadminctl -autologin set -userName "$ME" -password "$PASSWORD"
pmset -a sleep 0 displaysleep 0 disksleep 0
sudo -u "$ME" defaults -currentHost write com.apple.screensaver idleTime 0
defaults write /Library/Preferences/com.apple.screensaver loginWindowIdleTime 0
sudo -u "$ME" sysadminctl -screenLock off -password "$PASSWORD" || true
touch /var/db/zoo/done
echo "zoo first boot: done"
SCRIPT


if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then return 0; fi
parse_args "$@"
[ "$(id -u)" -eq 0 ] || die "run install.sh as root"

if [ "$(uname -s)" = Darwin ]; then
    [ "$NODE" = 1 ] || die "the control plane runs on Linux. To run sandboxes on this Mac, open Servers > Add server > Mac in your dashboard and run the command it shows."
    NODE_USER=${NODE_USER:-${SUDO_USER:-}}
    if [ -z "$NODE_USER" ] || [ "$NODE_USER" = root ]; then
        die "run this with sudo from the account that will own the VMs, or pass --user"
    fi
    node_home=$(dscl . -read "/Users/$NODE_USER" NFSHomeDirectory | awk '{print $2}')
    say "Checking this Mac"
    if [ "$(uname -m)" = arm64 ]; then ok "Apple Silicon"; else fail "Intel Mac: macOS VMs need Apple Silicon (Virtualization.framework restores macOS only on arm64)"; fi
    major=$(sw_vers -productVersion | cut -d. -f1)
    if [ "$major" -ge 14 ]; then ok "macOS $(sw_vers -productVersion)"; else fail "macOS $(sw_vers -productVersion), needs 14 or later"; fi
    need_disk "$node_home" 80
    need_memory $(( $(sysctl -n hw.memsize) / 1073741824 )) 16 32
    reach_control_plane
    preflight_done

    if ! nc -z 127.0.0.1 22 2>/dev/null; then
        say "Turning on Remote Login"
        systemsetup -setremotelogin on >/dev/null 2>&1 \
            || die "turn on Remote Login in System Settings > General > Sharing, then run this again"
    fi
    # SSH sessions get a bare PATH; zoovm lives in /usr/local/bin
    mkdir -p /etc/ssh/sshd_config.d
    printf 'Match User %s\n    SetEnv PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n' "$NODE_USER" \
        > /etc/ssh/sshd_config.d/100-zoo.conf
    launchctl kickstart -k system/com.openssh.sshd >/dev/null 2>&1 || true
    authorize "$NODE_USER" "$node_home" staff

    if [ -z "$ZOO_VERSION" ]; then
        ZOO_VERSION=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" | sed 's#.*/tag/v##')
    fi
    say "Installing zoovm $ZOO_VERSION"
    release="https://github.com/$REPO/releases/download/v$ZOO_VERSION"
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/zoovm-macos-arm64.tar.gz" "$release/zoovm-macos-arm64.tar.gz"
    curl -fsSL -o "$tmp/SHA256SUMS" "$release/SHA256SUMS"
    (cd "$tmp" && grep ' \./zoovm-macos-arm64\.tar\.gz$' SHA256SUMS | shasum -a 256 -c - >/dev/null) \
        || die "zoovm-macos-arm64.tar.gz doesn't match the release's SHA256SUMS"
    mkdir -p /usr/local/bin
    tar -xzf "$tmp/zoovm-macos-arm64.tar.gz" -C /usr/local/bin
    chmod 755 /usr/local/bin/zoovm
    rm -rf "$tmp"
    if codesign -dv /usr/local/bin/zoovm 2>&1 | grep -q '^Authority=Developer ID Application'; then
        ok "zoovm is signed with a Developer ID and notarized"
    else
        note "zoovm is ad-hoc signed (this release was built without the signing secrets)"
    fi
    # the Mac's egress daemon loads each VM's network policy into pf (macos/README.md, Network policy)
    printf '%s ALL=(root) NOPASSWD: /sbin/pfctl\n' "$NODE_USER" > /etc/sudoers.d/zoo-pf
    chmod 440 /etc/sudoers.d/zoo-pf
    visudo -cf /etc/sudoers.d/zoo-pf >/dev/null || { rm -f /etc/sudoers.d/zoo-pf; die "couldn't add the pfctl sudoers rule"; }
    [ "$BASE" = 0 ] || build_base "$node_home"

    join_node "$NODE_USER"
    if [ "$BASE" = 0 ]; then echo "  Then build its base macOS VM from the server's page; it restores macOS from Apple's IPSW."
    else echo "  The base VM is ready. For window tools, grant it Accessibility once (macos/README.md, step 4)."; fi
    exit 0
fi

install_linux
