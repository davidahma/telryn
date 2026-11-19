"""Reconcile completion episodes from supported vendor APIs."""

import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from .contracts import InputError, timestamp
from .integrations_http import query, slug


def object_at(value, field):
    if not isinstance(value, dict) or field not in value:
        raise InputError(f"vendor response missing {field}")
    return value[field]


def records(value, field):
    rows = object_at(value, field) if field else value
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise InputError("vendor response must contain a record list")
    return rows


def boolean(value):
    if type(value) is not bool:
        raise InputError("missing vendor completion indicator")
    return value


def cursor_next(value, previous):
    if (
        not isinstance(value, (str, int))
        or isinstance(value, bool)
        or not str(value)
        or str(value) in previous
    ):
        raise InputError("missing or repeated vendor page cursor")
    previous.add(str(value))
    return str(value)


def instant(value):
    if type(value) is int:
        return datetime.fromtimestamp(value, UTC)
    parsed, _ = timestamp(value, "vendor timestamp")
    return parsed.astimezone(UTC)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def source_revision(value):
    """Preserve vendor sub-microsecond precision in the frozen revision."""
    if type(value) is int:
        return iso(instant(value))
    parsed, remainder = timestamp(value, "vendor revision")
    utc = parsed.astimezone(UTC)
    if remainder:
        return utc.isoformat(timespec="microseconds")[:-6] + remainder + "Z"
    return iso(utc)


def time_key(value):
    """UTC time plus any provider sub-microsecond digits, for exact cutoff comparisons."""
    return timestamp(source_revision(value), "source timestamp")


def vendor_id(value):
    if type(value) is int:
        value = str(value)
    return slug(value, "vendor record ID")


def tags(row, vendor):
    raw = object_at(row, "tags")
    if vendor == "intercom":
        raw = object_at(raw, "tags")
    if not isinstance(raw, list):
        raise InputError("vendor tags field must be a list")
    values = (
        [object_at(item, "name") for item in raw]
        if vendor in {"decagon", "intercom"}
        else raw
    )
    if not all(isinstance(item, str) for item in values):
        raise InputError("vendor tag names must be strings")
    return values


def _intercom(client, since, until, fin_only, legacy):
    client.intercom_identity()
    # Query the boundary seconds too. Apply the half-open UTC window locally.
    filters = [
        {
            "field": "created_at" if legacy else "updated_at",
            "operator": ">",
            "value": int(since.timestamp()) - 1,
        },
        {"field": "created_at", "operator": "<", "value": int(until.timestamp())},
    ]
    if legacy:
        filters.append({"field": "state", "operator": "=", "value": "closed"})
    if fin_only:
        filters.append(
            {"field": "ai_agent_participated", "operator": "=", "value": True}
        )
    pagination = {"per_page": 100}
    seen, count, total = set(), 0, None
    while True:
        body = {
            "query": {"operator": "AND", "value": filters},
            "pagination": pagination,
        }
        data, _ = client.request("/conversations/search", method="POST", body=body)
        reported = object_at(data, "total_count")
        if (
            type(reported) is not int
            or reported < 0
            or (total is not None and total != reported)
        ):
            raise InputError("Intercom result count changed during export")
        total = reported
        rows = records(data, "conversations")
        count += len(rows)
        yield from rows
        pages = object_at(data, "pages")
        if not isinstance(pages, dict):
            raise InputError("missing Intercom pagination")
        following = pages.get("next")
        if following is None:
            if count != total:
                raise InputError(
                    "Intercom returned a partial result set; use a narrower interval"
                )
            return
        if not rows:
            raise InputError("Intercom returned an empty nonterminal page")
        pagination["starting_after"] = cursor_next(
            object_at(following, "starting_after"), seen
        )


