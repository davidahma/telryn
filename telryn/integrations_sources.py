"""Read closed conversations from supported vendor API contracts."""

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


def _intercom(client, since, until, fin_only):
    client.intercom_identity()
    # v2.14 supports strict inequalities for dates (second resolution).
    filters = [
        {"field": "created_at", "operator": ">", "value": int(since.timestamp()) - 1},
        {"field": "created_at", "operator": "<", "value": int(until.timestamp())},
        {"field": "state", "operator": "=", "value": "closed"},
    ]
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


def _zendesk(client, since, until):
    params = {
        "filter[type]": "ticket",
        "page[size]": 100,
        "query": f"created>={iso(since)} created<{iso(until)} status:closed",
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


def _salesforce(client, since, until):
    version = "/services/data/v61.0/query"
    statement = (
        f"SELECT Id, CreatedDate, LastModifiedDate, IsClosed FROM Case "
        f"WHERE IsClosed = true AND CreatedDate >= {iso(since)} AND CreatedDate < {iso(until)} ORDER BY Id"
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


def _freshdesk(client, since, until):
    params = {
        "updated_since": iso(since - timedelta(seconds=1)),
        "per_page": 100,
        "order_by": "created_at",
        "order_type": "desc",
    }
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
        if rows and previous_created < since:
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


def _decagon(client, since, until):
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


def population(client, since, until, *, fin_only=False, flag_tag=None, closed_tag=None):
    vendor = client.provider
    if fin_only and vendor != "intercom":
        raise InputError("--fin-only applies to Intercom")
    if flag_tag and vendor == "salesforce":
        raise InputError("apply your Salesforce screen to the extracted file")
    if (vendor == "decagon") != bool(closed_tag):
        raise InputError("--closed-tag is required for Decagon and applies only there")
    adapters = {
        "zendesk": _zendesk,
        "salesforce": _salesforce,
        "freshdesk": _freshdesk,
        "decagon": _decagon,
    }
    rows = (
        _intercom(client, since, until, fin_only)
        if vendor == "intercom"
        else adapters[vendor](client, since, until)
    )
    output = {}
    for row in rows:
        crm = vendor == "salesforce"
        created = instant(object_at(row, "CreatedDate" if crm else "created_at"))
        if not since <= created < until:
            if vendor in {"freshdesk", "decagon"}:
                continue
            raise InputError(f"{vendor} returned a record outside the requested dates")
        names = tags(row, vendor) if closed_tag or flag_tag else []
        if vendor == "decagon":
            closed = closed_tag in names
        elif crm:
            closed = boolean(object_at(row, "IsClosed"))
        else:
            status = object_at(row, "state" if vendor == "intercom" else "status")
            if vendor == "freshdesk" and type(status) is not int:
                raise InputError("invalid Freshdesk status")
            closed = status == (5 if vendor == "freshdesk" else "closed")
        if not closed:
            if vendor in {"freshdesk", "decagon"}:
                continue
            raise InputError(f"{vendor} returned a record outside the closed cohort")
        if fin_only and object_at(row, "ai_agent_participated") is not True:
            raise InputError(
                "Intercom returned a conversation without Fin participation"
            )
        field = "Id" if crm else "conversation_id" if vendor == "decagon" else "id"
        key = vendor_id(object_at(row, field))
        revision = source_revision(
            object_at(row, "LastModifiedDate" if crm else "updated_at")
        )
        updated = instant(revision)
        if updated < created or key in output:
            raise InputError(
                f"{vendor} returned a duplicate ID or inconsistent revision"
            )
        case_id = f"{vendor}:{client.scope}:{key}"
        output[key] = (
            case_id,
            revision,
            f"{case_id}@{revision}",
            str(bool(flag_tag and flag_tag in names)).lower(),
        )
    return [output[key] for key in sorted(output)]
