"""Trusted reverse-proxy identity for one organization's hosted complaints desk."""

import hmac
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import InputError


@dataclass(frozen=True)
class Proxy:
    origin: str
    authority: str
    secret: str

    @classmethod
    def configure(cls, origin, path):
        value = urlsplit(origin)
        if (
            value.scheme != "https"
            or not value.hostname
            or value.username is not None
            or value.password is not None
            or value.query
            or value.fragment
            or value.path
            or not re.fullmatch(r"[a-zA-Z0-9.-]+(?::[0-9]+)?", value.netloc)
            or value.port == 0
        ):
            raise InputError(
                "hosted origin must be an HTTPS scheme and hostname with optional port, without a path"
            )
        fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            mode = os.fstat(fd).st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                raise InputError(
                    "use a private regular proxy secret file with mode 0600 or 0400"
                )
            key = stream.read(260).rstrip(b"\r\n")
            if not re.fullmatch(rb"[A-Za-z0-9_-]{43,128}", key):
                raise InputError(
                    "use a randomly generated base64url or hex proxy secret of 43–128 characters"
                )
        return cls(origin, value.netloc, key.decode("ascii"))

    def principal(self, handler):
        def single(name):
            values = handler.headers.get_all(name, [])
            return values[0] if len(values) == 1 else ""

        if (
            handler.client_address[0] != "127.0.0.1"
            or single("Host") != self.authority
            or not hmac.compare_digest(
                single("X-Telryn-Proxy").encode(), self.secret.encode()
            )
            or (handler.command == "POST" and single("Origin") != self.origin)
        ):
            return None
        subject = single("X-Telryn-Subject")
        role = single("X-Telryn-Role")
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:@|-]{0,127}", subject
        ) or role not in ("lead", "reviewer"):
            return None
        return role, subject


def reviewer_id(subject):
    """Bind case-sensitive proxy subjects even in legacy case-insensitive reviewer comparisons."""
    return f"sso:{subject[:48]}:{hashlib.sha256(subject.encode('ascii')).hexdigest()}"
