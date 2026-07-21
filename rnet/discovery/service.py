"""Service + peer discovery over RNS announces.

A node's :class:`CapabilityAdvertisement` rides its RNS announce ``app_data``.
Peers register an announce handler on the ``rnet.node`` aspect, parse the ad,
and upsert the peer into the local registry. The full signed profile is
fetched on demand over a link and verified against the ad's ``prof_sig``.

This module is split from :mod:`rnet.core.node` so the parsing/verification
logic is unit-testable without a live RNS stack.
"""
from __future__ import annotations

import logging
from typing import Optional  # noqa: F401  (kept for annotation compatibility)

import RNS

from rnet.errors import DiscoveryError, SignatureError, WireError
from rnet.identity import IdentityManager, SignedProfile
from rnet.protocol.capabilities import CapabilityAdvertisement

log = logging.getLogger(__name__)

# RNS app aspect shared by all RNet nodes for presence/capability announces.
NODE_ASPECT = "rnet.node"

# Event names (mirrors rnet.core.events; duplicated as literals here to break
# an import cycle: discovery.service <- core.events <- core.node <- discovery).
_ANNOUNCE_RECEIVED = "announce.received"
_PEER_DISCOVERED = "peer.discovered"


class AnnounceHandler:
    """RNS announce handler contract: aspect_filter + received_announce.

    RNS calls :meth:`received_announce` on an internal thread; this handler
    only parses the ad and forwards to the bus/registry, leaving heavy work
    for the loop thread.
    """

    aspect_filter = NODE_ASPECT

    def __init__(self, bus: EventBus, on_announce):
        self._bus = bus
        self._on_announce = on_announce

    def received_announce(self, destination_hash, announced_identity, app_data):
        try:
            self._on_announce(destination_hash, announced_identity, app_data)
        except Exception:  # pragma: no cover - never let RNS see our crash
            log.exception("announce handling failed")


class ServiceDiscovery:
    """Builds capability ads, parses incoming ones, fetches+verifies profiles."""

    def __init__(self, bus: EventBus, registry, identity_manager: IdentityManager):
        self.bus = bus
        self.registry = registry
        self.idm = identity_manager

    # -- building our own advertisement -----------------------------------
    @staticmethod
    def build_capadv(name: str, caps, profile_sig: bytes, fp: bytes,
                     ts: int, max_bw: int, low_power: bool) -> CapabilityAdvertisement:
        adv = CapabilityAdvertisement(
            name=name,
            caps=list(caps),
            prof_sig=profile_sig,
            fp=fp,
            ts=ts,
            max_bw=int(max_bw),
            low_power=1 if low_power else 0,
        )
        adv_bytes = adv.to_bytes()
        if len(adv_bytes) > 223:
            raise DiscoveryError(
                f"capability advertisement too large ({len(adv_bytes)} > 223)"
            )
        return adv

    # -- handling incoming announces --------------------------------------
    def handle_announce(self, dest_hash, announced_identity, app_data) -> None:
        """Called from RNS thread. Parse, persist, emit."""
        if not app_data:
            return
        try:
            adv = CapabilityAdvertisement.from_bytes(app_data)
        except WireError:
            log.debug("ignoring non-rnet announce from %s",
                      dest_hash.hex() if isinstance(dest_hash, bytes) else dest_hash)
            return
        dest_hex = dest_hash.hex() if isinstance(dest_hash, (bytes, bytearray)) else str(dest_hash)
        # Persist the announced identity's pubkey so profiles can be verified.
        try:
            pubkey = announced_identity.get_public_key()
        except Exception:
            pubkey = None
        from rnet.identity.util import fingerprint

        try:
            fp = fingerprint(announced_identity)
        except Exception:
            fp = adv.fp
        self.idm.store.upsert_known(
            dest_hash=dest_hex,
            fingerprint_bytes=fp,
            pubkey=pubkey or b"",
            name=adv.name,
            verified=True,  # RNS has already proven the announce signature
        )
        self.registry.upsert_from_announce(adv, dest_hex)
        self.bus.emit_threadsafe(_ANNOUNCE_RECEIVED, {"dest": dest_hex, "adv": adv})
        self.bus.emit_threadsafe(
            _PEER_DISCOVERED, {"dest": dest_hex, "name": adv.name, "caps": adv.caps}
        )

    # -- profile fetch + verify ------------------------------------------
    def verify_profile_from_ad(self, adv: CapabilityAdvertisement,
                               signed_profile: SignedProfile,
                               identity: RNS.Identity) -> bool:
        """Verify a fetched profile against the prof_sig in the announce.

        Two checks: the profile's own signature (identity-bound) and the
        ``prof_sig`` carried in the ad (binds the ad to the same profile).
        """
        try:
            prof = IdentityManager.verify_profile(signed_profile, identity)
        except SignatureError:
            return False
        if prof.fp != adv.fp:
            return False
        # prof_sig is the node identity's signature over the profile bytes;
        # it is the same bytes the SignedProfile wraps.
        return identity.validate(adv.prof_sig, signed_profile.profile_bytes)