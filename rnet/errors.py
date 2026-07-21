"""RNet exception hierarchy."""


class RNetError(Exception):
    """Base class for all RNet errors."""


class ConfigError(RNetError):
    """Invalid configuration."""


class IdentityError(RNetError):
    """Identity load/save/verify failure."""


class SignatureError(RNetError):
    """Signature missing or invalid."""


class WireError(RNetError):
    """Malformed wire frame or unsupported version/type."""


class ReplayError(RNetError):
    """Frame rejected by anti-replay window."""


class DiscoveryError(RNetError):
    """Peer/service discovery failure."""


class MessageError(RNetError):
    """Messaging failure (no route, unknown recipient, etc.)."""


class StorageError(RNetError):
    """Content-addressed storage failure."""


class NamingError(RNetError):
    """Name resolution/publish failure."""


class SchemaError(RNetError):
    """Database schema mismatch or migration failure."""