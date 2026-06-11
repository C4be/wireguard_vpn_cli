from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .remote import Local
from .wireguard import emit

SERVICE_PATH = "/etc/systemd/system/vpnctl-bot.service"


@dataclass(frozen=True)
class BotServiceOptions:
    workdir: Path
    python: Path
    env: Mapping[str, str]


def install_bot_service(
    remote: Local,
    options: BotServiceOptions,
    progress=None,
) -> str:
    required = ["VPNCTL_BOT_TOKEN", "VPNCTL_ADMIN_PASSWORD", "VPNCTL_ENDPOINT"]
    missing = [name for name in required if not options.env.get(name, "").strip()]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))

    emit(progress, f"Writing {SERVICE_PATH}")
    remote.write_root_file(SERVICE_PATH, service_unit(options), "0644")
    emit(progress, "Reloading systemd and enabling vpnctl-bot")
    remote.run_root_script(
        """
set -euo pipefail
systemctl daemon-reload
systemctl enable vpnctl-bot >/dev/null
systemctl restart vpnctl-bot
systemctl --no-pager --full status vpnctl-bot | sed -n '1,35p'
"""
    )
    emit(progress, "vpnctl-bot service is installed and running")
    return SERVICE_PATH


def service_unit(options: BotServiceOptions) -> str:
    env_names = [
        "VPNCTL_BOT_TOKEN",
        "VPNCTL_ADMIN_PASSWORD",
        "VPNCTL_ENDPOINT",
        "VPNCTL_ADMIN_CHAT_IDS",
        "VPNCTL_SSH_PORT",
        "VPNCTL_LISTEN_PORT",
        "VPNCTL_NETWORK",
        "VPNCTL_DNS",
        "VPNCTL_MTU",
    ]
    env_lines = []
    for name in env_names:
        value = options.env.get(name, "").strip()
        if value:
            env_lines.append(f"Environment={name}={_systemd_quote(value)}")

    return (
        "[Unit]\n"
        "Description=vpnctl Telegram bot\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={options.workdir}\n"
        + "\n".join(env_lines)
        + "\n"
        f"ExecStart={options.python} -m vpnctl bot\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
