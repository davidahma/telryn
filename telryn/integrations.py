"""Explicit live imports and opt-in handoff of a frozen weekly review sample."""

import csv
import math
import os
import re
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path

from . import integrations_coverage, integrations_sierra, integrations_transcripts
from .artifacts import sync_directory
from .contracts import InputError
from .integrations_http import Client, query, slug
from .integrations_sources import (
    instant,
    iso,
    object_at,
    population,
    records,
    source_revision,
    vendor_id,
)

VENDORS = ("intercom", "zendesk", "salesforce", "freshdesk", "decagon")
FIELDS = ("case_id", "revision", "evidence_ref", "screen_flag")


def register_arguments(commands):
    command = commands.add_parser(
        "integrations", help="import vendor records or hand off a weekly sample"
    )
    actions = command.add_subparsers(dest="integration_action", required=True)
    integrations_sierra.register_arguments(actions)
    integrations_transcripts.register_arguments(actions)
    cohort = actions.add_parser(
        "cohort",
        help="build a weekly cohort from an authoritative completion-event export",
    )
    cohort.add_argument("events", type=Path)
    cohort.add_argument("--since", required=True)
    cohort.add_argument("--until", required=True)
    cohort.add_argument(
        "--scope", required=True, help="source and population scanned by this ledger"
    )
    cohort.add_argument("--out", required=True, type=Path)
    pull = actions.add_parser(
        "pull", help="fetch cases resolved or closed in a date range"
    )
    pull.add_argument("vendor", choices=VENDORS)
    pull.add_argument(
        "--since",
        required=True,
        metavar="YYYY-MM-DD",
        help="inclusive UTC start date",
    )
    pull.add_argument(
        "--until",
        required=True,
        metavar="YYYY-MM-DD",
        help="exclusive UTC end date",
    )
    pull.add_argument(
        "--expected-count",
        type=int,
        help="independent number of eligible records in this cohort",
    )
    pull.add_argument(
        "--fin-only", action="store_true", help="restrict Intercom to Fin conversations"
    )
    pull.add_argument(
        "--flag-tag", help="customer screen tag; use the same definition in the audit"
    )
    pull.add_argument(
        "--closed-tag", help="Decagon tag marking a completed conversation"
    )
    pull.add_argument(
        "--basis",
        choices=("completion", "created"),
        default="completion",
        help="completion event time (or explicitly request v0.4 creation cohort)",
    )
    pull.add_argument(
        "--dimension",
        action="append",
        default=[],
        help="map channel, agent_type or product to a source field, e.g. channel=via.channel",
    )
    pull.add_argument("--out", required=True, type=Path)
    qa = actions.add_parser(
        "reviewed-zendesk",
        help="match ordinary human QA reviews to a Zendesk population",
    )
    qa.add_argument("population", type=Path)
    qa.add_argument("--workspace", required=True, help="Zendesk QA workspace ID")
    qa.add_argument(
        "--scorecard",
        required=True,
        help="native scorecard ID for ordinary human reviews",
    )
    qa.add_argument("--since", required=True, metavar="YYYY-MM-DD")
    qa.add_argument("--until", required=True, metavar="YYYY-MM-DD")
    qa.add_argument("--out", required=True, type=Path)
    load = actions.add_parser(
        "import-agent", help="map a customer-approved closed-conversation CSV"
    )
    load.add_argument("vendor", choices=("decagon", "sierra", "fin"))
    load.add_argument("export", type=Path)
    load.add_argument("--workspace", required=True)
    load.add_argument("--id-column", default="conversation_id")
    load.add_argument("--revision-column", default="revision")
    load.add_argument("--evidence-column", default="evidence_ref")
    load.add_argument(
        "--flag-column", help="optional field with explicit true/false screen results"
    )
    load.add_argument("--out", required=True, type=Path)
    handoff = actions.add_parser(
        "handoff", help="preview or apply a review tag to the sampled records"
    )
    handoff.add_argument("vendor", choices=("intercom", "zendesk"))
    handoff.add_argument("report", type=Path)
    handoff.add_argument(
        "--tag", required=True, help="Zendesk tag name or existing Intercom tag ID"
    )
    handoff.add_argument(
        "--apply", action="store_true", help="apply tag in the configured tenant"
    )
    maestro = actions.add_parser(
        "maestroqa", help="export sampled source ticket IDs for MaestroQA assignment"
    )
    maestro.add_argument("report", type=Path)
    maestro.add_argument("--source", required=True, choices=(*VENDORS, "sierra", "fin"))
    maestro.add_argument("--workspace", required=True)
    maestro.add_argument("--out", required=True, type=Path)


