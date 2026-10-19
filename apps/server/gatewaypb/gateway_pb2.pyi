from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ScreenRequest(_message.Message):
    __slots__ = ("sandbox_id", "format", "scale", "quality", "session", "timeout_ms", "quiet_ms", "threshold", "x", "y", "mask", "keysym", "down", "text", "since_version")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    SCALE_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    SESSION_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    QUIET_MS_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    MASK_FIELD_NUMBER: _ClassVar[int]
    KEYSYM_FIELD_NUMBER: _ClassVar[int]
    DOWN_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    SINCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    format: str
    scale: float
    quality: int
    session: str
    timeout_ms: int
    quiet_ms: int
    threshold: float
    x: int
    y: int
    mask: int
    keysym: int
    down: bool
    text: str
    since_version: int
    def __init__(self, sandbox_id: _Optional[str] = ..., format: _Optional[str] = ..., scale: _Optional[float] = ..., quality: _Optional[int] = ..., session: _Optional[str] = ..., timeout_ms: _Optional[int] = ..., quiet_ms: _Optional[int] = ..., threshold: _Optional[float] = ..., x: _Optional[int] = ..., y: _Optional[int] = ..., mask: _Optional[int] = ..., keysym: _Optional[int] = ..., down: _Optional[bool] = ..., text: _Optional[str] = ..., since_version: _Optional[int] = ...) -> None: ...

class ScreenReply(_message.Message):
    __slots__ = ("image", "width", "height", "changed", "x", "y", "region_width", "region_height", "stable", "elapsed_ms", "version", "changed_tiles", "boxes")
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    CHANGED_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    REGION_WIDTH_FIELD_NUMBER: _ClassVar[int]
    REGION_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    STABLE_FIELD_NUMBER: _ClassVar[int]
    ELAPSED_MS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    CHANGED_TILES_FIELD_NUMBER: _ClassVar[int]
    BOXES_FIELD_NUMBER: _ClassVar[int]
    image: bytes
    width: int
    height: int
    changed: bool
    x: int
    y: int
    region_width: int
    region_height: int
    stable: bool
    elapsed_ms: int
    version: int
    changed_tiles: _containers.RepeatedCompositeFieldContainer[ScreenTile]
    boxes: _containers.RepeatedCompositeFieldContainer[ScreenBox]
    def __init__(self, image: _Optional[bytes] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., changed: _Optional[bool] = ..., x: _Optional[int] = ..., y: _Optional[int] = ..., region_width: _Optional[int] = ..., region_height: _Optional[int] = ..., stable: _Optional[bool] = ..., elapsed_ms: _Optional[int] = ..., version: _Optional[int] = ..., changed_tiles: _Optional[_Iterable[_Union[ScreenTile, _Mapping]]] = ..., boxes: _Optional[_Iterable[_Union[ScreenBox, _Mapping]]] = ...) -> None: ...

class ScreenTile(_message.Message):
    __slots__ = ("x", "y", "width", "height", "hash", "data")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    x: int
    y: int
    width: int
    height: int
    hash: int
    data: bytes
    def __init__(self, x: _Optional[int] = ..., y: _Optional[int] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., hash: _Optional[int] = ..., data: _Optional[bytes] = ...) -> None: ...

class ScreenBox(_message.Message):
    __slots__ = ("x", "y", "width", "height")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    x: int
    y: int
    width: int
    height: int
    def __init__(self, x: _Optional[int] = ..., y: _Optional[int] = ..., width: _Optional[int] = ..., height: _Optional[int] = ...) -> None: ...

class ViewerFrame(_message.Message):
    __slots__ = ("sandbox_id", "data", "view_only")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    VIEW_ONLY_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    data: bytes
    view_only: bool
    def __init__(self, sandbox_id: _Optional[str] = ..., data: _Optional[bytes] = ..., view_only: _Optional[bool] = ...) -> None: ...

class GuestCallRequest(_message.Message):
    __slots__ = ("sandbox_id", "op", "header_json", "payload")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    OP_FIELD_NUMBER: _ClassVar[int]
    HEADER_JSON_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    op: str
    header_json: bytes
    payload: bytes
    def __init__(self, sandbox_id: _Optional[str] = ..., op: _Optional[str] = ..., header_json: _Optional[bytes] = ..., payload: _Optional[bytes] = ...) -> None: ...

class GuestCallReply(_message.Message):
    __slots__ = ("header_json", "payload")
    HEADER_JSON_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    header_json: bytes
    payload: bytes
    def __init__(self, header_json: _Optional[bytes] = ..., payload: _Optional[bytes] = ...) -> None: ...

class Frame(_message.Message):
    __slots__ = ("sandbox_id", "header_json", "payload")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    HEADER_JSON_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    header_json: bytes
    payload: bytes
    def __init__(self, sandbox_id: _Optional[str] = ..., header_json: _Optional[bytes] = ..., payload: _Optional[bytes] = ...) -> None: ...

class Bytes(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    def __init__(self, data: _Optional[bytes] = ...) -> None: ...

class NodeRelayFrame(_message.Message):
    __slots__ = ("certificate_der", "message")
    CERTIFICATE_DER_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    certificate_der: bytes
    message: bytes
    def __init__(self, certificate_der: _Optional[bytes] = ..., message: _Optional[bytes] = ...) -> None: ...

class NodeStatusRequest(_message.Message):
    __slots__ = ("server_id",)
    SERVER_ID_FIELD_NUMBER: _ClassVar[int]
    server_id: str
    def __init__(self, server_id: _Optional[str] = ...) -> None: ...

class NodeStatusReply(_message.Message):
    __slots__ = ("online", "report_json")
    ONLINE_FIELD_NUMBER: _ClassVar[int]
    REPORT_JSON_FIELD_NUMBER: _ClassVar[int]
    online: bool
    report_json: bytes
    def __init__(self, online: _Optional[bool] = ..., report_json: _Optional[bytes] = ...) -> None: ...

class GuestStatusRequest(_message.Message):
    __slots__ = ("sandbox_id",)
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    def __init__(self, sandbox_id: _Optional[str] = ...) -> None: ...

class GuestStatusReply(_message.Message):
    __slots__ = ("connected", "version", "os", "services", "metrics_json", "quiet_seconds", "heartbeat")
    CONNECTED_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    OS_FIELD_NUMBER: _ClassVar[int]
    SERVICES_FIELD_NUMBER: _ClassVar[int]
    METRICS_JSON_FIELD_NUMBER: _ClassVar[int]
    QUIET_SECONDS_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    connected: bool
    version: int
    os: str
    services: _containers.RepeatedScalarFieldContainer[str]
    metrics_json: bytes
    quiet_seconds: float
    heartbeat: bool
    def __init__(self, connected: _Optional[bool] = ..., version: _Optional[int] = ..., os: _Optional[str] = ..., services: _Optional[_Iterable[str]] = ..., metrics_json: _Optional[bytes] = ..., quiet_seconds: _Optional[float] = ..., heartbeat: _Optional[bool] = ...) -> None: ...
