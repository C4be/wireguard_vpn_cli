from __future__ import annotations

import base64
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RemoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class Local:
    connect_timeout: int = 12

    @property
    def target(self) -> str:
        return "local"

    def run(
        self,
        command: str,
        *,
        input_data: bytes | str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        if isinstance(input_data, str):
            input_bytes = input_data.encode()
        else:
            input_bytes = input_data
        result = subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            executable="/bin/bash",
        )
        if check and result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            stdout = result.stdout.decode(errors="replace").strip()
            detail = stderr or stdout or f"exit code {result.returncode}"
            raise RemoteError(f"local: {detail}")
        return result

    def run_text(self, command: str, *, check: bool = True) -> str:
        return self.run(command, check=check).stdout.decode(errors="replace")

    def run_root_script(self, script: str, *, check: bool = True) -> str:
        command = (
            'if [ "$(id -u)" -eq 0 ]; then '
            "bash -se; "
            "else sudo -n bash -se; "
            "fi"
        )
        return self.run(command, input_data=script, check=check).stdout.decode(
            errors="replace"
        )

    def read_root_file(self, path: str, *, check: bool = True) -> str:
        qpath = shlex.quote(path)
        command = (
            'if [ "$(id -u)" -eq 0 ]; then '
            f"cat {qpath}; "
            f"else sudo -n cat {qpath}; "
            "fi"
        )
        return self.run(command, check=check).stdout.decode(errors="replace")

    def write_root_file(self, path: str, content: str, mode: str = "0600") -> None:
        encoded = base64.b64encode(content.encode()).decode()
        qpath = shlex.quote(path)
        qmode = shlex.quote(mode)
        script = f"""
set -euo pipefail
mkdir -p "$(dirname {qpath})"
base64 -d > {qpath} <<'VPNCTL_B64'
{encoded}
VPNCTL_B64
chmod {qmode} {qpath}
chown root:root {qpath}
"""
        self.run_root_script(script)

    def qr_png(self, content: str) -> bytes:
        result = self.run(
            "qrencode -t png -o -",
            input_data=content,
            check=True,
        )
        return result.stdout

    def download_root_file(self, remote_path: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(self.read_root_file(remote_path), encoding="utf-8")


@dataclass(frozen=True)
class Remote:
    host: str
    user: str = "root"
    port: int = 22
    identity: str | None = None
    connect_timeout: int = 12

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"

    def _base(self) -> list[str]:
        cmd = [
            "ssh",
            "-p",
            str(self.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
        ]
        if self.identity:
            cmd.extend(["-i", self.identity])
        cmd.append(self.target)
        return cmd

    def run(
        self,
        command: str,
        *,
        input_data: bytes | str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        if isinstance(input_data, str):
            input_bytes = input_data.encode()
        else:
            input_bytes = input_data
        result = subprocess.run(
            [*self._base(), command],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if check and result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            stdout = result.stdout.decode(errors="replace").strip()
            detail = stderr or stdout or f"exit code {result.returncode}"
            raise RemoteError(f"{self.target}: {detail}")
        return result

    def run_text(self, command: str, *, check: bool = True) -> str:
        return self.run(command, check=check).stdout.decode(errors="replace")

    def run_root_script(self, script: str, *, check: bool = True) -> str:
        command = (
            'if [ "$(id -u)" -eq 0 ]; then '
            "bash -se; "
            "else sudo -n bash -se; "
            "fi"
        )
        return self.run(command, input_data=script, check=check).stdout.decode(
            errors="replace"
        )

    def read_root_file(self, path: str, *, check: bool = True) -> str:
        qpath = shlex.quote(path)
        command = (
            'if [ "$(id -u)" -eq 0 ]; then '
            f"cat {qpath}; "
            f"else sudo -n cat {qpath}; "
            "fi"
        )
        return self.run(command, check=check).stdout.decode(errors="replace")

    def write_root_file(self, path: str, content: str, mode: str = "0600") -> None:
        encoded = base64.b64encode(content.encode()).decode()
        qpath = shlex.quote(path)
        qmode = shlex.quote(mode)
        script = f"""
set -euo pipefail
mkdir -p "$(dirname {qpath})"
base64 -d > {qpath} <<'VPNCTL_B64'
{encoded}
VPNCTL_B64
chmod {qmode} {qpath}
chown root:root {qpath}
"""
        self.run_root_script(script)

    def qr_png(self, content: str) -> bytes:
        result = self.run(
            "qrencode -t png -o -",
            input_data=content,
            check=True,
        )
        return result.stdout

    def download_root_file(self, remote_path: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(self.read_root_file(remote_path), encoding="utf-8")
