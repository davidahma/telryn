"""Team-held signing key, sampling commitments and latest signed report revisions."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .artifacts import sync_directory
from .contracts import InputError, canonical, fingerprint

DRAW_DOMAIN = "telryn-weekly-draw-v1"


def directory() -> Path:
    base = os.environ.get("TELRYN_TRUST_DIR")
    return (
        Path(base).expanduser() if base else Path.home() / ".local/state/telryn/audit"
    )


def _root() -> Path:
    root = directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _private(root, directory=True)
    return root


def _private(path: Path, *, directory: bool = False) -> None:
    data = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(data.st_mode) or data.st_uid != os.getuid() or data.st_mode & 0o077:
        raise InputError(
            f"trust storage must be owner-only and owned by this account: {path}"
        )


def _key(*, create=False) -> bytes:
    root = _root()
    path = root / "audit.key"
    if not path.exists() and create:
        # A fully written key becomes visible atomically across concurrent first runs.
        temporary = root / (".key-" + secrets.token_hex(16))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
                sync_directory(root)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    if not path.exists():
        raise InputError(
            "no team audit key in TELRYN_TRUST_DIR; obtain the original trust store"
        )
    _private(path)
    value = path.read_bytes()
    if len(value) != 32:
        raise InputError("audit.key must contain exactly 32 secret bytes")
    return value


def key_id(key: bytes) -> str:
    return hashlib.sha256(b"telryn-team-key-v1\0" + key).hexdigest()


def mac(key: bytes, purpose: str, value: bytes) -> str:
    return hmac.digest(key, purpose.encode() + b"\0" + value, "sha256").hex()


@contextmanager
def registry(*, create=False):
    root = _root()
    stamp = root / "initialized"
    keypath = root / "audit.key"
    path = root / "ledger.sqlite3"
    if stamp.exists():
        _private(stamp)
        if not path.is_file() or not keypath.is_file():
            raise InputError(
                "team trust store is incomplete; restore the original key and revision registry"
            )
    if create and path.exists() and not keypath.exists():
        raise InputError(
            "the existing audit registry has lost its signing key; restore the trust backup"
        )
    key = _key(create=create)
    if create:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    if not path.exists():
        raise InputError(
            "missing audit revision registry; restore the team trust backup"
        )
    _private(path)
    db = sqlite3.connect(path, timeout=30)
    try:
        db.execute("BEGIN IMMEDIATE")
        if create and not stamp.exists():
            db.execute(
                "CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), key_id TEXT NOT NULL)"
            )
            db.execute("INSERT OR IGNORE INTO identity VALUES (1,?)", (key_id(key),))
            db.execute(
                "CREATE TABLE IF NOT EXISTS draws (id TEXT PRIMARY KEY, week TEXT NOT NULL, frame TEXT NOT NULL, design TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, signature TEXT NOT NULL)"
            )
        row = db.execute("SELECT key_id FROM identity WHERE id=1").fetchone()
        if not row or row[0] != key_id(key):
            raise InputError("team key differs from the committed audit registry")
        db.execute(
            "CREATE TABLE IF NOT EXISTS pending_reports (path TEXT PRIMARY KEY, "
            "staged TEXT NOT NULL, prior_digest TEXT NOT NULL, digest TEXT NOT NULL)"
        )
        yield db, key
        db.commit()
        if create and not stamp.exists():
            # Refuse to recreate a missing revision ledger or key during a future start.
            try:
                fd = os.open(stamp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
                sync_directory(root)
    except sqlite3.Error as exc:
        raise InputError(f"unable to access the audit trust registry: {exc}") from exc
    finally:
        db.close()


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def recover_report(db, path: Path) -> None:
    """Materialize a committed revision only over the file it was prepared for."""
    path = path.resolve()
    row = db.execute(
        "SELECT staged,prior_digest,digest FROM pending_reports WHERE path=?",
        (str(path),),
    ).fetchone()
    if row is None:
        return
    name, prior, digest = row
    if Path(name).name != name or not name.startswith(".telryn-pending-"):
        raise InputError("invalid staged report name in the team registry")
    staged = path.with_name(name)
    try:
        current = _file_digest(path)
        if current != digest:
            if current != prior:
                raise InputError(
                    "pending report conflicts with the file at its original path; restore the recorded version"
                )
            _private(staged)
            if _file_digest(staged) != digest:
                raise InputError(
                    "pending report file failed its digest check; restore the original private artifacts"
                )
            staged.replace(path)
        sync_directory(path.parent)
    except FileNotFoundError as exc:
        raise InputError(
            "pending report or staged evidence missing at its original path; restore the original private artifacts"
        ) from exc
    db.execute("DELETE FROM pending_reports WHERE path=?", (str(path),))


def replace_report(db, path: Path, data: bytes) -> None:
    """Commit the new authority and recovery intent before exposing the new file."""
    path = path.resolve(strict=True)
    prior = _file_digest(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".telryn-pending-")
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(path.parent)
        db.execute(
            "INSERT INTO pending_reports VALUES (?,?,?,?)",
            (str(path), staged.name, prior, hashlib.sha256(data).hexdigest()),
        )
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    # The caller already updated its revision head within this transaction.
    # Keep the staged file if COMMIT returns an ambiguous I/O failure.
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    recover_report(db, path)


def committed_draw(week: str, frame: str, design: str) -> tuple[str, dict]:
    """Persist the design before returning a secret-keyed lottery for this frame."""
    with registry(create=True) as (db, key):
        identifier = fingerprint([key_id(key), week, frame, design])
        db.execute(
            "INSERT OR IGNORE INTO draws(id,week,frame,design) VALUES (?,?,?,?)",
            (identifier, week, frame, design),
        )
        seed = mac(key, DRAW_DOMAIN, canonical([week, frame]).encode())
        return seed, {
            "key_id": key_id(key),
            "draw_id": identifier,
            "frame": frame,
            "design": design,
        }


def check_draw(db, key: bytes, state: dict) -> None:
    commitment = state.get("draw_commitment")
    if not isinstance(commitment, dict) or commitment.get("key_id") != key_id(key):
        raise InputError("weekly report requires the team key used to commit its draw")
    row = db.execute(
        "SELECT week,frame,design FROM draws WHERE id=?", (commitment.get("draw_id"),)
    ).fetchone()
    if row != (state.get("week"), commitment.get("frame"), commitment.get("design")):
        raise InputError("this draw is absent from the team's trusted registry")


def register_arguments(commands):
    parser = commands.add_parser(
        "trust", help="initialize or inspect the local weekly audit trust store"
    )
    parser.add_argument("operation", choices=("init", "status", "draws"))


def execute(args) -> int:
    with registry(create=args.operation == "init") as (db, key):
        if args.operation == "draws":
            rows = db.execute(
                "SELECT id,week,frame,design,created FROM draws ORDER BY rowid"
            )
            print(
                canonical(
                    [
                        dict(
                            zip(
                                ("draw_id", "week", "frame", "design", "committed_utc"),
                                row,
                            )
                        )
                        for row in rows
                    ]
                )
            )
        else:
            print(
                canonical(
                    {
                        "key_id": key_id(key),
                        "trust_directory": str(directory()),
                        "draws": db.execute("SELECT COUNT(*) FROM draws").fetchone()[0],
                        "reports": db.execute(
                            "SELECT COUNT(*) FROM reports"
                        ).fetchone()[0],
                    }
                )
            )
    return 0
