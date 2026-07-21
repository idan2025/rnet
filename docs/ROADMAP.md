# RNet Implementation Roadmap

Build incrementally. Each phase is independently useful and shippable.

## Phase 1 — Core node, identity, messaging, discovery  *(done)*

**Deliverables**
- `rnet.core`: RNS abstraction, node lifecycle, asyncio event bus.
- `rnet.identity`: create/load/save identities, signed profiles, SQLite
  keystore.
- `rnet.protocol`: msgpack wire frames, signed/encrypted envelopes, capability
  advertisements, anti-replay window.
- `rnet.discovery`: announce-based peer + service discovery, peer registry.
- `rnet.messaging`: encrypted direct messages, store-and-forward mailbox,
  signed delivery receipts, outbox retry queue.
- `rnet.storage.cas`: content-addressed block foundation (hash + put/get, no
  replication yet).
- `rnet.cli`: `rnet identity`, `rnet node`, `rnet msg`, `rnet peers`.
- Tests for each module; `pytest` green.

**Done when**: two RNet nodes on a shared RNS testnet can discover each other,
exchange signed profiles, send an encrypted DM with delivery receipt, and
deliver an offline message through the mailbox on reconnect.

## Phase 2 — Naming, RHTTP, web hosting, storage network  *(done)*

- `rnet.naming`: `.rns` name records, signed ownership, replication, caching,
  transfers; `resolve_name()` API.
- `rnet.protocol.RHTTP`: `RHTTPRequest`/`RHTTPResponse`, range/resume, signed
  responses.
- `rnet.web`: `rnet-host` server — registers identity, publishes domain,
  serves files over RHTTP, replicates content into CAS.
- `rnet.storage`: chunking, manifests, replication across `storage` peers,
  peer gossip of chunk availability, verification.
- CLI: `rnet host`, `rnet share`, `rnet get`.
- Tests: hosting a static site + fetching from another node; CAS round-trip
  with a chunk held by a third node.

## Phase 3 — Browser, search, network explorer  *(done)*

- `rnet.browser`: PySide6 desktop browser — URL bar (`rhttp://...` and
  `name.rns`), bookmarks, history, cache, identity-verification indicator,
  signature display.
- `rnet.search`: crawler (discovers `web` services, fetches RHTTP pages),
  indexer (local inverted index), distributed query (fan-out `SearchQuery`
  to `search` peers, merge ranked results).
- `rnet.explorer`: network visualization — known nodes, links, services,
  latency, availability (Qt or web view backed by a local RHTTP endpoint).
- CLI: `rnet browse`, `rnet search`.
- Tests: crawl a small testnet, index, query, ranked results returned.

## Phase 4 — Application SDK, social layer, advanced services  *(done)*

- `rnet.apps`: SDK — `register_service()`, `send_message()`,
  `store_content()`, `resolve_name()`, `discover_peers()`, plus an app
  manifest + capability to publish app endpoints.
- `rnet.social`: profiles, posts, follows, communities, sharing; user-owned
  data via CAS manifests signed by the author; feeds assembled by walking
  follow graph.
- Advanced: group/channel messaging with member rosters, forums, markets as
  reference apps; hybrid AES-256-GCM body encryption for large attachments;
  proof-of-work spam control option; naming transfers; ratcheted messaging
  (RNS identity ratchets).

## Cross-cutting work (continuous)

- Hardening: fuzz the wire codec; property-test the anti-replay window;
  test against RNS' `rnsd` testnet config.
- Docs: user guide, operator guide (running a relay/storage node), app
  developer guide.
- Packaging: `pyproject.toml` entry points, wheels, optional `[gui]` extra
  for PySide6.
- Observability: node stats endpoint, per-capability counters.

## Non-goals (explicit)

- Reimplementing RNS transport, routing, or link crypto.
- A global, human-meaningful naming hierarchy with ICANN-style governance.
  `.rns` is local-first; collision resolution is by signature + replication,
  not by a central authority.
- Anonymity at the level of Tor. RNet provides identity-based
  authentication and encryption, not traffic-analysis resistance.