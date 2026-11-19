"""Read an immutable Sierra conversation export delivered to customer-owned S3."""

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .contracts import InputError
from .integrations_http import slug

MAX_BYTES = 64 * 1024 * 1024


def register_arguments(actions) -> None:
    parser = actions.add_parser(
        "pull-sierra", help="read a versioned Sierra metadata export from customer S3"
    )
    parser.add_argument("--bucket", required=True)
    parser.add_argument(
        "--bucket-owner",
        required=True,
        help="12-digit AWS account that owns the bucket",
    )
    parser.add_argument("--key", required=True)
    parser.add_argument(
        "--version-id",
        required=True,
        help="immutable S3 object version ID from delivery",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        required=True,
        help="independently reconciled closed conversation count",
    )
    parser.add_argument("--sha256", help="optional SHA-256 of the delivered CSV")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--id-column", default="conversation_id")
    parser.add_argument("--revision-column", default="revision")
    parser.add_argument("--evidence-column", default="evidence_ref")
    parser.add_argument(
        "--flag-column", help="optional field with explicit true/false screen results"
    )
    parser.add_argument("--out", required=True, type=Path)


def _request(
    operation: str, identity: list[str], local_file: Path | None = None
) -> dict:
    command = [
        "aws",
        "s3api",
        operation,
        *identity,
        "--output",
        "json",
        "--no-cli-pager",
    ]
    if local_file is not None:
        command.append(str(local_file))
    env = {
        **os.environ,
        "AWS_CLI_AUTO_PROMPT": "off",
        "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "true",
    }
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=120,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise InputError("install AWS CLI v2 to read the Sierra S3 delivery") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise InputError(
            f"S3 {operation} failed. Check your AWS profile, region and object version."
        ) from exc
    try:
        metadata = json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise InputError("AWS CLI returned invalid object metadata") from exc
    if not isinstance(metadata, dict):
        raise InputError("AWS CLI returned invalid object metadata")
    return metadata


def execute(args: argparse.Namespace) -> int:
    from .integrations import FIELDS, agent_export, write_csv

    if args.out.exists() or args.out.is_symlink():
        raise FileExistsError(f"output file already exists: {args.out}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", args.bucket):
        raise InputError("provide a general-purpose S3 bucket name")
    if not re.fullmatch(r"[0-9]{12}", args.bucket_owner):
        raise InputError("provide the 12-digit expected S3 bucket owner")
    if (
        not args.version_id.strip()
        or args.version_id.lower() == "null"
        or not args.key.strip()
    ):
        raise InputError("provide an object key and a non-null S3 version ID")
    if args.expected_count < 0:
        raise InputError("expected count must be nonnegative")
    if args.sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", args.sha256):
        raise InputError("provide a lowercase SHA-256 digest")
    slug(args.workspace, "workspace")
    # AWS CLI interprets URL-prefixed parameters as files. Accept only literal object references.
    if any(
        re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value) or not value.isprintable()
        for value in (args.key, args.version_id)
    ):
        raise InputError("use a literal S3 object key and version ID")
    identity = [
        f"--bucket={args.bucket}",
        f"--expected-bucket-owner={args.bucket_owner}",
        f"--key={args.key}",
        f"--version-id={args.version_id}",
    ]
    head = _request("head-object", identity)
    size = head.get("ContentLength")
    if (
        head.get("VersionId") != args.version_id
        or type(size) is not int
        or not 0 < size <= MAX_BYTES
    ):
        raise InputError(
            "S3 delivery must be the requested immutable version and at most 64 MiB"
        )
    with tempfile.TemporaryDirectory(prefix="telryn-sierra-") as tmp:
        source = Path(tmp) / "delivery.csv"
        result = _request("get-object", identity, source)
        if (
            result.get("VersionId") != args.version_id
            or result.get("ContentLength") != size
            or source.stat().st_size != size
        ):
            raise InputError(
                "S3 download does not match the recorded version and length"
            )
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if args.sha256 and args.sha256 != digest:
            raise InputError("Sierra S3 delivery failed its SHA-256 check")
        with source.open(encoding="utf-8-sig", newline="") as stream:
            columns = next(csv.reader(stream, strict=True), [])
        allowed = [args.id_column, args.revision_column, args.evidence_column]
        if args.flag_column:
            allowed.append(args.flag_column)
        if sorted(columns) != sorted(allowed):
            raise InputError(
                "use a metadata-only Sierra delivery with exactly the mapped columns"
            )
        rows = agent_export(
            argparse.Namespace(**vars(args), vendor="sierra", export=source)
        )
        if len(rows) != args.expected_count:
            raise InputError(
                "Sierra S3 record count differs from the expected cohort count"
            )
        write_csv(args.out, FIELDS, rows)
    print(
        f"Read {len(rows)} Sierra records from the pinned S3 delivery. Source SHA-256 {digest}."
    )
    return 0
