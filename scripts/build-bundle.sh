#!/usr/bin/env bash
set -euo pipefail

[ "$#" = 3 ] || { printf 'usage: bash scripts/build-bundle.sh VERSION OUTPUT_DIR amd64|arm64\n' >&2; exit 2; }
repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$repository_root/install.sh"
ZOO_VERSION="${1#v}"
output_directory="$2"
ARCH="$3"
[[ "$ZOO_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die "invalid release version"
case "$ARCH" in amd64|arm64) ;; *) die "architecture must be amd64 or arm64" ;; esac
case "$(uname -m):$ARCH" in x86_64:amd64|aarch64:arm64) ;; *) die "build the bundle on its native Linux architecture" ;; esac
[ "$(uname -s)" = Linux ] || die "bundle packing requires Linux"
require_tools docker curl python3 openssl zstd tar sha256sum split
mkdir -p "$output_directory"
output_directory=$(cd "$output_directory" && pwd)
bundle_work=$(mktemp -d)
trap 'rm -rf "$bundle_work"' EXIT
bundle_root="$bundle_work/bundle"
mkdir -p "$bundle_root/deploy/helm" "$bundle_root/images/$ARCH" "$bundle_work/rendered"
cp "$repository_root/compose.yml" "$repository_root/Caddyfile" "$repository_root/.env.example" "$repository_root/install.sh" "$bundle_root/"
cp -R "$repository_root/deploy/helm/zoo" "$bundle_root/deploy/helm/"
stamp_chart "$bundle_root/deploy/helm/zoo/Chart.yaml"
prepare_components "$bundle_root" all
component_manifest "$bundle_root/bundle.json"

mkdir -p "$bundle_work/helm"
tar -xzf "$bundle_root/artifacts/$ARCH/helm.tar.gz" -C "$bundle_work/helm"
bundle_helm="$bundle_work/helm/linux-$ARCH/helm"
export HELM_CONFIG_HOME="$bundle_work/helm-config"
export HELM_CACHE_HOME="$bundle_work/helm-cache"
export HELM_DATA_HOME="$bundle_work/helm-data"
ADMIN_EMAILS=""
ZOO_DOMAIN=""
WEB_HOST=zoo.test
API_HOST=api.zoo.test
write_zoo_values "$bundle_root/zoo-values.json"
write_bootstrap "$bundle_root/manifests/bootstrap.yaml"
"$bundle_helm" template kata-deploy "$bundle_root/charts/kata-deploy.tgz" --namespace kube-system \
    --include-crds --kube-version "${K3S_VERSION#v}" -f "$bundle_root/kata-values.yaml" > "$bundle_work/rendered/kata.yaml"
"$bundle_helm" template topolvm "$bundle_root/charts/topolvm.tgz" --namespace topolvm-system \
    --include-crds --kube-version "${K3S_VERSION#v}" -f "$bundle_root/topolvm-values.yaml" > "$bundle_work/rendered/topolvm.yaml"
"$bundle_helm" template zoo "$bundle_root/deploy/helm/zoo" --namespace "$NAMESPACE" \
    --include-crds --kube-version "${K3S_VERSION#v}" -f "$bundle_root/zoo-values.json" > "$bundle_work/rendered/zoo.yaml"

JWT_SECRET=bundle ZOO_VERSION="$ZOO_VERSION" ZOO_REGISTRY="$ZOO_REGISTRY" \
    docker compose --env-file /dev/null -f "$bundle_root/compose.yml" --profile domain config --images \
    | sort -u > "$bundle_root/images/$ARCH/docker.images"
printf '%s/zoo-sandbox-desktop:%s\n%s/zoo-sandbox-code:%s\n' "$ZOO_REGISTRY" "$ZOO_VERSION" "$ZOO_REGISTRY" "$ZOO_VERSION" \
    >> "$bundle_root/images/$ARCH/docker.images"
sort -u "$bundle_root/images/$ARCH/docker.images" -o "$bundle_root/images/$ARCH/docker.images"
python3 - "$bundle_work/rendered" "$bundle_root/manifests" "$bundle_root/images/$ARCH/docker.images" \
    > "$bundle_root/images/$ARCH/kube.images" <<'PY'
import pathlib, re, sys
images = set(pathlib.Path(sys.argv[3]).read_text().splitlines())
for directory in sys.argv[1:3]:
    for path in pathlib.Path(directory).glob("*.yaml"):
        for line in path.read_text().splitlines():
            match = re.match(r"""^\s*image:\s*["']?([^ \t"']+)""", line)
            if match:
                images.add(match[1])
for image in sorted(images):
    print(image)
PY
while IFS= read -r image; do
    docker pull --platform "linux/$ARCH" "$image"
done < "$bundle_root/images/$ARCH/docker.images"
mapfile -t docker_images < "$bundle_root/images/$ARCH/docker.images"
docker image save -o "$bundle_root/images/$ARCH/docker.tar" "${docker_images[@]}"
python3 - "$bundle_root/images/$ARCH/kube.images" "$bundle_root/images/$ARCH/kube.tar" "$ARCH" "$bundle_work/oci" \
    "$bundle_root/images/$ARCH/digests.json" <<'PY'
import hashlib, json, pathlib, re, tarfile, urllib.error, urllib.parse, urllib.request, sys

image_list, archive_name, architecture, directory, digest_file = sys.argv[1:]
root = pathlib.Path(directory)
blobs = root / "blobs" / "sha256"
blobs.mkdir(parents=True)
tokens = {}
accepted = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])

class RegistryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        redirected = super().redirect_request(request, response, code, message, headers, url)
        if redirected and urllib.parse.urlsplit(request.full_url).netloc != urllib.parse.urlsplit(url).netloc:
            redirected.remove_header("Authorization")
        return redirected

