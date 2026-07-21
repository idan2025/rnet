"""RHTTP server: serve a local directory over RHTTP, signed by the host.

``rnet-host ./website`` maps URL paths to files under a root directory. Small
files are returned inline (content_hash = SHA-256 of the body); large files
are content-addressed into CAS and the response references the manifest hash,
so the client can fetch chunks from any storage peer. Every response is
signed by the host identity so a client can verify it against the resolved
``.rns`` name.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from typing import Optional

import RNS

from rnet.storage.cas import (
    ContentStore,
    ManifestStore,
    build_manifest,
    hash_data,
)
from rnet.web.protocol import (
    BAD_REQUEST,
    FORBIDDEN,
    INLINE_BODY_MAX,
    META,
    NOT_FOUND,
    OK,
    RANGE_NOT_SATISFIABLE,
    RHTTPResponse,
    RHTTPRequest,
    response_for_bytes,
)

log = logging.getLogger(__name__)

# Index files tried when a directory path is requested.
INDEX_FILES = ("index.html", "index.htm")


class RHTTPServer:
    """File-backed RHTTP server. Testable via :meth:`handle_request`."""

    def __init__(self, root: str, host_identity: RNS.Identity,
                 content_store: ContentStore, manifest_store: ManifestStore,
                 inline_max: int = INLINE_BODY_MAX):
        self.root = os.path.realpath(root)
        self.identity = host_identity
        self.store = content_store
        self.manifests = manifest_store
        self.inline_max = inline_max

    # -- path resolution --------------------------------------------------
    def _resolve_path(self, url_path: str) -> Optional[str]:
        # Normalize, strip query, prevent traversal.
        clean = url_path.split("?", 1)[0]
        if not clean.startswith("/"):
            clean = "/" + clean
        rel = clean.lstrip("/")
        target = os.path.realpath(os.path.join(self.root, rel))
        # Ensure target is within root.
        if not (target == self.root or target.startswith(self.root + os.sep)):
            return None
        if os.path.isdir(target):
            for idx in INDEX_FILES:
                cand = os.path.join(target, idx)
                if os.path.isfile(cand):
                    return cand
            return None
        if os.path.isfile(target):
            return target
        return None

    # -- request handling -------------------------------------------------
    def handle_request(self, req: RHTTPRequest) -> RHTTPResponse:
        if req.method not in ("GET", "POST", META):
            return self._error(BAD_REQUEST, "unsupported method")
        fs_path = self._resolve_path(req.path)
        if fs_path is None:
            return self._error(NOT_FOUND, "not found")

        try:
            with open(fs_path, "rb") as f:
                data = f.read()
        except OSError:
            return self._error(NOT_FOUND, "unreadable")

        ctype = mimetypes.guess_type(fs_path)[0] or "application/octet-stream"
        size = len(data)

        # Range / resume: return the requested byte slice inline.
        if req.range:
            start, end = req.range
            if start < 0 or start >= size or end >= size or start > end:
                return self._error(RANGE_NOT_SATISFIABLE, "bad range")
            slice_ = data[start:end + 1]
            resp = response_for_bytes(slice_, ctype=ctype, headers={"Content-Range": f"{start}-{end}/{size}"})
            resp.sign(self.identity)
            return resp

        if req.method == META:
            # Metadata only: no body, just hash + size + headers.
            resp = RHTTPResponse(
                status=OK,
                headers={"Content-Type": ctype, "Content-Length": str(size)},
                content_hash=hash_data(data),
                size=size,
                body=b"",
            )
            resp.sign(self.identity)
            return resp

        # GET / POST body handling.
        if size <= self.inline_max:
            resp = response_for_bytes(data, ctype=ctype)
        else:
            # Content-address large files: build/fetch manifest, reference it.
            manifest = build_manifest(data, self.store, name=os.path.basename(fs_path),
                                      ctype=ctype)
            mhash = self.manifests.put(manifest)
            resp = RHTTPResponse(
                status=OK,
                headers={"Content-Type": ctype, "Content-Length": str(size),
                         "X-CAS-Manifest": mhash.hex()},
                content_hash=mhash,
                size=size,
                body=b"",
                manifest=manifest.to_bytes(),
            )
        resp.sign(self.identity)
        return resp

    def _error(self, status: int, message: str) -> RHTTPResponse:
        body = message.encode("utf-8")
        resp = RHTTPResponse(
            status=status,
            headers={"Content-Type": "text/plain"},
            content_hash=hash_data(body),
            size=len(body),
            body=body,
        )
        resp.sign(self.identity)
        return resp

    # -- live RNS wiring --------------------------------------------------
    def request_handler(self):
        """Return a callback for ``Destination.register_request_handler``."""
        server = self

        def generator(path, data, request_id, remote_identity, requested_at):
            try:
                req = RHTTPRequest.from_bytes(bytes(data))
                resp = server.handle_request(req)
                return resp.to_bytes()
            except Exception:  # pragma: no cover
                log.exception("RHTTP request handler crashed")
                return server._error(500, "internal error").to_bytes()

        return generator