"""Freeze approved warehouse projections as private Telryn source files."""

import csv
import hashlib
import re
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import complaints, integrations_coverage, policy, transcripts, weekly
from .artifacts import new_directory
from .contracts import (
    InputError,
    canonical,
    decode_json,
    integer,
    mapping,
    read_json,
    sequence,
    timestamp,
)
from .integrations_http import setting

PROVIDERS = ("snowflake", "bigquery", "databricks")
POPULATION_COLUMNS = (*weekly.CASE_COLUMNS, *weekly.DIMENSIONS)
SOURCE_COLUMNS = {
    "conversations": (*POPULATION_COLUMNS, "completed_at", "messages_json"),
    "register": complaints.REGISTER,
    "actions": complaints.ACTIONS,
}
OUTPUTS = {
    "population.csv",
    "population.csv.coverage.json",
    "transcripts.jsonl",
    "register.csv",
    "actions.csv",
}
MAX_BYTES = 32 * 1024 * 1024
IDENTIFIER = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]{0,127}")
PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
KEY = re.compile(r"[a-zA-Z0-9_.-]{1,128}")
HEX = re.compile(r"[0-9a-f]{64}")


def _plan(path):
    cfg = policy.read_input(path, "warehouse plan")
    policy.fields(
        cfg,
        (
            "schema",
            "provider",
            "scope",
            "scope_key",
            "week",
            "snapshot_at",
            "max_rows",
            "sources",
            *(("max_bytes_billed",) if cfg.get("provider") == "bigquery" else ()),
        ),
        "warehouse plan",
    )
    provider = cfg["provider"]
    if provider not in PROVIDERS:
        raise InputError("select snowflake, bigquery or databricks")
    policy.label(cfg["scope"], "warehouse scope")
    _safe(cfg["scope_key"], KEY, "warehouse scope key")
    week = weekly._week(cfg["week"])
    first = datetime.fromisoformat(week).replace(tzinfo=UTC)
    end = first + timedelta(days=7)
    if first.weekday() != 0:
        raise InputError("choose a Monday for the audit week")
    instant = timestamp(cfg["snapshot_at"], "warehouse snapshot")
    when, fraction = instant
    if fraction or (provider == "snowflake" and when.microsecond % 1000):
        raise InputError("snapshot is finer than the warehouse time travel precision")
    if not (end <= when <= datetime.now(UTC)):
        raise InputError("warehouse snapshot must be between the week end and now")
    cfg["snapshot_at"] = when.astimezone(UTC).isoformat(
        sep=" ", timespec="microseconds"
    )
    if integer(cfg["max_rows"], "warehouse row limit", minimum=1) > 200_000:
        raise InputError("warehouse row limit must be at most 200000")
    if provider == "bigquery":
        integer(cfg["max_bytes_billed"], "BigQuery bytes billed limit", minimum=1)
    policy.fields(cfg["sources"], SOURCE_COLUMNS, "named warehouse sources")
    for name, source in cfg["sources"].items():
        policy.fields(source, ("table", "complete_through"), "warehouse source")
        _table(cfg, source["table"])
        complete = source["complete_through"]
        if (
            complete is not None
            and timestamp(complete, "source completeness") > instant
        ):
            raise InputError("warehouse completeness cannot exceed snapshot time")
        if name == "conversations" and (
            complete is None or timestamp(complete, "cohort completeness")[0] < end
        ):
            raise InputError(
                "the source owner must affirm complete conversation coverage through the week end"
            )
    return cfg, first, end


