"""Validate and build the independent release archive using standard Python."""

import argparse
from datetime import date
import gzip
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import re
import tarfile


HERE = Path(__file__).resolve().parent
INSTALLED = HERE.parent.name == "_bundled"
PACKAGE_ROOT = HERE.parents[1] if INSTALLED else HERE.parent / "telryn"
CALENDARS = PACKAGE_ROOT / "policy_packs" / "calendars"
MANIFEST = "SHA256SUMS.txt"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key}")
        result[key] = value
    return result


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected date in YYYY-MM-DD format")
    return value


def _validate_rules(raw, schema):
    doc = json.loads(raw, object_pairs_hook=_unique)
    days = _day(doc["published"])
    release = doc["version"]
    if (
        type(doc["schema"]) is not int
        or doc["schema"] != 1
        or doc["dataset"] != "telryn-deadlines"
        or not re.fullmatch(r"\d+\.\d+\.\d+", release)
        or not doc["rules"]
    ):
        raise ValueError("Unsupported deadline dataset metadata")
    rule = schema["$defs"]["rule"]
    period = schema["$defs"]["period"]
    units = period["properties"]["unit"]["enum"]
    ids = set()
    for row in doc["rules"]:
        if set(row) - set(rule["properties"]) or not set(rule["required"]) <= set(row):
            raise ValueError("Unexpected or missing regulatory data field")
        ident = row["id"]
        first = _day(row["effective_from"])
        last = row["effective_through"]
        if (
            ident in ids
            or first > days
            or (last is not None and _day(last) < first)
            or not row["source"].startswith("https://")
        ):
            raise ValueError(
                f"Invalid rule identity, source or effective dates for {ident}"
            )
        ids.add(ident)
        for key in ("calendar_basis", "computation"):
            if row[key] not in rule["properties"][key]["enum"]:
                raise ValueError(f"Unknown {key} for {ident}")
        for bound in [
            row["period"],
            *([row["outer_limit"]] if "outer_limit" in row else []),
        ]:
            if (
                set(bound) != set(period["required"])
                or type(bound["count"]) is not int
                or bound["count"] < 1
                or bound["unit"] not in units
            ):
                raise ValueError(f"Invalid duration for {ident}")
    return release


def _validate_calendar(name, raw):
    doc = json.loads(raw, object_pairs_hook=_unique)
    first, last = _day(doc["valid_from"]), _day(doc["valid_through"])
    dates = doc["holidays"]
    if (
        set(doc)
        != {
            "schema",
            "id",
            "version",
            "valid_from",
            "valid_through",
            "source",
            "holidays",
        }
        or type(doc["schema"]) is not int
        or doc["schema"] != 1
        or not doc["source"].startswith("https://")
        or name != f"{doc['id']}@{_day(doc['version'])}.json"
        or first > last
        or dates != sorted(set(dates))
        or any(not first <= _day(day) <= last for day in dates)
    ):
        raise ValueError(f"Invalid holiday calendar {name}")


def _license():
    if not INSTALLED:
        return (HERE.parent / "LICENSE").read_bytes()
    package = metadata.distribution("telryn")
    for entry in package.files or ():
        if entry.parts[-2:] == ("licenses", "LICENSE"):
            return package.locate_file(entry).read_bytes()
    raise ValueError("Installed Telryn is missing its distribution license")


def build(output):
    data = {
        "LICENSE": _license(),
        **{
            name: (HERE / name).read_bytes()
            for name in (
                "README.md",
                "rules.json",
                "schema.json",
                "calendar.schema.json",
            )
        },
    }
    release = _validate_rules(data["rules.json"], json.loads(data["schema.json"]))
    for path in sorted(CALENDARS.glob("*.json")):
        raw = path.read_bytes()
        _validate_calendar(path.name, raw)
        data[f"calendars/{path.name}"] = raw
    if not any(name.startswith("calendars/") for name in data):
        raise ValueError("No holiday calendars available")
    data[MANIFEST] = "".join(
        f"{hashlib.sha256(raw).hexdigest()}  {name}\n"
        for name, raw in sorted(data.items())
    ).encode("ascii")
    prefix = f"telryn-deadlines-{release}"
    with (
        output.open("xb") as stream,
        gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0) as zipped,
    ):
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, raw in sorted(data.items()):
                item = tarfile.TarInfo(f"{prefix}/{name}")
                item.size = len(raw)
                item.mode = 0o644
                tar.addfile(item, io.BytesIO(raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(args.out)


if __name__ == "__main__":
    main()
