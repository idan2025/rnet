# RNet Usage Guide

How to install and use RNet, phase by phase. Commands assume the venv is
activated (`.venv/bin/activate`) or you call the `rnet` script directly.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Requirements: Python ≥3.9, the `rns` and `msgpack` packages (installed
automatically). GUI extras (`pip install -e ".[gui]"`) add PySide6 for the
Phase 3 browser.

## Configuration

- **Data dir:** defaults to `~/.rnet`; override with `--datadir` or the
  `RNET_DATADIR` environment variable. Holds the SQLite DB (`rnet.db`),
  identity keyfiles (`keys/`), and CAS blocks (`cas/`).
- **RNS config dir:** defaults to RNS' own (`~/.reticulum`); override with
  `--rns-configdir`. RNS interface config (LoRa, radio, Wi-Fi, serial) lives
  there — see the [Reticulum docs](https://reticulum.network) for interface
  setup.

> Private keys are stored as RNS keyfiles under `keys/`, never in the
> database. Back up your data dir to keep your identities.

---

## Phase 1 — Identity, Node, Messaging

### Create an identity

```bash
rnet identity create alice --node      # create a node identity
rnet identity create bob               # create a user identity
rnet identity list                     # list owned identities
rnet identity show alice               # show one identity's fingerprint
```

The fingerprint (8-byte hex) is your stable public identifier.

### Start a node

```bash
rnet node start --name alice-node \
    --capabilities messaging,relay \
    --announce-interval 120
```

The node initializes RNS, announces its presence + capabilities on the
`rnet.node` aspect, and discovers peers. Discovered peers are printed live
as `[peer] ...`; incoming messages as `[msg] ...`. Ctrl-C stops cleanly.

Flags:
- `--capabilities` — comma list from `messaging,relay,web,storage,naming,search,social`.
- `--low-power` — sleepy/solar node: announce ~6× less often, prefer
  store-and-forward, advertise `low_power=1`.
- `--max-bandwidth low|medium|high` — highest bandwidth class this node's
  transports can serve (radio-first adaptation).
- `--rns-configdir` — RNS config to use.

### List discovered peers

```bash
rnet node peers
```

Reads the peer registry the running node maintains. (The node must have run
at least once in this data dir.)

### Send + read messages

```bash
# Queue an encrypted DM (a running node delivers it)
rnet msg send --sender alice-node <recipient-fingerprint> "hello over the mesh"

# List your inbox
rnet msg list
```

Messages are signed by the sender and encrypted to the recipient. Delivery
uses a direct RNS link when the peer is reachable, or store-and-forward via
the recipient's mailbox when offline. A signed receipt is returned on
delivery.

> The recipient's fingerprint must be in your known-identity cache (you've
> seen its announce). Run a node to populate the cache.

---

## Phase 2 — Naming, Web Hosting, Storage

### Publish a `.rns` name

A name binds a human-readable label to an identity and its services, signed
by the owner.

```bash
rnet name publish library \
    --owner library \
    --node-dest <node-dest-hash> \
    --services web \
    --seq 1 --ttl 86400
```

### Resolve a name

```bash
rnet resolve library          # or: rnet resolve library.rns
```

Resolution is cache-first. If the name isn't in the local cache, run a node
with the `naming` capability to query naming peers across the network.

### Host a website over RHTTP

```bash
rnet host ./website --name library-node \
    --capabilities web --max-bandwidth medium
```

This runs a node with the `web` capability and serves `./website/` over
RHTTP on the host's `rnet.http` destination. Small files are returned
inline; large files are content-addressed into CAS and the response carries
a manifest hash plus the manifest, so clients fetch chunks from any storage
peer. Every response is signed by the host identity.

Flags: `--messaging` (also enable messaging), `--inline-max N` (bytes;
bodies larger than N go through CAS).

### Share a file (content-addressed storage)

```bash
rnet share ./document.pdf --chunk-size 1024
# prints: manifest hash <hex>
```

