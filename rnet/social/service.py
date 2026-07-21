"""Social service: publish posts, follow, assemble feeds, pull from peers.

User-owned data: posts are signed by the author and content-addressed, so a
user's content lives wherever it's replicated — no central database. Feeds
are assembled locally by walking the follow graph and pulling posts from
peers' social endpoints.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional

import RNS

from rnet.errors import SignatureError
from rnet.identity import IdentityManager
from rnet.identity.util import fingerprint
from rnet.social.post import Post, Follow
from rnet.social.store import FollowStore, PostStore
from rnet.storage.cas import ContentStore

log = logging.getLogger(__name__)


class PostSource:
    """Fetches a peer's recent posts (signed bytes) for ingestion."""

    async def recent(self, peer_dest_hash: str, limit: int = 50) -> List[bytes]:
        raise NotImplementedError


class FakePostSource(PostSource):
    """Serves post bytes from an in-process PostStore."""

    def __init__(self, store: PostStore, author_fp: str):
        self.store = store
        self.author_fp = author_fp

    async def recent(self, peer_dest_hash: str, limit: int = 50) -> List[bytes]:
        await asyncio.sleep(0)
        posts = self.store.list_by_author(self.author_fp, limit=limit)
        return [p.to_bytes() for p in posts]


class RNSPostSource(PostSource):
    """Fetches recent posts from a peer's ``rnet.social`` destination."""
    REQUEST_PATH = "social"

    def __init__(self, peer_dest_hash: str, timeout: float = 20.0):
        self.peer_dest_hash = peer_dest_hash
        self.timeout = timeout

    async def recent(self, peer_dest_hash: str, limit: int = 50) -> List[bytes]:
        import RNS
        loop = asyncio.get_running_loop()

        def _do() -> List[bytes]:
            ident = RNS.Identity.recall(bytes.fromhex(self.peer_dest_hash))
            if ident is None:
                return []
            dest = RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE,
                                   "rnet", "social")
            if not RNS.Transport.has_path(dest.hash):
                RNS.Transport.request_path(dest.hash)
                return []
            link = RNS.Link(dest)
            resp = link.request(self.REQUEST_PATH, data=str(limit).encode(),
                                timeout=self.timeout)
            if resp is None:
                return []
            import msgpack
            try:
                return list(msgpack.unpackb(bytes(resp), raw=False))
            except Exception:
                return []

        try:
            return await asyncio.wait_for(loop.run_in_executor(None, _do),
                                          timeout=self.timeout)
        except asyncio.TimeoutError:
            return []


class SocialService:
    """Publish posts, manage follows, assemble feeds, ingest peer posts."""

    def __init__(self, post_store: PostStore, follow_store: FollowStore,
                 idm: IdentityManager):
        self.posts = post_store
        self.follows = follow_store
        self.idm = idm

    # -- posting ----------------------------------------------------------
    def publish_post(self, author_identity: RNS.Identity, body: str,
                     reply_to: bytes = b"", attachments: Optional[List[bytes]] = None,
                     community: str = "", ts: Optional[int] = None) -> Post:
        post = Post(
            ts=int(ts if ts is not None else time.time()),
            body=body,
            reply_to=reply_to,
            attachments=list(attachments or []),
            community=community,
        )
        post.sign(author_identity)
        self.posts.put(post)
        return post

    def ingest(self, post_bytes: bytes) -> Optional[Post]:
        """Verify + store a post received from a peer. Returns the Post or None."""
        try:
            post = Post.from_bytes(post_bytes)
        except Exception:
            return None
        # Verify signature against the author's cached pubkey.
        row = self.idm.store.get_known_by_fp(bytes.fromhex(post.author))
        if not row or not row["pubkey"]:
            log.debug("cannot verify post: unknown author %s", post.author)
            return None
        try:
            post.verify_pubkey(bytes(row["pubkey"]))
        except SignatureError:
            log.warning("post signature invalid for %s", post.author)
            return None
        self.posts.put(post)
        return post

    # -- follows ----------------------------------------------------------
    def follow(self, follower_identity: RNS.Identity, followed_fp_hex: str) -> Follow:
        f = Follow(followed=followed_fp_hex, ts=int(time.time()))
        f.sign(follower_identity)
        self.follows.put(f)
        return f

    def unfollow(self, follower_fp_hex: str, followed_fp_hex: str) -> None:
        self.follows.remove(follower_fp_hex, followed_fp_hex)

    def following(self, follower_fp_hex: str) -> List[str]:
        return self.follows.following(follower_fp_hex)

    def followers(self, followed_fp_hex: str) -> List[str]:
        return self.follows.followers(followed_fp_hex)

    # -- feed -------------------------------------------------------------
    def feed(self, follower_fp_hex: str, limit: int = 100) -> List[dict]:
        """Local feed: posts from accounts this identity follows."""
        fps = self.following(follower_fp_hex)
        return self.posts.feed(fps, limit=limit)

    async def pull_feed(self, follower_fp_hex: str,
                        sources: Optional[List[PostSource]] = None,
                        limit: int = 50) -> int:
        """Pull recent posts from followed peers' sources. Returns ingested count."""
        if not sources:
            return 0
        fps = self.following(follower_fp_hex)
        n = 0
        for src in sources:
            for fp in fps:
                # Map fp -> a dest hash the source can address; sources carry
                # their own peer routing. Here we just ask each source for the
                # posts it knows about for this follow.
                blobs = await src.recent(fp, limit=limit)
                for b in blobs:
                    if await asyncio.to_thread(self.ingest, b):
                        n += 1
        return n

    def thread(self, root_hash: bytes) -> List[Post]:
        """Root post + its replies (depth-1)."""
        root = self.posts.get(root_hash)
        out = [root] if root else []
        out.extend(self.posts.replies(root_hash))
        return out


class SocialServiceEndpoint:
    """RNS endpoint serving a peer's recent posts for replication."""

    def __init__(self, service: SocialService, identity: RNS.Identity,
                 self_dest_hash: str = "", author_fp: str = ""):
        self.service = service
        self.identity = identity
        self.self_dest_hash = self_dest_hash
        self.author_fp = author_fp
        self.destination = None

    def start(self) -> str:
        import RNS
        self.destination = RNS.Destination(
            self.identity, RNS.Destination.IN, RNS.Destination.SINGLE,
            "rnet", "social",
        )
        endpoint = self

        def generator(path, data, request_id, remote_identity, requested_at):
            try:
                limit = int(bytes(data).decode() or "50")
            except Exception:
                limit = 50
            posts = endpoint.service.posts.list_by_author(endpoint.author_fp, limit=limit)
            import msgpack
            return msgpack.packb([p.to_bytes() for p in posts], use_bin_type=True)

        self.destination.register_request_handler(
            "social", response_generator=generator, allow=RNS.Destination.ALLOW_ALL
        )
        return self.destination.hash.hex()

    def stop(self) -> None:
        if self.destination is not None:
            try:
                self.destination.deregister_request_handler("social")
            except Exception:  # pragma: no cover
                pass
            self.destination = None