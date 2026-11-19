"""Local filesystem operations shared by artifact publishers."""

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def new_directory(target: Path):
    """Build a private bundle alongside its destination, then publish it at once."""
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"output directory already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".telryn-", dir=target.parent))
    try:
        yield staging
        for file in staging.iterdir():
            file.chmod(0o600)
            with file.open("rb") as stream:
                os.fsync(stream.fileno())
        sync_directory(staging)
        staging.rename(target)
        sync_directory(target.parent)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
