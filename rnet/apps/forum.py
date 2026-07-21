"""Reference forum app — threaded discussions on the SDK + social layer.

A forum is a community (a dest hash) where posts are threads: a root post +
replies (``reply_to``). Demonstrates the SDK surface: it stores posts as
signed, content-addressed CAS objects, answers peer requests via its service
destination, and uses the social feed/thread primitives.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import RNS

from rnet.apps import App, AppManifest, RNet
from rnet.social import FollowStore, PostStore, SocialService
from rnet.social.post import Post

log = logging.getLogger(__name__)


class ForumApp(App):
    """A threaded forum bound to a community dest hash."""

    def __init__(self, community_dest_hash: str = "", name: str = "forum"):
        super().__init__(AppManifest(
            name=name, version="0.1.0", cap=name,
            description="mesh forum (threaded discussions)",
            permissions=["store_content", "send_message"],
        ))
        self.community = community_dest_hash
        self.social: Optional[SocialService] = None

    # -- lifecycle --------------------------------------------------------
    def on_start(self) -> None:
        # The SDK gives us db, content store, identity manager.
        sdk: RNet = self.sdk
        self.social = SocialService(
            PostStore(sdk.db, sdk.content_store),
            FollowStore(sdk.db),
            sdk.idm,
        )

    # -- API --------------------------------------------------------------
    def post(self, author_identity: RNS.Identity, body: str,
             reply_to: bytes = b"", attachments: Optional[List[bytes]] = None,
             ts: Optional[int] = None) -> Post:
        """Create a thread root or a reply."""
        assert self.social is not None
        return self.social.publish_post(
            author_identity, body, reply_to=reply_to,
            attachments=attachments, community=self.community, ts=ts,
        )

    def recent(self, limit: int = 50) -> List[dict]:
        assert self.social is not None
        if self.community:
            return self.social.posts.list_by_community(self.community, limit=limit)
        return self.social.posts.list_recent(limit=limit)

    def thread(self, root_hash: bytes) -> List[Post]:
        assert self.social is not None
        return self.social.thread(root_hash)

    def ingest(self, post_bytes: bytes) -> Optional[Post]:
        assert self.social is not None
        return self.social.ingest(post_bytes)

    # -- RNS request handler ---------------------------------------------
    def handle_request(self, path: str, data: bytes, remote_identity=None) -> bytes:
        """Dispatch peer requests: post / recent / thread."""
        import msgpack
        if path == "post" or path == self.manifest.cap + "/post":
            p = self.ingest(data)
            return msgpack.packb({"ok": p is not None, "hash": p.hash if p else b""},
                                 use_bin_type=True)
        if path in ("recent", self.manifest.cap + "/recent"):
            try:
                limit = int(bytes(data).decode() or "50")
            except Exception:
                limit = 50
            rows = self.recent(limit=limit)
            return msgpack.packb(rows, use_bin_type=True)
        if path in ("thread", self.manifest.cap + "/thread"):
            h = bytes(data)
            posts = self.thread(h)
            return msgpack.packb([p.to_bytes() for p in posts], use_bin_type=True)
        return b""