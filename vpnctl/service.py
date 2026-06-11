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


def restart_bot_service(remote: Local, progress=None) -> str:
    emit(progress, "Restarting vpnctl-bot")
    return remote.run_root_script(
        """
set -euo pipefail
systemctl restart vpnctl-bot
systemctl --no-pager --full status vpnctl-bot 2>&1 | sed -n '1,45p'
"""
    )


def bot_service_status(remote: Local) -> str:
    return remote.run_root_script(
        f"""
set +e
echo "== vpnctl-bot service =="
systemctl --no-pager --full status vpnctl-bot 2>&1 | sed -n '1,80p'
echo
echo "== vpnctl-bot unit =="
if [ -f {SERVICE_PATH} ]; then
  sed -E \
    -e 's/(VPNCTL_BOT_TOKEN=)[^ "]+/\\1<hidden>/g' \
    -e 's/(VPNCTL_ADMIN_PASSWORD=)[^ "]+/\\1<hidden>/g' \
    {SERVICE_PATH}
else
  echo "missing {SERVICE_PATH}"
fi
echo
echo "== recent vpnctl-bot logs =="
journalctl -u vpnctl-bot -n 120 --no-pager 2>&1
echo
echo "== running bot processes =="
ps -eo pid,ppid,cmd | grep -E '[p]ython.*vpnctl bot|[v]pnctl bot'
""",
        check=False,
    )


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
            env_lines.append(f"Environment={_systemd_quote(f'{name}={value}')}")

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