def _zendesk(client, since, until, legacy):
    params = {
        "filter[type]": "ticket",
        "page[size]": 100,
        "query": f"created>={iso(since)} created<{iso(until)} status:closed"
        if legacy
        else f"updated>={iso(since)} created<{iso(until)}",
    }
    seen = set()
    while True:
        data, _ = client.request(query("/api/v2/search/export", **params))
        rows = records(data, "results")
        yield from rows
        meta = object_at(data, "meta")
        if not boolean(object_at(meta, "has_more")):
            return
        if not rows:
            raise InputError("Zendesk returned an empty nonterminal page")
        params["page[after]"] = cursor_next(object_at(meta, "after_cursor"), seen)


def _salesforce(client, since, until, legacy, dimension_fields=()):
    version = "/services/data/v61.0/query"
    predicate = "IsClosed = true AND CreatedDate" if legacy else "LastModifiedDate"
    fields = ", ".join(
        dict.fromkeys(
            (
                "Id",
                "CreatedDate",
                "LastModifiedDate",
                "IsClosed",
                "ClosedDate",
                *dimension_fields,
            )
        )
    )
    statement = (
        f"SELECT {fields} FROM Case "
        f"WHERE {predicate} >= {iso(since)} AND CreatedDate < {iso(until)} ORDER BY Id"
    )
    path = query(version, q=statement)
    seen, count, total = set(), 0, None
    while True:
        data, _ = client.request(path)
        size = object_at(data, "totalSize")
        if type(size) is not int or size < 0 or (total is not None and size != total):
            raise InputError("Salesforce result count changed during export")
        total = size
        rows = records(data, "records")
        count += len(rows)
        yield from rows
        if boolean(object_at(data, "done")):
            if count != total:
                raise InputError("Salesforce returned a partial result set")
            return
        if not rows:
            raise InputError("Salesforce returned an empty nonterminal page")
        path = cursor_next(object_at(data, "nextRecordsUrl"), seen)
        if not re.fullmatch(re.escape(version) + r"/[a-zA-Z0-9_-]+", path):
            raise InputError("invalid Salesforce query locator")


def _freshdesk(client, since, until, legacy):
    params = {
        "updated_since": iso(since - timedelta(seconds=1)),
        "per_page": 100,
        "order_by": "created_at",
        "order_type": "desc",
    }
    if not legacy:
        params["include"] = "stats"
    previous_created = None
    for page in range(1, 301):
        data, headers = client.request(query("/api/v2/tickets", page=page, **params))
        rows = records(data, None)
        for row in rows:
            created = instant(object_at(row, "created_at"))
            if previous_created is not None and created > previous_created:
                raise InputError(
                    "Freshdesk creation order changed during export; reconcile against the source"
                )
            previous_created = created
            yield row
        if legacy and rows and previous_created < since:
            return
        link = headers.get("Link")
        if not link:
            if page == 300 and len(rows) >= 100:
                break
            return
        match = re.fullmatch(r'\s*<([^<>]+)>\s*;\s*rel="?next"?\s*', link, re.I)
        if match is None:
            raise InputError("unrecognized Freshdesk pagination header")
        target = urlsplit(match[1])
        if (
            target.scheme != "https"
            or target.netloc != client.host
            or target.path != "/api/v2/tickets"
            or parse_qs(target.query).get("page") != [str(page + 1)]
        ):
            raise InputError("invalid Freshdesk next page")
    raise InputError(
        "Freshdesk's 300-page limit reached; reconcile with a customer export"
    )


def _decagon(client, since, until, legacy):
    params = {
        "timestamp_filter": "created_at",
        "min_timestamp": int(since.timestamp()),
        "max_timestamp": int(until.timestamp()),
    }
    seen = set()
    while True:
        data, _ = client.request(query("/conversation/export", **params))
        rows = records(data, "conversations")
        yield from rows
        field_names = ("next_page_cursor", "next_cursor", "next_page_updated_after")
        cursors = {
            cursor_next(data[k], set())
            for k in field_names
            if data.get(k) is not None and data[k] != ""
        }
        if len(cursors) > 1:
            raise InputError("Decagon returned conflicting page cursors")
        more = boolean(data["has_more"]) if "has_more" in data else None
        if not cursors:
            if more or len(rows) >= 100 and not any(k in data for k in field_names):
                raise InputError("Decagon ended without a verifiable final page")
            return
        if more is False or not rows:
            raise InputError("inconsistent Decagon pagination")
        params["cursor"] = cursor_next(cursors.pop(), seen)
        time.sleep(1)  # Vendor accepts one request per second.


