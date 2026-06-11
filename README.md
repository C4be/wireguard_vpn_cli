# vpnctl

`vpnctl` configures WireGuard directly on a VPS and then serves client configs
through a Telegram bot. There is no SSH control mode and no email sender: the VPS
is the only place where the tool runs.

The project has two connection modes:

- WireGuard on `UDP 443` for normal networks. It is fast and is the default.
- sing-box fallback on `TCP 443` for Wi-Fi networks that break or block UDP.

The bot flow is simple:

- a user sends a WireGuard device name, for example `dima-iphone`;
- if that user exists, the bot sends a QR image and the `.conf` file;
- if UDP is unstable, the user asks for an устойчивый профиль and the bot sends
  a sing-box JSON profile;
- an admin unlocks management commands with a password and can add/remove users,
  check device status, repair WireGuard, restart it, configure fallback, run
  diagnostics, and run setup.

## Requirements

- VPS with Python 3.10+.
- Debian/Ubuntu or Fedora/RHEL-like Linux.
- Run setup and the bot as `root`, or as a user with passwordless `sudo`.
- Public UDP port `443` open in the VPS provider firewall for WireGuard.
- Public TCP port `443` open for sing-box fallback.
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

The package config explicitly includes only `vpnctl`, so generated folders like
`configs/` do not break editable installation.

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
export VPNCTL_LISTEN_PORT="443"
export VPNCTL_NETWORK="10.66.66.0/24"
export VPNCTL_DNS="1.1.1.1, 8.8.8.8"
export VPNCTL_MTU="1280"
```

`VPNCTL_ENDPOINT` is the public IP or DNS name that phones will use in their
WireGuard profiles.

`UDP 443` is the default WireGuard port because it survives more Wi-Fi networks
than the usual `51820`. Pure WireGuard still cannot work on networks that block
all outbound UDP traffic.

`TCP 443` is used by the fallback profile. It can coexist with WireGuard on the
same numeric port because WireGuard uses UDP and sing-box uses TCP.

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

## TCP Fallback

Fallback is for networks where WireGuard handshakes once but traffic does not
flow, or where Wi-Fi blocks UDP entirely. It installs `sing-box` from the
official SagerNet apt repository and exposes a Shadowsocks TCP listener on
`443`.

Set it up once on the VPS:

```bash
sudo -E .venv/bin/python -m vpnctl fallback-setup --endpoint "$VPNCTL_ENDPOINT" --port 443
```

Export a profile manually:

```bash
sudo -E .venv/bin/python -m vpnctl export-fallback dima-iphone
```

Then import `configs/dima-iphone-sing-box.json` into the sing-box app on iOS or
Android.

From Telegram:

```text
/admin <password>
/fallback_setup
/fallback dima-iphone
/fallback_status
```

The bot buttons expose the same flow:

```text
Настроить fallback
Устойчивый профиль
Статус fallback
```

WireGuard users are still managed normally with `/add` and `/remove`. The
fallback profile is a server-level emergency transport; if its secret is exposed,
rerun `fallback-setup` to rotate it and send fresh profiles.

## Run The Bot

Foreground run:

```bash
sudo -E .venv/bin/python -m vpnctl bot
```

Install and start the systemd service automatically:

```bash
sudo -E .venv/bin/python -m vpnctl install-bot-service
sudo systemctl status vpnctl-bot --no-pager -l
```

Admin commands in Telegram:

```text
/admin <password>
/setup [endpoint]
/fallback_setup [endpoint]
/add dima-iphone
/fallback dima-iphone
/remove dima-iphone
/list
/status
/repair
/restart
/fallback_status
/diagnose
```

After `/admin`, the bot also shows a button menu:

```text
Получить WireGuard
Устойчивый профиль
Добавить устройство
Список устройств
Статус VPN
Починить VPN
Перезапустить VPN
Настроить fallback
Статус fallback
Диагностика
```

User flow:

```text
dima-iphone
```

The bot replies with `dima-iphone.png` QR and `dima-iphone.conf`.

## systemd Service

The recommended path is:

```bash
sudo -E .venv/bin/python -m vpnctl install-bot-service
```

It writes `/etc/systemd/system/vpnctl-bot.service` from the current environment,
enables it, and restarts the bot.

Manual service file, if you prefer to inspect every line yourself:

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
sudo -E .venv/bin/python -m vpnctl add-user dima-iphone --qr
sudo -E .venv/bin/python -m vpnctl export-user dima-iphone --qr
sudo -E .venv/bin/python -m vpnctl remove-user dima-iphone
sudo -E .venv/bin/python -m vpnctl list-users
sudo -E .venv/bin/python -m vpnctl status
sudo -E .venv/bin/python -m vpnctl repair
sudo -E .venv/bin/python -m vpnctl restart
sudo -E .venv/bin/python -m vpnctl fallback-setup --endpoint "$VPNCTL_ENDPOINT" --port 443
sudo -E .venv/bin/python -m vpnctl export-fallback dima-iphone
sudo -E .venv/bin/python -m vpnctl fallback-status
sudo -E .venv/bin/python -m vpnctl install-bot-service
sudo -E .venv/bin/python -m vpnctl diagnose
```

## Notes

- Existing unmanaged `/etc/wireguard/wg0.conf` is backed up before vpnctl writes
  its managed config.
- If a failed run leaves a stale `wg0` interface behind, vpnctl reconciles it
  before starting `wg-quick@wg0`.
- User names may contain only letters, digits, dots, underscores and dashes, and
  must start with a letter or digit.
- One WireGuard config is for one physical device only. Do not import the same
  QR on two phones; create names like `dima-iphone`, `dima-ipad`,
  `nata-android`.
- `repair` rewrites `/etc/wireguard/wg0.conf` from saved state, reconciles stale
  `wg0` interfaces, reapplies NAT/MSS rules, and restarts WireGuard.
- `status` shows the latest handshake, endpoint, and transfer counters per
  device.
- Fallback uses sing-box TUN mode on the phone, Shadowsocks over TCP to the VPS,
  and UDP-over-TCP in the generated client profile.
