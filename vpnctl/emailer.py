from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


class EmailConfigError(RuntimeError):
    pass


def send_config_email(
    *,
    recipient: str,
    subject: str,
    body: str,
    config_path: Path,
    qr_path: Path | None = None,
) -> None:
    host = os.environ.get("VPNCTL_SMTP_HOST", "smtp.yandex.ru")
    port = int(os.environ.get("VPNCTL_SMTP_PORT", "465"))
    username = os.environ.get("VPNCTL_SMTP_USER", "dmitrycube@yandex.ru")
    password = os.environ.get("VPNCTL_SMTP_PASSWORD")
    sender = os.environ.get("VPNCTL_SMTP_FROM", username)

    if not password:
        raise EmailConfigError(
            "Set VPNCTL_SMTP_PASSWORD before sending email. "
            "For Yandex, use an app password."
        )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    conf_bytes = config_path.read_bytes()
    message.add_attachment(
        conf_bytes,
        maintype="application",
        subtype="x-wireguard-profile",
        filename=config_path.name,
    )

    if qr_path and qr_path.exists():
        message.add_attachment(
            qr_path.read_bytes(),
            maintype="image",
            subtype="png",
            filename=qr_path.name,
        )

    with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)