def write_csv(path, fields, rows):
    """Publish a complete private CSV without overwriting existing snapshots."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".telryn-import-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            if fields:
                writer.writerow(fields)
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, path)
        sync_directory(path.parent)
    finally:
        os.unlink(name)


def _date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise InputError("use a UTC date in YYYY-MM-DD format")
    return datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC)


def agent_export(args):
    scope = slug(args.workspace, "workspace")
    columns = [args.id_column, args.revision_column, args.evidence_column]
    if args.flag_column:
        columns.append(args.flag_column)
    if len(set(columns)) != len(columns):
        raise InputError("map each export column only once")
    result = {}
    with args.export.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        header = reader.fieldnames or []
        if len(set(header)) != len(header) or not set(columns) <= set(header):
            raise InputError("agent CSV headers do not match the mapping")
        for row in reader:
            if None in row or None in row.values():
                raise InputError("agent CSV row does not match its header")
            key = vendor_id(row[args.id_column])
            rev, evidence = row[args.revision_column], row[args.evidence_column]
            for value in (rev, evidence):
                if (
                    not value.strip()
                    or value != value.strip()
                    or not value.isprintable()
                    or value.lstrip().startswith(("=", "+", "-", "@"))
                ):
                    raise InputError(
                        "use printable, spreadsheet-safe revision and evidence references"
                    )
            flag = row[args.flag_column].lower() if args.flag_column else "false"
            if flag not in ("true", "false") or key in result:
                raise InputError("duplicate agent ID or invalid screen flag")
            result[key] = (f"{args.vendor}:{scope}:{key}", rev, evidence, flag)
    return [result[key] for key in sorted(result)]


def reviewed_zendesk(client, args, since, until):
    if not re.fullmatch(r"[1-9][0-9]*", args.workspace):
        raise InputError("use the numeric Zendesk QA workspace ID")
    prefix = f"zendesk:{client.scope}:"
    with args.population.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        integrations_coverage.projection(reader.fieldnames, FIELDS)
        eligible = set()
        for row in reader:
            key = row["case_id"]
            if (
                not key
                or not key.startswith(prefix)
                or key in eligible
                or not key.removeprefix(prefix).isdecimal()
            ):
                raise InputError(
                    "population contains an invalid, duplicate, or foreign ticket"
                )
            eligible.add(key)
    selected, walked, seen, total = set(), 0, set(), None
    for page in range(1, 100001):
        path = query(
            f"/qa/api/export/workspace/{args.workspace}/reviews",
            fromDate=iso(since),
            toDate=iso(until),
            page=page,
            pageSize=100,
        )
        data, _ = client.request(path)
        meta = object_at(data, "pagination")
        # QA's gRPC JSON may encode int64 as decimal strings.
        if (
            str(object_at(meta, "page")) != str(page)
            or not str(object_at(meta, "total")).isdecimal()
        ):
            raise InputError("invalid Zendesk QA pagination")
        size = int(meta["total"])
        if total is not None and size != total:
            raise InputError("Zendesk QA export changed during pagination")
        total = size
        items = records(data, "conversations")
        for record in items:
            external = vendor_id(object_at(record, "externalId"))
            url = object_at(record, "externalUrl")
            if (
                not isinstance(url, str)
                or str(object_at(record, "workspaceId")) != args.workspace
                or (url, external) in seen
            ):
                raise InputError(
                    "unexpected workspace or repeated Zendesk QA conversation"
                )
            seen.add((url, external))
            if url != f"https://{client.host}/agent/tickets/{external}":
                # Workspaces can contain connections to other help desks.
                if prefix + external in eligible:
                    raise InputError(
                        "ambiguous Zendesk ticket source; use a single-source QA workspace"
                    )
                continue
            if prefix + external not in eligible:
                continue
            if record.get("sourceType", "zendesk") != "zendesk":
                raise InputError("Zendesk QA source type conflicts with the ticket URL")
            for review in records(record, "reviews"):
                if (
                    str(object_at(object_at(review, "scorecard"), "id"))
                    != args.scorecard
                ):
                    continue
                created = instant(
                    review.get("createdISO") or object_at(review, "created")
                )
                if not since <= created < until:
                    continue
                updated = instant(
                    review.get("updatedISO") or object_at(review, "updated")
                )
                reviewer = object_at(review, "reviewer")
                score = object_at(review, "score")
                if (
                    not created <= updated < until
                    or not isinstance(reviewer, dict)
                    or not any(
                        isinstance(reviewer.get(k), str) and reviewer[k].strip()
                        for k in ("id", "email")
                    )
                    or type(score) not in (int, float)
                    or not math.isfinite(score)
                ):
                    raise InputError(
                        "cannot establish a scored human review by the QA cutoff; supply an archived human review ledger"
                    )
                selected.add(prefix + external)
        walked += len(items)
        if walked == total:
            return [(key,) for key in sorted(selected)]
        if walked > total or not items:
            break
    raise InputError("Zendesk QA returned an incomplete review export")


def _cases(state, provider, workspace):
    prefix = f"{provider}:{workspace}:"
    selected = {}
    for row in state["cases"]:
        case = row["case_id"]
        if not case.startswith(prefix):
            raise InputError(
                "sample contains a different source or workspace; handoff stopped"
            )
        key = vendor_id(case.removeprefix(prefix))
        if key in selected:
            raise InputError("duplicate sampled record")
        selected[key] = row["revision"]
    return selected


def _record(client, key):
    is_intercom = client.provider == "intercom"
    path = f"/conversations/{key}" if is_intercom else f"/api/v2/tickets/{key}.json"
    data, _ = client.request(path)
    record = data if is_intercom else object_at(data, "ticket")
    if vendor_id(object_at(record, "id")) != key:
        raise InputError("handoff received a different record")
    tags = object_at(record, "tags")
    if is_intercom:
        tags = object_at(tags, "tags")
        if not isinstance(tags, list):
            raise InputError("invalid Intercom tag list")
        tags = [vendor_id(object_at(item, "id")) for item in tags]
    if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
        raise InputError("invalid vendor tag list")
    return record, tags


def _zendesk_tag(client, pending, tag):
    for start in range(0, len(pending), 100):
        batch = pending[start : start + 100]
        updates = [
            {
                "id": int(key),
                "updated_stamp": revision,
                "safe_update": True,
                "additional_tags": [tag],
            }
            for key, revision in batch
        ]
        queued, _ = client.request(
            "/api/v2/tickets/update_many.json", method="PUT", body={"tickets": updates}
        )
        job = slug(object_at(object_at(queued, "job_status"), "id"), "Zendesk job ID")
        for _ in range(30):
            reply, _ = client.request(f"/api/v2/job_statuses/{job}.json")
            status = object_at(reply, "job_status")
            stage = object_at(status, "status")
            if stage == "completed":
                results = object_at(status, "results")
                expected = {key for key, _ in batch}
                if (
                    not isinstance(results, list)
                    or len(results) != len(batch)
                    or any(
                        not isinstance(row, dict) or row.get("success") is not True
                        for row in results
                    )
                    or {str(row.get("id")) for row in results} != expected
                ):
                    raise InputError(
                        f"Zendesk job {job} did not confirm every tag; rerun to reconcile"
                    )
                for key, _revision in batch:
                    _, current_tags = _record(client, key)
                    if tag not in current_tags:
                        raise InputError(
                            f"Zendesk job {job} finished without the requested tag on {key}"
                        )
                break
            if stage not in {"queued", "working"}:
                raise InputError(f"Zendesk job {job} failed; rerun to reconcile")
            time.sleep(2)
        else:
            raise InputError(
                f"Zendesk job {job} is still running; check its status before retrying"
            )


def handoff(client, state, tag, apply):
    cases = _cases(state, client.provider, client.scope)
    is_intercom = client.provider == "intercom"
    if not re.fullmatch(r"[0-9]+" if is_intercom else r"[a-z0-9][a-z0-9_-]{0,79}", tag):
        raise InputError(
            "supply an existing numeric Intercom tag ID or a Zendesk tag name"
        )
    if any(not re.fullmatch(r"[1-9][0-9]{0,39}", key) for key in cases):
        raise InputError("unexpected native ticket ID")
    if not apply:
        return len(cases), 0
    admin = client.intercom_identity() if is_intercom else None

    def needs_tag(key, revision):
        record, tags = _record(client, key)
        if tag in tags:
            return False
        if source_revision(object_at(record, "updated_at")) != revision:
            raise InputError(
                f"source revision changed for {key}; inspect the frozen record before handoff"
            )
        return True

    pending = [(key, rev) for key, rev in cases.items() if needs_tag(key, rev)]
    if is_intercom:
        tagged = 0
        for key, revision in pending:
            # This endpoint has no revision precondition. Recheck after preflight.
            if not needs_tag(key, revision):
                continue
            response, _ = client.request(
                f"/conversations/{key}/tags",
                method="POST",
                body={"id": tag, "admin_id": admin},
            )
            if vendor_id(object_at(response, "id")) != tag:
                raise InputError("Intercom did not confirm tag; rerun to reconcile")
            _, current_tags = _record(client, key)
            if tag not in current_tags:
                raise InputError(f"Intercom tag absent on {key}; rerun to reconcile")
            tagged += 1
        return len(cases), tagged
    _zendesk_tag(client, pending, tag)
    return len(cases), len(pending)


def execute(args):
    if args.integration_action == "pull-sierra":
        return integrations_sierra.execute(args)
    if args.integration_action == "transcripts":
        count = integrations_transcripts.fetch(
            Client(args.vendor), args.population, args.out
        )
        print(f"Fetched {count} case revisions to the private transcript file.")
        return 0
    if args.integration_action in {"pull", "reviewed-zendesk", "cohort"}:
        since, until = _date(args.since), _date(args.until)
        if not since < until <= datetime.now(UTC):
            raise InputError("choose a completed UTC interval with since before until")
        if args.integration_action == "cohort":
            rows, receipt = integrations_coverage.from_events(
                args.events, since, until, args.scope
            )
            integrations_coverage.publish(args.out, rows, receipt)
            print(
                f"Included {len(rows)} conversations. Scope and exclusions: {args.out.name}.coverage.json"
            )
            return 0
        if args.integration_action == "reviewed-zendesk":
            rows = reviewed_zendesk(Client("zendesk_qa"), args, since, until)
            write_csv(args.out, ("case_id",), rows)
            print(f"Matched {len(rows)} ordinarily reviewed Zendesk tickets.")
            return 0
        if args.vendor == "decagon" and args.expected_count is None:
            raise InputError(
                "Decagon needs --expected-count from an independent closed-conversation count"
            )
        rows, receipt = population(
            Client(args.vendor),
            since,
            until,
            fin_only=args.fin_only,
            flag_tag=args.flag_tag,
            closed_tag=args.closed_tag,
            basis=args.basis,
            dimension_specs=args.dimension,
        )
        if args.expected_count is not None and len(rows) != args.expected_count:
            raise InputError("source count does not match --expected-count")
        integrations_coverage.publish(args.out, rows, receipt)
        print(
            f"Included {len(rows)} {args.vendor} conversations. Scope and exclusions: {args.out.name}.coverage.json"
        )
    elif args.integration_action == "import-agent":
        rows = agent_export(args)
        write_csv(args.out, FIELDS, rows)
        print(f"Mapped {len(rows)} {args.vendor} records.")
    else:
        from .weekly_report import read_state

        state = read_state(args.report)
        if args.integration_action == "maestroqa":
            cases = _cases(state, args.source, args.workspace)
            write_csv(args.out, (), ((key,) for key in cases))
            print(f"Exported {len(cases)} source ticket IDs for MaestroQA.")
        else:
            client = Client(args.vendor)
            total, tagged = handoff(client, state, args.tag, args.apply)
            if args.apply:
                print(
                    f"Confirmed review tag on {total} records ({tagged} newly tagged)."
                )
            else:
                print(
                    f"Preview: {total} selected records in {client.scope}. Use --apply to add the review tag."
                )
    return 0
