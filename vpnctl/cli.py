from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .emailer import EmailConfigError, send_config_email
from .remote import Local, Remote, RemoteError
from .wireguard import (
    ServerOptions,
    add_peer,
    diagnose,
    ensure_server,
    export_peer,
    list_peers,
    reboot_server,
    remove_peer,
    restart_wireguard,
)

DEFAULT_EMAIL = "dmitrycube@yandex.ru"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vpnctl",
        description="WireGuard manager for a personal VPS.",
    )
    parser.add_argument(
        "--host",
        help=(
            "Optional VPS IP address or DNS name. If omitted, vpnctl runs "
            "directly on this server."
        ),
    )
    parser.add_argument("--user", default="root", help="SSH user, default: root")
    parser.add_argument("--port", type=int, default=22, help="SSH port, default: 22")
    parser.add_argument("--identity", help="Path to SSH private key")

    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Install and configure WireGuard")
    setup.add_argument(
        "--endpoint",
        help=(
            "Public endpoint for clients. Defaults to --host in SSH mode; "
            "required when running directly on the VPS."
        ),
    )
    setup.add_argument("--listen-port", type=int, default=51820)
    setup.add_argument("--ssh-port", type=int, default=22)
    setup.add_argument("--network", default="10.66.66.0/24")
    setup.add_argument("--dns", default="1.1.1.1, 8.8.8.8")
    setup.add_argument("--mtu", type=int, default=1280)

    add = sub.add_parser("add-user", help="Create a client config")
    add.add_argument("name")
    add.add_argument("--email", default=DEFAULT_EMAIL)
    add.add_argument("--send-email", action="store_true")
    add.add_argument("--output-dir", default="configs")
    add.add_argument("--qr", action="store_true", help="Also write a QR PNG")

    remove = sub.add_parser("remove-user", help="Delete a client from WireGuard")
    remove.add_argument("name")

    export = sub.add_parser("export-user", help="Export an existing client config")
    export.add_argument("name")
    export.add_argument("--output-dir", default="configs")
    export.add_argument("--qr", action="store_true")
    export.add_argument("--email", default=DEFAULT_EMAIL)
    export.add_argument("--send-email", action="store_true")

    sub.add_parser("list-users", help="List configured clients")
    sub.add_parser("diagnose", help="Print WireGuard, network and firewall diagnostics")

    restart = sub.add_parser("restart", help="Restart WireGuard or reboot the VPS")
    restart.add_argument(
        "--reboot",
        action="store_true",
        help="Reboot the whole VPS instead of only restarting WireGuard",
    )

    return parser


def make_remote(args: argparse.Namespace) -> Remote | Local:
    if not args.host:
        return Local()
    return Remote(
        host=args.host,
        user=args.user,
        port=args.port,
        identity=args.identity,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    remote = make_remote(args)

    try:
        if args.command == "setup":
            endpoint = args.endpoint or args.host
            if not endpoint:
                raise RuntimeError(
                    "Pass setup --endpoint with the VPS public IP or DNS name "
                    "when running directly on the VPS."
                )
            state = ensure_server(
                remote,
                ServerOptions(
                    endpoint=endpoint,
                    listen_port=args.listen_port,
                    ssh_port=args.ssh_port,
                    network=args.network,
                    dns=args.dns,
                    mtu=args.mtu,
                ),
            )
            print(
                "WireGuard is ready: "
                f"{state['endpoint']}:{state['listen_port']} "
                f"network={state['network']}"
            )
            return 0

        if args.command == "add-user":
            add_peer(remote, args.name, email=args.email)
            conf_path, qr_path = export_peer(
                remote,
                args.name,
                Path(args.output_dir),
                with_qr=args.qr or args.send_email,
            )
            maybe_email(args, conf_path, qr_path)
            print(f"Created {args.name}: {conf_path}")
            if qr_path:
                print(f"QR: {qr_path}")
            return 0

        if args.command == "remove-user":
            removed = remove_peer(remote, args.name)
            print(f"Removed {args.name}" if removed else f"{args.name} was not found")
            return 0

        if args.command == "export-user":
            conf_path, qr_path = export_peer(
                remote,
                args.name,
                Path(args.output_dir),
                with_qr=args.qr or args.send_email,
            )
            maybe_email(args, conf_path, qr_path)
            print(f"Exported {args.name}: {conf_path}")
            if qr_path:
                print(f"QR: {qr_path}")
            return 0

        if args.command == "list-users":
            peers = list_peers(remote)
            if not peers:
                print("No users yet.")
                return 0
            for peer in peers:
                email = f" <{peer['email']}>" if peer.get("email") else ""
                print(f"{peer['name']}: {peer['address']}{email}")
            return 0

        if args.command == "diagnose":
            print(diagnose(remote))
            return 0

        if args.command == "restart":
            if args.reboot:
                reboot_server(remote)
                print("Reboot requested. SSH may be unavailable for a minute.")
            else:
                restart_wireguard(remote)
                print("WireGuard restarted.")
            return 0

    except (RemoteError, RuntimeError, KeyError, EmailConfigError) as exc:
        print(f"vpnctl: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


def maybe_email(args: argparse.Namespace, conf_path: Path, qr_path: Path | None) -> None:
    if not getattr(args, "send_email", False):
        return
    send_config_email(
        recipient=args.email,
        subject=f"WireGuard config: {conf_path.stem}",
        body=(
            "Attached is your WireGuard profile. "
            "Import the .conf file in the WireGuard app on iOS or Android. "
            "If a QR image is attached, you can scan it from the app too."
        ),
        config_path=conf_path,
        qr_path=qr_path,
    )
    print(f"Email sent to {args.email}")


if __name__ == "__main__":
    raise SystemExit(main())
