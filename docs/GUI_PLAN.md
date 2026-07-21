# RNet GUI — Design Plan

A unified PySide6 desktop dashboard that handles everything the CLI does, in
one window. Friendly + stable.

## Decision

Researched MeshChatX (Quad4-Software/MeshChatX): it runs the RNS stack
**in-process** in a Python backend and ships a GUI with wheel/AppImage/desktop
packaging. RNet adopts the **in-process node model** (MeshChatX-style) but
keeps a **native PySide6** front end (not a web/Electron UI) because:

- pure Python, no Node/Electron/WebEngine dependencies,
- reuses RNet's existing tested browser + explorer PySide6 widgets,
- one process, one shared asyncio loop = fewer moving parts,
- installs cleanly on a Pi / headless box.

## Architecture

- **One process.** Qt event loop on the main thread; one asyncio loop on a
  daemon background thread. The GUI controller creates the loop and passes it
  to `Node.start(loop=...)` so `node.bridge.loop` is the single shared loop for
  all async SDK calls.
- **Qt↔asyncio marshalling.** Async SDK calls are scheduled with
  `asyncio.run_coroutine_threadsafe(coro, controller.loop)`; results return via
  Qt signals on a `QObject` bridge (queued connections auto-handle
  cross-thread). Mirrors `rnet/browser/view.py`.
- **Live updates.** Subscribe to `node.bus` events (`peer.discovered`,
  `message.received`, `receipt.received`, `node.started`, `node.stopped`).
  Handlers run on the asyncio thread and emit Qt signals so the UI updates on
  the main thread.
- **In-process node + SDK.** Controller builds `NodeConfig` + loads/creates the
  node identity, constructs `Node`, `await node.start(loop=...)`, drives
  everything through `node.sdk` (the `RNet` facade). RNS singleton reuse is
  already handled in `Node.start`.
- **Tabs** (QStackedWidget + sidebar): Node, Identities, Messages, Peers,
  Hosting, Files, Browser, Social, Forum, Explorer.

## Module layout

```
rnet/gui/
  launch.py        main(); QApplication + controller + window; offscreen-safe
  controller.py    GuiController: asyncio loop, Node lifecycle, SDK handle
  bridge.py        QtSignalBridge(QObject): bus events + async results -> signals
  app.py           MainWindow: sidebar + QStackedWidget, builds tabs
  workers.py       run_async(coro) helper, offload blocking sync calls
  tabs/
    node_tab.py      start/stop, name/caps/low-power/bandwidth, live log, dest
    identity_tab.py  create/list/show identities
    messages_tab.py  send DM, inbox list, live incoming
    peers_tab.py     table from ExplorerModel.summary()
    hosting_tab.py   pick dir, start host, show dest
    files_tab.py     share/get files, CAS stats
    browser_tab.py   embed BrowserWidget (reuses BrowserModel)
    social_tab.py    post/feed/follow
    forum_tab.py     post/recent/thread (ForumApp)
    explorer_tab.py  embed ExplorerWidget (reuses ExplorerModel)
```

## Reuse

`Node`, `RNet` facade, `IdentityManager`, `InboxStore`/`OutboxStore`,
`ContentStore`/`ManifestStore`/`build_manifest`/`assemble`, `NamingService`,
`SocialService`, `ForumApp`, `BrowserModel`, `ExplorerModel` — no new logic.

## Refactor (preserves CLI)

- `rnet/browser/view.py` → extract `BrowserWidget(QWidget)` (takes
  `BrowserModel` + shared `loop`); `launch_browser()` wraps it in a QMainWindow.
- `rnet/explorer/view.py` → extract `ExplorerWidget(QWidget)` (takes
  `ExplorerModel`); `launch_explorer()` wraps it.

## CLI + packaging

- `rnet gui` subcommand → `rnet.gui.launch:main`.
- `pyproject.toml` entry point `rnet-gui = "rnet.gui.launch:main"`; `[gui]`
  extra already pulls PySide6.
- README + USAGE: new GUI section.

## Tests (headless, `QT_QPA_PLATFORM=offscreen`)

- `GuiController` start/stop with temp datadir + reused RNS singleton.
- Event→signal bridge fires on a bus emit.
- `BrowserWidget`/`ExplorerWidget` construct headless.
- `run_async` resolves. No `app.exec()` in CI.

## Verification

1. `pytest -q` — 96 + new GUI tests green.
2. `QT_QPA_PLATFORM=offscreen rnet gui` imports + constructs the window.
3. Desktop: `rnet gui` → Start → peers populate → send DM live → host →
   browse → share/get → social/forum.
4. `rnet --help` shows `gui`; `rnet-gui` launches the window.