def zendesk_pages(client, path, name):
    params = {"page[size]": 100}
    if name == "audits":
        params["include_boundary_indicators"] = "true"
    seen = set()
    while True:
        data, _ = client.request(query(path, **params))
        rows = records(data, name)
        yield from rows
        if "meta" not in data:
            if (
                data.get("next_page", "missing") is None
                and str(data.get("count")) == str(len(rows))
                and not seen
            ):
                return
            raise InputError(f"cannot verify complete Zendesk {name} pagination")
        meta = object_at(data, "meta")
        if not boolean(object_at(meta, "has_more")):
            return
        if not rows:
            raise InputError("empty nonterminal Zendesk page")
        params["page[after]"] = cursor_next(object_at(meta, "after_cursor"), seen)


def _lifecycle(client, key, row):
    """Return open status, observed completion dates and whether history is complete."""
    vendor = client.provider
    if vendor == "zendesk":
        status = object_at(row, "status")
        current_open = status not in ("solved", "closed")
        times = []
        for audit in zendesk_pages(
            client, f"/api/v2/tickets/{key}/audits.json", "audits"
        ):
            if vendor_id(object_at(audit, "ticket_id")) != key:
                raise InputError("Zendesk returned another ticket's audit")
            for event in records(audit, "events"):
                if (
                    event.get("field_name") == "status"
                    and event.get("type") in ("Change", "Create")
                    and event.get("value") in ("solved", "closed")
                    and event.get("previous_value") not in ("solved", "closed")
                ):
                    times.append(source_revision(object_at(audit, "created_at")))
        known_status = status in ("new", "open", "pending", "hold", "solved", "closed")
        return current_open if known_status else None, times, True
    if vendor == "freshdesk":
        status = object_at(row, "status")
        if type(status) is not int:
            raise InputError("invalid Freshdesk status")
        stats = row.get("stats") or {}
        if not isinstance(stats, dict):
            raise InputError("invalid Freshdesk stats")
        # A later administrative closure completes the same resolution episode.
        dates = [stats.get("resolved_at") or stats.get("closed_at")]
        current_open = status not in (4, 5) if status in (2, 3, 4, 5, 6, 7) else None
    elif vendor == "salesforce":
        dates = [row.get("ClosedDate")]
        current_open = not boolean(object_at(row, "IsClosed"))
    else:
        stats = row.get("statistics") or {}
        if not isinstance(stats, dict):
            raise InputError("invalid Intercom statistics")
        dates = [stats.get("first_close_at"), stats.get("last_close_at")]
        status = object_at(row, "state")
        current_open = (
            status != "closed" if status in ("open", "snoozed", "closed") else None
        )
    return current_open, [source_revision(t) for t in dates if t is not None], False


