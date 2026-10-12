import asyncio
import os
import socket
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from db.connection import db_manager
from db.generated.query import Querier
from server import backups
from server.auth_api import AuthApi, Member, UserResponse
from server.sandbox_api import SandboxApi, SandboxResponse, to_response
from utils.time import to_stamp

PUBLIC_IP = os.environ.get("ZOO_PUBLIC_IP")


class DomainRequest(BaseModel):
    hostname: str = Field(max_length=253, pattern=r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

    @field_validator("hostname", mode="before")
    @classmethod
    def lower(cls, value):
        return canonical(value) if isinstance(value, str) else value


class DomainResponse(BaseModel):
    id: str
    hostname: str
    url: str
    addresses: list[str]
    public_ip: str | None
    points_here: bool | None
    created_at: str


def resolve(hostname: str) -> list[str]:
    """The hostname's IPv4 and IPv6 addresses, or [] when it doesn't resolve."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return []
    return sorted({str(info[4][0]) for info in infos if info[0] in (socket.AF_INET, socket.AF_INET6)})


def canonical(hostname: str) -> str:
    """A hostname as Caddy may ask about it: any case, maybe with the root's trailing dot."""
    return hostname.strip().rstrip(".").lower()


def points_here(addresses: list[str]) -> bool | None:
    """Whether DNS sends the hostname to this server (ZOO_PUBLIC_IP, one or more comma-separated addresses), or None
    when the server doesn't know its public address."""
    if not PUBLIC_IP:
        return None
    ours = {ip.strip() for ip in PUBLIC_IP.split(",") if ip.strip()}
    return bool(ours & set(addresses))


class BackupResponse(BaseModel):
    # restore with: python -m server.backups restore <key>
    key: str
    size: int
    created_at: str


def backup_response(key: str, size: int) -> BackupResponse:
    return BackupResponse(key=key, size=size, created_at=to_stamp(backups.taken_at(key)))


class AdminApi:
    def __init__(self, app: APIRouter, auth: AuthApi, sandboxes: SandboxApi):
        self.sandboxes = sandboxes
        self.app = app
        self.auth = auth
        self.admins = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        def admin_user(user: Member = Depends(current_user)) -> Member:
            if user.email.lower() not in self.admins:
                raise HTTPException(status_code=403, detail="admin only")
            return user

        @self.app.get("/admin/users", response_model=list[UserResponse])
        def list_users(
            _: Member = Depends(admin_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[UserResponse]:
            return [UserResponse(id=u.id, email=u.email, name=u.name, avatar_url=u.avatar_url) for u in db.list_users()]

        @self.app.get("/admin/sandboxes", response_model=list[SandboxResponse])
        def list_sandboxes(
            _: Member = Depends(admin_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[SandboxResponse]:
            return [to_response(s, db) for s in db.list_all_sandboxes()]

        @self.app.get("/admin/domains", response_model=list[DomainResponse])
        def list_domains(
            _: Member = Depends(admin_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[DomainResponse]:
            return [self._domain(d) for d in db.list_domains()]

        @self.app.post("/admin/domains", response_model=DomainResponse, status_code=201)
        def add_domain(
            payload: DomainRequest, user: Member = Depends(admin_user), db: Querier = Depends(db_manager.get_client)
        ) -> DomainResponse:
            hostname = canonical(payload.hostname)
            if db.get_domain_by_hostname(hostname=hostname) is not None:
                raise HTTPException(status_code=409, detail="domain already added")
            return self._domain(db.create_domain(id=str(uuid.uuid4()), hostname=hostname, created_by=user.id))

        @self.app.post("/admin/domains/{domain_id}/verify", response_model=DomainResponse)
        def verify_domain(
            domain_id: str, _: Member = Depends(admin_user), db: Querier = Depends(db_manager.get_client)
        ) -> DomainResponse:
            """Looks the domain up again, for after its DNS record was changed."""
            domain = next((d for d in db.list_domains() if d.id == domain_id), None)
            if domain is None:
                raise HTTPException(status_code=404, detail="domain not found")
            return self._domain(domain)

        @self.app.delete("/admin/domains/{domain_id}", status_code=204)
        def delete_domain(
            domain_id: str, _: Member = Depends(admin_user), db: Querier = Depends(db_manager.get_client)
        ):
            db.delete_domain(id=domain_id)

        @self.app.get("/domains/check")
        def check_domain(domain: str = "", db: Querier = Depends(db_manager.get_client)):
            """Caddy's on-demand TLS `ask`: a certificate is only issued for a hostname this answers 200 for, so
            nobody can make the server request certificates for names it doesn't serve."""
            hostname = canonical(domain)
            configured = canonical(os.environ.get("ZOO_DOMAIN", ""))
            if not hostname or (hostname != configured and db.get_domain_by_hostname(hostname=hostname) is None):
                raise HTTPException(status_code=404, detail="unknown domain")
            return {"ok": True}

        @self.app.post("/admin/backups", response_model=BackupResponse, status_code=201)
        async def create_backup(_: Member = Depends(admin_user)) -> BackupResponse:
            """Takes a backup now (server/backups.py): the database dump is done when this returns; the sandbox
            snapshots are queued."""
            key = await asyncio.to_thread(backups.run, self.sandboxes)
            return next(backup_response(k, size) for k, size in backups.dumps() if k == key)

        @self.app.get("/admin/backups", response_model=list[BackupResponse])
        def list_backups(_: Member = Depends(admin_user)) -> list[BackupResponse]:
            return [backup_response(key, size) for key, size in backups.dumps()]

    def _domain(self, domain) -> DomainResponse:
        addresses = resolve(domain.hostname)
        return DomainResponse(
            id=domain.id,
            hostname=domain.hostname,
            url=f"https://{domain.hostname}",
            addresses=addresses,
            public_ip=PUBLIC_IP,
            points_here=points_here(addresses),
            created_at=to_stamp(domain.created_at),
        )
