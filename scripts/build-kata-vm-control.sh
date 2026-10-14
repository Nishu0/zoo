#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PATCH_ROOT="$ROOT/patches/kata/4.2.0"
source "$PATCH_ROOT/source.lock"
OUT=${1:?usage: build-kata-vm-control.sh OUTPUT_DIRECTORY amd64\|arm64 [PINNED_SOURCE_ARCHIVE]}
ARCH=${2:?architecture required}
case "$ARCH" in
    amd64) KATA_ARCH=x86_64 ;;
    arm64) KATA_ARCH=aarch64 ;;
    *) printf 'unsupported architecture: %s\n' "$ARCH" >&2; exit 1 ;;
esac
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
ARCHIVE="$WORK/source.tar.gz"
if [ "$#" -eq 3 ]; then
    cp "$3" "$ARCHIVE"
else
    curl --fail --location --retry 3 "$KATA_SOURCE_URL" --output "$ARCHIVE"
fi
ACTUAL=$(openssl dgst -sha256 "$ARCHIVE" | awk '{print $NF}')
[ "$ACTUAL" = "$KATA_SOURCE_SHA256" ] || { printf 'pinned Kata source checksum mismatch\n' >&2; exit 1; }
tar -xzf "$ARCHIVE" -C "$WORK"
SOURCE="$WORK/kata-containers-$KATA_VERSION"
git -C "$SOURCE" apply --check "$PATCH_ROOT/lifecycle.patch"
git -C "$SOURCE" apply "$PATCH_ROOT/lifecycle.patch"
cp -R "$PATCH_ROOT/overlay/src/runtime/." "$SOURCE/src/runtime/"
MAKE=${GNU_MAKE:-make}
printf '%s' "$KATA_REVISION" > "$SOURCE/src/runtime/.git-commit"
"$MAKE" -C "$SOURCE/src/runtime" -o .git-commit pkg/katautils/config-settings.go PREFIX=/opt/kata ARCH="$KATA_ARCH" COMMIT="$KATA_REVISION"
export GOOS=linux GOARCH="$ARCH" CGO_ENABLED=0
go vet -C "$SOURCE/src/runtime" ./virtcontainers ./pkg/containerd-shim-v2
STAGE="$WORK/stage"
mkdir -p "$STAGE/opt/kata/bin" "$STAGE/opt/kata/share/zoo-vm-control" "$WORK/compile-only"
go build -C "$SOURCE/src/runtime" -trimpath \
    -ldflags "-X github.com/kata-containers/kata-containers/src/runtime/pkg/katautils.VERSION=$KATA_VERSION+zoo.vm-control.1 -X github.com/kata-containers/kata-containers/src/runtime/pkg/katautils.COMMIT=$KATA_REVISION" \
    -o "$STAGE/opt/kata/bin/containerd-shim-kata-v2" ./cmd/containerd-shim-kata-v2
go test -C "$SOURCE/src/runtime" -c -o "$WORK/compile-only/" ./virtcontainers ./pkg/containerd-shim-v2
BINARY_SHA=$(openssl dgst -sha256 "$STAGE/opt/kata/bin/containerd-shim-kata-v2" | awk '{print $NF}')
PATCH_SHA=$(openssl dgst -sha256 "$PATCH_ROOT/lifecycle.patch" | awk '{print $NF}')
OVERLAY_SHA=$(cd "$PATCH_ROOT/overlay"; find . -type f | LC_ALL=C sort | while read -r FILE; do openssl dgst -sha256 "$FILE"; done | openssl dgst -sha256 | awk '{print $NF}')
printf '{"protocol":"zoo-kata-vm-control/1","kata_version":"%s","kata_revision":"%s","source_sha256":"%s","lifecycle_patch_sha256":"%s","overlay_sha256":"%s","binary_sha256":"%s","architecture":"%s","runtime":"go-rootful","hypervisors":["qemu","clh"]}\n' \
    "$KATA_VERSION" "$KATA_REVISION" "$KATA_SOURCE_SHA256" "$PATCH_SHA" "$OVERLAY_SHA" "$BINARY_SHA" "$ARCH" \
    > "$STAGE/opt/kata/share/zoo-vm-control/manifest.json"
NAME="zoo-kata-shim-$KATA_VERSION-vm-control-1-linux-$ARCH.tar.gz"
python3 - "$STAGE" "$OUT/$NAME" <<'PY'
import gzip
import os
import pathlib
import sys
import tarfile

stage = pathlib.Path(sys.argv[1])
epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))

def root_owned(member):
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    member.mtime = epoch
    return member

with open(sys.argv[2], "wb") as output:
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=epoch) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in sorted(stage.rglob("*")):
                archive.add(path, arcname=str(path.relative_to(stage)), recursive=False, filter=root_owned)
PY
PACKAGE_SHA=$(openssl dgst -sha256 "$OUT/$NAME" | awk '{print $NF}')
printf '%s  %s\n' "$PACKAGE_SHA" "$NAME" > "$OUT/$NAME.sha256"
printf 'Built %s\n' "$OUT/$NAME"
