from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class NodeMessage(_message.Message):
    __slots__ = ("hello", "status", "data", "close", "renew")
    HELLO_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    CLOSE_FIELD_NUMBER: _ClassVar[int]
    RENEW_FIELD_NUMBER: _ClassVar[int]
    hello: Hello
    status: Status
    data: TunnelData
    close: TunnelClose
    renew: Renew
    def __init__(self, hello: _Optional[_Union[Hello, _Mapping]] = ..., status: _Optional[_Union[Status, _Mapping]] = ..., data: _Optional[_Union[TunnelData, _Mapping]] = ..., close: _Optional[_Union[TunnelClose, _Mapping]] = ..., renew: _Optional[_Union[Renew, _Mapping]] = ...) -> None: ...

class ApiMessage(_message.Message):
    __slots__ = ("welcome", "open", "data", "close", "update", "renewal")
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    OPEN_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    CLOSE_FIELD_NUMBER: _ClassVar[int]
    UPDATE_FIELD_NUMBER: _ClassVar[int]
    RENEWAL_FIELD_NUMBER: _ClassVar[int]
    welcome: Welcome
    open: TunnelOpen
    data: TunnelData
    close: TunnelClose
    update: UpdateChunk
    renewal: Renewal
    def __init__(self, welcome: _Optional[_Union[Welcome, _Mapping]] = ..., open: _Optional[_Union[TunnelOpen, _Mapping]] = ..., data: _Optional[_Union[TunnelData, _Mapping]] = ..., close: _Optional[_Union[TunnelClose, _Mapping]] = ..., update: _Optional[_Union[UpdateChunk, _Mapping]] = ..., renewal: _Optional[_Union[Renewal, _Mapping]] = ...) -> None: ...

class Driver(_message.Message):
    __slots__ = ("name", "available", "detail")
    NAME_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    name: str
    available: bool
    detail: str
    def __init__(self, name: _Optional[str] = ..., available: _Optional[bool] = ..., detail: _Optional[str] = ...) -> None: ...

class Hello(_message.Message):
    __slots__ = ("version", "os", "arch", "hostname", "drivers", "targets")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    OS_FIELD_NUMBER: _ClassVar[int]
    ARCH_FIELD_NUMBER: _ClassVar[int]
    HOSTNAME_FIELD_NUMBER: _ClassVar[int]
    DRIVERS_FIELD_NUMBER: _ClassVar[int]
    TARGETS_FIELD_NUMBER: _ClassVar[int]
    version: str
    os: str
    arch: str
    hostname: str
    drivers: _containers.RepeatedCompositeFieldContainer[Driver]
    targets: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, version: _Optional[str] = ..., os: _Optional[str] = ..., arch: _Optional[str] = ..., hostname: _Optional[str] = ..., drivers: _Optional[_Iterable[_Union[Driver, _Mapping]]] = ..., targets: _Optional[_Iterable[str]] = ...) -> None: ...

class Welcome(_message.Message):
    __slots__ = ("api_version", "endpoints", "status_seconds")
    API_VERSION_FIELD_NUMBER: _ClassVar[int]
    ENDPOINTS_FIELD_NUMBER: _ClassVar[int]
    STATUS_SECONDS_FIELD_NUMBER: _ClassVar[int]
    api_version: str
    endpoints: _containers.RepeatedScalarFieldContainer[str]
    status_seconds: int
    def __init__(self, api_version: _Optional[str] = ..., endpoints: _Optional[_Iterable[str]] = ..., status_seconds: _Optional[int] = ...) -> None: ...

class Check(_message.Message):
    __slots__ = ("name", "ok", "detail")
    NAME_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    name: str
    ok: bool
    detail: str
    def __init__(self, name: _Optional[str] = ..., ok: _Optional[bool] = ..., detail: _Optional[str] = ...) -> None: ...

class Status(_message.Message):
    __slots__ = ("cpus", "memory_total", "memory_available", "disk_total", "disk_free", "load", "sandboxes", "checks")
    CPUS_FIELD_NUMBER: _ClassVar[int]
    MEMORY_TOTAL_FIELD_NUMBER: _ClassVar[int]
    MEMORY_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    DISK_TOTAL_FIELD_NUMBER: _ClassVar[int]
    DISK_FREE_FIELD_NUMBER: _ClassVar[int]
    LOAD_FIELD_NUMBER: _ClassVar[int]
    SANDBOXES_FIELD_NUMBER: _ClassVar[int]
    CHECKS_FIELD_NUMBER: _ClassVar[int]
    cpus: int
    memory_total: int
    memory_available: int
    disk_total: int
    disk_free: int
    load: float
    sandboxes: _containers.RepeatedScalarFieldContainer[str]
    checks: _containers.RepeatedCompositeFieldContainer[Check]
    def __init__(self, cpus: _Optional[int] = ..., memory_total: _Optional[int] = ..., memory_available: _Optional[int] = ..., disk_total: _Optional[int] = ..., disk_free: _Optional[int] = ..., load: _Optional[float] = ..., sandboxes: _Optional[_Iterable[str]] = ..., checks: _Optional[_Iterable[_Union[Check, _Mapping]]] = ...) -> None: ...

class TunnelOpen(_message.Message):
    __slots__ = ("id", "target")
    ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    id: int
    target: str
    def __init__(self, id: _Optional[int] = ..., target: _Optional[str] = ...) -> None: ...

class TunnelData(_message.Message):
    __slots__ = ("id", "data")
    ID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    id: int
    data: bytes
    def __init__(self, id: _Optional[int] = ..., data: _Optional[bytes] = ...) -> None: ...

class TunnelClose(_message.Message):
    __slots__ = ("id", "error")
    ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    id: int
    error: str
    def __init__(self, id: _Optional[int] = ..., error: _Optional[str] = ...) -> None: ...

class UpdateChunk(_message.Message):
    __slots__ = ("version", "sha256", "size", "data", "last")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    LAST_FIELD_NUMBER: _ClassVar[int]
    version: str
    sha256: str
    size: int
    data: bytes
    last: bool
    def __init__(self, version: _Optional[str] = ..., sha256: _Optional[str] = ..., size: _Optional[int] = ..., data: _Optional[bytes] = ..., last: _Optional[bool] = ...) -> None: ...

class Renew(_message.Message):
    __slots__ = ("csr",)
    CSR_FIELD_NUMBER: _ClassVar[int]
    csr: bytes
    def __init__(self, csr: _Optional[bytes] = ...) -> None: ...

class Renewal(_message.Message):
    __slots__ = ("certificate", "error")
    CERTIFICATE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    certificate: bytes
    error: str
    def __init__(self, certificate: _Optional[bytes] = ..., error: _Optional[str] = ...) -> None: ...
