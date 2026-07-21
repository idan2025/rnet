"""SQLite index for posts (full post bytes live in CAS) + follows."""
from __future__ import annotations

import time
from typing import List, Optional

from rnet.db.connection import Database
from rnet.social.post import Post, Follow
from rnet.storage.cas import ContentStore


class PostStore:
    """Posts are content-addressed: the signed bytes are a CAS block keyed by
    the post hash. This table is an index for fast author/community/feed
    queries; the canonical Post is reconstructed from CAS on demand.
    """

    def __init__(self, db: Database, content_store: ContentStore):
        self.db = db
        self.cas = content_store

    def put(self, post: Post) -> bytes:
        h = self.cas.put_block(post.to_bytes())
        self.db.execute(
            """INSERT OR REPLACE INTO posts
               (hash, author, ts, body, reply_to, attachments, sig, retrieved, community)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (h, post.author, post.ts, post.body,
             post.reply_to or None,
             self._pack_attachments(post.attachments),
             post.sig, int(time.time()), post.community or None),
        )
        return h

    def _pack_attachments(self, atts):
        import msgpack
        return msgpack.packb(atts, use_bin_type=True)

    def _load(self, h: bytes) -> Optional[Post]:
        raw = self.cas.get_block(h)
        if raw is None:
            return None
        return Post.from_bytes(raw)

    def get(self, h: bytes) -> Optional[Post]:
        if self.db.query_one("SELECT 1 FROM posts WHERE hash=?", (h,)) is None:
            return None
        return self._load(h)

    def _index_rows(self, sql, params) -> List[dict]:
        return [dict(r) for r in self.db.query(sql, params)]

    def _posts_from_hashes(self, hashes: List[bytes]) -> List[Post]:
        out = []
        for h in hashes:
            p = self._load(h)
            if p is not None:
                out.append(p)
        return out

    def list_by_author(self, author_fp_hex: str, limit: int = 100) -> List[Post]:
        rows = self.db.query(
            "SELECT hash FROM posts WHERE author=? ORDER BY ts DESC LIMIT ?",
            (author_fp_hex, limit),
        )
        return self._posts_from_hashes([bytes(r["hash"]) for r in rows])

    def list_by_community(self, community: str, limit: int = 100) -> List[dict]:
        return self._index_rows(
            "SELECT hash, author, ts, body, reply_to, community FROM posts "
            "WHERE community=? ORDER BY ts DESC LIMIT ?",
            (community, limit),
        )

    def list_recent(self, limit: int = 100) -> List[dict]:
        """All posts, newest first (no community/follow filter)."""
        return self._index_rows(
            "SELECT hash, author, ts, body, reply_to, community FROM posts "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        )

    def feed(self, author_fps: List[str], limit: int = 100) -> List[dict]:
        """Lightweight feed rows (no CAS read) for display."""
        if not author_fps:
            return []
        placeholders = ",".join("?" for _ in author_fps)
        return self._index_rows(
            f"""SELECT hash, author, ts, body, reply_to, community FROM posts
                WHERE author IN ({placeholders}) ORDER BY ts DESC LIMIT ?""",
            (*author_fps, limit),
        )

    def replies(self, parent_hash: bytes) -> List[Post]:
        rows = self.db.query(
            "SELECT hash FROM posts WHERE reply_to=? ORDER BY ts ASC",
            (parent_hash,),
        )
        return self._posts_from_hashes([bytes(r["hash"]) for r in rows])


class FollowStore:
    def __init__(self, db: Database):
        self.db = db

    def put(self, follow: Follow) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO follows (follower, followed, ts, sig)
               VALUES (?,?,?,?)""",
            (follow.follower, follow.followed, follow.ts, follow.sig),
        )

    def remove(self, follower: str, followed: str) -> None:
        self.db.execute(
            "DELETE FROM follows WHERE follower=? AND followed=?",
            (follower, followed),
        )

    def following(self, follower: str) -> List[str]:
        rows = self.db.query(
            "SELECT followed FROM follows WHERE follower=?", (follower,)
        )
        return [r["followed"] for r in rows]

    def followers(self, followed: str) -> List[str]:
        rows = self.db.query(
            "SELECT follower FROM follows WHERE followed=?", (followed,)
        )
        return [r["follower"] for r in rows]

    def is_following(self, follower: str, followed: str) -> bool:
        return self.db.query_one(
            "SELECT 1 FROM follows WHERE follower=? AND followed=?",
            (follower, followed),
        ) is not None