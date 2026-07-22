"""Read/write RNS interface blocks in the Reticulum config file.

The Reticulum config is an INI-like file (RNS uses ConfigObj). Interfaces
live under an ``[interfaces]`` section, each interface a ``[[Name]]``
subsection with key = value options. This module does line-based read/insert/
remove so we don't need to pull in a full INI library and can preserve
everything else in the file untouched.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


# Maps our spec ``type`` to the RNS interface type string + the option keys
# that are meaningful for that type. Unknown options are ignored on write.
SPEC_TYPES: Dict[str, Dict[str, Any]] = {
    "AutoInterface": {"rns_type": "AutoInterface",
                      "keys": ["network", "device", "outgoing", "discovery_port"]},
    "TCPClient": {"rns_type": "TCPClientInterface",
                  "keys": ["target_host", "target_port", "interface_enabled",
                           "kiss_framing"]},
    "TCPHost": {"rns_type": "TCPServerInterface",
                "keys": ["listen_ip", "listen_port", "interface_enabled"]},
    "UDP": {"rns_type": "UDPInterface",
            "keys": ["listen_ip", "listen_port", "forward_ip", "forward_port",
                     "interface_enabled"]},
    "RNode": {"rns_type": "RNodeInterface",
              "keys": ["device", "baudrate", "frequency", "bandwidth", "txpower",
                       "spreadingfactor", "codingrate", "flow_control",
                       "interface_enabled"]},
    "SerialKISS": {"rns_type": "KISSInterface",
                  "keys": ["device", "baudrate", "preamble", "txtail",
                           "interface_enabled"]},
    "AX25KISS": {"rns_type": "AX25KISSInterface",
                 "keys": ["device", "baudrate", "callsign", "ssid", "interface_enabled"]},
    "I2P": {"rns_type": "I2PInterface",
            "keys": ["peers", "ifac_size", "interface_enabled"]},
    "RNSLocal": {"rns_type": "LocalInterface",
                 "keys": ["interface_enabled"]},
}


def default_config_path(rns_configdir: Optional[str]) -> str:
    base = rns_configdir or os.path.expanduser("~/.reticulum")
    return os.path.join(base, "config")


def _ensure_file(path: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("[reticulum]\nenable_transport = False\n\n[interfaces]\n")


def set_enable_transport(path: str, enabled: bool) -> None:
    """Set ``enable_transport`` in the ``[reticulum]`` section of the config.

    With transport on, the RNS instance forwards announces (and routes
    traffic) between its interfaces — i.e. the node acts as a mesh relay so
    rnet clients peering through it discover each other without a separate
    rnsd. Off (default) = plain client that only announces + links. Must be
    called before ``RNS.Reticulum(configdir)`` since transport is read at init.
    """
    _ensure_file(path)
    out: List[str] = []
    seen = False
    in_reticulum = False
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            s = line.strip()
            if s.startswith("[") and not s.startswith("[["):
                in_reticulum = (s == "[reticulum]")
                out.append(raw)
                continue
            if in_reticulum and s.startswith("enable_transport"):
                out.append(f"enable_transport = {'True' if enabled else 'False'}")
                seen = True
                continue
            out.append(raw)
    if not seen:
        # Insert at the top of the file (before any [interfaces] etc.) so it
        # lands inside [reticulum] when that section exists, or creates one.
        head = f"[reticulum]\nenable_transport = {'True' if enabled else 'False'}\n"
        # If [reticulum] already present but had no enable_transport line, slip
        # it right after the header; else prepend a section.
        for i, raw in enumerate(out):
            if raw.strip() == "[reticulum]":
                out.insert(i + 1, f"enable_transport = {'True' if enabled else 'False'}\n")
                break
        else:
            out.insert(0, head)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)


def read_interfaces(path: str) -> List[Dict[str, Any]]:
    """Return a list of ``{name, type, options}`` dicts for each interface."""
    _ensure_file(path)
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    in_interfaces = False
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("[") and not s.startswith("[["):
                in_interfaces = (s == "[interfaces]")
                if cur:
                    out.append(cur)
                    cur = None
                continue
            if s.startswith("[[") and s.endswith("]]"):
                if cur:
                    out.append(cur)
                name = s[2:-2].strip()
                cur = {"name": name, "type": "", "options": {}}
                in_interfaces = True
                continue
            if cur is not None and "=" in s:
                k, _, v = s.partition("=")
                k = k.strip()
                v = v.strip()
                if k == "type":
                    cur["type"] = v
                else:
                    cur["options"][k] = v
    if cur:
        out.append(cur)
    return out


def write_interface(path: str, name: str, spec: Dict[str, Any]) -> None:
    """Append (or replace) the ``[[name]]`` interface block from ``spec``.

    ``spec`` must include ``type`` (one of SPEC_TYPES keys). Other keys are
    written as options if they belong to the type's allowed keys.
    """
    spec_type = SPEC_TYPES.get(spec.get("type", ""))
    if spec_type is None:
        raise ValueError(f"unknown interface type: {spec.get('type')}")
    remove_interface(path, name)  # idempotent replace
    _ensure_file(path)
    lines = []
    lines.append(f"  [[{name}]]")
    lines.append(f"    type = {spec_type['rns_type']}")
    enabled = spec.get("interface_enabled", True)
    lines.append(f"    interface_enabled = {'True' if enabled else 'False'}")
    for k in spec_type["keys"]:
        if k in ("interface_enabled",):
            continue
        if k in spec and spec[k] not in (None, ""):
            lines.append(f"    {k} = {spec[k]}")
    lines.append("")
    # Make sure an [interfaces] section exists, then append the block.
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if "[interfaces]" not in content:
        content = content.rstrip() + "\n\n[interfaces]\n"
    content = content.rstrip() + "\n" + "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def remove_interface(path: str, name: str) -> bool:
    """Delete the ``[[name]]`` block. Returns True if something was removed."""
    if not os.path.exists(path):
        return False
    out: List[str] = []
    cur_name: Optional[str] = None
    removed = False
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            s = line.strip()
            if s.startswith("[[") and s.endswith("]]"):
                cur_name = s[2:-2].strip()
                if cur_name == name:
                    removed = True
                    continue  # drop header
            if cur_name == name:
                # drop body lines belonging to the removed interface
                if s.startswith("[") or s.startswith("[["):
                    cur_name = None
                    out.append(raw)
                continue
            out.append(raw)
    if removed:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out).rstrip() + "\n")
    return removed