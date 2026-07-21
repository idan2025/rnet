"""Identity helpers shared across the identity package (avoids circular imports)."""
from __future__ import annotations

import RNS

# Length of the short, stable identity fingerprint (bytes).
FP_LEN = 8


def fingerprint(identity: RNS.Identity) -> bytes:
    """Stable 8-byte fingerprint of an identity's public key.

    Binding used in profiles, capability ads, and name records: ties signed
    metadata to a specific keypair independent of any destination's app aspect.
    """
    return RNS.Identity.full_hash(identity.get_public_key())[:FP_LEN]