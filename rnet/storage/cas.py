"""Content-addressed storage: hashing, blocks, chunking, manifests.

Content addressing uses ``RNS.Identity.full_hash`` (SHA-256, 32 bytes) so
hashes are consistent with the rest of RNS. A file is split into chunks; each
chunk is stored as a block keyed by its hash. A :class:`Manifest` lists the
chunks and is itself content-addressed (the manifest hash *is* the file id).

Any node holding a chunk can serve it, so content survives node disappearance
and network fragmentation — fetch the manifest, then pull chunks from any
peer that has them, verifying each against its hash.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import RNS

from rnet.db.connection import Database
from rnet.errors import StorageError, WireError

HASH_SIZE = 32
# Default chunk payload: small enough to fit constrained radio links when
# carried as RNS resources, large enough to keep manifest sizes sane on
# higher-bandwidth transports. Tunable per transport in Phase 3 adaptive work.
DEFAULT_CHUNK_SIZE = 1024


def hash_data(data: bytes) -> bytes:
    """SHA-256 (32 bytes) of ``data`` via RNS primitives."""
    return RNS.Identity.full_hash(data)


@dataclass
class Chunk:
    hash: bytes   # 32 bytes
    offset: int
    size: int


@dataclass
class Manifest:
    """Describes a file as an ordered list of content-addressed chunks.

    The manifest hash (``manifest_hash``) is the content id users share. It is
    ``hash_data`` of the canonical msgpack of the manifest fields (excluding
    the signature), computed by :func:`Manifest.hash`.
    """

    chunks: List[Chunk] = field(default_factory=list)
    size: int = 0
    ctype: str = ""
    name: str = ""
    sig: bytes = b""  # optional publisher signature over canonical bytes

    def canonical_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {
                "chunks": [
                    {"h": c.hash, "o": c.offset, "s": c.size} for c in self.chunks
                ],
                "size": self.size,
                "ctype": self.ctype,
                "name": self.name,
            },
            use_bin_type=True,
        )

    def hash(self) -> bytes:
        return hash_data(self.canonical_bytes())

    def to_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {
                "chunks": [
                    {"h": c.hash, "o": c.offset, "s": c.size} for c in self.chunks
                ],
                "size": self.size,
                "ctype": self.ctype,
                "name": self.name,
                "sig": self.sig,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Manifest":
        import msgpack

        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad manifest: {exc}") from exc
        chunks = [
            Chunk(hash=c["h"], offset=int(c["o"]), size=int(c["s"]))
            for c in d.get("chunks", [])
        ]
        return cls(
            chunks=chunks,
            size=int(d.get("size", 0)),
            ctype=str(d.get("ctype", "")),
            name=str(d.get("name", "")),
            sig=d.get("sig", b"") or b"",
        )


class ContentStore:
    """Local content-addressed block store backed by disk + SQLite index."""

    def __init__(self, db: Database, blocks_dir: str):
        self.db = db
        self.blocks_dir = blocks_dir
        os.makedirs(blocks_dir, exist_ok=True)

    def _block_path(self, h: bytes) -> str:
        hexh = h.hex()
        # shard by first 2 hex chars to avoid huge flat dirs
        sub = os.path.join(self.blocks_dir, hexh[:2])
        os.makedirs(sub, exist_ok=True)
        return os.path.join(sub, hexh)

    def put_block(self, data: bytes, origin: Optional[str] = None) -> bytes:
        h = hash_data(data)
        if not self.has_block(h):
            path = self._block_path(h)
            with open(path, "wb") as f:
                f.write(data)
            self.db.execute(
                """INSERT OR IGNORE INTO cas_blocks
                   (hash, size, path, pinned, refcount, origin, created_at)
                   VALUES (?,?,?,?,0,?,?)""",
                (h, len(data), path, 0, origin, int(self.db.now())),
            )
        return h

    def has_block(self, h: bytes) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM cas_blocks WHERE hash=?", (h,)
        )
        return row is not None

    def get_block(self, h: bytes) -> Optional[bytes]:
        row = self.db.query_one(
            "SELECT path FROM cas_blocks WHERE hash=?", (h,)
        )
        if not row:
            return None
        try:
            with open(row["path"], "rb") as f:
                return f.read()
        except OSError:
            return None

    def pin(self, h: bytes) -> None:
        self.db.execute("UPDATE cas_blocks SET pinned=1 WHERE hash=?", (h,))

    def unpin(self, h: bytes) -> None:
        self.db.execute("UPDATE cas_blocks SET pinned=0 WHERE hash=?", (h,))

    def ref(self, h: bytes) -> None:
        self.db.execute(
            "UPDATE cas_blocks SET refcount=refcount+1 WHERE hash=?", (h,)
        )

    def unref(self, h: bytes) -> None:
        self.db.execute(
            "UPDATE cas_blocks SET refcount=refcount-1 WHERE hash=?", (h,)
        )

    def list_blocks(self) -> List[dict]:
        rows = self.db.query("SELECT * FROM cas_blocks ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM cas_blocks"
        )
        return {"blocks": int(row["n"]), "bytes": int(row["bytes"])}


class ManifestStore:
    """SQLite index of known manifests."""

    def __init__(self, db: Database):
        self.db = db

    def put(self, manifest: Manifest) -> bytes:
        h = manifest.hash()
        self.db.execute(
            """INSERT OR REPLACE INTO cas_manifests
               (hash, manifest, size, name, sig, created_at)
               VALUES (?,?,?,?,?,?)""",
            (h, manifest.to_bytes(), manifest.size, manifest.name,
             manifest.sig, int(self.db.now())),
        )
        return h

    def get(self, h: bytes) -> Optional[Manifest]:
        row = self.db.query_one(
            "SELECT manifest FROM cas_manifests WHERE hash=?", (h,)
        )
        if not row:
            return None
        return Manifest.from_bytes(bytes(row["manifest"]))

    def list(self) -> List[dict]:
        rows = self.db.query(
            "SELECT hash, name, size, created_at FROM cas_manifests ORDER BY created_at DESC"
        )
        return [
            {"hash": bytes(r["hash"]).hex(), "name": r["name"],
             "size": r["size"], "created_at": r["created_at"]}
            for r in rows
        ]


# -- chunking + manifest building -------------------------------------------
def chunk_data(data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE) -> List[bytes]:
    if chunk_size < 64:
        raise StorageError("chunk_size too small (min 64)")
    return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)] or [b""]


def build_manifest(data: bytes, store: ContentStore, name: str = "",
                   ctype: str = "", chunk_size: int = DEFAULT_CHUNK_SIZE,
                   origin: Optional[str] = None) -> Manifest:
    """Split ``data`` into chunks, store each, and return a Manifest."""
    chunks_data = chunk_data(data, chunk_size)
    chunks: List[Chunk] = []
    offset = 0
    for c in chunks_data:
        h = store.put_block(c, origin=origin)
        chunks.append(Chunk(hash=h, offset=offset, size=len(c)))
        offset += len(c)
    return Manifest(chunks=chunks, size=len(data), ctype=ctype, name=name)


def assemble(manifest: Manifest, store: ContentStore) -> bytes:
    """Reassemble file bytes from a manifest + local block store."""
    parts: List[bytes] = []
    for c in manifest.chunks:
        block = store.get_block(c.hash)
        if block is None:
            raise StorageError(f"missing chunk {c.hash.hex()}")
        if hash_data(block) != c.hash:
            raise StorageError(f"chunk hash mismatch for {c.hash.hex()}")
        parts.append(block)
    data = b"".join(parts)
    if len(data) != manifest.size:
        raise StorageError(
            f"assembled size {len(data)} != manifest size {manifest.size}"
        )
    return data


def verify_manifest(manifest: Manifest, store: ContentStore) -> bool:
    """True if every chunk is present and hashes match."""
    for c in manifest.chunks:
        block = store.get_block(c.hash)
        if block is None:
            return False
        if hash_data(block) != c.hash:
            return False
    return True