# vpnctl

`vpnctl` configures WireGuard directly on a VPS and then serves client configs
through a Telegram bot. There is no SSH control mode and no email sender: the VPS
is the only place where the tool runs.

The bot flow is simple:

- a user sends a WireGuard username, for example `dima`;
- if that user exists, the bot sends a QR image and the `.conf` file;
- an admin unlocks management commands with a password and can add/remove users,
  restart WireGuard, run diagnostics, and run setup.

## Requirements

- VPS with Python 3.10+.
- Debian/Ubuntu or Fedora/RHEL-like Linux.
- Run setup and the bot as `root`, or as a user with passwordless `sudo`.
- Public UDP port `51820` open in the VPS provider firewall.
- Telegram bot token from BotFather.

## Install

On the VPS:

```bash
cd /root/vpn/wireguard_vpn_cli
apt update
apt install -y python3-pip python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[bot]'
```

## Environment

Set these before starting the bot:

```bash
export VPNCTL_BOT_TOKEN="123456:telegram-bot-token"
export VPNCTL_ADMIN_PASSWORD="long-random-admin-password"
export VPNCTL_ENDPOINT="151.244.251.86"
```

Optional settings:

```bash
export VPNCTL_ADMIN_CHAT_IDS="123456789,987654321"
export VPNCTL_SSH_PORT="22"
export VPNCTL_LISTEN_PORT="51820"
export VPNCTL_NETWORK="10.66.66.0/24"
export VPNCTL_DNS="1.1.1.1, 8.8.8.8"
export VPNCTL_MTU="1280"
```

`VPNCTL_ENDPOINT` is the public IP or DNS name that phones will use in their
WireGuard profiles.

## First Setup

You can run setup from the terminal:

```bash
sudo -E .venv/bin/python -m vpnctl setup --endpoint "$VPNCTL_ENDPOINT" --ssh-port 22
```

Or from Telegram after starting the bot:

```text
/admin long-random-admin-password
/setup
```

If the old server private key was exposed in logs before masking was added, and
you have no users yet, rotate it:

```bash
sudo -E .venv/bin/python -m vpnctl setup \
  --endpoint "$VPNCTL_ENDPOINT" \
  --ssh-port 22 \
  --rotate-server-key
```

## Run The Bot

Foreground run:

```bash
sudo -E .venv/bin/python -m vpnctl bot
```

Admin commands in Telegram:

```text
/admin <password>
/setup [endpoint]
/add dima
/remove dima
/list
/restart
/diagnose
```

User flow:

```text
dima
```

The bot replies with `dima.png` QR and `dima.conf`.

## systemd Service

Create `/etc/systemd/system/vpnctl-bot.service`:

```ini
[Unit]
Description=vpnctl Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/vpn/wireguard_vpn_cli
Environment=VPNCTL_BOT_TOKEN=123456:telegram-bot-token
Environment=VPNCTL_ADMIN_PASSWORD=long-random-admin-password
Environment=VPNCTL_ENDPOINT=151.244.251.86
ExecStart=/root/vpn/wireguard_vpn_cli/.venv/bin/python -m vpnctl bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vpnctl-bot
sudo systemctl status vpnctl-bot --no-pager -l
```

## Local Emergency CLI

These commands still work directly on the VPS:

```bash
sudo -E .venv/bin/python -m vpnctl add-user dima --qr
sudo -E .venv/bin/python -m vpnctl export-user dima --qr
sudo -E .venv/bin/python -m vpnctl remove-user dima
sudo -E .venv/bin/python -m vpnctl list-users
sudo -E .venv/bin/python -m vpnctl restart
sudo -E .venv/bin/python -m vpnctl diagnose
```

## Notes

- Existing unmanaged `/etc/wireguard/wg0.conf` is backed up before vpnctl writes
  its managed config.
- If a failed run leaves a stale `wg0` interface behind, vpnctl reconciles it
  before starting `wg-quick@wg0`.
- User names may contain only letters, digits, dots, underscores and dashes, and
  must start with a letter or digit.