opener = urllib.request.build_opener(RegistryRedirect)

def request(registry, repository, url):
    headers = {"Accept": accepted, "User-Agent": "zoo-bundle"}
    token = tokens.get((registry, repository))
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        return opener.open(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
        challenge = dict(re.findall(r'(\w+)="([^"]*)"', error.headers.get("WWW-Authenticate", "")))
        realm = challenge.get("realm", "")
        if not realm.startswith("https://"):
            raise SystemExit(f"unsupported registry authentication: {registry}")
        parameters = {"service": challenge.get("service", registry), "scope": challenge.get("scope", f"repository:{repository}:pull")}
        with opener.open(realm + "?" + urllib.parse.urlencode(parameters), timeout=60) as response:
            credentials = json.load(response)
        token = credentials.get("token") or credentials.get("access_token")
        if not token:
            raise SystemExit(f"registry returned no pull token: {registry}")
        tokens[(registry, repository)] = token
        headers["Authorization"] = "Bearer " + token
        return opener.open(urllib.request.Request(url, headers=headers), timeout=60)

def blob_path(digest):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit(f"unsupported OCI digest: {digest}")
    return blobs / digest.split(":", 1)[1]

def store_manifest(registry, repository, reference):
    url = f"https://{registry}/v2/{repository}/manifests/{urllib.parse.quote(reference, safe=':')}"
    with request(registry, repository, url) as response:
        data = response.read()
        media_type = response.headers.get("Content-Type", "").split(";")[0]
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if reference.startswith("sha256:") and reference != digest:
        raise SystemExit(f"manifest checksum mismatch: {repository}${reference}")
    blob_path(digest).write_bytes(data)
    manifest = json.loads(data)
    descriptor = {"mediaType": manifest.get("mediaType", media_type), "digest": digest, "size": len(data)}
    if "manifests" in manifest:
        matches = [item for item in manifest["manifests"] if item.get("platform", {}).get("os") == "linux" and item.get("platform", {}).get("architecture") == architecture]
        if not matches:
            raise SystemExit(f"no linux/{architecture} image: {repository}:{reference}")
        store_manifest(registry, repository, matches[0]["digest"])
    elif "config" in manifest and "layers" in manifest:
        for component in [manifest["config"], *manifest["layers"]]:
            destination = blob_path(component["digest"])
            if destination.exists():
                continue
            url = f"https://{registry}/v2/{repository}/blobs/{component['digest']}"
            digest_value = hashlib.sha256()
            length = 0
            with request(registry, repository, url) as response, destination.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    digest_value.update(chunk)
                    length += len(chunk)
                    output.write(chunk)
            if "sha256:" + digest_value.hexdigest() != component["digest"] or length != component["size"]:
                raise SystemExit(f"blob checksum mismatch: {component['digest']}")
    else:
        raise SystemExit(f"unsupported image manifest: {repository}:{reference}")
    return descriptor

descriptors = []
digests = {}
for image in pathlib.Path(image_list).read_text().splitlines():
    first, separator, remainder = image.partition("/")
    if separator and ("." in first or ":" in first or first == "localhost"):
        registry, name = first, remainder
    else:
        registry, name = "docker.io", image
    if registry == "docker.io":
        repository = name if "/" in name else "library/" + name
        transport = "registry-1.docker.io"
    else:
        repository, transport = name, registry
    if "@" in repository:
        repository, reference = repository.split("@", 1)
        repository = repository.rsplit(":", 1)[0]
    else:
        repository, reference = repository.rsplit(":", 1) if ":" in repository.rsplit("/", 1)[-1] else (repository, "latest")
    descriptor = store_manifest(transport, repository, reference)
    named = f"{registry}/{repository}:{reference}" if not reference.startswith("sha256:") else f"{registry}/{repository}${reference}"
    descriptor["annotations"] = {"org.opencontainers.image.ref.name": named, "io.containerd.image.name": named}
    descriptors.append(descriptor)
    digests[image] = descriptor["digest"]
(root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
(root / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": descriptors}) + "\n")
with tarfile.open(archive_name, "w") as archive:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            archive.add(path, arcname=str(path.relative_to(root)))
pathlib.Path(digest_file).write_text(json.dumps(digests, indent=2) + "\n")
PY
(cd "$bundle_root" && find . -type f -print0 | sort -z | xargs -0 sha256sum > "$bundle_work/SHA256SUMS")
cp "$bundle_work/SHA256SUMS" "$bundle_root/SHA256SUMS"
bundle_name="zoo-bundle-$ZOO_VERSION-$ARCH.tar.zst"
archive="$output_directory/$bundle_name"
tar -C "$bundle_root" -cf - . | zstd -T0 -10 -o "$archive"
archive_bytes=$(stat -c %s "$archive")
if [ "$archive_bytes" -gt 1900000000 ]; then
    split -b 1800M -d -a 4 "$archive" "$archive.part-"
    python3 - "$archive" > "$archive.parts" <<'PY'
import hashlib, json, pathlib, sys
archive = pathlib.Path(sys.argv[1])
parts = []
for path in sorted(archive.parent.glob(archive.name + ".part-*")):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    parts.append({"file": path.name, "sha256": digest.hexdigest()})
json.dump(parts, sys.stdout, indent=2)
sys.stdout.write("\n")
PY
    rm "$archive"
fi
(cd "$output_directory" && sha256sum "$bundle_name"* > "zoo-bundle-$ZOO_VERSION-$ARCH.sha256")
printf 'Bundle files: %s/zoo-bundle-%s-%s*\n' "$output_directory" "$ZOO_VERSION" "$ARCH"
