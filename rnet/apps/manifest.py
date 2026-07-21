"""App manifests: describe an RNet application + its service capability."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import msgpack

from rnet.errors import WireError


@dataclass
class AppManifest:
    """Describes an installable RNet app.

    ``cap`` is the service capability token the app registers (e.g. ``forum``,
    ``market``). ``permissions`` declares which SDK calls the app intends to
    use, so a user can review before hosting.
    """

    name: str = ""
    version: str = "0.1.0"
    cap: str = ""               # service capability token (also the RNS aspect)
    description: str = ""
    permissions: List[str] = field(default_factory=list)  # e.g. ["send_message","store_content"]
    author_fp: bytes = b""      # optional, app author fingerprint

    def to_bytes(self) -> bytes:
        return msgpack.packb(
            {
                "name": self.name,
                "version": self.version,
                "cap": self.cap,
                "description": self.description,
                "permissions": self.permissions,
                "author_fp": self.author_fp,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "AppManifest":
        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad app manifest: {exc}") from exc
        return cls(
            name=str(d.get("name", "")),
            version=str(d.get("version", "0.1.0")),
            cap=str(d.get("cap", "")),
            description=str(d.get("description", "")),
            permissions=list(d.get("permissions", [])),
            author_fp=d.get("author_fp", b"") or b"",
        )

    @property
    def app_id(self) -> str:
        return f"{self.name}@{self.version}"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "version": self.version, "cap": self.cap,
            "description": self.description, "permissions": list(self.permissions),
            "author_fp": self.author_fp.hex() if self.author_fp else "",
        }