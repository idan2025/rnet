"""RNS naming system: decentralized .rns name resolution."""
from rnet.naming.records import NameRecord, is_transfer  # noqa: F401
from rnet.naming.registry import NameRegistry  # noqa: F401
from rnet.naming.service import (  # noqa: F401
    NamingService,
    NameSource,
    FakeNameSource,
    RNSNameSource,
)