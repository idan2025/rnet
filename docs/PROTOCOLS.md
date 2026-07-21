# RNet Protocol Specifications

All on-wire structures use **msgpack** with a small framing layer. Hashes are
BLAKE2b-256 (32 bytes) unless noted; hex encodings are lowercase. Timestamps
are unsigned 64-bit Unix seconds. Identity references are 16-byte RNS
destination hashes (hex) unless a full public key is required.

## 1. Wire framing (`rnet.protocol.wire`)

Every RNet payload is a **Frame**:

```
Frame = msgpack({
  "v":  uint8,        # protocol version (1)
  "t":  uint8,        # frame type (see below)
  "n":  uint64,       # monotonic sequence from sender (anti-replay)
  "ts": uint64,       # sender timestamp (seconds)
  "p":  bytes,        # payload (type-specific, may be compressed)
})
```

Frame types:

| `t` | name        | payload `p`                              |
|-----|-------------|------------------------------------------|
| 0   | PROFILE     | signed `Profile` (see §3)               |
| 1   | MESSAGE     | `Envelope` (see §4)                      |
| 2   | CAPADV      | `CapabilityAdvertisement` (see §2)       |
| 3   | RECEIPT     | `Receipt` (delivery confirmation, §5)    |
| 4   | NAME_REC    | `NameRecord` (see §6)                    |
| 5   | RHTTP_REQ   | `RHTTPRequest` (Phase 2, §7)             |
| 6   | RHTTP_RES   | `RHTTPResponse` (Phase 2, §7)            |
| 7   | CAS_MAN     | `Manifest` (Phase 2, §8)                 |
| 8   | SEARCH_Q    | `SearchQuery` (Phase 3)                  |
| 9   | SEARCH_R    | `SearchResults` (Phase 3)                |

`Frame.p` is `zlib`-compressed when its raw size exceeds 256 bytes. The
codec records compression by frame type convention: any payload ≥256 bytes is
compressed; receivers attempt zlib decompression lazily (try inflate, fall
back to raw) — this is unambiguous because compressed payloads carry a zlib
header.

### Anti-replay

Receivers keep a per-sender sliding window (default 64 slots) of seen
sequence numbers `n` in SQLite (`replay_window`). A frame is rejected if `n`
is older than the sender's stored high-water minus the window width, or has
been seen. `ts` must be within ±300s of receiver time (configurable) to
reject stale replays; nodes without a reliable clock relax this to a
monotonic bound.

## 2. CapabilityAdvertisement (`CAPADV`, `t=2`)

Carried in the RNS announce `app_data` of a node destination. Must be small
(≤ RNS `app_data` budget; RNet caps at 223 bytes after framing/signing
overhead).

```
CapabilityAdvertisement = msgpack({
  "v":   1,
  "name":  str,            # node name, <= 32 chars
  "caps": [str, ...],      # capability tokens (web,messaging,relay,...)
  "prof_sig": bytes,       # signature over the sender's Profile (binds node identity)
  "fp":   bytes(8),        # identity fingerprint (truncated hash of pubkey)
  "ts":   uint64,
})
```

The announce itself is RNS-signed (RNS proves announces against the
destination identity). `prof_sig` additionally binds the node's **profile** to
this identity so a peer can fetch+verify the full profile later. The
advertisement carries no free text — large metadata lives in the Profile,
fetched on demand.

Capability tokens (extensible):

```
web, storage, relay, messaging, naming, search, social, apps
```

## 3. Profile (`PROFILE`, `t=0`)

```
Profile = msgpack({
  "v":         1,
  "name":      str,        # <= 64 chars
  "display":   str,        # <= 128 chars, optional
  "caps":      [str],
  "bio":       str,        # <= 1024 chars, optional
  "avatar":    bytes(32),  # content hash, optional
  "created":   uint64,
  "node":      str(16hex), # node destination hash
  "fp":        bytes(8),   # identity fingerprint
})

SignedProfile = msgpack({
  "profile": bytes,        # msgpack(Profile)  -- the canonical bytes that were signed
  "sig":     bytes,        # identity.sign(profile_bytes)
  "fp":      bytes(8),
})
```

Verification: re-derive the identity fingerprint from the sender's public key
(equal to `fp`), then `identity.validate(sig, profile_bytes)`. A profile whose
`fp` does not match the announcing/linked identity is **rejected**.

## 4. Envelope (`MESSAGE`, `t=1`)

```
Envelope = msgpack({
  "v":    1,
  "from": str(16hex),       # sender dest hash
  "to":   str(16hex),       # recipient dest hash (or group hash for groups)
  "kind": uint8,            # 0=dm, 1=group, 2=channel (Phase 2)
  "id":   bytes(16),        # message id (random)
  "ts":   uint64,
  "ct":   bytes,            # ciphertext: RSA/ECIES-style sealed body
  "nonce": bytes(16),       # per-message nonce
})
```

Body encryption uses RNS `Identity.encrypt` against the recipient identity
for the content key, then AES-256-GCM for the body (Phase 1 uses RNS
`Identity.encrypt`/`decrypt` directly for the whole body — simple and
interoperable with RNS identities; a hybrid AES-GCM layer is specified in
`docs/ROADMAP.md` for larger attachments). The cleartext body:

```
Body = msgpack({
  "text":  str,             # optional
  "files": [{hash,size,name}, ...]   # optional, CAS refs (Phase 2)
  "reply": bytes(16),       # optional, message id being replied to
})
```