The file is chunked, each chunk hashed and stored locally under `cas/`, and a
manifest is recorded. Share the **manifest hash** (the file's content id) —
anyone can retrieve it from the storage network.

### Retrieve a file

```bash
rnet get <manifest-hash> --out document.pdf
```

Assembles the file from locally-known chunks. If chunks are missing, run a
node with the `storage` capability to fetch them from storage peers (the
`Replicator` pulls chunks from any peer that has them and verifies each
against its hash).

---

## Phase 3 — Browser, Search, Network Explorer

### Browse the RNet web

```bash
rnet browse                       # open the browser
rnet browse library.rns           # open a name directly
rnet browse rhttp://library.rns/books
```

The browser (PySide6) resolves `.rns` names, fetches pages over RHTTP,
verifies each response's signature against the resolved host identity, caches
locally, and shows a green **verified** / red **unverified** indicator. URL
bar accepts bare names (`library.rns`), name + path (`library.rns/books`), or
full `rhttp://` URLs. Back/forward, history, and bookmarks are stored locally.

> A display is required. On a headless machine set
> `QT_QPA_PLATFORM=offscreen` (the underlying model is fully usable without
> the GUI — see `rnet browse`'s cache + `BrowserModel` in tests).

### Search the network

```bash
rnet search "mesh network routing"      # query the local index
rnet crawl --seed rhttp://library.rns/  # seed the crawler
```

Search is decentralized: each `search`-capable node answers from its own
inverted index. The crawler discovers `web` services from the peer registry,
fetches RHTTP pages, extracts text + links, and indexes them. Queries fan out
to search peers and merge ranked results (score = summed term frequencies,
more matched terms ranks higher). No central search company.

> Building a real index needs RNS interfaces so the crawler can reach hosts.
> Run a node with `--capabilities search,web` to both publish and index.

### Network explorer

```bash
rnet explorer          # text summary of known nodes/services/latency
rnet explorer --gui    # PySide6 graph view
```

Text mode prints each discovered peer (name, dest, capabilities, age, RSSI,
hops) plus a capability histogram. GUI mode draws peers as nodes around you,
colored by capability, with reachable/unreachable outlines and RTT labels.

### Running a search/naming/host node together

```bash
rnet node start --name hub --capabilities relay,messaging,storage,naming,search
```

The more capability nodes, the richer the network: relays carry traffic,
storage nodes pin content, naming nodes replicate `.rns` records, search
nodes answer queries.

---

## Phase 4 — App SDK, Social, Groups, Reference Apps

### Social: posts, follows, feeds

User-owned data: posts are signed by the author and content-addressed (stored
as CAS objects), so your content lives wherever it's replicated — no central
database. Feeds are assembled locally from accounts you follow.

```bash
rnet social post --author alice hello mesh world
rnet social follow --follower alice <bob-fingerprint>
rnet social feed --identity alice
```

### Reference forum app

A threaded forum built on the SDK + social layer (posts with `reply_to`):

```bash
rnet forum post --author alice --name board mesh routing question
rnet forum post --author alice --name board --reply-to <root-hash> my answer
rnet forum recent --name board
rnet forum thread --name board <root-hash>
```

### Group messaging

Shared-key group: the founder creates a group identity and invites members by
sealing the group private key to each member's identity (sent as a DM). Group
messages are encrypted to the group identity; only members can decrypt.

In code (see `rnet.messaging.group`):

```python
from rnet.messaging import GroupManager, GroupRegistry
reg = GroupRegistry(db, keys_dir)
gm = GroupManager(reg)
group = gm.create_group(founder_identity, "mesh-team")
invite = gm.invite_bytes(group, member_identity)   # send via DM
# member side:
joined = gm.accept_invite(member_identity, "mesh-team", founder_fp, invite)
env = gm.build_group_envelope(sender, group.identity, "hi team")
```

### Encrypted attachments

Hybrid AES-256-GCM: a random AES key seals the file; the key is wrapped to the
recipient identity; the ciphertext is chunked into CAS. Relays and CAS peers
see only ciphertext.

```python
from rnet.messaging import encrypt_attachment, decrypt_attachment
ref = encrypt_attachment(data, recipient_identity, content_store, manifest_store, name="file.pdf")
# send ref.manifest_hash + ref.wrapped in the message Body
out = decrypt_attachment(ref, recipient_identity, content_store, manifest_store)
```

### Application SDK

Build apps on RNet. An `App` subclasses `rnet.apps.App`, sets an
`AppManifest` (name/version/capability/permissions), and implements
`handle_request(path, data, remote_identity)`. The SDK facade (`RNet`) gives
apps `register_service`, `send_message`, `store_content`, `fetch_content`,
`resolve_name`, `discover_peers`, and `social`.

```python
from rnet.apps import App, AppManifest

class EchoApp(App):
    def __init__(self):
        super().__init__(AppManifest(name="echo", version="0.1.0", cap="echo",
                                     permissions=[]))
    def handle_request(self, path, data, remote_identity=None):
        return b"echo:" + data

# on a running node:
node.sdk.register_service(EchoApp())
```

See `rnet/apps/forum.py` for a full reference app.

### Ratcheted messaging (forward secrecy)

Enable RNS identity ratchets on the messaging link for forward secrecy:

```bash
rnet node start --name alice --capabilities messaging \
    --ratchets-path ~/.rnet/ratchets
```

(or set `NodeConfig.ratchets_path`). New ratchets are derived per link; old
keys are destroyed, so a later compromise doesn't reveal past traffic.

---

## Running a relay / storage / naming node

A node advertises whatever capabilities you give it. To help the network:

```bash
rnet node start --name relay-1 --capabilities relay,messaging,storage,naming
```

- `relay` — forwards announces/traffic between interfaces.
- `storage` — pins and serves CAS chunks for content others have shared.
- `naming` — replicates `.rns` name records.

The more capability nodes, the more resilient the network against
fragmentation and node loss.

---

## Testing

```bash
pip install -e ".[test]"
pytest -q
```

59 tests cover identity signing, wire framing + anti-replay, fragmentation,
messaging (delivery, receipts, store-and-forward, replay rejection), CAS
(chunking, manifests, replication across peers, corruption detection),
naming (publish, resolve, transfers, forgery rejection), and RHTTP (inline,
CAS, range/resume, META, signature verification).

---

## Troubleshooting

- **`no peers discovered yet`** — RNS has no interfaces configured, or no
  other RNet nodes are in range. Add an interface in your RNS config
  (AutoInterface for LAN, RNode for LoRa, etc.) and ensure another node is
  running.
- **`recipient unknown`** when sending a message — you haven't seen the
  recipient's announce yet. Run your node alongside the recipient's to
  populate the known-identity cache.
- **`could not load config file` (RNS Notice)** — normal on first run in a
  fresh RNS config dir; RNS creates a default config. Edit it to add
  interfaces and restart.
- **announce fails on a transport** — RNS needs the interface up; check
  `rnsd` / interface logs. RNet announces best-effort and retries on the
  schedule.