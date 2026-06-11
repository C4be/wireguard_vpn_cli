# vpnctl

`vpnctl` is a small CLI that configures WireGuard on a remote VPS over SSH, keeps
the SSH path open, and manages mobile-ready client configs for iOS and Android.

The recommended mode is native WireGuard on the VPS. Docker is detected in
diagnostics, but WireGuard itself runs as a systemd service because it is easier
to audit, repair and keep alive after reboots than a privileged VPN container.

## Requirements

- Local machine: Python 3.10+, `ssh`.
- VPS: Debian/Ubuntu, Fedora/RHEL-like Linux with SSH access as `root` or a user
  with passwordless `sudo`.
- Public UDP port `51820` open at the cloud/VPS provider level.

## First setup

```bash
python3 -m vpnctl --host YOUR_VPS_IP --user root setup \
  --endpoint YOUR_VPS_IP \
  --ssh-port 22
```

What setup does:

- installs `wireguard-tools`, `iptables` and `qrencode` when missing;
- creates `/etc/wireguard/vpnctl/server.json`;
- backs up an existing unmanaged `/etc/wireguard/wg0.conf` before replacing it;
- allows the SSH TCP port and WireGuard UDP port if `ufw` or `firewalld` is
  already active;
- enables IPv4 forwarding and starts `wg-quick@wg0`.

## Add a phone or another user

```bash
python3 -m vpnctl --host YOUR_VPS_IP add-user dima --qr
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
python3 -m vpnctl --host YOUR_VPS_IP add-user friend \
  --email dmitrycube@yandex.ru \
  --send-email
```

Optional SMTP settings:

- `VPNCTL_SMTP_HOST`, default `smtp.yandex.ru`
- `VPNCTL_SMTP_PORT`, default `465`
- `VPNCTL_SMTP_FROM`, default same as `VPNCTL_SMTP_USER`

## Manage users

```bash
python3 -m vpnctl --host YOUR_VPS_IP list-users
python3 -m vpnctl --host YOUR_VPS_IP export-user dima --qr
python3 -m vpnctl --host YOUR_VPS_IP remove-user dima
```

## Restart and diagnostics

Restart only WireGuard:

```bash
python3 -m vpnctl --host YOUR_VPS_IP restart
```

Reboot the whole VPS:

```bash
python3 -m vpnctl --host YOUR_VPS_IP restart --reboot
```

Print diagnostics:

```bash
python3 -m vpnctl --host YOUR_VPS_IP diagnose
```

Diagnostics include the managed state, default route, UDP listener, systemd
status, `wg show`, firewall details, NAT rules, and Docker version when Docker is
installed.

## Notes

- Run `setup` again after changing endpoint, DNS or SSH port. It is idempotent
  and keeps existing users.
- Changing the VPN subnet is blocked while users exist, because existing client
  addresses would otherwise become inconsistent.
- Existing unmanaged WireGuard config is backed up before `vpnctl` writes its own
  `/etc/wireguard/wg0.conf`.
- If SSH runs on a non-standard port, pass both `--port` for the SSH connection
  and `setup --ssh-port` for firewall preservation.