The envelope is **signed by the sender identity**; the signature travels in
the outer Frame as a sidecar (`Frame` plus a trailing 64-byte signature in
the transport — see `wire.py`'s `SignedFrame`). Receivers verify before
accepting into the inbox.

## 5. Receipt (`RECEIPT`, `t=3`)

```
Receipt = msgpack({
  "v":   1,
  "mid": bytes(16),         # message id being acknowledged
  "by":  str(16hex),        # acknowledging identity dest hash
  "ts":  uint64,
  "sig": bytes,             # signature over (mid || by || ts)
})
```

A delivered message triggers the recipient to send a `RECEIPT` back over the
same link (or queued to the sender's mailbox if the link is gone).

## 6. NameRecord (`NAME_REC`, `t=4`)  — Phase 2

```
NameRecord = msgpack({
  "v":     1,
  "name":  str,             # "example", without .rns
  "owner": str(16hex),      # owning identity dest hash
  "fp":    bytes(8),
  "node":  str(16hex),      # hosting node
  "services": [{cap, desthash}, ...],
  "seq":   uint64,          # monotonic, replaces older records for same name
  "ts":    uint64,
  "ttl":   uint32,          # seconds
  "prev":  bytes(8),        # fingerprint of previous owner (for transfers), optional
  "sig":   bytes,           # owner signature over the record minus sig
})
```

Resolution queries the naming app aspect for an announce carrying `NAME_REC`
for `name`. The record with the highest `seq` and a valid `sig` wins. Cached
records are honored while `now < ts + ttl`; stale caches trigger a background
re-resolve. Transfers set `prev` and require the new record to be signed by
the new owner and to reference the old owner's `fp`; a name is only
transferred when both the old record's `prev` chain and the new signature
validate.

## 7. RHTTP (`t=5`,`t=6`) — Phase 2

```
RHTTPRequest = msgpack({
  "v":    1,
  "method": str,            # GET | POST | META
  "path":  str,             # e.g. "/books"
  "query": {str: str},
  "headers": {str: str},
  "body":  bytes,           # POST body (chunked via RNS resource for large)
  "range": [uint64, uint64] # optional resume
})

RHTTPResponse = msgpack({
  "v":    1,
  "status": uint16,         # 200, 404, ...
  "headers": {str: str},
  "content_hash": bytes(32),# CAS hash of the body
  "size":  uint64,
  "body":  bytes,           # inline if small, else fetch via CAS
  "sig":  bytes,            # host identity signature over (status||content_hash||size)
})
```

`GET rhttp://library.rns/books` → resolve `library.rns` → find a node
offering `web` for that identity → establish link → send `RHTTPRequest` →
receive `RHTTPResponse` → verify `sig` against the resolved identity →
optionally fetch body chunks from CAS. Responses are cacheable by
`content_hash`; resume uses `range`.

## 8. CAS Manifest (`t=7`) — Phase 2

```
Manifest = msgpack({
  "v":    1,
  "hash": bytes(32),        # hash of the file = hash of this manifest
  "size": uint64,
  "ctype": str,
  "chunks": [{hash: bytes(32), offset: uint64, size: uint64}, ...],
  "name": str,
  "sig":  bytes,            # optional publisher signature
})
```

Chunk size defaults to RNS `RESOURCE` MDU-aligned (≈ 256 bytes payload after
framing, batched by RNS resource windows). Replication: a node advertising
`storage` MAY pin chunks it has fetched; manifests list peers known to hold
chunks (gossiped, best-effort).

## 9. Discovery protocol

RNS announces are the discovery substrate. RNet nodes:

1. Announce their node destination on app aspect `rnet.node` with `CAPADV`
   in `app_data`, at a configurable interval (default 120s + jitter).
2. Listen for announces on `rnet.node`; parse `CAPADV`; upsert the peer into
   the local registry with last-seen time and announced capabilities.
3. On demand, fetch the peer's full `Profile` over a link to verify the
   `prof_sig` in the `CAPADV`.

Service discovery = capability tokens in `CAPADV` + (Phase 2) `NAME_REC`
services lists. There is no separate "service announce"; capabilities ride
the node announce.

## 10. Message delivery protocol

Direct, peer reachable:

1. Sender resolves recipient dest hash (from registry or a `.rns` name).
2. Sender establishes an RNS link to the recipient's messaging destination.
3. Sender sends a `SignedFrame(Envelope)` via `link.request("msg", frame)`.
4. Recipient verifies signature + anti-replay, decrypts body, stores in inbox,
   returns a `RECEIPT` in the response.
5. Sender marks the queued message delivered.

Direct, peer offline (store-and-forward):

1. Sender encrypts the `Envelope` body to the recipient identity (RNS
   `Identity.encrypt`) and wraps it in a `MailboxItem` addressed to the
   recipient's dest hash.
2. Sender delivers the `MailboxItem` to the recipient's **mailbox
   destination** via any reachable relay node offering `messaging` (or
   directly if the mailbox destination on the recipient's node is reachable).
   The mailbox accepts items only for identities it hosts, signed by the
   sender.
3. Recipient reconnects, polls its mailbox, pulls encrypted items, decrypts,
   verifies, ingests, and emits `RECEIPT`s back through the relay.

Relays do not read message bodies (they are encrypted to the recipient).