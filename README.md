# RNet — The Reticulum Internet

**RNet is an alternative internet that runs entirely on top of
[Reticulum](https://reticulum.network) (RNS).** It lets you host websites, send
messages, share files, run applications, and build communities **without** DNS,
central servers, cloud providers, IP addresses, or certificate authorities.

It works over LoRa, packet radio, Wi-Fi, Ethernet, serial links, satellite
links, and offline mesh networks. It is built for high latency, low bandwidth,
intermittent connections, and sleeping nodes.

> **You don't need to be online.** RNet is offline-first. Messages wait in
> store-and-forward mailboxes until a peer reappears. Content is
> content-addressed and replicated, so it survives nodes disappearing.

---

## Table of contents

- [What you can do with RNet](#what-you-can-do-with-rnet)
- [Install](#install)
- [Your first 10 minutes](#your-first-10-minutes)
- [Host a website](#host-a-website)
- [Share and fetch files](#share-and-fetch-files)
- [Names (.rns)](#names-rns)
- [Browse, search, explore](#browse-search-explore)
- [Social posts and forums](#social-posts-and-forums)
- [Help the network: run a relay](#help-the-network-run-a-relay)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [For developers](#for-developers)
- [Documentation](#documentation)
- [License](#license)

---

## What you can do with RNet

| You want to… | Use |
|---|---|
| Talk to someone over a mesh | Encrypted direct + group messaging |
| Put up a website without a server | `rnet host` (RHTTP over RNS) |
| Share a file so anyone can grab it | `rnet share` (content-addressed storage) |
| Give your site a human name | `rnet name publish` (`.rns` naming) |
| Browse the mesh web | `rnet browse` (PySide6 browser) |
| Search the network | `rnet search` (distributed, no Google) |
| See who's around | `rnet explorer` (network map) |
| Post and follow people | `rnet social` |
| Run a threaded forum | `rnet forum` |
| Build your own app | the [App SDK](#for-developers) |

Everything is peer-to-peer. Every node can host, relay, store, and serve. Your
identity is a cryptographic keypair — no usernames, no passwords, no signup.

---

## Install

You need **Python 3.9 or newer**.

```bash
# 1. Get the code
git clone https://github.com/idan2025/rnet.git
cd rnet

# 2. Create a virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# (optional) GUI + tests
pip install -e ".[gui,test]"
```

That's it. The `rnet` command is now available.

> **Windows / macOS:** same steps. Use `python` instead of `python3` on
> Windows. The GUI needs a normal desktop; everything else is headless.

Verify it works:

```bash
rnet --help
```

---

## Use the GUI (easiest way to start)

If you prefer a desktop app over the command line, launch the dashboard:

```bash
rnet gui        # or: rnet-gui
```

One window, a sidebar of tabs, handles everything:

| Tab | What you do |
|---|---|
| **Node** | Start/stop your node, pick identity, capabilities, low-power, bandwidth; live log |
| **Identities** | Create and list your identities |
| **Messages** | Send encrypted DMs, read your inbox, see messages arrive live |
| **Peers** | Watch discovered nodes + their services |
| **Hosting** | Pick a directory and host it as a mesh website |
| **Files** | Share a file (get its manifest hash) and fetch files by hash |
| **Browser** | Browse `name.rns` sites, with verified/unverified indicator |
| **Social** | Post, follow, read your feed |
| **Forum** | Threaded discussions |
| **Explorer** | Graphical map of the network |

> **Headless / no display?** Set `QT_QPA_PLATFORM=offscreen` for import/CI
> testing. The GUI needs a real desktop to render.

Everything the GUI does, the CLI also does (`rnet --help`). They share the same
data dir (`~/.rnet` by default, or `$RNET_DATADIR`).

---

## Your first 10 minutes

This walkthrough runs **two nodes on one computer** so you can see RNet work
without any extra hardware. Open **two terminals**.

### Step 1 — Create an identity for each node

Your identity is a cryptographic keypair stored on your disk. It's your
permanent address on the network.

**Terminal A (Alice):**
```bash
export RNET_DATADIR=~/.rnet-alice
rnet identity create alice --node
```
Output:
```
created identity 'alice'
  fingerprint: 12e95047d5eb53ad
  role: node
```
Copy Alice's fingerprint (`12e95047d5eb53ad` in this example — yours differs).

**Terminal B (Bob):**
```bash
export RNET_DATADIR=~/.rnet-bob
rnet identity create bob --node
```
Copy Bob's fingerprint too.

> **What is `RNET_DATADIR`?** It's the folder where a node keeps its database,
> keys, and cached content. By default it's `~/.rnet`. Here we give Alice and
> Bob separate folders so they're independent nodes on one machine.

### Step 2 — Start both nodes

**Terminal A:**
```bash
export RNET_DATADIR=~/.rnet-alice
rnet node start --name alice --capabilities messaging,relay
```

**Terminal B:**
```bash
export RNET_DATADIR=~/.rnet-bob
rnet node start --name bob --capabilities messaging,relay
```

Each node initializes RNS and announces itself on the network. On the first
run you'll see RNS print a notice that it created a default config — that's
normal.

Wait about **30 seconds**. RNS' `AutoInterface` discovers peers on your local
network via multicast. When Alice and Bob spot each other, each terminal prints
a line like:

```
[peer] bob  <bob-dest-hash>  caps=messaging,relay
```

> **No peer showing up?** See [Troubleshooting](#troubleshooting). On a
> headless Pi with no LAN, you may need to add a `TCPInterface` between the two
> nodes.

### Step 3 — Check who you can see

Leave both nodes running. Open a **third terminal**:

```bash
export RNET_DATADIR=~/.rnet-alice
rnet node peers
```
```
NAME                 DEST                               CAPS                     AGE   RSSI HOPS
bob                  a1b2c3...                          messaging,relay          4s    -    -
```

### Step 4 — Send a message

Alice sends Bob an encrypted, signed message (use Bob's fingerprint from
Step 1):

```bash
export RNET_DATADIR=~/.rnet-alice
rnet msg send --sender alice 12e95047d5eb53ad "hello over the mesh"
```
```
queued message <id> for 12e95047d5eb53ad
```

Alice's running node delivers it over an RNS link. Bob's terminal immediately
prints:
```
[msg]  from a1b2c3…: hello over the mesh
```

Bob can also read his inbox any time:
```bash
export RNET_DATADIR=~/.rnet-bob
rnet msg list
```

**You just sent an encrypted message over a decentralized mesh.** 🎉

If Bob's node was offline, Alice's node would have queued the message and
delivered it through Bob's **mailbox** when he reconnected — that's
store-and-forward, and it needs no central server.

Press **Ctrl-C** in the node terminals to stop them cleanly.

---

## Host a website

RNet's web protocol is **RHTTP** (Reticulum Hypertext Transfer Protocol). You
point `rnet host` at a directory and it serves it to the mesh, signed by your
identity.

```bash
export RNET_DATADIR=~/.rnet-alice
mkdir -p ~/my-site
echo "<h1>Welcome to my mesh site</h1><p>Served over RNS, no cloud.</p>" > ~/my-site/index.html
echo "body { font-family: sans-serif }" > ~/my-site/style.css

rnet host ~/my-site --name library --messaging
```

Your node now:
- announces itself with the `web` capability,
- serves `index.html`, `style.css`, etc. over RHTTP,
- content-addresses large files into the storage network (so they survive
  even if your node goes down),
- signs every response with your identity so visitors can verify it.

Small files are sent inline; large files are split into content-addressed
chunks that any storage peer can serve.

To give your site a name others can remember, see [Names](#names-rns).

---

## Share and fetch files

Files are **content-addressed**: a file is split into chunks, each chunk is
hashed, and a **manifest** lists the chunks. The manifest hash *is* the file's
address. Anyone holding any chunk can serve it, so content survives node loss.

**Share a file:**
```bash
export RNET_DATADIR=~/.rnet-alice
rnet share ./report.pdf
```
```
shared: ./report.pdf
  manifest hash: 8f92jd82...
  size: 240000 bytes  chunks: 235  chunk_size: 1024
```

Send that **manifest hash** to someone however you like (a message, a forum
post, a piece of paper). They fetch it with:

```bash
export RNET_DATADIR=~/.rnet-bob
rnet get 8f92jd82... --out report.pdf
```

If Bob's node doesn't have all chunks locally, he runs a node with the
`storage` capability — the `Replicator` pulls missing chunks from any peer
that has them and verifies each against its hash.

---

## Names (.rns)

RNet has its own naming system — a decentralized replacement for DNS. A name
like `library.rns` resolves to an identity and the services it offers. No
central registrar: ownership is proven by a cryptographic signature.

**Publish a name** (binds `library.rns` to your identity, hosted on your node):
```bash
export RNET_DATADIR=~/.rnet-alice
rnet name publish library \
    --owner library \
    --node-dest $(rnet identity show library | grep fingerprint | awk '{print $2}') \
    --services web
```

**Resolve a name:**
```bash
rnet resolve library        # or: rnet resolve library.rns
```
```
name:    library.rns
owner:   12e95047d5eb53ad
node:    a1b2c3...
  service: web  dest: a1b2c3...
```

Resolution is cache-first. To resolve names from across the network, run a node
with the `naming` capability — it replicates name records so others can find
your name even when your own node is offline.

---

## Browse, search, explore

### Browser

```bash
rnet browse                     # open the browser
rnet browse library.rns         # go straight to a name
rnet browse rhttp://library.rns/books
```

The PySide6 browser resolves `.rns` names, fetches pages over RHTTP, verifies
each response's signature against the host identity, and shows a green
**verified** / red **unverified** indicator. It has a URL bar, back/forward,
history, and bookmarks, all stored locally.

> The browser needs a display. On a headless machine set
> `QT_QPA_PLATFORM=offscreen` for testing, or run on a desktop.

### Search

Search is decentralized: each `search`-capable node answers from its own
index. The crawler discovers `web` services, fetches pages, and indexes them.

```bash
rnet crawl --seed rhttp://library.rns/   # build a local index
rnet search "mesh routing"               # query local index (+ network if node runs)
```

### Network explorer

```bash
rnet explorer          # text table of known nodes, services, latency
rnet explorer --gui    # graphical map (PySide6)
```

---

## Social posts and forums

Your data is yours. Posts are **signed by you and content-addressed**, so they
live wherever they're replicated — no central database can delete them.

**Post and follow:**
```bash
rnet social post --author alice "building offline mesh tools"
rnet social follow --follower alice <bob-fingerprint>
rnet social feed --identity alice
```

**Reference forum app** (threaded discussions):
```bash
rnet forum post --author alice --name board "mesh routing question"
rnet forum post --author alice --name board --reply-to <root-hash> "my answer"
rnet forum recent --name board
rnet forum thread --name board <root-hash>
```

---

## Help the network: run a relay

The more capability nodes, the more resilient the network against
fragmentation and node loss. A relay node forwards traffic between interfaces
and helps messages and content reach peers that aren't directly connected.

```bash
rnet node start --name my-relay \
    --capabilities relay,messaging,storage,naming,search
```

- `relay` — forwards announces/traffic between interfaces.
- `messaging` — store-and-forward mailbox for offline peers.
- `storage` — pins and serves content chunks.
- `naming` — replicates `.rns` name records.
- `search` — answers distributed search queries.

A solar/battery node can add `--low-power` to announce rarely and prefer
store-and-forward.

---

## Troubleshooting

**No peers show up.**
RNS needs at least one interface to talk to peers. The default config adds an
`AutoInterface` (LAN multicast). On a machine with no LAN, edit your RNS config
(`$RNET_DATADIR/rns/config` or `~/.reticulum/config`) and add a
`TCPInterface` or an `RNodeInterface` (LoRa). See the
[Reticulum docs](https://reticulum.network/manual/interfaces.html).

**`recipient unknown` when sending a message.**
You haven't seen the recipient's announce yet. Keep both nodes running for
~30 seconds so they discover each other, then retry.

**`could not load config file` (RNS Notice) on first run.**
Normal. RNS creates a default config. Edit it to add interfaces and restart.

**GUI commands fail (`rnet browse`, `rnet explorer --gui`).**
You're headless. Either run on a desktop, or set
`QT_QPA_PLATFORM=offscreen` for import/CI testing. The text commands
(`rnet explorer`, `rnet search`, etc.) work headless.

**`announce failed` / messages don't arrive.**
RNS interface isn't up. Check the RNS config and that the interface hardware
(RNode, radio) is connected. RNet announces best-effort and retries on a
schedule.

**Two nodes on one machine can't see each other.**
Make sure each has its own `RNET_DATADIR` (so separate databases/configs) and
that the `AutoInterface` can multicast on your network. Loopback-only
environments may need a `TCPInterface` connecting the two configs.

---

## FAQ

**Do I need the regular internet?**
No. RNet runs over RNS, which runs over any interface (radio, serial, Wi-Fi
direct, Ethernet). Once installed, it never needs DNS, IPs, or cloud.

**Is it anonymous?**
RNet provides identity-based authentication and encryption, not
traffic-analysis resistance like Tor. Peers see your identity's destination
hash. Use a separate identity if you want pseudonymity.

**Can anyone read my messages?**
No. Direct messages are signed by the sender and encrypted to the recipient.
Group messages are encrypted to a shared group key only members hold.
Attachments use hybrid AES-256-GCM; relays and storage peers see only
ciphertext.

**What if my node goes offline?**
Messages to you wait in peers' store-and-forward mailboxes and arrive when you
reconnect. Content you've shared is replicated across storage peers, so it
stays reachable.

**What stops someone from stealing my name?**
Names are bound to an identity by signature. A name record is only accepted if
signed by the owner. Transfers chain to the previous owner's fingerprint.

**How is this different from IPFS?**
RNet is built for delay-tolerant, low-bandwidth radio links (LoRa, packet
radio) where IPFS' assumptions (always-on, high-bandwidth) don't hold. It adds
identity, naming, messaging, and an app layer on top of content-addressed
storage, all over RNS.

---

## For developers

### Build an app with the SDK

```python
from rnet.apps import App, AppManifest

class EchoApp(App):
    def __init__(self):
        super().__init__(AppManifest(
            name="echo", version="0.1.0", cap="echo",
            description="echo service", permissions=[],
        ))

    def handle_request(self, path, data, remote_identity=None):
        return b"echo:" + data

# on a running node:
node.sdk.register_service(EchoApp())
```

The SDK facade (`RNet`) gives apps: `register_service`, `send_message`,
`store_content`, `fetch_content`, `resolve_name`, `discover_peers`, and
`social`. See `rnet/apps/forum.py` for a full reference app (threaded forum).

### Project structure

```
rnet/
  core/        node, event bus, send queue
  identity/    identities + signed profiles
  protocol/    wire frames, envelopes, capabilities, fragmentation
  discovery/   announce-based peer/service discovery
  messaging/   DM, groups, attachments, receipts, store-and-forward
  storage/     content-addressed storage + replication
  naming/      .rns name records
  web/         RHTTP protocol + server + client
  browser/     PySide6 browser
  search/      crawler + indexer + distributed query
  explorer/    network explorer
  social/      posts, follows, feeds
  apps/        app SDK + reference forum app
  gui/         unified PySide6 dashboard (all tabs)
  cli/         the `rnet` command
  db/          SQLite schema + migrations
```

### Run the tests

```bash
pip install -e ".[test]"
pytest -q          # 96 tests, all green
```

### Read the specs

- [Usage guide](docs/USAGE.md) — every command in detail
- [GUI design plan](docs/GUI_PLAN.md) — dashboard architecture
- [Architecture](docs/ARCHITECTURE.md) — layering and design
- [Protocol specifications](docs/PROTOCOLS.md) — wire formats
- [Database schemas](docs/DATABASE.md) — SQLite tables
- [Radio-first requirements](docs/RADIO_FIRST.md) — 100-byte-packet adaptation
- [Implementation roadmap](docs/ROADMAP.md) — phases 1–4

---

## Documentation

Start with this README and [docs/USAGE.md](docs/USAGE.md). For internals, see
the docs above.

---

## License

MIT — see [LICENSE](LICENSE). RNet is independent of and not affiliated with
the Reticulum project.

## Contributing

Contributions welcome. RNet is modular by design — each subsystem
(`rnet.storage`, `rnet.web`, `rnet.social`, …) is independently useful and
testable. Open issues or pull requests on GitHub.