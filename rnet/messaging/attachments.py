"""Hybrid AES-256-GCM attachments for messaging.

RNS ``Identity.encrypt`` is fine for tiny payloads but RSA-style sealing is
expensive for large files. For attachments we use a hybrid scheme:

  1. Generate a random AES-256-GCM key.
  2. AES-GCM encrypt the file -> ciphertext; chunk it into CAS -> manifest hash.
  3. Wrap (seal) the AES key + nonce with the recipient identity
     (``recipient_identity.encrypt``) -> ``wrapped`` blob.
  4. Send the manifest hash + wrapped blob in the message Body.

The recipient unwraps the AES key with their identity, pulls the ciphertext
from CAS, and AES-GCM decrypts. Relays and CAS peers see only ciphertext.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import RNS
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from rnet.errors import MessageError
from rnet.storage.cas import (
    ContentStore,
    ManifestStore,
    build_manifest,
    assemble,
    hash_data,
)

AES_KEY_SIZE = 32
NONCE_SIZE = 12


@dataclass
class AttachmentRef:
    """Reference placed in a message Body."""

    manifest_hash: bytes  # CAS manifest hash of the ciphertext
    wrapped: bytes        # recipient-sealed (aes_key || nonce)
    name: str = ""
    size: int = 0         # plaintext size

    def to_dict(self) -> dict:
        return {
            "hash": self.manifest_hash.hex(),
            "wrapped": self.wrapped.hex(),
            "name": self.name,
            "size": self.size,
        }


def encrypt_attachment(data: bytes, recipient_identity: RNS.Identity,
                       content_store: ContentStore,
                       manifest_store: ManifestStore,
                       name: str = "") -> AttachmentRef:
    """Encrypt ``data`` for ``recipient_identity`` and store ciphertext in CAS."""
    aes_key = AESGCM.generate_key(AES_KEY_SIZE * 8)  # bits
    nonce = os.urandom(NONCE_SIZE)
    aead = AESGCM(aes_key)
    ciphertext = aead.encrypt(nonce, data, None)  # includes GCM tag
    manifest = build_manifest(ciphertext, content_store, name=name + ".enc",
                              ctype="application/octet-stream")
    mhash = manifest_store.put(manifest)
    # Seal (aes_key || nonce) to the recipient identity.
    wrapped = recipient_identity.encrypt(aes_key + nonce)
    return AttachmentRef(manifest_hash=mhash, wrapped=wrapped, name=name, size=len(data))


def decrypt_attachment(ref: AttachmentRef, recipient_identity: RNS.Identity,
                       content_store: ContentStore,
                       manifest_store: ManifestStore,
                       sources=None) -> bytes:
    """Decrypt an attachment ref addressed to ``recipient_identity``."""
    manifest = manifest_store.get(ref.manifest_hash)
    if manifest is None:
        raise MessageError(f"unknown attachment manifest {ref.manifest_hash.hex()[:12]}")
    if sources:
        # Replicate missing chunks if needed.
        import asyncio
        from rnet.storage.replication import Replicator
        from rnet.storage.cas import verify_manifest
        if not verify_manifest(manifest, content_store):
            asyncio.run(Replicator(content_store).fetch_manifest(manifest, sources))
    ciphertext = assemble(manifest, content_store)
    sealed = recipient_identity.decrypt(ref.wrapped)
    if sealed is None or len(sealed) != AES_KEY_SIZE + NONCE_SIZE:
        raise MessageError("could not unwrap attachment key (not for us?)")
    aes_key, nonce = sealed[:AES_KEY_SIZE], sealed[AES_KEY_SIZE:]
    aead = AESGCM(aes_key)
    try:
        return aead.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise MessageError(f"attachment decrypt failed: {exc}") from exc