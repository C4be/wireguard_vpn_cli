from __future__ import annotations

import ipaddress
import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .remote import Remote

STATE_DIR = "/etc/wireguard/vpnctl"
STATE_FILE = f"{STATE_DIR}/server.json"
WG_CONF = "/etc/wireguard/wg0.conf"
SERVER_PRIVATE = f"{STATE_DIR}/server_private.key"
SERVER_PUBLIC = f"{STATE_DIR}/server_public.key"
MANAGED_MARKER = "# Managed by vpnctl"
PEER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ServerOptions:
    endpoint: str
    listen_port: int = 51820
    ssh_port: int = 22
    network: str = "10.66.66.0/24"
    dns: str = "1.1.1.1, 8.8.8.8"
    mtu: int = 1280
    interface: str = "wg0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_server(remote: Remote, options: ServerOptions) -> dict[str, Any]:
    install_script = f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

install_packages() {{
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y wireguard wireguard-tools iptables qrencode ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y wireguard-tools iptables qrencode
  elif command -v yum >/dev/null 2>&1; then
    yum install -y wireguard-tools iptables qrencode
  else
    echo "Unsupported package manager. Install wireguard-tools, iptables and qrencode first." >&2
    exit 2
  fi
}}

command -v wg >/dev/null 2>&1 || install_packages
command -v qrencode >/dev/null 2>&1 || install_packages
command -v iptables >/dev/null 2>&1 || install_packages

mkdir -p {shlex.quote(STATE_DIR)}
chmod 0700 {shlex.quote(STATE_DIR)}
mkdir -p /etc/wireguard
chmod 0700 /etc/wireguard

if [ ! -f {shlex.quote(SERVER_PRIVATE)} ]; then
  umask 077
  wg genkey > {shlex.quote(SERVER_PRIVATE)}
  wg pubkey < {shlex.quote(SERVER_PRIVATE)} > {shlex.quote(SERVER_PUBLIC)}
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -qi '^Status: active'; then
  ufw allow {int(options.ssh_port)}/tcp >/dev/null || true
  ufw allow {int(options.listen_port)}/udp >/dev/null || true
fi

if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port={int(options.ssh_port)}/tcp >/dev/null || true
  firewall-cmd --permanent --add-port={int(options.listen_port)}/udp >/dev/null || true
  firewall-cmd --reload >/dev/null || true
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null
printf 'net.ipv4.ip_forward=1\\n' > /etc/sysctl.d/99-vpnctl-wireguard.conf
sysctl --system >/dev/null || true
"""
    remote.run_root_script(install_script)

    private_key = remote.read_root_file(SERVER_PRIVATE).strip()
    public_key = remote.read_root_file(SERVER_PUBLIC).strip()
    existing_state = load_state(remote)

    network = ipaddress.ip_network(options.network, strict=False)
    server_address = f"{network.network_address + 1}/{network.prefixlen}"
    state = existing_state or {
        "created_at": utc_now(),
        "peers": {},
    }
    if (
        existing_state
        and existing_state.get("peers")
        and existing_state.get("network") != str(network)
    ):
        raise RuntimeError(
            "Refusing to change the VPN network while users exist. "
            "Remove users first or keep the previous network."
        )
    state.update(
        {
            "version": 1,
            "interface": options.interface,
            "endpoint": options.endpoint,
            "listen_port": options.listen_port,
            "ssh_port": options.ssh_port,
            "network": str(network),
            "server_address": server_address,
            "dns": options.dns,
            "mtu": options.mtu,
            "server_public_key": public_key,
            "updated_at": utc_now(),
        }
    )
    save_state(remote, state)
    write_server_config(remote, state, private_key)
    apply_service(remote)
    return state


def load_state(remote: Remote) -> dict[str, Any] | None:
    result = remote.run_root_script(
        f"""
set -euo pipefail
if [ -f {shlex.quote(STATE_FILE)} ]; then
  cat {shlex.quote(STATE_FILE)}