def population(
    client,
    since,
    until,
    *,
    fin_only=False,
    flag_tag=None,
    closed_tag=None,
    basis="completion",
    dimension_specs=(),
):
    from .integrations_coverage import new_receipt, dimension_mapping, dimension_values

    dims = dimension_mapping(dimension_specs)
    legacy = basis == "created"
    vendor = client.provider
    if fin_only and vendor != "intercom":
        raise InputError("--fin-only applies to Intercom")
    if flag_tag and vendor == "salesforce":
        raise InputError("apply your Salesforce screen to the extracted file")
    if vendor == "decagon" and not legacy:
        raise InputError(
            "Decagon completion tags have no event timestamp. Use integrations cohort on a dated lifecycle ledger, or --basis created for the legacy cohort."
        )
    if (vendor == "decagon") != bool(closed_tag):
        raise InputError("--closed-tag is required for Decagon and applies only there")
    adapters = {
        "zendesk": _zendesk,
        "salesforce": _salesforce,
        "freshdesk": _freshdesk,
        "decagon": _decagon,
    }
    if vendor == "intercom":
        rows = _intercom(client, since, until, fin_only, legacy)
    elif vendor == "salesforce":
        rows = _salesforce(client, since, until, legacy, dims.values())
    else:
        rows = adapters[vendor](client, since, until, legacy)
    scope = (
        f"{vendor}/{client.scope}. Created in interval and terminal at retrieval; source filters can omit open records."
        if legacy
        else f"{vendor}/{client.scope}. Visible records created before interval end and updated since start. Open counts describe this scan at retrieval."
    )
    if fin_only:
        scope += " Fin participation only."
    if not legacy:
        scope += (
            " Zendesk status-change history, one record per conversation."
            if vendor == "zendesk"
            else " Snapshot completion timestamps; superseded or missing earlier episodes can be unavailable."
        )
    receipt = new_receipt(
        since, until, scope, "legacy-created" if legacy else "completion-snapshot"
    )
    receipt["dimensions"] = list(dims)
    output, seen = [], set()
    for row in rows:
        crm = vendor == "salesforce"
        created = instant(object_at(row, "CreatedDate" if crm else "created_at"))
        revision = source_revision(
            object_at(row, "LastModifiedDate" if crm else "updated_at")
        )
        updated = instant(revision)
        if updated < created:
            raise InputError("source revision precedes case creation")
        field = "Id" if crm else "conversation_id" if vendor == "decagon" else "id"
        key = vendor_id(object_at(row, field))
        if key in seen:
            raise InputError(f"{vendor} returned a duplicate case")
        seen.add(key)
        lower = created if legacy else updated
        if created >= until or lower < since:
            if vendor in ("freshdesk", "decagon"):
                continue
            if (
                vendor == "intercom"
                and created <= until
                and lower >= since - timedelta(seconds=1)
            ):
                continue
            raise InputError(f"{vendor} returned a record outside the requested dates")
        names = tags(row, vendor) if closed_tag or flag_tag else []
        if fin_only and object_at(row, "ai_agent_participated") is not True:
            raise InputError(
                "Intercom returned a conversation without Fin participation"
            )
        receipt["scanned"] += 1
        case_id = f"{vendor}:{client.scope}:{key}"
        evidence_ref = f"{case_id}@{revision}"
        if legacy:
            if vendor == "decagon":
                closed = closed_tag in names
            elif crm:
                closed = boolean(object_at(row, "IsClosed"))
            else:
                status = object_at(row, "state" if vendor == "intercom" else "status")
                closed = status == (5 if vendor == "freshdesk" else "closed")
            if not closed:
                if vendor not in ("freshdesk", "decagon"):
                    raise InputError(
                        "source returned a record outside the requested closed cohort"
                    )
                receipt["outside"] += 1
                continue
        else:
            current_open, times, full_history = _lifecycle(client, key, row)
            receipt["still_open"] += current_open is True
            if any(
                instant(t) < created or time_key(t) > time_key(revision) for t in times
            ):
                raise InputError(
                    "completion falls outside the source revision, retry the export"
                )
            qualifying = [t for t in times if since <= instant(t) < until]
            if not qualifying:
                if (
                    current_open is None
                    or (not current_open and not times)
                    or (
                        not full_history
                        and (any(instant(t) >= until for t in times) or current_open)
                    )
                ):
                    receipt["unknown"] += 1
                elif current_open:
                    receipt["open"] += 1
                else:
                    receipt["outside"] += 1
                continue
            completed = min(qualifying, key=time_key)
            receipt["completed_at"][case_id] = completed
            evidence_ref += "#as-of=" + completed
            if (flag_tag or dims or fin_only) and time_key(revision) != time_key(
                completed
            ):
                raise InputError(
                    "current tag, Fin participation or dimension may postdate completion; use source metadata frozen as of completion"
                )
        receipt["eligible"] += 1
        output.append(
            (
                case_id,
                revision,
                evidence_ref,
                str(bool(flag_tag and flag_tag in names)).lower(),
                *dimension_values(row, dims),
            )
        )
    return sorted(output), receipt
