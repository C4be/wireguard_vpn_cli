# vpnctl

`vpnctl` is a small CLI that configures WireGuard on a VPS, keeps the SSH path
open, and manages mobile-ready client configs for iOS and Android.

The recommended mode is native WireGuard on the VPS. Docker is detected in
diagnostics, but WireGuard itself runs as a systemd service because it is easier
to audit, repair and keep alive after reboots than a privileged VPN container.

## Requirements

- VPS: Python 3.10+, Debian/Ubuntu or Fedora/RHEL-like Linux.
- Run commands as `root`, or as a user with passwordless `sudo`.
- Public UDP port `51820` open at the cloud/VPS provider level.

## First setup

Run this directly on the VPS:

```bash
sudo python3 -m vpnctl setup \
  --endpoint YOUR_VPS_PUBLIC_IP_OR_DOMAIN \
  --ssh-port 22
```

During long operations, vpnctl prints progress messages like:

```text
[vpnctl] Mode: local VPS
[vpnctl] Starting setup
[vpnctl] Checking packages and preparing the server
[vpnctl] Base packages, firewall allowances and sysctl are ready
[vpnctl] Writing /etc/wireguard/wg0.conf
[vpnctl] Enabling and restarting wg-quick@wg0
```

Use `--quiet` before the command name if you need machine-friendly output:

```bash
sudo python3 -m vpnctl --quiet list-users
```

What setup does:

- installs `wireguard-tools`, `iptables` and `qrencode` when missing;
- creates `/etc/wireguard/vpnctl/server.json`;
- backs up an existing unmanaged `/etc/wireguard/wg0.conf` before replacing it;
- allows the SSH TCP port and WireGuard UDP port if `ufw` or `firewalld` is
  already active;
- enables IPv4 forwarding and starts `wg-quick@wg0`.

If a previous failed run left `wg0` behind while the systemd service is failed,
vpnctl automatically stops the stale interface before starting WireGuard again.

If the server private key was exposed in logs before masking was added, rotate it
before creating users:

```bash
sudo python3 -m vpnctl setup \
  --endpoint YOUR_VPS_PUBLIC_IP_OR_DOMAIN \
  --ssh-port 22 \
  --rotate-server-key
```

## Add a phone or another user

```bash
sudo python3 -m vpnctl add-user dima --qr
```

The config will be written to `configs/dima.conf`; the QR image will be written
to `configs/dima.png`. Both iOS and Android WireGuard apps can import the same
`.conf` file. The generated client profile uses:

- `AllowedIPs = 0.0.0.0/0` to route all traffic through the VPS;
- `PersistentKeepalive = 25` for mobile networks and Wi-Fi NAT stability;
- `MTU = 1280` to avoid common mobile-path MTU issues.

User names may contain only letters, digits, dots, underscores and dashes. They
are used as local config filenames, so names like `../phone` are rejected.

## Email a config

For Yandex, create an app password and export it locally:

```bash
export VPNCTL_SMTP_USER="dmitrycube@yandex.ru"
export VPNCTL_SMTP_PASSWORD="YOUR_YANDEX_APP_PASSWORD"
```

Then:

```bash
sudo python3 -m vpnctl add-user friend \
  --email dmitrycube@yandex.ru \
  --send-email
```

Optional SMTP settings:

- `VPNCTL_SMTP_HOST`, default `smtp.yandex.ru`
- `VPNCTL_SMTP_PORT`, default `465`
- `VPNCTL_SMTP_FROM`, default same as `VPNCTL_SMTP_USER`

## Manage users

```bash
sudo python3 -m vpnctl list-users
sudo python3 -m vpnctl export-user dima --qr
sudo python3 -m vpnctl remove-user dima
```

## Restart and diagnostics

Restart only WireGuard:

```bash
sudo python3 -m vpnctl restart
```

Reboot the whole VPS:

```bash
sudo python3 -m vpnctl restart --reboot
```

Print diagnostics:

```bash
sudo python3 -m vpnctl diagnose
```

Diagnostics include the managed state, default route, UDP listener, systemd
status, `wg show`, firewall details, NAT rules, and Docker version when Docker is
installed.

## Notes

- Run `setup` again after changing endpoint, DNS or SSH port. It is idempotent
  and keeps existing users.
- `setup --endpoint` must be the public IP or DNS name that phones will use to
  reach the VPS.
- Changing the VPN subnet is blocked while users exist, because existing client
  addresses would otherwise become inconsistent.
- Existing unmanaged WireGuard config is backed up before `vpnctl` writes its own
  `/etc/wireguard/wg0.conf`.
- If SSH runs on a non-standard port, pass `setup --ssh-port YOUR_PORT` for
  firewall preservation.

## Optional SSH mode

You can still control the VPS remotely from another machine by adding `--host`:

```bash
python3 -m vpnctl --host YOUR_VPS_IP --user root setup \
  --endpoint YOUR_VPS_IP \
  --ssh-port 22
```

In SSH mode, `--port` is the SSH connection port, and `setup --ssh-port` is the
port that vpnctl preserves in the VPS firewall.
