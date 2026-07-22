"""RNet CLI: ``rnet identity``, ``rnet node``, ``rnet peers``, ``rnet msg``.

Entry point declared in ``pyproject.toml`` as ``rnet = rnet.cli:main``.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Optional

from rnet.config import NodeConfig, default_datadir
from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore, fingerprint
from rnet.messaging.store import InboxStore, OutboxStore
from rnet.protocol import Bandwidth


# -- helpers -----------------------------------------------------------------
def _db(args) -> Database:
    path = os.path.join(args.datadir, "rnet.db")
    os.makedirs(args.datadir, exist_ok=True)
    return Database(path)


def _idm(args, db: Database) -> IdentityManager:
    return IdentityManager(IdentityStore(db), os.path.join(args.datadir, "keys"))


def _parse_bandwidth(s: Optional[str]) -> int:
    if not s:
        return int(Bandwidth.LOW)
    return int(Bandwidth.parse(s))


# -- identity ----------------------------------------------------------------
def cmd_identity_create(args) -> int:
    db = _db(args)
    idm = _idm(args, db)
    ident = idm.create(args.name, is_node=args.node)
    fp = fingerprint(ident).hex()
    print(f"created identity '{args.name}'")
    print(f"  fingerprint: {fp}")
    if args.node:
        print("  role: node")
    return 0


def cmd_identity_list(args) -> int:
    db = _db(args)
    idm = _idm(args, db)
    rows = idm.list_own()
    if not rows:
        print("(no identities; run: rnet identity create <name>)")
        return 0
    print(f"{'NAME':<20} {'DEST/FINGERPRINT':<34} ROLE")
    for r in rows:
        print(f"{r['name']:<20} {r['dest_hash']:<34} {'node' if r['is_node'] else 'user'}")
    return 0


def cmd_identity_show(args) -> int:
    db = _db(args)
    idm = _idm(args, db)
    row = idm.store.get_own_by_name(args.name)
    if not row:
        print(f"no identity named '{args.name}'", file=sys.stderr)
        return 1
    print(f"name:        {row['name']}")
    print(f"fingerprint: {row['dest_hash']}")
    print(f"role:        {'node' if row['is_node'] else 'user'}")
    print(f"keyfile:     {row['keyfile']}")
    return 0


# -- node --------------------------------------------------------------------
def _load_or_create_node_identity(args, idm: IdentityManager):
    row = idm.store.get_own_by_name(args.name)
    if row:
        ident = idm.load(row["dest_hash"])
        if ident is None:
            print(f"could not load keyfile for '{args.name}'", file=sys.stderr)
            sys.exit(1)
        return ident
    return idm.create(args.name, is_node=True)


async def _run_node(args) -> int:
    from rnet.core import EventBus, Node
    from rnet.protocol.capabilities import Bandwidth as BW

    db = _db(args)
    idm = _idm(args, db)
    ident = _load_or_create_node_identity(args, idm)

    caps = [c.strip() for c in (args.capabilities or "").split(",") if c.strip()]
    if not caps:
        caps = ["messaging", "relay"]
    # Default the RNS config dir to <datadir>/reticulum so the CLI node shares
    # the same interfaces as the GUI (which writes ~/.rnet/reticulum/config).
    # Without this the CLI fell back to RNS's own ~/.reticulum and never saw
    # GUI-added interfaces like the tcp-peer -> rnsd link.
    rns_configdir = args.rns_configdir or os.path.join(args.datadir, "reticulum")
    cfg = NodeConfig(
        name=args.name,
        capabilities=caps,
        rns_configdir=rns_configdir,
        datadir=args.datadir,
        low_power=args.low_power,
        max_bandwidth=_parse_bandwidth(args.max_bandwidth),
        announce_interval=args.announce_interval,
        ratchets_path=args.ratchets_path,
    )
    # Apply the transport toggle to the RNS config before Node.start creates
    # the Reticulum instance (enable_transport is read once at init).
    if args.transport:
        from rnet.gui.rns_config import default_config_path, set_enable_transport
        set_enable_transport(default_config_path(cfg.rns_configdir), True)
    bus = EventBus()
    node = Node(cfg, ident, db, bus=bus, identity_manager=idm)

    stop_event = asyncio.Event()

    def _on_peer(event):
        print(f"[peer] {event['name']}  {event['dest']}  caps={','.join(event['caps'])}")

    def _on_msg(event):
        print(f"[msg]  from {event['sender'][:16]}…: {event['text']}")

    bus.subscribe("peer.discovered", _on_peer)
    bus.subscribe("message.received", _on_msg)

    await node.start()
    print(f"node '{cfg.name}' started  dest={node.node_dest_hash}")
    print(f"capabilities: {','.join(caps)}  low_power={cfg.low_power}  "
          f"max_bw={BW(cfg.max_bandwidth).name}")
    print("Ctrl-C to stop.\n")

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await node.stop()
    return 0


def cmd_node_start(args) -> int:
    try:
        return asyncio.run(_run_node(args))
    except KeyboardInterrupt:
        return 0


def cmd_node_peers(args) -> int:
    db = _db(args)
    from rnet.discovery import PeerRegistry
    reg = PeerRegistry(db)
    peers = reg.list_all()
    if not peers:
        print("(no peers discovered yet)")
        return 0
    print(f"{'NAME':<20} {'DEST':<34} {'CAPS':<24} LAST_SEEN")
    for p in peers:
        age = int(time.time()) - int(p["last_seen"])
        print(f"{(p['name'] or '?'):<20} {p['dest_hash']:<34} "
              f"{(p['capabilities'] or ''):<24} {age}s ago")
    return 0


# -- messaging ---------------------------------------------------------------
def cmd_msg_send(args) -> int:
    """Build + queue an encrypted DM. A running node delivers it."""
    db = _db(args)
    idm = _idm(args, db)
    # Load the sender (node) identity by name.
    row = idm.store.get_own_by_name(args.sender)
    if not row:
        print(f"no identity named '{args.sender}'; create one first", file=sys.stderr)
        return 1
    ident = idm.load(row["dest_hash"])
    if ident is None:
        print("sender keyfile unreadable", file=sys.stderr)
        return 1

    recipient = args.recipient.lower()
    # Look up recipient pubkey from known identities cache.
    known = idm.store.get_known(recipient)
    if not known or not known["pubkey"]:
        print(f"recipient {recipient} unknown; wait for its announce or run a node",
              file=sys.stderr)
        return 1
    import RNS
    recip_ident = RNS.Identity(create_keys=False)
    recip_ident.load_public_key(known["pubkey"])

    from rnet.config import NodeConfig
    from rnet.core.events import EventBus
    from rnet.messaging import Messenger, FakeTransport
    from rnet.messaging.store import InboxStore, MailboxStore, OutboxStore
    from rnet.protocol import ReplayWindow
    cfg = NodeConfig(name=args.sender, datadir=args.datadir)
    messenger = Messenger(
        cfg, ident, idm, EventBus(), InboxStore(db), OutboxStore(db),
        MailboxStore(db), ReplayWindow(db, clock_skew=0), FakeTransport(),
    )
    mid = asyncio.run(messenger.send_dm(recipient, recip_ident, args.text,
                                        bw=_parse_bandwidth(args.bandwidth)))
    print(f"queued message {mid} for {recipient}")
    print("(start the node with `rnet node start` to deliver)")
    return 0


def cmd_msg_list(args) -> int:
    db = _db(args)
    from rnet.messaging.store import InboxStore
    inbox = InboxStore(db)
    rows = inbox.list()
    if not rows:
        print("(inbox empty)")
        return 0
    for r in rows:
        from rnet.protocol import Body
        body = Body.from_bytes(bytes(r["body"]))
        flag = " " if r["read_at"] else "*"
        print(f"{flag} {r['id'][:12]}  from {r['sender'][:16]}…  {body.text}")
    return 0


# -- web / hosting (Phase 2) -------------------------------------------------
def cmd_host(args) -> int:
    """Host a directory over RHTTP. Runs a node with the `web` capability."""
    args.capabilities = "web" + (",messaging" if args.messaging else "")
    args.name = args.name or "rnet-host"
    args.low_power = False
    args.max_bandwidth = args.max_bandwidth or "medium"
    args.web_root = os.path.abspath(args.directory)
    args.inline_max = args.inline_max
    try:
        return asyncio.run(_run_node_with_web(args))
    except KeyboardInterrupt:
        return 0


async def _run_node_with_web(args):
    from rnet.core import EventBus, Node
    from rnet.protocol.capabilities import Bandwidth as BW

    db = _db(args)
    idm = _idm(args, db)
    ident = _load_or_create_node_identity(args, idm)
    caps = [c.strip() for c in (args.capabilities or "").split(",") if c.strip()]
    # Share the GUI's RNS config dir by default (see _run_node).
    rns_configdir = args.rns_configdir or os.path.join(args.datadir, "reticulum")
    cfg = NodeConfig(
        name=args.name,
        capabilities=caps,
        rns_configdir=rns_configdir,
        datadir=args.datadir,
        low_power=args.low_power,
        max_bandwidth=_parse_bandwidth(args.max_bandwidth),
        announce_interval=args.announce_interval,
        web_root=args.web_root,
        web_inline_max=args.inline_max,
    )
    bus = EventBus()
    node = Node(cfg, ident, db, bus=bus, identity_manager=idm)
    await node.start()
    print(f"hosting '{args.web_root}' as '{cfg.name}'  dest={node.node_dest_hash}")
    print(f"capabilities: {','.join(caps)}  max_bw={BW(cfg.max_bandwidth).name}")
    print("Ctrl-C to stop.\n")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await node.stop()
    return 0


def cmd_share(args) -> int:
    """Chunk a file into local CAS and print its manifest hash."""
    db = _db(args)
    from rnet.storage import ContentStore, ManifestStore, build_manifest
    store = ContentStore(db, os.path.join(args.datadir, "cas"))
    manifests = ManifestStore(db)
    with open(args.file, "rb") as f:
        data = f.read()
    m = build_manifest(data, store, name=os.path.basename(args.file),
                       chunk_size=args.chunk_size)
    h = manifests.put(m)
    print(f"shared: {args.file}")
    print(f"  manifest hash: {h.hex()}")
    print(f"  size: {len(data)} bytes  chunks: {len(m.chunks)}  chunk_size: {args.chunk_size}")
    print("others can fetch with: rnet get <manifest-hash>")
    return 0


def cmd_get(args) -> int:
    """Assemble a file from a locally-known manifest + chunks."""
    db = _db(args)
    from rnet.storage import ContentStore, ManifestStore, assemble, verify_manifest
    store = ContentStore(db, os.path.join(args.datadir, "cas"))
    manifests = ManifestStore(db)
    h = bytes.fromhex(args.hash)
    m = manifests.get(h)
    if m is None:
        print(f"manifest {args.hash} unknown locally; run a node with `storage` "
              "capability to fetch from peers", file=sys.stderr)
        return 1
    if not verify_manifest(m, store):
        print(f"not all chunks present locally; run a node with `storage` "
              "capability to fetch missing chunks from peers", file=sys.stderr)
        return 1
    data = assemble(m, store)
    out = args.out or m.name or "rnet-output"
    with open(out, "wb") as f:
        f.write(data)
    print(f"retrieved {len(data)} bytes -> {out}")
    return 0


# -- naming (Phase 2) --------------------------------------------------------
def cmd_resolve(args) -> int:
    db = _db(args)
    idm = _idm(args, db)
    from rnet.naming import NameRegistry, NamingService
    svc = NamingService(NameRegistry(db), idm)
    import asyncio as _a
    record = _a.run(svc.resolve_name(args.name, sources=[]))
    if record is None:
        print(f"name '{args.name}' not resolved (not in cache; run a node with "
              "`naming` capability to query the network)", file=sys.stderr)
        return 1
    print(f"name:    {record.name}.rns")
    print(f"owner:   {record.owner}")
    print(f"node:    {record.node}")
    print(f"seq:     {record.seq}  ttl: {record.ttl}  expires: {record.expires_at()}")
    for s in record.services:
        print(f"  service: {s.get('cap')}  dest: {s.get('dest')}")
    return 0


def cmd_name_publish(args) -> int:
    db = _db(args)
    idm = _idm(args, db)
    row = idm.store.get_own_by_name(args.owner)
    if not row:
        print(f"no identity named '{args.owner}'; create one first", file=sys.stderr)
        return 1
    ident = idm.load(row["dest_hash"])
    if ident is None:
        print("owner keyfile unreadable", file=sys.stderr)
        return 1
    from rnet.naming import NameRegistry, NamingService
    from rnet.identity import fingerprint
    svc = NamingService(NameRegistry(db), idm)
    services = []
    for cap in (args.services or "").split(","):
        cap = cap.strip()
        if cap:
            services.append({"cap": cap, "dest": args.node_dest or fingerprint(ident).hex()})
    record = svc.publish(ident, args.name, node_dest_hash=args.node_dest or "",
                         services=services, seq=args.seq, ttl=args.ttl)
    print(f"published {record.name}.rns")
    print(f"  owner: {record.owner}")
    print(f"  seq:   {record.seq}  ttl: {record.ttl}")
    print("run a node with `naming` capability to replicate it across the network")
    return 0


# -- browser + explorer (Phase 3) -------------------------------------------
def cmd_browse(args) -> int:
    """Launch the RNet browser GUI."""
    db = _db(args)
    idm = _idm(args, db)
    from rnet.browser import BrowserModel
    from rnet.naming import NameRegistry, NamingService
    from rnet.storage import ContentStore, ManifestStore
    from rnet.web import RNSWebTransport, WebClient
    naming = NamingService(NameRegistry(db), idm)
    store = ContentStore(db, os.path.join(args.datadir, "cas"))
    ms = ManifestStore(db)
    web = WebClient(RNSWebTransport(), store, ms, idm)
    model = BrowserModel(db, idm, web, naming)
    if args.url:
        # Pre-seed URL bar by navigating once after launch is GUI-driven; we
        # just print the normalized URL for reference.
        print(f"opening: {model.normalize_url(args.url)}")
    from rnet.browser.view import launch_browser
    try:
        return launch_browser(model)
    except Exception as exc:
        print(f"browser failed (need a display, or set QT_QPA_PLATFORM=offscreen): {exc}",
              file=sys.stderr)
        return 1


def cmd_search(args) -> int:
    """Search the local index (and, with a running node, the network)."""
    db = _db(args)
    from rnet.search import Indexer, SearchQuery
    idx = Indexer(db)
    res = idx.query(__import__("rnet").search.tokenize(args.query))
    if not res:
        print("(no results; run `rnet crawl` to build a local index)")
        return 0
    for r in res:
        print(f"  {r['score']:>4}  {r['url']}  —  {r['title']}")
    return 0


def cmd_crawl(args) -> int:
    """Crawl seeded URLs (or --seed) into the local search index."""
    db = _db(args)
    from rnet.search import Crawler, FakePageFetcher, Indexer
    idx = Indexer(db)
    # Real crawling needs RNS + discovered web hosts; without interfaces we
    # can only report. The crawler logic is exercised in tests.
    seeds = args.seed or []
    if not seeds:
        print("no seeds; pass --seed rhttp://name.rns/ ... (or run a node to "
              "auto-discover web hosts)", file=sys.stderr)
        return 1
    print(f"seeds: {seeds}")
    print("full network crawling requires a running node with RNS interfaces; "
          "see docs/USAGE.md")
    return 0


def cmd_gui(args) -> int:
    """Launch the RNet GUI dashboard."""
    from rnet.gui.launch import main as gui_main
    try:
        return gui_main()
    except Exception as exc:
        print(f"GUI failed (need a display, or set QT_QPA_PLATFORM=offscreen): {exc}",
              file=sys.stderr)
        return 1


def cmd_explorer(args) -> int:
    """Print a network explorer summary (text mode)."""
    db = _db(args)
    from rnet.explorer import ExplorerModel
    model = ExplorerModel(db)
    summary = model.summary()
    print(f"nodes:      {summary['nodes']}")
    print(f"reachable:  {summary['reachable']}")
    print(f"capabilities: {summary['capabilities']}")
    print()
    print(f"{'NAME':<20} {'DEST':<34} {'CAPS':<24} {'AGE':<8} RSSI HOPS")
    for n in summary["peers"]:
        age = int(time.time()) - int(n["last_seen"])
        print(f"{(n['name'] or '?'):<20} {n['dest_hash']:<34} "
              f"{(n['capabilities'] or ''):<24} {age:<8} "
              f"{n['rssi'] or '-'} {n['hops'] or '-'}")
    if args.gui:
        from rnet.explorer.view import launch_explorer
        try:
            return launch_explorer(model)
        except Exception as exc:
            print(f"explorer GUI failed (need a display): {exc}", file=sys.stderr)
            return 1
    return 0


# -- social + apps (Phase 4) -------------------------------------------------
def _social(tmp_args):
    db = _db(args=tmp_args)
    idm = _idm(tmp_args, db)
    from rnet.social import FollowStore, PostStore, SocialService
    from rnet.storage import ContentStore
    cas = ContentStore(db, os.path.join(tmp_args.datadir, "cas"))
    return db, idm, cas, SocialService(PostStore(db, cas), FollowStore(db), idm)


def cmd_social_post(args) -> int:
    db, idm, cas, svc = _social(args)
    row = idm.store.get_own_by_name(args.author)
    if not row:
        print(f"no identity named '{args.author}'", file=sys.stderr)
        return 1
    ident = idm.load(row["dest_hash"])
    from rnet.identity import fingerprint
    idm.store.upsert_known(fingerprint(ident).hex(), fingerprint(ident),
                           ident.get_public_key(), args.author, True)
    reply = bytes.fromhex(args.reply_to) if args.reply_to else b""
    text = " ".join(args.text)
    post = svc.publish_post(ident, text, reply_to=reply,
                            community=args.community or "")
    print(f"posted: {post.hash.hex()}")
    print(f"  author: {post.author}")
    return 0


def cmd_social_feed(args) -> int:
    db, idm, cas, svc = _social(args)
    # feed for the given identity (by name)
    from rnet.identity import fingerprint
    row = idm.store.get_own_by_name(args.identity)
    if not row:
        print(f"no identity named '{args.identity}'", file=sys.stderr)
        return 1
    fp = row["dest_hash"]
    feed = svc.feed(fp, limit=args.limit)
    if not feed:
        print("(feed empty; follow someone with `rnet social follow`)")
        return 0
    for p in feed:
        print(f"  {p['ts']}  {p['author'][:12]}…  {p['body']}")
    return 0


def cmd_social_follow(args) -> int:
    db, idm, cas, svc = _social(args)
    row = idm.store.get_own_by_name(args.follower)
    if not row:
        print(f"no identity named '{args.follower}'", file=sys.stderr)
        return 1
    ident = idm.load(row["dest_hash"])
    svc.follow(ident, args.followed)
    print(f"{args.follower} now follows {args.followed}")
    return 0


def cmd_forum(args) -> int:
    """Reference forum app: post / recent / thread (local)."""
    db, idm, cas, svc = _social(args)
    from rnet.apps import ForumApp
    forum = ForumApp(community_dest_hash=args.community or "", name=args.name)
    forum.sdk = type("S", (), {"db": db, "content_store": cas, "idm": idm})()
    forum.on_start()
    action = getattr(args, "forumcmd", None)
    if action == "post":
        row = idm.store.get_own_by_name(args.author)
        if not row:
            print(f"no identity named '{args.author}'", file=sys.stderr)
            return 1
        ident = idm.load(row["dest_hash"])
        from rnet.identity import fingerprint
        idm.store.upsert_known(fingerprint(ident).hex(), fingerprint(ident),
                               ident.get_public_key(), args.author, True)
        reply = bytes.fromhex(args.reply_to) if args.reply_to else b""
        text = " ".join(args.text) if args.text else ""
        if not text:
            print("post text required", file=sys.stderr)
            return 1
        post = forum.post(ident, text, reply_to=reply)
        print(f"posted: {post.hash.hex()}")
    elif action == "recent":
        for p in forum.recent(limit=args.limit):
            print(f"  {p['ts']}  {p['author'][:12]}…  {p['body']}")
    elif action == "thread":
        posts = forum.thread(bytes.fromhex(args.hash))
        for p in posts:
            indent = "  " if p.reply_to else ""
            print(f"{indent}{p.ts}  {p.author[:12]}…  {p.body}")
    return 0


# -- parser ------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rnet", description="RNet — the Reticulum internet")
    p.add_argument("--datadir", default=default_datadir(),
                   help=f"data directory (default: {default_datadir()}; $RNET_DATADIR)")
    sub = p.add_subparsers(dest="cmd", required=True)

    # identity
    idp = sub.add_parser("identity", help="manage identities")
    idsub = idp.add_subparsers(dest="idcmd", required=True)
    c = idsub.add_parser("create", help="create a new identity")
    c.add_argument("name"); c.add_argument("--node", action="store_true", help="node role")
    c.set_defaults(func=cmd_identity_create)
    l = idsub.add_parser("list", help="list owned identities")
    l.set_defaults(func=cmd_identity_list)
    s = idsub.add_parser("show", help="show one identity")
    s.add_argument("name"); s.set_defaults(func=cmd_identity_show)

    # node
    np = sub.add_parser("node", help="run / inspect a node")
    nsub = np.add_subparsers(dest="nodecmd", required=True)
    ns = nsub.add_parser("start", help="start the node (foreground)")
    ns.add_argument("--name", default="rnet-node")
    ns.add_argument("--capabilities", default="messaging,relay",
                    help="comma list: messaging,relay,web,storage,naming,search,social")
    ns.add_argument("--rns-configdir", default=None)
    ns.add_argument("--low-power", action="store_true")
    ns.add_argument("--max-bandwidth", default="medium", choices=["low", "medium", "high"])
    ns.add_argument("--announce-interval", type=float, default=120.0)
    ns.add_argument("--ratchets-path", default=None,
                    help="enable ratcheted messaging (forward secrecy); path for ratchet state")
    ns.add_argument("--transport", action="store_true",
                    help="enable RNS transport — relay/mesh hub that forwards announces "
                         "between interfaces so rnet clients peering through this node "
                         "discover each other (no separate rnsd needed)")
    ns.set_defaults(func=cmd_node_start)
    npe = nsub.add_parser("peers", help="list discovered peers")
    npe.set_defaults(func=cmd_node_peers)

    # msg
    mp = sub.add_parser("msg", help="messaging")
    msub = mp.add_subparsers(dest="msgcmd", required=True)
    ms = msub.add_parser("send", help="queue an encrypted DM")
    ms.add_argument("--sender", default="rnet-node", help="sender identity name")
    ms.add_argument("--bandwidth", default="low", choices=["low", "medium", "high"])
    ms.add_argument("recipient", help="recipient dest hash (hex)")
    ms.add_argument("text")
    ms.set_defaults(func=cmd_msg_send)
    ml = msub.add_parser("list", help="list inbox")
    ml.set_defaults(func=cmd_msg_list)

    # host (web)
    hp = sub.add_parser("host", help="host a directory over RHTTP")
    hp.add_argument("directory", help="directory to serve")
    hp.add_argument("--name", default=None)
    hp.add_argument("--rns-configdir", default=None)
    hp.add_argument("--messaging", action="store_true", help="also enable messaging")
    hp.add_argument("--max-bandwidth", default=None, choices=["low", "medium", "high"])
    hp.add_argument("--announce-interval", type=float, default=120.0)
    hp.add_argument("--inline-max", type=int, default=16 * 1024,
                    help="max inline RHTTP body bytes before CAS")
    hp.set_defaults(func=cmd_host)

    # storage
    sp = sub.add_parser("share", help="chunk a file into content-addressed storage")
    sp.add_argument("file")
    sp.add_argument("--chunk-size", type=int, default=1024)
    sp.set_defaults(func=cmd_share)

    gp = sub.add_parser("get", help="retrieve a file from a manifest hash")
    gp.add_argument("hash", help="manifest hash (hex)")
    gp.add_argument("--out", default=None, help="output path (default: manifest name)")
    gp.set_defaults(func=cmd_get)

    # naming
    rp = sub.add_parser("resolve", help="resolve a .rns name")
    rp.add_argument("name")
    rp.set_defaults(func=cmd_resolve)

    nm = sub.add_parser("name", help="manage .rns names")
    nmsub = nm.add_subparsers(dest="namecmd", required=True)
    nmp = nmsub.add_parser("publish", help="publish a signed name record")
    nmp.add_argument("name")
    nmp.add_argument("--owner", required=True, help="owner identity name")
    nmp.add_argument("--node-dest", default=None, help="hosting node dest hash (hex)")
    nmp.add_argument("--services", default="", help="comma list: web,storage,...")
    nmp.add_argument("--seq", type=int, default=1)
    nmp.add_argument("--ttl", type=int, default=86400)
    nmp.set_defaults(func=cmd_name_publish)

    # browser (Phase 3)
    bp = sub.add_parser("browse", help="launch the RNet browser GUI")
    bp.add_argument("url", nargs="?", default=None, help="name.rns or rhttp URL")
    bp.set_defaults(func=cmd_browse)

    # search (Phase 3)
    spq = sub.add_parser("search", help="search the local index")
    spq.add_argument("query")
    spq.set_defaults(func=cmd_search)

    crp = sub.add_parser("crawl", help="seed the crawler / build a search index")
    crp.add_argument("--seed", nargs="*", default=[], help="seed URLs")
    crp.set_defaults(func=cmd_crawl)

    # explorer (Phase 3)
    ep = sub.add_parser("explorer", help="network explorer (peers/services)")
    ep.add_argument("--gui", action="store_true", help="launch the graphical view")
    ep.set_defaults(func=cmd_explorer)

    # GUI dashboard (Phase 4+)
    gp = sub.add_parser("gui", help="launch the RNet GUI dashboard")
    gp.set_defaults(func=cmd_gui)

    # social (Phase 4)
    soc = sub.add_parser("social", help="social layer (posts/follows/feeds)")
    scsub = soc.add_subparsers(dest="socialcmd", required=True)
    sp_ = scsub.add_parser("post", help="publish a signed post")
    sp_.add_argument("--author", required=True)
    sp_.add_argument("--reply-to", default=None, help="parent post hash (hex)")
    sp_.add_argument("--community", default=None)
    sp_.add_argument("text", nargs="+", help="post body (multiple words allowed)")
    sp_.set_defaults(func=cmd_social_post)
    sf = scsub.add_parser("feed", help="show your feed")
    sf.add_argument("--identity", required=True)
    sf.add_argument("--limit", type=int, default=50)
    sf.set_defaults(func=cmd_social_feed)
    sfl = scsub.add_parser("follow", help="follow an identity")
    sfl.add_argument("--follower", required=True)
    sfl.add_argument("followed", help="identity fingerprint hex to follow")
    sfl.set_defaults(func=cmd_social_follow)

    # forum (Phase 4 reference app)
    fp_ = sub.add_parser("forum", help="reference forum app")
    fpsub = fp_.add_subparsers(dest="forumcmd", required=True)
    fp_post = fpsub.add_parser("post", help="post a thread root or reply")
    fp_post.add_argument("--name", default="forum")
    fp_post.add_argument("--community", default=None)
    fp_post.add_argument("--author", required=True)
    fp_post.add_argument("--reply-to", default=None)
    fp_post.add_argument("text", nargs="+", help="post body")
    fp_post.set_defaults(func=cmd_forum)
    fp_rec = fpsub.add_parser("recent", help="list recent posts")
    fp_rec.add_argument("--name", default="forum")
    fp_rec.add_argument("--community", default=None)
    fp_rec.add_argument("--limit", type=int, default=50)
    fp_rec.set_defaults(func=cmd_forum)
    fp_thr = fpsub.add_parser("thread", help="show a thread")
    fp_thr.add_argument("--name", default="forum")
    fp_thr.add_argument("--community", default=None)
    fp_thr.add_argument("hash", help="root post hash (hex)")
    fp_thr.set_defaults(func=cmd_forum)
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())