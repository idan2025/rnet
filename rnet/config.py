"""Node configuration and path resolution."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from rnet.errors import ConfigError


def default_datadir() -> str:
    """Default per-user data dir, overridable by RNET_DATADIR."""
    env = os.environ.get("RNET_DATADIR")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser("~/.rnet")


@dataclass
class NodeConfig:
    """Runtime configuration for a node."""

    name: str = "rnet-node"
    # Capability tokens this node advertises. Validated against CapabilitySet.
    capabilities: List[str] = field(default_factory=lambda: ["messaging", "relay"])
    # RNS config dir. None => let RNS pick its default (~/.reticulum).
    rns_configdir: str = None
    # RNet data dir (SQLite DB, keyfiles, CAS blocks).
    datadir: str = field(default_factory=default_datadir)
    # Announce interval (seconds) + jitter fraction.
    announce_interval: float = 120.0
    announce_jitter: float = 0.2
    # Anti-replay window width and clock skew tolerance (seconds).
    replay_window: int = 64
    clock_skew: int = 300
    # Mailbox item TTL (seconds).
    mailbox_ttl: int = 14 * 86400
    # Outbox retry backoff base (seconds) and max attempts.
    outbox_base_delay: float = 30.0
    outbox_max_attempts: int = 12
    # Ratcheted messaging (RNS identity ratchets) for forward secrecy on links.
    # Path is passed to Destination.enable_ratchets; None disables.
    ratchets_path: str = None
    # Announce app_data hard cap (RNS budget minus framing/signature slop).
    capadv_max_bytes: int = 223

    # --- radio-first -----------------------------------------------------
    # Low-power mode: lengthen announces, avoid background fetches, prefer
    # store-and-forward, smaller fragment size. For solar/sleepy nodes.
    low_power: bool = False
    # Highest bandwidth class this node's transports can serve. Peers use
    # this to avoid routing high-bandwidth requests to a 100-byte radio node.
    max_bandwidth: int = 1  # rnet.protocol.capabilities.Bandwidth.MEDIUM
    # Max body bytes sent inline in one envelope before fragmenting into CAS.
    inline_body_max: int = 4096
    # Fragment payload size for app-layer fragmentation on constrained links.
    fragment_size: int = 80

    # --- web (Phase 2) ---------------------------------------------------
    # If the node offers `web`, serve this directory over RHTTP. None = no host.
    web_root: str = None
    # Max inline RHTTP body before content-addressing into CAS (bytes).
    web_inline_max: int = 16 * 1024

    def validate(self) -> None:
        if not self.name or len(self.name) > 32:
            raise ConfigError("node name must be 1..32 chars")
        if self.announce_interval < 5:
            raise ConfigError("announce_interval too low (min 5s)")
        if self.replay_window < 8:
            raise ConfigError("replay_window too small (min 8)")
        # Capabilities validated lazily against CapabilitySet in discovery.

    def effective_announce_interval(self) -> float:
        """Low-power nodes announce far less often to save energy."""
        if self.low_power:
            return self.announce_interval * 6.0
        return self.announce_interval

    def paths(self) -> dict:
        d = self.datadir
        return {
            "datadir": d,
            "db": os.path.join(d, "rnet.db"),
            "keys": os.path.join(d, "keys"),
            "cas": os.path.join(d, "cas"),
            "backup": os.path.join(d, "rnet.db.bak"),
        }