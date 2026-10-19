"""Wraps the data keys that encrypt stored secrets (server/security.py). ZOO_KMS picks what wraps new ones:

  (unset)                     ZOO_SECRETS_KEY, kept by this API
  aws:<key id or ARN>         AWS KMS. Credentials from AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (and
                              AWS_SESSION_TOKEN), else the pod's identity on EKS (aws_credentials); the
                              region from the ARN, else AWS_REGION
  gcp:projects/P/locations/L/keyRings/R/cryptoKeys/K
                              Google Cloud KMS. A service account key file in GOOGLE_APPLICATION_CREDENTIALS, else
                              the metadata server of the VM the API runs on
  vault:<mount>/<key>         HashiCorp Vault's transit engine, at VAULT_ADDR with VAULT_TOKEN

A wrapped key names what wrapped it, so keys from an earlier setting still open; `make rotate-secrets` rewraps them
all with the current one. Data keys stay the same, so no secret needs encrypting again."""

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import UTC, datetime

import httpx
import jwt
from cryptography.fernet import Fernet, MultiFernet

TIMEOUT = 10
GCP_SCOPE = "https://www.googleapis.com/auth/cloudkms"
GCP_METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"

_gcp_token: dict = {}
_lock = threading.Lock()


def _key(material: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()))


def local_fernet() -> MultiFernet:
    """ZOO_SECRETS_KEY encrypts; older keys only decrypt until rotate() rewraps."""
    keys = [os.environ["ZOO_SECRETS_KEY"]]
    keys += [k for k in os.environ.get("ZOO_SECRETS_KEY_PREVIOUS", "").split(",") if k]
    return MultiFernet([_key(k) for k in dict.fromkeys(keys)])


def wrap(data_key: bytes) -> str:
    setting = os.environ.get("ZOO_KMS", "").strip()
    if not setting:
        return "local:" + local_fernet().encrypt(data_key).decode()
    provider, _, key = setting.partition(":")
    if provider == "aws":
        region = aws_region(key)
        blob = aws("Encrypt", {"KeyId": key, "Plaintext": b64(data_key)}, region)["CiphertextBlob"]
        return f"aws:{region}:{blob}"
    if provider == "gcp":
        return f"gcp:{key}:{gcp(key, 'encrypt', {'plaintext': b64(data_key)})['ciphertext']}"
    if provider == "vault":
        return f"vault:{key}:{vault(key, 'encrypt', {'plaintext': b64(data_key)})['ciphertext']}"
    raise RuntimeError(f"ZOO_KMS must start with aws:, gcp: or vault:, not {setting!r}")


def unwrap(wrapped: str) -> bytes:
    provider, _, rest = wrapped.partition(":")
    if provider == "local":
        return local_fernet().decrypt(rest.encode())
    # the key (or region) never contains a colon; the ciphertext after it may (Vault's does)
    where, _, ciphertext = rest.partition(":")
    if provider == "aws":
        return unb64(aws("Decrypt", {"CiphertextBlob": ciphertext}, where)["Plaintext"])
    if provider == "gcp":
        return unb64(gcp(where, "decrypt", {"ciphertext": ciphertext})["plaintext"])
    if provider == "vault":
        return unb64(vault(where, "decrypt", {"ciphertext": ciphertext})["plaintext"])
    raise RuntimeError(f"a data key was wrapped by {provider!r}, which this API doesn't know")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    return base64.b64decode(text)


# AWS KMS, signed with Signature Version 4


def aws_region(key: str) -> str:
    if key.startswith("arn:"):
        return key.split(":")[3]
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise RuntimeError("set AWS_REGION, or give ZOO_KMS the key's full ARN")
    return region


def signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    key = ("AWS4" + secret_key).encode()
    for part in (date, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def sigv4(
    method: str,
    host: str,
    path: str,
    headers: dict[str, str],
    payload: bytes,
    region: str,
    service: str,
    access_key: str,
    secret_key: str,
    amz_date: str,
) -> str:
    """The Authorization header for a request with no query string; headers must include host and x-amz-date."""
    names = sorted(k.lower() for k in headers)
    lower = {k.lower(): v.strip() for k, v in headers.items()}
    signed = ";".join(names)
    canonical = "\n".join(
        [method, path, "", *(f"{k}:{lower[k]}" for k in names), "", signed, hashlib.sha256(payload).hexdigest()]
    )
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signature = hmac.new(signing_key(secret_key, amz_date[:8], region, service), to_sign.encode(), hashlib.sha256)
    signature = signature.hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"


_aws_credentials: dict = {}


def aws_credentials(what: str = "AWS") -> tuple[str, str, str | None]:
    """Access key, secret key and session token: from AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, else the pod's
    identity on EKS, through IAM roles for service accounts (AWS_ROLE_ARN and AWS_WEB_IDENTITY_TOKEN_FILE) or EKS
    Pod Identity (AWS_CONTAINER_CREDENTIALS_FULL_URI). Pod identity credentials are kept until shortly before
    they expire."""
    access_key, secret_key = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        return access_key, secret_key, os.environ.get("AWS_SESSION_TOKEN")
    with _lock:
        if _aws_credentials.get("expires", 0) > time.time():
            return _aws_credentials["access"], _aws_credentials["secret"], _aws_credentials["token"]
        if os.environ.get("AWS_ROLE_ARN") and os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE"):
            found = assume_role_with_web_identity()
        elif os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI"):
            found = container_credentials()
        else:
            raise RuntimeError(f"{what} needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or a pod identity on EKS")
        _aws_credentials.update(found)
        return found["access"], found["secret"], found["token"]


def assume_role_with_web_identity() -> dict:
    import xml.etree.ElementTree as ET

    with open(os.environ["AWS_WEB_IDENTITY_TOKEN_FILE"]) as f:
        web_token = f.read().strip()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    host = f"sts.{region}.amazonaws.com" if region else "sts.amazonaws.com"
    params = {
        "Action": "AssumeRoleWithWebIdentity",
        "Version": "2011-06-15",
        "RoleArn": os.environ["AWS_ROLE_ARN"],
        "RoleSessionName": os.environ.get("AWS_ROLE_SESSION_NAME", "zoo"),
        "WebIdentityToken": web_token,
    }
    response = httpx.post(f"https://{host}/", data=params, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"AWS STS AssumeRoleWithWebIdentity failed: {response.status_code} {response.text[:300]}")
    found = {e.tag.rsplit("}", 1)[-1]: e.text or "" for e in ET.fromstring(response.text).iter()}
    return {
        "access": found["AccessKeyId"],
        "secret": found["SecretAccessKey"],
        "token": found["SessionToken"],
        "expires": datetime.fromisoformat(found["Expiration"]).timestamp() - 300,
    }


def container_credentials() -> dict:
    headers = {}
    if path := os.environ.get("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE"):
        with open(path) as f:
            headers["Authorization"] = f.read().strip()
    response = httpx.get(os.environ["AWS_CONTAINER_CREDENTIALS_FULL_URI"], headers=headers, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"EKS Pod Identity credentials failed: {response.status_code} {response.text[:300]}")
    found = response.json()
    return {
        "access": found["AccessKeyId"],
        "secret": found["SecretAccessKey"],
        "token": found.get("Token"),
        "expires": datetime.fromisoformat(found["Expiration"]).timestamp() - 300,
    }


def aws(action: str, body: dict, region: str) -> dict:
    access_key, secret_key, session_token = aws_credentials("AWS KMS")
    host = f"kms.{region}.amazonaws.com"
    payload = json.dumps(body).encode()
    headers = {
        "content-type": "application/x-amz-json-1.1",
        "host": host,
        "x-amz-date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "x-amz-target": f"TrentService.{action}",
    }
    if session_token:
        headers["x-amz-security-token"] = session_token
    headers["authorization"] = sigv4(
        "POST", host, "/", headers, payload, region, "kms", access_key, secret_key, headers["x-amz-date"]
    )
    response = httpx.post(f"https://{host}/", content=payload, headers=headers, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"AWS KMS {action} failed: {response.status_code} {response.text[:300]}")
    return response.json()


# Google Cloud KMS


def gcp_token() -> str:
    with _lock:
        if _gcp_token.get("expires", 0) > time.time():
            return _gcp_token["token"]
        path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if path:
            with open(path) as f:
                account = json.load(f)
            now = int(time.time())
            assertion = jwt.encode(
                {
                    "iss": account["client_email"],
                    "scope": GCP_SCOPE,
                    "aud": account["token_uri"],
                    "iat": now,
                    "exp": now + 3600,
                },
                account["private_key"],
                algorithm="RS256",
            )
            response = httpx.post(
                account["token_uri"],
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
                timeout=TIMEOUT,
            )
        else:
            response = httpx.get(GCP_METADATA_TOKEN, headers={"Metadata-Flavor": "Google"}, timeout=TIMEOUT)
        if response.status_code != 200:
            raise RuntimeError(f"no Google Cloud token: {response.status_code} {response.text[:300]}")
        data = response.json()
        _gcp_token.update(token=data["access_token"], expires=time.time() + int(data["expires_in"]) - 60)
        return _gcp_token["token"]


def gcp(key: str, verb: str, body: dict) -> dict:
    response = httpx.post(
        f"https://cloudkms.googleapis.com/v1/{key}:{verb}",
        json=body,
        headers={"Authorization": f"Bearer {gcp_token()}"},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Google Cloud KMS {verb} failed: {response.status_code} {response.text[:300]}")
    return response.json()


# HashiCorp Vault transit


def vault(key: str, verb: str, body: dict) -> dict:
    address, token = os.environ.get("VAULT_ADDR"), os.environ.get("VAULT_TOKEN")
    if not address or not token:
        raise RuntimeError("Vault needs VAULT_ADDR and VAULT_TOKEN")
    mount, _, name = key.rpartition("/")
    if not mount or not name:
        raise RuntimeError("ZOO_KMS for Vault is vault:<transit mount>/<key name>")
    headers = {"X-Vault-Token": token}
    if namespace := os.environ.get("VAULT_NAMESPACE"):
        headers["X-Vault-Namespace"] = namespace
    response = httpx.post(
        f"{address.rstrip('/')}/v1/{mount}/{verb}/{name}", json=body, headers=headers, timeout=TIMEOUT
    )
    if response.status_code != 200:
        raise RuntimeError(f"Vault {verb} failed: {response.status_code} {response.text[:300]}")
    return response.json()["data"]
