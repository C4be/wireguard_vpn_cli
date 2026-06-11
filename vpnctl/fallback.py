from __future__ import annotations

import json
import secrets
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .remote import Local
from .wireguard import STATE_DIR, emit, utc_now

FALLBACK_STATE_FILE = f"{STATE_DIR}/fallback.json"
SING_BOX_CONFIG = "/etc/sing-box/config.json"
Progress = Callable[[str], None]


@dataclass(frozen=True)
class FallbackOptions:
    endpoint: str
    port: int = 443
    method: str = "xchacha20-ietf-poly1305"
    mtu: int = 1280


def ensure_fallback(
    remote: Local,
    options: FallbackOptions,
    progress: Progress | None = None,
) -> dict[str, Any]:
    emit(progress, "Installing sing-box if missing")
    install_sing_box(remote)

    state = load_fallback_state(remote) or {
        "created_at": utc_now(),
        "password": secrets.token_urlsafe(32),
    }
    state.update(
        {
            "version": 1,
            "endpoint": options.endpoint,
            "port": options.port,
            "method": options.method,
            "mtu": options.mtu,
            "updated_at": utc_now(),
        }
    )

    emit(progress, f"Saving fallback state in {FALLBACK_STATE_FILE}")
    save_fallback_state(remote, state)
    emit(progress, f"Writing {SING_BOX_CONFIG}")
    remote.write_root_file(
        SING_BOX_CONFIG,
        json.dumps(server_config(state), indent=2, sort_keys=True) + "\n",
        "0600",
    )
    emit(progress, "Enabling and restarting sing-box")
    remote.run_root_script(
        f"""
set -euo pipefail
if command -v ufw >/dev/null 2>&1 && ufw status | grep -qi '^Status: active'; then
  ufw allow {int(options.port)}/tcp >/dev/null || true
fi
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port={int(options.port)}/tcp >/dev/null || true
  firewall-cmd --reload >/dev/null || true
fi
systemctl enable sing-box >/dev/null
systemctl restart sing-box
"""
    )
    emit(progress, "Fallback transport is ready")
    return state


def install_sing_box(remote: Local) -> None:
    remote.run_root_script(
        """
set -euo pipefail
if command -v sing-box >/dev/null 2>&1; then
  exit 0
fi
if ! command -v apt-get >/dev/null 2>&1; then
  echo "Automatic sing-box install currently supports apt-based systems only." >&2
  exit 2
fi
apt-get update
apt-get install -y curl ca-certificates
mkdir -p /etc/apt/keyrings
curl -fsSL https://sing-box.app/gpg.key -o /etc/apt/keyrings/sagernet.asc
chmod a+r /etc/apt/keyrings/sagernet.asc
cat > /etc/apt/sources.list.d/sagernet.sources <<'EOF'
Types: deb
URIs: https://deb.sagernet.org/
Suites: *
Components: *
Enabled: yes
Signed-By: /etc/apt/keyrings/sagernet.asc
EOF
apt-get update
apt-get install -y sing-box
"""
    )


def load_fallback_state(remote: Local) -> dict[str, Any] | None:
    result = remote.run_root_script(
        f"""
set -euo pipefail
if [ -f {shlex.quote(FALLBACK_STATE_FILE)} ]; then
  cat {shlex.quote(FALLBACK_STATE_FILE)}
fi
""",
        check=True,
    )
    data = result.strip()
    if not data:
        return None
    return json.loads(data)


def save_fallback_state(remote: Local, state: dict[str, Any]) -> None:
    safe_state = dict(state)
    remote.write_root_file(
        FALLBACK_STATE_FILE,
        json.dumps(safe_state, indent=2, sort_keys=True) + "\n",
        "0600",
    )


def server_config(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "log": {"level": "info", "timestamp": True},
        "inbounds": [
            {
                "type": "shadowsocks",
                "tag": "ss-tcp-in",
                "listen": "::",
                "listen_port": int(state["port"]),
                "network": "tcp",
                "method": state["method"],
                "password": state["password"],
            }
        ],
        "outbounds": [{"type": "direct", "tag": "direct"}],
    }


def client_config(state: dict[str, Any], name: str) -> dict[str, Any]:
    return {
        "log": {"level": "info", "timestamp": True},
        "dns": {
            "servers": [
                {"tag": "cloudflare", "address": "1.1.1.1"},
                {"tag": "google", "address": "8.8.8.8"},
            ],
            "final": "cloudflare",
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "address": ["172.19.0.1/30"],
                "mtu": int(state.get("mtu", 1280)),
                "auto_route": True,
                "strict_route": True,
            }
        ],
        "outbounds": [
            {
                "type": "shadowsocks",
                "tag": "fallback",
                "server": state["endpoint"],
                "server_port": int(state["port"]),
                "method": state["method"],
                "password": state["password"],
                "network": "tcp",
                "udp_over_tcp": {"enabled": True, "version": 2},
            },
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "auto_detect_interface": True,
            "final": "fallback",
        },
        "experimental": {
            "cache_file": {
                "enabled": True,
                "path": f"cache-{name}.db",
            }
        },
    }


def export_fallback_profile(
    remote: Local,
    name: str,
    output_dir: Path,
    progress: Progress | None = None,
) -> Path:
    emit(progress, "Loading fallback state")
    state = load_fallback_state(remote)
    if not state:
        raise RuntimeError("Fallback transport is not initialized. Run fallback-setup first.")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}-sing-box.json"
    emit(progress, f"Writing fallback profile: {path}")
    path.write_text(
        json.dumps(client_config(state, name), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def fallback_status(remote: Local) -> str:
    return remote.run_root_script(
        f"""
set +e
echo "== fallback state =="
if [ -f {shlex.quote(FALLBACK_STATE_FILE)} ]; then
  sed -E 's/"password": "([^"]+)"/"password": "<hidden>"/g' {shlex.quote(FALLBACK_STATE_FILE)}
else
  echo "missing {FALLBACK_STATE_FILE}"
fi
echo
echo "== sing-box service =="
systemctl --no-pager --full status sing-box 2>&1 | sed -n '1,60p'
echo
echo "== tcp listeners =="
ss -ltnp 2>&1 | grep -E '(:443|sing-box)' || true
echo
echo "== recent logs =="
journalctl -u sing-box -n 60 --no-pager 2>&1
""",
        check=False,
    )
