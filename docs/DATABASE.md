# RNet Database Schemas

RNet uses **SQLite** per-node for all local persistent state. One database
file per node at `<datadir>/rnet.db`. Schema is managed by
`rnet/db/schema.py` with versioned migrations (`PRAGMA user_version`).

Conventions:
- All hashes stored as `BLOB` (raw bytes) or hex `TEXT`.
- Timestamps as `INTEGER` Unix seconds.
- `created_at`/`updated_at` mirrors insertion/update time.

## `schema_version`

```
PRAGMA user_version = <int>
```

Not a table; migrations bump it. `schema.py` applies forward-only migrations
on open.

## `identities` — known identities (cache)

```sql
CREATE TABLE identities (
  dest_hash    TEXT PRIMARY KEY,          -- hex 16-byte RNS dest hash
  fingerprint  BLOB NOT NULL,             -- 8-byte truncated pubkey hash
  pubkey       BLOB,                      -- full public key bytes (if known)
  name         TEXT,                      -- claimed name (from profile)
  display      TEXT,
  profile      BLOB,                      -- msgpack(SignedProfile)
  profile_sig  BLOB,
  verified     INTEGER NOT NULL DEFAULT 0,-- signature verified?
  first_seen   INTEGER NOT NULL,
  last_seen    INTEGER NOT NULL
);
CREATE INDEX idx_id_fp ON identities(fingerprint);
```

## `own_identities` — identities this node owns (private keys on disk)

```sql
CREATE TABLE own_identities (
  dest_hash    TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  keyfile      TEXT NOT NULL,             -- path to RNS Identity keyfile
  is_node      INTEGER NOT NULL DEFAULT 0,
  created      INTEGER NOT NULL
);
```

Private keys are stored as RNS keyfiles (encrypted by RNS), referenced by
path. The DB never holds private key material.

## `peers` — discovered nodes

```sql
CREATE TABLE peers (
  dest_hash    TEXT PRIMARY KEY,          -- node destination hash
  name         TEXT,
  capabilities TEXT,                      -- comma-separated tokens
  fingerprint  BLOB,
  last_seen    INTEGER NOT NULL,
  first_seen   INTEGER NOT NULL,
  reachable    INTEGER NOT NULL DEFAULT 1,
  rssi         INTEGER,
  hops         INTEGER
);
CREATE INDEX idx_peers_caps ON peers(capabilities);
CREATE INDEX idx_peers_seen ON peers(last_seen);
```

## `replay_window` — anti-replay per sender

```sql
CREATE TABLE replay_window (
  sender     TEXT NOT NULL,               -- dest hash
  seq        INTEGER NOT NULL,
  seen_at    INTEGER NOT NULL,
  PRIMARY KEY (sender, seq)
);
CREATE INDEX idx_rw_sender ON replay_window(sender);
```

A sliding window is reconstructed per sender on demand; rows older than the
window high-water minus `WINDOW_SIZE` (default 64) are pruned on write.

## `inbox` — received messages

```sql
CREATE TABLE inbox (
  id          TEXT PRIMARY KEY,           -- envelope id (hex)
  sender      TEXT NOT NULL,
  recipient   TEXT NOT NULL,              -- this node / hosted identity
  kind        INTEGER NOT NULL,           -- 0=dm,1=group,...
  ts          INTEGER NOT NULL,
  body        BLOB NOT NULL,              -- msgpack(Body), decrypted
  ciphertext  BLOB,                       -- original ciphertext (for re-spread)
  received_at INTEGER NOT NULL,
  read_at     INTEGER,
  signature   BLOB,
  verified    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_inbox_recv ON inbox(recipient, received_at);
CREATE INDEX idx_inbox_unread ON inbox(recipient, read_at);
```

## `outbox` — outbound messages (queued + retry)

```sql
CREATE TABLE outbox (
  id           TEXT PRIMARY KEY,
  recipient    TEXT NOT NULL,
  envelope     BLOB NOT NULL,             -- msgpack(SignedFrame)
  attempts     INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 12,
  next_try     INTEGER NOT NULL,
  delivered    INTEGER NOT NULL DEFAULT 0,
  delivered_at INTEGER,
  ack_received INTEGER NOT NULL DEFAULT 0,
  created_at   INTEGER NOT NULL,
  last_error   TEXT
);
CREATE INDEX idx_outbox_next ON outbox(next_try) WHERE delivered=0;
```

## `mailbox` — store-and-forward items held for hosted identities

```sql
CREATE TABLE mailbox (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  recipient   TEXT NOT NULL,              -- hosted identity dest hash
  sender      TEXT NOT NULL,
  item        BLOB NOT NULL,              -- encrypted MailboxItem
  received_at INTEGER NOT NULL,
  expires_at  INTEGER NOT NULL,           -- TTL, default 14 days
  delivered   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_mb_recip ON mailbox(recipient, delivered, expires_at);
```

## `cache` — generic TTL cache (profiles, name records, RHTTP responses)

```sql
CREATE TABLE cache (
  key        TEXT PRIMARY KEY,            -- namespace:hash
  value      BLOB NOT NULL,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  kind       TEXT                          -- 'profile','namerec','rhttp',...
);
CREATE INDEX idx_cache_exp ON cache(expires_at);
```

## `name_records` — `.rns` name cache (Phase 2)

```sql
CREATE TABLE name_records (
  name      TEXT NOT NULL,
  seq       INTEGER NOT NULL,
  owner     TEXT NOT NULL,
  node      TEXT,
  services  BLOB,                         -- msgpack
  ttl       INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  sig       BLOB NOT NULL,
  cached_at INTEGER NOT NULL,
  PRIMARY KEY (name, seq)
);
CREATE INDEX idx_nr_name ON name_records(name, expires_at);
```

## `cas_blocks` — content-addressed storage index (Phase 2)

```sql
CREATE TABLE cas_blocks (
  hash       BLOB PRIMARY KEY,            -- BLAKE2b-256
  size       INTEGER NOT NULL,
  path       TEXT NOT NULL,               -- on-disk block file
  pinned     INTEGER NOT NULL DEFAULT 0,
  refcount   INTEGER NOT NULL DEFAULT 0,
  origin     TEXT,                         -- peer dest hash
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_cas_pin ON cas_blocks(pinned, refcount);

CREATE TABLE cas_manifests (
  hash       BLOB PRIMARY KEY,
  manifest   BLOB NOT NULL,
  size       INTEGER NOT NULL,
  name       TEXT,
  sig        BLOB,
  created_at INTEGER NOT NULL
);
```

## `search_index` — inverted index (Phase 3)

```sql
CREATE TABLE search_documents (
  doc_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  url       TEXT NOT NULL,                -- rhttp://...
  host      TEXT NOT NULL,                -- identity/node
  title     TEXT,
  fetched   INTEGER NOT NULL,
  content_hash BLOB
);

CREATE TABLE search_terms (
  term      TEXT NOT NULL,
  doc_id    INTEGER NOT NULL,
  freq      INTEGER NOT NULL,
  PRIMARY KEY (term, doc_id)
);
CREATE INDEX idx_st_term ON search_terms(term);
```

## Migrations

`schema.py` keeps a list `MIGRATIONS = [(version, [sql, ...]), ...]` applied in
order on `connect()`. Unknown future versions are rejected to prevent silent
schema drift. Backups (`<datadir>/rnet.db.bak`) are taken on first open of a
new version.