fi
""",
        check=True,
    )
    data = result.strip()
    if not data:
        return None
    return json.loads(data)


def save_state(remote: Remote, state: dict[str, Any]) -> None:
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    remote.write_root_file(STATE_FILE, payload, "0600")


def add_peer(remote: Remote, name: str, email: str | None = None) -> dict[str, Any]:
    validate_peer_name(name)
    state = require_state(remote)
    peers = state.setdefault("peers", {})
    if name in peers:
        return peers[name]

    network = ipaddress.ip_network(state["network"], strict=False)
    used = {
        ipaddress.ip_interface(peer["address"]).ip
        for peer in peers.values()
        if "address" in peer
    }
    address = None
    for host in network.hosts():
        if host == ipaddress.ip_interface(state["server_address"]).ip:
            continue
        if host not in used:
            address = f"{host}/32"
            break
    if not address:
        raise RuntimeError(f"No free addresses left in {state['network']}")

    private_key = remote.run_root_script("wg genkey").strip()
    public_key = remote.run(
        "wg pubkey",
        input_data=private_key + "\n",
        check=True,
    ).stdout.decode().strip()
    peer = {
        "name": name,
        "email": email,
        "address": address,
        "private_key": private_key,
        "public_key": public_key,
        "created_at": utc_now(),
    }
    peers[name] = peer
    state["updated_at"] = utc_now()
    save_state(remote, state)
    private = remote.read_root_file(SERVER_PRIVATE).strip()
    write_server_config(remote, state, private)
    apply_service(remote)
    return peer


def remove_peer(remote: Remote, name: str) -> bool:
    validate_peer_name(name)
    state = require_state(remote)
    peers = state.setdefault("peers", {})
    if name not in peers:
        return False
    del peers[name]
    state["updated_at"] = utc_now()
    save_state(remote, state)
    private = remote.read_root_file(SERVER_PRIVATE).strip()
    write_server_config(remote, state, private)
    apply_service(remote)
    return True


def client_config(state: dict[str, Any], peer: dict[str, Any]) -> str:
    endpoint = f"{state['endpoint']}:{state['listen_port']}"
    return f"""[Interface]
PrivateKey = {peer['private_key']}
Address = {peer['address']}
DNS = {state['dns']}
MTU = {state['mtu']}

[Peer]
PublicKey = {state['server_public_key']}
Endpoint = {endpoint}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""


