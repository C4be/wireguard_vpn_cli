from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bot import BotConfigError, run_bot
from .remote import Local, RemoteError
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vpnctl",
        description="Local WireGuard manager and Telegram bot for a personal VPS.",
    )
    parser.add_argument("--quiet", action="store_true", help="Hide progress messages")

    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Install and configure WireGuard")
    setup.add_argument(
        "--endpoint",
        help="Public VPS IP address or DNS name for WireGuard clients.",
    )
    setup.add_argument("--listen-port", type=int, default=443)
    setup.add_argument("--ssh-port", type=int, default=22)
    setup.add_argument("--network", default="10.66.66.0/24")
    setup.add_argument("--dns", default="1.1.1.1, 8.8.8.8")
    setup.add_argument("--mtu", type=int, default=1280)
    setup.add_argument(
        "--rotate-server-key",
        action="store_true",
        help="Rotate the WireGuard server key. Refuses to run if users exist.",
    )

    add = sub.add_parser("add-user", help="Create a client config")
    add.add_argument("name")
    add.add_argument("--output-dir", default="configs")
    add.add_argument("--qr", action="store_true", help="Also write a QR PNG")

    remove = sub.add_parser("remove-user", help="Delete a client from WireGuard")
    remove.add_argument("name")

    export = sub.add_parser("export-user", help="Export an existing client config")
    export.add_argument("name")
    export.add_argument("--output-dir", default="configs")
    export.add_argument("--qr", action="store_true")

    sub.add_parser("list-users", help="List configured clients")
    sub.add_parser("diagnose", help="Print WireGuard, network and firewall diagnostics")

    restart = sub.add_parser("restart", help="Restart WireGuard or reboot the VPS")
    restart.add_argument(
        "--reboot",
        action="store_true",
        help="Reboot the whole VPS instead of only restarting WireGuard",
    )

    sub.add_parser("bot", help="Run the Telegram bot")

    return parser


def make_remote() -> Local:
    return Local()


class ProgressPrinter:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def __call__(self, message: str) -> None:
        if self.enabled:
            print(f"[vpnctl] {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    remote = make_remote()
    progress = ProgressPrinter(enabled=not args.quiet)
    progress("Mode: local VPS")

    try:
        if args.command == "bot":
            progress("Starting Telegram bot")
            run_bot()
            return 0

        if args.command == "setup":
            progress("Starting setup")
            endpoint = args.endpoint
            if not endpoint:
                raise RuntimeError(
                    "Pass setup --endpoint with the VPS public IP or DNS name."
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
                    rotate_server_key=args.rotate_server_key,
                ),
                progress=progress,
            )
            print(
                "WireGuard is ready: "
                f"{state['endpoint']}:{state['listen_port']} "
                f"network={state['network']}"
            )
            return 0

        if args.command == "add-user":
            progress(f"Adding user: {args.name}")
            add_peer(remote, args.name, progress=progress)
            conf_path, qr_path = export_peer(
                remote,
                args.name,
                Path(args.output_dir),
                with_qr=args.qr,
                progress=progress,
            )
            print(f"Created {args.name}: {conf_path}")
            if qr_path:
                print(f"QR: {qr_path}")
            return 0

        if args.command == "remove-user":
            progress(f"Removing user: {args.name}")
            removed = remove_peer(remote, args.name, progress=progress)
            print(f"Removed {args.name}" if removed else f"{args.name} was not found")
            return 0

        if args.command == "export-user":
            progress(f"Exporting user: {args.name}")
            conf_path, qr_path = export_peer(
                remote,
                args.name,
                Path(args.output_dir),
                with_qr=args.qr,
                progress=progress,
            )
            print(f"Exported {args.name}: {conf_path}")
            if qr_path:
                print(f"QR: {qr_path}")
            return 0

        if args.command == "list-users":
            peers = list_peers(remote, progress=progress)
            if not peers:
                print("No users yet.")
                return 0
            for peer in peers:
                print(f"{peer['name']}: {peer['address']}")
            return 0

        if args.command == "diagnose":
            print(diagnose(remote, progress=progress))
            return 0

        if args.command == "restart":
            if args.reboot:
                reboot_server(remote, progress=progress)
                print("Reboot requested. SSH may be unavailable for a minute.")
            else:
                restart_wireguard(remote, progress=progress)
                print("WireGuard restarted.")
            return 0

    except (RemoteError, RuntimeError, KeyError, BotConfigError) as exc:
        print(f"vpnctl: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
