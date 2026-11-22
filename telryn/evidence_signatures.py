"""Portable Ed25519 signatures with an explicitly pinned recipient trust key."""

from base64 import b64decode, b64encode
from pathlib import Path
import re
import struct
import subprocess
from tempfile import TemporaryDirectory

from .contracts import InputError, canonical

LIMIT = 8 * 1024 * 1024


def _run(*args, data=None):
    try:
        return subprocess.run(
            ["ssh-keygen", *args],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputError(
            "OpenSSH evidence signing or verification failed; check the keys and ssh-keygen installation"
        ) from exc


def _message(payload, purpose):
    if not isinstance(payload, dict) or not re.fullmatch(
        r"telryn-[a-z0-9-]{1,48}", purpose
    ):
        raise InputError("invalid evidence signature purpose or payload")
    result = canonical({"schema": 1, "purpose": purpose, "payload": payload}).encode(
        "ascii"
    )
    if len(result) > LIMIT:
        raise InputError("evidence signature payload exceeds 8 MiB")
    return result


def _public(value):
    fields = value.split(None, 2)
    if len(fields) < 2 or fields[0] != b"ssh-ed25519":
        raise InputError("supply a plain OpenSSH Ed25519 public key")
    try:
        blob = b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise InputError("invalid Ed25519 public key") from exc
    if (
        not blob.startswith(
            struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
        )
        or len(blob) != 51
    ):
        raise InputError("invalid Ed25519 public key")
    return b"ssh-ed25519 " + b64encode(blob)


def key_bytes(path: Path):
    with path.open("rb") as input_file:
        value = input_file.read(4097)
    if len(value) > 4096:
        raise InputError("Ed25519 public key exceeds 4 KiB")
    return _public(value)


def sign(payload: dict, private_key: Path, purpose: str) -> dict:
    private_key = private_key.resolve(strict=True)
    if not private_key.is_file() or private_key.stat().st_mode & 0o077:
        raise InputError("evidence signing key must be a private owner-only file")
    # Derive the public part from the private key, independently of a neighboring .pub.
    public = _public(_run("-y", "-P", "", "-f", str(private_key), data=b""))
    message = _message(payload, purpose)
    signature = _run("-Y", "sign", "-f", str(private_key), "-n", purpose, data=message)
    envelope = {
        "schema": 1,
        "purpose": purpose,
        "payload": payload,
        "signature": signature.decode("ascii"),
    }
    with TemporaryDirectory(prefix="telryn-sign-") as tmp:
        pub = Path(tmp) / "key.pub"
        pub.write_bytes(public)
        verify(envelope, pub, purpose)
    return envelope


def verify(envelope: dict, public_key: Path, purpose: str) -> dict:
    return verify_key(envelope, key_bytes(public_key), purpose)


def verify_key(envelope: dict, public: bytes, purpose: str) -> dict:
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"schema", "purpose", "payload", "signature"}
        or type(envelope["schema"]) is not int
        or envelope["schema"] != 1
        or envelope["purpose"] != purpose
    ):
        raise InputError("unrecognized signed evidence envelope")
    message = _message(envelope["payload"], purpose)
    signature = envelope["signature"]
    if (
        not isinstance(signature, str)
        or len(signature) > 2048
        or not signature.isascii()
    ):
        raise InputError("invalid Ed25519 signature")
    with TemporaryDirectory(prefix="telryn-verify-") as tmp:
        folder = Path(tmp)
        allowed = folder / "allowed_signers"
        allowed.write_bytes(b"telryn " + _public(public) + b"\n")
        path = folder / "signature"
        path.write_text(signature, encoding="ascii")
        _run(
            "-Y",
            "verify",
            "-f",
            str(allowed),
            "-I",
            "telryn",
            "-n",
            purpose,
            "-s",
            str(path),
            data=message,
        )
    return envelope["payload"]