def export_peer(
    remote: Remote,
    name: str,
    output_dir: Path,
    *,
    with_qr: bool = False,
) -> tuple[Path, Path | None]:
    validate_peer_name(name)
    state = require_state(remote)
    peer = state.get("peers", {}).get(name)
    if not peer:
        raise KeyError(f"Unknown peer: {name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    conf_path = output_dir / f"{name}.conf"
    conf = client_config(state, peer)
    conf_path.write_text(conf, encoding="utf-8")
    qr_path = None
    if with_qr:
        qr_path = output_dir / f"{name}.png"
        qr_path.write_bytes(remote.qr_png(conf))
    return conf_path, qr_path


def list_peers(remote: Remote) -> list[dict[str, Any]]:
    state = require_state(remote)
    return sorted(state.get("peers", {}).values(), key=lambda item: item["name"])


def diagnose(remote: Remote) -> str:
    return remote.run_root_script(
        f"""
set +e
WG_PORT="51820"
if [ -f {shlex.quote(STATE_FILE)} ]; then
  DETECTED_PORT="$(sed -n 's/.*"listen_port": \\([0-9][0-9]*\\).*/\\1/p' {shlex.quote(STATE_FILE)} | head -n1)"
  [ -n "$DETECTED_PORT" ] && WG_PORT="$DETECTED_PORT"
fi
echo "== vpnctl state =="
if [ -f {shlex.quote(STATE_FILE)} ]; then
  sed -E 's/"private_key": "([^"]+)"/"private_key": "<hidden>"/g' {shlex.quote(STATE_FILE)}
else
  echo "missing {STATE_FILE}"
fi
echo
echo "== system =="
uname -a
command -v docker >/dev/null 2>&1 && docker --version || echo "docker: not installed"
echo
echo "== network =="
ip route show default
ss -lunp | grep -E "(:$WG_PORT|wireguard)" || true
echo
echo "== wireguard service =="
systemctl --no-pager --full status wg-quick@wg0 2>&1 | sed -n '1,35p'
echo
echo "== wg show =="
wg show 2>&1
echo
echo "== firewall =="
command -v ufw >/dev/null 2>&1 && ufw status verbose || true
command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --list-all || true
iptables -t nat -S | grep -E '(MASQUERADE|wg0)' || true
iptables -S FORWARD | grep wg0 || true
""",
        check=False,
    )


def restart_wireguard(remote: Remote) -> None:
    remote.run_root_script(
        """
set -euo pipefail
systemctl restart wg-quick@wg0
systemctl --no-pager --full status wg-quick@wg0 | sed -n '1,20p'
"""
    )


def reboot_server(remote: Remote) -> None:
    remote.run_root_script(
        """
set -euo pipefail
nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &
"""
    )


def require_state(remote: Remote) -> dict[str, Any]:
    state = load_state(remote)
    if not state:
        raise RuntimeError("Server is not initialized. Run setup first.")
    return state


def validate_peer_name(name: str) -> None:
    if not PEER_NAME_RE.fullmatch(name):
        raise RuntimeError(
            "User name must be 1-64 chars and contain only letters, digits, dot, "
            "underscore or dash; it must start with a letter or digit."
        )


def write_server_config(remote: Remote, state: dict[str, Any], private_key: str) -> None:
    default_interface = remote.run_text(
        "ip route show default | awk '{print $5; exit}'",
        check=False,
    ).strip() or "eth0"
    network = state["network"]
    port = int(state["listen_port"])
    interface = state.get("interface", "wg0")
    peer_blocks = []
    for peer in sorted(state.get("peers", {}).values(), key=lambda item: item["name"]):
        peer_blocks.append(
            f"""
# peer: {peer['name']}
[Peer]
PublicKey = {peer['public_key']}
AllowedIPs = {peer['address']}
"""
        )
    peer_text = "\n".join(peer_blocks).strip()
    config = f"""{MANAGED_MARKER}
[Interface]
Address = {state['server_address']}
ListenPort = {port}
PrivateKey = {private_key}
SaveConfig = false
PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -C FORWARD -i {interface} -j ACCEPT || iptables -A FORWARD -i {interface} -j ACCEPT; iptables -C FORWARD -o {interface} -j ACCEPT || iptables -A FORWARD -o {interface} -j ACCEPT; iptables -t nat -C POSTROUTING -s {network} -o {default_interface} -j MASQUERADE || iptables -t nat -A POSTROUTING -s {network} -o {default_interface} -j MASQUERADE
PostDown = iptables -D FORWARD -i {interface} -j ACCEPT || true; iptables -D FORWARD -o {interface} -j ACCEPT || true; iptables -t nat -D POSTROUTING -s {network} -o {default_interface} -j MASQUERADE || true

{peer_text}
""".rstrip() + "\n"
    backup_script = f"""
set -euo pipefail
if [ -f {shlex.quote(WG_CONF)} ] && ! grep -q {shlex.quote(MANAGED_MARKER)} {shlex.quote(WG_CONF)}; then
  cp {shlex.quote(WG_CONF)} {shlex.quote(WG_CONF)}.vpnctl-backup.$(date +%Y%m%d%H%M%S)
fi
"""
    remote.run_root_script(backup_script)
    remote.write_root_file(WG_CONF, config, "0600")


def apply_service(remote: Remote) -> None:
    remote.run_root_script(
        """
set -euo pipefail
systemctl enable wg-quick@wg0 >/dev/null
systemctl restart wg-quick@wg0
"""
    )
