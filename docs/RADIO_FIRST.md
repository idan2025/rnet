# Radio-First Requirements

RNet must work on extremely constrained radio networks: **100-byte packets,
seconds-to-minutes latency, intermittent connectivity, sleeping nodes.** This
document specifies how RNet adapts. It is normative for the implementation.

## Assumptions

- Smallest MTU transports carry ~100-byte payloads after RNS framing.
- A single end-to-end link may never stay up long enough for RNS' built-in
  resource transfer to complete.
- Nodes sleep for long stretches to save power (solar, battery).
- Bandwidth varies wildly per interface on the same node (LoRa + Wi-Fi).

## Mechanisms

### 1. Packet prioritization

Every RNet frame carries a `priority` byte (`Frame.pr`, see
`docs/PROTOCOLS.md` §1):

| value | class    | examples                                   |
|-------|----------|--------------------------------------------|
| 0     | control  | receipts, acks, announces, link control    |
| 1     | normal   | DMs, profiles, small requests              |
| 2     | bulk     | CAS chunks, large transfers, search bulk   |

The core send loop dequeues highest-priority-first within a fairness budget so
a flood of bulk transfers cannot starve a chat ack on a 100-byte radio link.
Low-power nodes drop `priority=2` outbound entirely unless on a high-bandwidth
interface.

### 2. Compression

Frame payloads ≥256 bytes are zlib-compressed at the wire layer
(`compress_if_big`). Profiles and envelopes are msgpack (compact) before
compression. On 100-byte links the goal is to keep most control and DM frames
under one packet.

### 3. Message fragmentation

Two levels:

- **Tiny messages** (DM text, receipts) fit inline in a single frame.
- **Larger bodies** are fragmented at the app layer
  (`rnet.protocol.fragment`) into ~80-byte fragments, each independently
  sendable and signed, identified by a 16-byte transfer id + index/total.
  Fragments can travel different relays and arrive out of order. The receiver
  reassembles from whatever arrived and requests only the missing indices
  (resume). This works even when no single link survives the whole transfer.
- **Very large content** (files, media) is content-addressed into CAS chunks
  (Phase 2); the envelope then references a manifest, not the bytes.

### 4. Resumable transfers

Every fragment transfer has a random `transfer_id`. A receiver tracks received
indices and can issue a `FragmentResume` request listing missing indices; the
sender (or any peer holding the fragments) re-sends only those. Reassembly is
idempotent. Transfers survive link drops and node sleeps.

### 5. Store-and-forward routing

Messaging uses store-and-forward by default on constrained links
(`rnet.messaging`): outbound messages are queued in the `outbox` and delivered
when a link appears; messages to offline peers are dropped at a relay's
`mailbox` keyed to the recipient, who pulls them on reconnect. Relays do not
read bodies (encrypted to the recipient). This is the primary delivery mode
for sleeping nodes.

### 6. Bandwidth-aware content

Capabilities and apps declare a **bandwidth class**:
`low | medium | high` (`rnet.protocol.capabilities.Bandwidth`).

| class   | transport needed        | examples            |
|---------|-------------------------|---------------------|
| low     | 100-byte radio, LoRa    | chat, weather, naming, receipts |
| medium  | packet radio, slow Wi-Fi| web, search, storage, social    |
| high    | Wi-Fi/fiber             | video, large sync              |

Apps set `Body.bw`; capabilities carry default bandwidths in `CapabilitySet`.
A node advertises `CapabilityAdvertisement.max_bw` = the highest class its
transports can serve.

### 7. Adaptive content delivery

The network adapts delivery to available transport:

1. A sender learns a peer's `max_bw` from its capability advertisement and the
   observed link's effective rate (RNS link stats).
2. If the app's required bandwidth exceeds the peer's `max_bw`, the sender:
   - downgrades (e.g. serves a text-only variant, or a low-res avatar), or
   - defers until a high-bandwidth interface is available, or
   - routes through a relay that has a higher-bandwidth path to the peer.
3. For `high`-bandwidth content, the browser/host refuses to serve over a
   sub-medium link and advertises the requirement so peers don't attempt it.

Examples:

```
weather.rns    low bandwidth    -> served over LoRa, tiny frames
chat.rns       low bandwidth    -> DM + S&F, fits 100-byte packets
video.rns      high bandwidth   -> only served over Wi-Fi/fiber
```

### 8. Low-power node mode

`NodeConfig.low_power=True`:

- announce interval ×6 (fewer wake-ups),
- no background profile fetches or proactive replication,
- prefer store-and-forward; never originate `priority=2` bulk,
- advertise `low_power=1` in `CapabilityAdvertisement` so peers route to the
  mailbox instead of holding a link open,
- smaller `fragment_size` to fit the worst-case radio packet.

## Mapping to phases

- **Phase 1:** priority field + send-loop scheduling, compression, app-layer
  fragmentation + reassembly + resume request, store-and-forward messaging,
  bandwidth class in capability ads, low-power config flag.
- **Phase 2:** CAS chunking as the large-content path; adaptive delivery
  (downgrade/defer/route) in `rnet.web` and `rnet.storage`.
- **Phase 3+:** search crawler respects per-peer bandwidth; browser shows
  bandwidth-class and refused-high-bandwidth indicators.