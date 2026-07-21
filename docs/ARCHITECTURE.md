# RNet Architecture

## 1. Goals and constraints

RNet is a decentralized application and content layer built **on top of**
Reticulum (RNS). RNS already provides: cryptographic identities, single-hop
and multi-hop routing over heterogeneous interfaces, link-level encryption,
announce-based destination discovery, link establishment with ECDH, request/
response semantics over links, and large-resource transfer with chunking.

RNet does **not** reimplement transport. It composes RNS primitives into:

- a node lifecycle and capability model,
- a naming system (`.rns`),
- a web protocol (`RHTTP`),
- content-addressed replicated storage,
- a distributed search index,
- messaging and a social layer,
- an application SDK.

Hard constraints inherited from the target links (LoRa, packet radio,
satellite, offline mesh):

- **Low bandwidth.** Announce `app_data` is tiny; payloads are compressed and
  chunked.
- **High latency.** Synchronous request/response is avoided where possible;
  store-and-forward is the default for messaging.
- **Intermittent connectivity.** Peers vanish; content and messages must
  survive via replication and queued delivery.
- **Delay tolerance.** Operations are async with explicit timeouts and
  retransmission, never blocking the event loop.
- **Hostile nodes.** Every payload is signed; identities are verified;
  announces carry proofs; replay and spam are mitigated.

## 2. Layering

```
┌──────────────────────────────────────────────────────────┐
│  Applications: browser, CLI, social, forums, markets      │  (Phase 3-4)
├──────────────────────────────────────────────────────────┤
│  Services: web (RHTTP), search, storage, messaging, social│  (Phase 2-3)
├──────────────────────────────────────────────────────────┤
│  RNet core: node, identity, discovery, protocol, events   │  (Phase 1)
├──────────────────────────────────────────────────────────┤
│  Reticulum: identities, destinations, links, announces,   │
│             routing, resource transfer                    │
├──────────────────────────────────────────────────────────┤
│  Interfaces: LoRa, radio, Wi-Fi, Ethernet, serial, sat    │
└──────────────────────────────────────────────────────────┘
```

Each RNet layer is a set of `rnet.*` Python packages. Services are modular
and optional — a node advertises only the capabilities it runs.

## 3. Node model

A **node** is a running RNet process anchored to a single persistent
`RNS.Identity` (the *node identity*). A node:

1. Initializes `RNS.Reticulum` against a config dir.
2. Loads or creates its node identity (saved to disk, encrypted at rest by
   RNS' own keyfile handling).
3. Creates one **node destination** (named aspect) used for presence and
   capability advertisement, and announces it with a compact capability blob
   in `app_data`.
4. Mounts zero or more **service destinations** for the capabilities it
   offers (e.g. a messaging mailbox, an RHTTP server, a storage provider).
5. Maintains a local **peer registry** (SQLite) of discovered nodes and their
   advertised capabilities, updated from announce `app_data`.

### Node identity vs user identities

- The **node identity** identifies the host process and carries its
  capabilities. Its destination hash is the node's stable address.
- **User identities** (e.g. `alice.rns`) are separate cryptographic
  identities owned by a human or service. A node may host multiple user
  identities and publish names that resolve to them (Phase 2).

This separation keeps a node reachable even when no user is "logged in", and
lets a single node host many names.

## 4. Identity model

Every actor — node, user, service — is an `RNS.Identity`. RNet adds a
**profile** on top:

```
Profile {
  version: 1,
  name: "alice",
  display_name: "Alice",
  capabilities: [messaging, storage, ...],
  bio: "...",               # optional, free text
  avatar_hash: "...",       # optional, content-addressed (Phase 2)
  created: <unix ts>,
  node_dest: <hex dest hash>,  # where the node lives
  keys_fingerprint: <hex>,     # truncated hash of the identity pubkey
}
```

A profile is `msgpack`-encoded, then **signed** by the identity it describes.
The signature is verifiable by anyone holding the public key (which RNS
distributes via announces and link establishment). **No username exists
without a matching signature** — a name claim without a valid signature over
the profile is ignored.

## 5. Naming (`.rns`)

`example.rns` resolves to an `RNS.Identity` and a list of offered services.
Resolution is decentralized (see `docs/PROTOCOLS.md` §RNS Naming):

- **No central registrar.** Ownership is proven by signature.
- **Signed ownership records** published by the hosting node.
- **Replication** by any node that opts into the naming capability.
- **Caching with expiration** in a local SQLite table.
- **Transfers** via signed transfer records countersigned by old and new
  owners.

Names are scoped to an RNS app aspect; resolution asks the network for an
announce whose `app_data` carries the name and a signature binding it to the
identity. Resolution falls back to cached records when offline.

## 6. Storage (content-addressed)

Files are chunked and addressed by the hash of their content (BLAKE2b-256,
matching RNS' hash primitives). Chunks are replicated across volunteering
nodes. A **manifest** lists chunk hashes and is itself content-addressed.
Retrieval fetches the manifest, then pulls chunks from any peer that has them,
verifying each against its hash. This survives node disappearance: any node
holding a given chunk can serve it. (Implemented in Phase 2; the `cas.py`
foundation ships in Phase 1.)

## 7. Messaging

Direct messages use a **signed+encrypted envelope** delivered over an
established RNS link when the peer is reachable, or via **store-and-forward**
through a peer's mailbox destination when offline. The mailbox holds encrypted
blobs keyed to the recipient identity; the recipient pulls them on reconnect.
Delivery confirmations are signed receipts. Group/channel messaging extends
the envelope with a group identity and member roster (Phase 1 ships DM + S&F;
groups land later in Phase 1/Phase 2).

## 8. Security model

- **Authentication:** RNS link establishment + identity verification; RNet
  envelopes are signed by the sender identity.
- **Authorization:** capability-gated request handlers; per-service ACLs.
- **Signed content:** profiles, name records, messages, and (Phase 2) RHTTP
  responses are signed and verified.
- **Encryption:** RNS link encryption for transport; envelope-level
  public-key encryption for offline S&F blobs.
- **Replay prevention:** monotonic counters + timestamps in envelopes,
  checked against a per-sender seen-window in SQLite.
- **Spam controls:** announce rate observation, capability-gated mailboxes,
  per-sender quota in the inbox, proof-of-work option reserved.

RNet assumes hostile nodes exist on the network. Unverified identities are
never trusted for write operations; unsigned announces for naming are dropped.

## 9. Offline operation

- **Synchronization queues:** outbound messages and requests are queued in
  SQLite and replayed when connectivity returns.
- **Caching:** name records, profiles, content chunks, and RHTTP responses
  are cached locally with TTLs.
- **Store-and-forward:** messaging mailbox + storage replication.
- **Conflict resolution:** last-writer-wins on monotonic counters for
  mutable profile fields; content is immutable so storage has no conflicts.

## 10. Concurrency model

All network I/O is driven by RNS' internal threads, which call back into
RNet. RNet marshals those callbacks onto a single **asyncio event loop** via
a thread-safe queue, so service logic is async and single-threaded. Long
operations yield. SQLite is accessed from the loop thread with
`check_same_thread=False` and a connection lock. This keeps the model simple
and avoids races without giving up RNS' blocking transport internals.