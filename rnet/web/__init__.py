"""Web: RHTTP protocol, server, transport, service + client."""
from rnet.web.protocol import (  # noqa: F401
    RHTTPRequest,
    RHTTPResponse,
    GET,
    POST,
    META,
    OK,
    NOT_FOUND,
    BAD_REQUEST,
    FORBIDDEN,
    RANGE_NOT_SATISFIABLE,
    INLINE_BODY_MAX,
    response_for_bytes,
)
from rnet.web.transport import (  # noqa: F401
    WebTransport,
    FakeWebTransport,
    RNSWebTransport,
    WEB_APP,
    WEB_ASPECT,
)
from rnet.web.server import RHTTPServer  # noqa: F401
from rnet.web.service import WebClient, WebService  # noqa: F401