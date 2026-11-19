"""Send an explicitly requested aggregate weekly digest using customer-managed SMTP."""

import os
import smtplib
import ssl
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path

from .contracts import InputError
from .weekly_report import digest


def _address(value: str, label: str) -> Address:
    try:
        address = Address(addr_spec=value)
    except (ValueError, HeaderParseError) as exc:
        raise InputError(f"{label} must be one email address") from exc
    if not address.username or not address.domain or value != address.addr_spec:
        raise InputError(f"{label} must be one email address")
    return address


def send_digest(report_path: Path, recipient: str, sender: str) -> None:
    message = EmailMessage()
    message["To"] = _address(recipient, "recipient")
    message["From"] = _address(sender, "sender")
    body = digest(report_path)
    message["Subject"] = body.splitlines()[0]
    message.set_content(body)
    host = os.environ.get("TELRYN_SMTP_HOST")
    if not host:
        raise InputError("email delivery requires TELRYN_SMTP_HOST")
    try:
        port = int(os.environ.get("TELRYN_SMTP_PORT", "465"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise InputError("TELRYN_SMTP_PORT must be between 1 and 65535") from exc
    user = os.environ.get("TELRYN_SMTP_USERNAME")
    password = os.environ.get("TELRYN_SMTP_PASSWORD")
    if bool(user) != bool(password):
        raise InputError(
            "set both TELRYN_SMTP_USERNAME and TELRYN_SMTP_PASSWORD for SMTP authentication"
        )
    with smtplib.SMTP_SSL(
        host, port, context=ssl.create_default_context(), timeout=30
    ) as smtp:
        if user:
            smtp.login(user, password)
        smtp.send_message(message)
