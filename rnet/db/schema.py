"""Versioned SQLite schema. Forward-only migrations."""
from __future__ import annotations

from typing import List, Tuple

from rnet.errors import SchemaError

# Current schema version. Bump on every migration.
SCHEMA_VERSION = 5

# Each migration: (version, [sql statements]). Applied in order at connect().
MIGRATIONS: List[Tuple[int, List[str]]] = [
    (
        1,
        [
            """CREATE TABLE IF NOT EXISTS identities (
                dest_hash    TEXT PRIMARY KEY,
                fingerprint  BLOB NOT NULL,
                pubkey       BLOB,
                name         TEXT,
                display      TEXT,
                profile      BLOB,
                profile_sig  BLOB,
                verified     INTEGER NOT NULL DEFAULT 0,
                first_seen   INTEGER NOT NULL,
                last_seen    INTEGER NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_id_fp ON identities(fingerprint)",

            """CREATE TABLE IF NOT EXISTS own_identities (
                dest_hash    TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                keyfile      TEXT NOT NULL,
                is_node      INTEGER NOT NULL DEFAULT 0,
                created      INTEGER NOT NULL
            )""",

            """CREATE TABLE IF NOT EXISTS peers (
                dest_hash    TEXT PRIMARY KEY,
                name         TEXT,
                capabilities TEXT,
                fingerprint  BLOB,
                last_seen    INTEGER NOT NULL,
                first_seen   INTEGER NOT NULL,
                reachable    INTEGER NOT NULL DEFAULT 1,
                rssi         INTEGER,
                hops         INTEGER
            )""",
            "CREATE INDEX IF NOT EXISTS idx_peers_caps ON peers(capabilities)",
            "CREATE INDEX IF NOT EXISTS idx_peers_seen ON peers(last_seen)",

            """CREATE TABLE IF NOT EXISTS replay_window (
                sender     TEXT NOT NULL,
                seq        INTEGER NOT NULL,
                seen_at    INTEGER NOT NULL,
                PRIMARY KEY (sender, seq)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_rw_sender ON replay_window(sender)",

            """CREATE TABLE IF NOT EXISTS inbox (
                id          TEXT PRIMARY KEY,
                sender      TEXT NOT NULL,
                recipient   TEXT NOT NULL,
                kind        INTEGER NOT NULL,
                ts          INTEGER NOT NULL,
                body        BLOB NOT NULL,
                ciphertext  BLOB,
                received_at INTEGER NOT NULL,
                read_at     INTEGER,
                signature   BLOB,
                verified    INTEGER NOT NULL DEFAULT 0
            )""",
            "CREATE INDEX IF NOT EXISTS idx_inbox_recv ON inbox(recipient, received_at)",
            "CREATE INDEX IF NOT EXISTS idx_inbox_unread ON inbox(recipient, read_at)",

            """CREATE TABLE IF NOT EXISTS outbox (
                id           TEXT PRIMARY KEY,
                recipient    TEXT NOT NULL,
                envelope     BLOB NOT NULL,
                attempts     INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 12,
                next_try     INTEGER NOT NULL,
                delivered    INTEGER NOT NULL DEFAULT 0,
                delivered_at INTEGER,
                ack_received INTEGER NOT NULL DEFAULT 0,
                created_at   INTEGER NOT NULL,
                last_error   TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS mailbox (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient   TEXT NOT NULL,
                sender      TEXT NOT NULL,
                item        BLOB NOT NULL,
                received_at INTEGER NOT NULL,
                expires_at  INTEGER NOT NULL,
                delivered   INTEGER NOT NULL DEFAULT 0
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mb_recip ON mailbox(recipient, delivered, expires_at)",

            """CREATE TABLE IF NOT EXISTS cache (
                key        TEXT PRIMARY KEY,
                value      BLOB NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                kind       TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cache_exp ON cache(expires_at)",

            """CREATE TABLE IF NOT EXISTS cas_blocks (
                hash       BLOB PRIMARY KEY,
                size       INTEGER NOT NULL,
                path       TEXT NOT NULL,
                pinned     INTEGER NOT NULL DEFAULT 0,
                refcount   INTEGER NOT NULL DEFAULT 0,
                origin     TEXT,
                created_at INTEGER NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cas_pin ON cas_blocks(pinned, refcount)",

            """CREATE TABLE IF NOT EXISTS cas_manifests (
                hash       BLOB PRIMARY KEY,
                manifest   BLOB NOT NULL,
                size       INTEGER NOT NULL,
                name       TEXT,
                sig        BLOB,
                created_at INTEGER NOT NULL
            )""",
        ],
    ),
    (
        2,
        [
            # .rns name record cache (Phase 2). `services` column stores the
            # full msgpack(NameRecord) blob so the registry can reconstruct
            # every field (signature, prev, etc.) without a second column set.
            """CREATE TABLE IF NOT EXISTS name_records (
                name        TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                owner       TEXT NOT NULL,
                node        TEXT,
                services    BLOB NOT NULL,
                ttl         INTEGER NOT NULL,
                expires_at  INTEGER NOT NULL,
                sig         BLOB NOT NULL,
                cached_at   INTEGER NOT NULL,
                PRIMARY KEY (name, seq)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_nr_name ON name_records(name, expires_at)",
        ],
    ),
    (
        3,
        [
            # Distributed search index (Phase 3).
            """CREATE TABLE IF NOT EXISTS search_documents (
                doc_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                url          TEXT NOT NULL UNIQUE,
                host         TEXT NOT NULL,
                title        TEXT,
                fetched      INTEGER NOT NULL,
                content_hash BLOB
            )""",
            "CREATE INDEX IF NOT EXISTS idx_sd_host ON search_documents(host)",

            """CREATE TABLE IF NOT EXISTS search_terms (
                term     TEXT NOT NULL,
                doc_id   INTEGER NOT NULL,
                freq     INTEGER NOT NULL,
                PRIMARY KEY (term, doc_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_st_term ON search_terms(term)",

            # Crawl frontier + visited set.
            """CREATE TABLE IF NOT EXISTS crawl_queue (
                url       TEXT PRIMARY KEY,
                host      TEXT,
                queued_at INTEGER NOT NULL,
                priority  INTEGER NOT NULL DEFAULT 1
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cq_prio ON crawl_queue(priority, queued_at)",

            """CREATE TABLE IF NOT EXISTS crawl_seen (
                url       TEXT PRIMARY KEY,
                seen_at   INTEGER NOT NULL
            )""",
        ],
    ),
    (
        4,
        [
            # Browser history + bookmarks (Phase 3).
            """CREATE TABLE IF NOT EXISTS browser_history (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                url     TEXT NOT NULL,
                title   TEXT,
                visited INTEGER NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_bh_visited ON browser_history(visited)",

            """CREATE TABLE IF NOT EXISTS bookmarks (
                url     TEXT PRIMARY KEY,
                title   TEXT,
                added   INTEGER NOT NULL
            )""",
        ],
    ),
    (
        5,
        [
            # Social layer (Phase 4): posts cache, follows, communities.
            """CREATE TABLE IF NOT EXISTS posts (
                hash       BLOB PRIMARY KEY,        -- hash of signed post bytes
                author     TEXT NOT NULL,           -- author fingerprint hex
                ts         INTEGER NOT NULL,
                body       TEXT NOT NULL,
                reply_to   BLOB,                    -- parent post hash or NULL
                attachments BLOB,                   -- msgpack list of CAS hashes
                sig        BLOB NOT NULL,
                retrieved  INTEGER NOT NULL,
                community  TEXT                     -- community dest hash or NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author, ts)",
            "CREATE INDEX IF NOT EXISTS idx_posts_comm ON posts(community, ts)",
            "CREATE INDEX IF NOT EXISTS idx_posts_reply ON posts(reply_to)",

            """CREATE TABLE IF NOT EXISTS follows (
                follower  TEXT NOT NULL,            -- fingerprint hex
                followed  TEXT NOT NULL,
                ts        INTEGER NOT NULL,
                sig       BLOB,
                PRIMARY KEY (follower, followed)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_follows_followed ON follows(followed)",

            # Group / channel messaging (Phase 4).
            """CREATE TABLE IF NOT EXISTS groups (
                group_dest   TEXT PRIMARY KEY,      -- group identity fingerprint hex
                name         TEXT NOT NULL,
                founder      TEXT NOT NULL,
                created      INTEGER NOT NULL,
                keyfile      TEXT,                  -- path to group identity keyfile (members)
                is_member    INTEGER NOT NULL DEFAULT 1
            )""",

            """CREATE TABLE IF NOT EXISTS group_members (
                group_dest   TEXT NOT NULL,
                member       TEXT NOT NULL,         -- member fingerprint hex
                added        INTEGER NOT NULL,
                PRIMARY KEY (group_dest, member)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_gm_group ON group_members(group_dest)",

            # Application registry (Phase 4): apps this node hosts.
            """CREATE TABLE IF NOT EXISTS apps (
                app_id      TEXT PRIMARY KEY,       -- app name + version
                name        TEXT NOT NULL,
                version     TEXT NOT NULL,
                cap         TEXT NOT NULL,          -- service capability token
                dest_hash   TEXT,                   -- app service dest hash
                manifest    BLOB,                   -- msgpack(AppManifest)
                installed   INTEGER NOT NULL
            )""",
        ],
    ),
]


def migrations_to_apply(current_version: int) -> List[Tuple[int, List[str]]]:
    """Return migrations with version > current_version, in order."""
    return [(v, sql) for (v, sql) in MIGRATIONS if v > current_version]


def assert_known_version(version: int) -> None:
    """Reject versions ahead of what this build knows (avoid silent drift)."""
    if version > SCHEMA_VERSION:
        raise SchemaError(
            f"database schema version {version} is ahead of this build "
            f"(knows up to {SCHEMA_VERSION}); upgrade rnet."
        )