def _safe(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise InputError(f"invalid {label}")
    return value


def _table(cfg, name):
    parts = name.split(".") if isinstance(name, str) else []
    if len(parts) != 3:
        raise InputError("use a fully-qualified three-part warehouse table name")
    provider = cfg["provider"]
    for i, part in enumerate(parts):
        _safe(
            part,
            PROJECT if provider == "bigquery" and i == 0 else IDENTIFIER,
            "warehouse identifier",
        )
    return f"`{name}`" if provider == "bigquery" else name


def _query(cfg, kind):
    table = _table(cfg, cfg["sources"][kind]["table"])
    # Only validated identifiers and normalized timestamps enter the SQL text.
    # They cannot introduce quoting, comments, paths, expressions or statements.
    as_of = cfg["snapshot_at"]
    provider = cfg["provider"]
    if provider == "snowflake":
        historical = f"AT (TIMESTAMP => '{as_of}'::TIMESTAMP_TZ)"
    elif provider == "bigquery":
        historical = f"FOR SYSTEM_TIME AS OF TIMESTAMP '{as_of}'"
    else:
        historical = f"TIMESTAMP AS OF '{as_of}'"
    filters = f"scope_key = '{cfg['scope_key']}'"
    if kind == "conversations":
        filters += f" AND audit_week = '{cfg['week']}'"
    columns = ", ".join(SOURCE_COLUMNS[kind])
    return f"SELECT {columns} FROM {table} {historical} WHERE {filters} LIMIT {cfg['max_rows'] + 1}"


@contextmanager
def _reader(cfg):
    provider = cfg["provider"]
    if provider == "bigquery":
        from google.cloud import bigquery

        with closing(
            bigquery.Client(project=setting("TELRYN_BIGQUERY_PROJECT"))
        ) as client:

            def select(query):
                job = client.query(
                    query,
                    location=setting("TELRYN_BIGQUERY_LOCATION"),
                    timeout=30,
                    job_config=bigquery.QueryJobConfig(
                        maximum_bytes_billed=cfg["max_bytes_billed"],
                        use_query_cache=False,
                        use_legacy_sql=False,
                    ),
                )
                return job.job_id, (
                    tuple(row.values()) for row in job.result(timeout=120)
                )

            yield select
    else:
        if provider == "snowflake":
            from snowflake import connector

            connection = connector.connect(
                connection_name=setting("TELRYN_SNOWFLAKE_CONNECTION"),
                login_timeout=30,
                network_timeout=120,
                session_parameters={"STATEMENT_TIMEOUT_IN_SECONDS": 120},
                application="Telryn",
            )
        else:
            from databricks import sql

            hostname = setting("DATABRICKS_SERVER_HOSTNAME")
            _safe(hostname, re.compile(r"[a-zA-Z0-9.-]{1,253}"), "Databricks hostname")
            connection = sql.connect(
                server_hostname=hostname,
                http_path=setting("DATABRICKS_HTTP_PATH"),
                access_token=setting("DATABRICKS_TOKEN"),
                user_agent_entry="Telryn",
                enable_telemetry=False,
            )
        with closing(connection), closing(connection.cursor()) as cursor:

            def select(query):
                cursor.execute(query)
                identifier = (
                    cursor.sfqid if provider == "snowflake" else cursor.query_id
                )
                return identifier, iter(lambda: cursor.fetchone(), None)

            yield select


@contextmanager
def _rows(reader, cfg, kind):
    query = _query(cfg, kind)
    try:
        query_id, results = reader(query)
        policy.label(query_id, "provider query ID", limit=256)

        def convert():
            for index, row in enumerate(results):
                if index >= cfg["max_rows"]:
                    raise InputError(
                        f"warehouse {kind} exceeds the configured row limit"
                    )
                if len(row) != len(SOURCE_COLUMNS[kind]):
                    raise InputError(
                        f"warehouse {kind} row has the wrong number of columns"
                    )
                yield dict(zip(SOURCE_COLUMNS[kind], row, strict=True))

        yield query, query_id, convert()
    except InputError:
        raise
    except Exception as exc:
        raise InputError(
            f"warehouse {kind} read failed; check its scoped snapshot and query identity"
        ) from exc


def _csv(path, rows, columns):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, columns, lineterminator="\n", extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            if stream.tell() > MAX_BYTES:
                raise InputError("warehouse CSV exceeds the 32 MiB evidence limit")


def _json(path, obj):
    path.write_text(canonical(obj) + "\n", encoding="utf-8")


def _sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _conversation(raw, first, end):
    row = {k: policy.label(raw[k], k) for k in POPULATION_COLUMNS if k != "screen_flag"}
    flag = raw["screen_flag"]
    if type(flag) is not bool:
        raise InputError("warehouse screen_flag must be a SQL BOOLEAN")
    row["screen_flag"] = flag
    closed = policy.label(raw["completed_at"], "completion time", limit=64)
    if not first <= timestamp(closed, "completion time")[0] < end:
        raise InputError(
            "warehouse completed conversation is outside its projected audit week"
        )
    messages = raw["messages_json"]
    if not isinstance(messages, str) or len(messages.encode("utf-8")) > 65536:
        raise InputError("messages_json must be UTF-8 text up to 64 KiB")
    messages = sequence(
        decode_json(messages, "warehouse conversation"), "warehouse messages"
    )
    if not messages:
        raise InputError("warehouse conversation needs at least one message")
    for message in messages:
        policy.fields(message, ("role", "text"), "warehouse message")
        if (
            not isinstance(message["role"], str)
            or message["role"] not in transcripts.ROLES
            or not isinstance(message["text"], str)
            or not message["text"].strip()
        ):
            raise InputError(
                "warehouse messages require a valid role and nonempty text"
            )
    return row, closed, messages


def _write_sources(stage, cfg, first, end, reader):
    queried = {}
    cutoff = timestamp(cfg["snapshot_at"], "warehouse snapshot")
    population = {}
    total_message_bytes = 0
    for kind in SOURCE_COLUMNS:
        rows = {}
        ledger_bytes = 0
        with _rows(reader, cfg, kind) as (query, query_id, dataset):
            for raw in dataset:
                if kind == "conversations":
                    row, closed, messages = _conversation(raw, first, end)
                    identity = row["case_id"]
                    if identity in population:
                        raise InputError("duplicate frozen warehouse case")
                    population[identity] = (row, closed, messages)
                    total_message_bytes += len(canonical([row, closed, messages]))
                    if total_message_bytes > MAX_BYTES:
                        raise InputError(
                            "warehouse conversation messages exceed the 32 MiB evidence limit"
                        )
                    continue
                row = {k: policy.label(v, k) for k, v in raw.items()}
                identity = row[SOURCE_COLUMNS[kind][1]]
                if (
                    identity in rows
                    or timestamp(row["recorded_at"], "ledger timestamp") > cutoff
                ):
                    raise InputError("duplicate or future warehouse ledger event")
                if kind == "actions" and row["status"] not in (
                    "requested",
                    "accepted",
                    "completed",
                ):
                    raise InputError("invalid warehouse owner action")
                rows[identity] = row
                ledger_bytes += sum(
                    len(value.encode("utf-8")) for value in row.values()
                )
                if ledger_bytes > MAX_BYTES:
                    raise InputError(
                        "warehouse ledger exceeds the 32 MiB evidence limit"
                    )
        queried[kind] = {
            **cfg["sources"][kind],
            "query_id": query_id,
            "sql_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "rows": len(population) if kind == "conversations" else len(rows),
        }
        if kind != "conversations":
            _csv(
                stage / f"{kind}.csv",
                (rows[i] for i in sorted(rows)),
                SOURCE_COLUMNS[kind],
            )
    ordered = [population[i] for i in sorted(population)]
    _csv(stage / "population.csv", (row for row, _, _ in ordered), POPULATION_COLUMNS)
    with (stage / "transcripts.jsonl").open("w", encoding="utf-8") as stream:
        for item, closed, messages in ordered:
            row = {
                key: item[key]
                for key in ("case_id", "revision", "evidence_ref", *weekly.DIMENSIONS)
            }
            stream.write(
                canonical({**row, "as_of": closed, "messages": messages}) + "\n"
            )
            if stream.tell() > MAX_BYTES:
                raise InputError(
                    "warehouse transcript file exceeds the 32 MiB evidence limit"
                )
    cohort = integrations_coverage.new_receipt(
        first, end, cfg["scope"], "completion-snapshot"
    )
    cohort.update(
        dimensions=list(weekly.DIMENSIONS),
        completed_at={row["case_id"]: closed for row, closed, _ in ordered},
        scanned=len(ordered),
        eligible=len(ordered),
    )
    cohort["membership"] = integrations_coverage.membership(
        [row for row, _, _ in ordered], cohort["completed_at"]
    )
    _json(stage / "population.csv.coverage.json", cohort)
    for kind in ("register", "actions"):
        source = queried[kind]
        source["complaint_source"] = {
            "file": f"{kind}.csv",
            "source_ref": f"{cfg['provider']}/{source['table']}/{source['query_id']}",
            "complete_through": source["complete_through"],
        }
    return queried


def capture(plan, out, *, reader=None):
    cfg, first, end = _plan(plan)
    with new_directory(out) as stage:
        try:
            if reader is not None:
                queried = _write_sources(stage, cfg, first, end, reader)
            else:
                with _reader(cfg) as connected:
                    queried = _write_sources(stage, cfg, first, end, connected)
        except ImportError as exc:
            raise InputError(f"install the optional SDK for {cfg['provider']}") from exc
        except InputError:
            raise
        except Exception as exc:
            raise InputError(
                "warehouse capture failed without publishing a snapshot"
            ) from exc
        receipt = {
            "schema": 1,
            "provider": cfg["provider"],
            "scope": cfg["scope"],
            "scope_key": cfg["scope_key"],
            "week": cfg["week"],
            "snapshot_at": cfg["snapshot_at"],
            "sources": queried,
            "files": {name: _sha(stage / name) for name in sorted(OUTPUTS)},
        }
        _json(stage / "snapshot.json", receipt)
    return _sha(out / "snapshot.json")


def verify(path, expected):
    _safe(expected, HEX, "warehouse snapshot digest")
    receipt = path / "snapshot.json"
    if (
        not receipt.is_file()
        or receipt.is_symlink()
        or receipt.stat().st_size > MAX_BYTES
        or _sha(receipt) != expected
    ):
        raise InputError("warehouse snapshot receipt differs from the approved digest")
    value = read_json(receipt, "warehouse snapshot receipt")
    files = mapping(value.get("files"), "warehouse snapshot file digests")
    if set(files) != OUTPUTS:
        raise InputError("warehouse snapshot receipt names unexpected files")
    for name, digest in files.items():
        data = path / name
        _safe(digest, HEX, "warehouse file digest")
        if (
            not data.is_file()
            or data.is_symlink()
            or data.stat().st_size > MAX_BYTES
            or _sha(data) != digest
        ):
            raise InputError(f"warehouse {name} differs from the snapshot")
    return value


def register_arguments(commands):
    command = commands.add_parser(
        "warehouse", help="capture and verify read-only warehouse snapshots"
    )
    sub = command.add_subparsers(dest="warehouse_command", required=True)
    pull = sub.add_parser(
        "capture", help="read three scoped projections as of one snapshot"
    )
    pull.add_argument("plan", type=Path)
    pull.add_argument("--out", type=Path, required=True)
    check = sub.add_parser(
        "verify", help="verify snapshot files against the approved digest"
    )
    check.add_argument("snapshot", type=Path)
    check.add_argument("--sha256", required=True)


def execute(args):
    if args.warehouse_command == "capture":
        print(
            canonical(
                {"snapshot": str(args.out), "sha256": capture(args.plan, args.out)}
            )
        )
    else:
        print(canonical(verify(args.snapshot, args.sha256)))
    return 0
