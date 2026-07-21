"""Discovery: announce-based peer + service discovery."""
from rnet.discovery.registry import PeerRegistry  # noqa: F401
from rnet.discovery.service import (  # noqa: F401
    ServiceDiscovery,
    AnnounceHandler,
    NODE_ASPECT,
)