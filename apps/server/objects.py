"""Object storage: app profiles, agent screenshots and backups, and the parts of a sandbox moving between hosts.

ZOO_OBJECT_STORE is s3://<bucket>[/<prefix>], on AWS S3 or, with ZOO_S3_ENDPOINT (e.g. a MinIO or R2 URL), any
S3-compatible store. Credentials come from AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (and AWS_SESSION_TOKEN), or
the pod's identity on EKS (kms.aws_credentials); the region from ZOO_S3_REGION, else AWS_REGION, else us-east-1.

It is required: Zoo refuses to start without it (require). Small objects (profiles, screenshots) go through the API
with put and get; large ones (moves, home snapshots) never do: the API presigns URLs and the hosts upload and
download with curl."""

import hashlib
import hmac
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

import httpx

from apps.server.kms import aws_credentials, signing_key

EXPIRES = 6 * 3600


def require():
    """Fails startup when ZOO_OBJECT_STORE isn't an s3:// URL."""
    if not os.environ.get("ZOO_OBJECT_STORE", "").startswith("s3://"):
        raise RuntimeError("ZOO_OBJECT_STORE must be an s3://<bucket>[/<prefix>] URL (see server/objects.py)")


def region() -> str:
    return os.environ.get("ZOO_S3_REGION") or os.environ.get("AWS_REGION") or "us-east-1"


def location(key: str) -> tuple[str, str]:
    """The host and path of an object, under ZOO_OBJECT_STORE's prefix: path-style on a custom endpoint, and
    virtual-hosted on AWS."""
    target = urlparse(os.environ["ZOO_OBJECT_STORE"])
    bucket, prefix = target.netloc, target.path.strip("/")
    name = f"{prefix}/{key}" if prefix else key
    endpoint = os.environ.get("ZOO_S3_ENDPOINT", "").rstrip("/")
    if endpoint:
        return urlparse(endpoint).netloc, f"/{bucket}/{name}"
    return f"{bucket}.s3.{region()}.amazonaws.com", f"/{name}"


def presign_url(
    method: str,
    host: str,
    path: str,
    region: str,
    access_key: str,
    secret_key: str,
    amz_date: str,
    expires: int,
    token: str | None = None,
    scheme: str = "https",
    query: dict[str, str] | None = None,
) -> str:
    """A SigV4 query-string presigned URL (signed headers: host; payload: UNSIGNED-PAYLOAD); `query` adds request
    parameters, which are signed with the rest."""
    scope = f"{amz_date[:8]}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
        **(query or {}),
    }
    if token:
        params["X-Amz-Security-Token"] = token
    encoded = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(params.items()))
    uri = quote(path, safe="/")
    canonical = "\n".join([method, uri, encoded, f"host:{host}", "", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signature = hmac.new(signing_key(secret_key, amz_date[:8], region, "s3"), to_sign.encode(), hashlib.sha256)
    return f"{scheme}://{host}{uri}?{encoded}&X-Amz-Signature={signature.hexdigest()}"


def presign(method: str, key: str, expires: int = EXPIRES) -> str:
    access_key, secret_key, session_token = aws_credentials("object storage")
    host, path = location(key)
    scheme = urlparse(os.environ.get("ZOO_S3_ENDPOINT") or "https://").scheme or "https"
    return presign_url(
        method,
        host,
        path,
        region(),
        access_key,
        secret_key,
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        expires,
        session_token,
        scheme,
    )


def put(key: str, data: bytes):
    response = httpx.put(presign("PUT", key, 600), content=data, timeout=120)
    if response.status_code not in (200, 201, 204):
        raise RuntimeError(f"couldn't write {key} to object storage: {response.status_code} {response.text[:300]}")


def get(key: str) -> bytes:
    """The object's bytes; FileNotFoundError when there is no such object."""
    response = httpx.get(presign("GET", key, 600), timeout=120)
    if response.status_code == 404:
        raise FileNotFoundError(key)
    if response.status_code != 200:
        raise RuntimeError(f"couldn't read {key} from object storage: {response.status_code} {response.text[:300]}")
    return response.content


def put_file(key: str, path: str):
    """Uploads a file without reading it into memory (a database dump can be large)."""
    with open(path, "rb") as f:
        response = httpx.put(
            presign("PUT", key, 3600),
            content=f,
            headers={"Content-Length": str(os.path.getsize(path))},
            timeout=3600,
        )
    if response.status_code not in (200, 201, 204):
        raise RuntimeError(f"couldn't write {key} to object storage: {response.status_code} {response.text[:300]}")


def get_file(key: str, path: str):
    """Downloads an object into a file; FileNotFoundError when there is no such object."""
    with httpx.stream("GET", presign("GET", key, 3600), timeout=3600) as response:
        if response.status_code == 404:
            raise FileNotFoundError(key)
        if response.status_code != 200:
            raise RuntimeError(f"couldn't read {key} from object storage: {response.status_code}")
        with open(path, "wb") as f:
            f.writelines(response.iter_bytes())


def keys(prefix: str) -> list[str]:
    """Every key under prefix (relative to ZOO_OBJECT_STORE's prefix, like the keys put takes)."""
    return [key for key, _ in listing(prefix)]


def listing(prefix: str) -> list[tuple[str, int]]:
    """Every key under prefix with its size in bytes."""
    target = urlparse(os.environ["ZOO_OBJECT_STORE"])
    bucket, store = target.netloc, target.path.strip("/")
    endpoint = os.environ.get("ZOO_S3_ENDPOINT", "").rstrip("/")
    # ListObjectsV2 is a GET on the bucket itself
    host, path = (
        (urlparse(endpoint).netloc, f"/{bucket}") if endpoint else (f"{bucket}.s3.{region()}.amazonaws.com", "/")
    )
    scheme = urlparse(endpoint or "https://").scheme or "https"
    found: list[tuple[str, int]] = []
    token = None
    while True:
        query = {"list-type": "2", "prefix": f"{store}/{prefix}" if store else prefix}
        if token:
            query["continuation-token"] = token
        access_key, secret_key, session_token = aws_credentials("object storage")
        amz_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        url = presign_url(
            "GET", host, path, region(), access_key, secret_key, amz_date, 600, session_token, scheme, query
        )
        response = httpx.get(url, timeout=60)
        if response.status_code != 200:
            raise RuntimeError(
                f"couldn't list {prefix} in object storage: {response.status_code} {response.text[:300]}"
            )
        root = ET.fromstring(response.content)
        ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
        for item in root.iter(f"{ns}Contents"):
            key = item.findtext(f"{ns}Key") or ""
            found.append((key[len(store) + 1 :] if store else key, int(item.findtext(f"{ns}Size") or 0)))
        token = root.findtext(f"{ns}NextContinuationToken")
        if root.findtext(f"{ns}IsTruncated") != "true" or not token:
            return found


def delete_prefix(prefix: str):
    for key in keys(prefix):
        delete(key)


def delete(key: str):
    response = httpx.delete(presign("DELETE", key, 600), timeout=30)
    if response.status_code not in (200, 204, 404):
        raise RuntimeError(f"couldn't delete {key} from object storage: {response.status_code} {response.text[:300]}")
