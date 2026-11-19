"""Private loopback browser intake and review for a weekly support audit."""

import csv
import hmac
import json
import math
import re
import secrets
import tempfile
import webbrowser
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import weekly_report, weekly_review
from .contracts import InputError, integer, mapping
from .screen import RUBRICS

PAGE = Path(__file__).with_name("desk.html")
MAX_FILE = 24 * 1024 * 1024
MAX_BODY = 64 * 1024 * 1024
FILES = {
    "population": "population.csv",
    "reviewed": "reviewed.csv",
    "receipt": "receipt.json",
    "coverage": "coverage.json",
    "grades": "return.csv",
}
PLAN_FILES = {
    "population": "population",
    "reviewed": "reviewed",
    "receipt": "screen_receipt",
    "coverage": "coverage_receipt",
}


class UploadLimit(InputError):
    """The desk could not accept this upload within its published size limits."""


def _form(content_type, body):
    if content_type.split(";", 1)[0].strip() == "application/json":
        fields = mapping(json.loads(body), "weekly intake")
    else:
        if "\r" in content_type or "\n" in content_type:
            raise InputError("invalid form content type")
        envelope = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + content_type + "\r\n\r\n").encode("ascii") + body
        )
        if (
            envelope.get_content_type() != "multipart/form-data"
            or not envelope.is_multipart()
            or envelope.defects
        ):
            raise InputError("invalid browser upload")
        files = {}
        fields = None
        for part in envelope.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if (
                part.get_content_disposition() != "form-data"
                or part.is_multipart()
                or part.defects
                or part.get("Content-Transfer-Encoding") is not None
            ):
                raise InputError("invalid form part")
            raw = part.get_payload(decode=True)
            if name == "settings" and fields is None and len(raw) <= 32_768:
                fields = mapping(json.loads(raw), "weekly settings")
            elif name in FILES and name not in files:
                if len(raw) > MAX_FILE:
                    raise UploadLimit(f"{name} exceeds the 24 MiB per-file limit")
                files[name] = raw.decode("utf-8")
            else:
                raise InputError("unexpected or repeated form field")
        if fields is None or fields.keys() & FILES.keys():
            raise InputError("missing settings or duplicate file fields")
        fields.update(files)
    for name in fields.keys() & FILES.keys():
        if not isinstance(fields[name], str):
            raise InputError(f"choose a UTF-8 file for {name}")
        if len(fields[name].encode("utf-8")) > MAX_FILE:
            raise UploadLimit(f"{name} exceeds the 24 MiB per-file limit")
    return fields


@contextmanager
def _plan(fields):
    required = {
        "week",
        "rubric",
        "rubric_question",
        "screen_version",
        "population",
        "reviewed",
    }
    allowed = required | {
        "receipt",
        "coverage",
        "group_by",
        "flagged_sample",
        "unflagged_sample",
        "planning",
    }
    if not required <= fields.keys() <= allowed:
        raise InputError("the weekly intake form has missing or unknown fields")
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        lines = ["schema = 1"]
        for key in ("week", "rubric", "rubric_question", "screen_version"):
            value = fields[key]
            if not isinstance(value, str) or not value.isprintable():
                raise InputError(f"enter a single line for {key}")
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        groups = fields.get("group_by", [])
        if not isinstance(groups, list) or any(
            key not in ("channel", "agent_type", "product") for key in groups
        ):
            raise InputError("unknown grouping column")
        if groups:
            lines.append("group_by = " + json.dumps(groups))
        for key, config_key in PLAN_FILES.items():
            value = fields.get(key, "")
            if not value and key in ("receipt", "coverage"):
                continue
            (base / FILES[key]).write_text(value, encoding="utf-8", newline="")
            lines.append(f'{config_key} = "{FILES[key]}"')
        if "planning" in fields:
            options = mapping(fields["planning"], "staffing budget")
            required_budget = {"minutes", "minutes_per_grade"}
            if (
                not required_budget
                <= options.keys()
                <= required_budget
                | {
                    "expected_rate",
                    "flagged_rate",
                    "unflagged_rate",
                    "detection_cases",
                    "detection_target",
                    "second_review_minutes",
                    "adjudication_minutes",
                }
            ):
                raise InputError(
                    "enter a staffing budget with supported planning fields"
                )
            lines.append("[planning]")
            for key, value in options.items():
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise InputError(f"enter a finite number for {key}")
                lines.append(f"{key} = {value}")
        else:
            for key in ("flagged_sample", "unflagged_sample"):
                lines.append(f"{key} = {integer(fields.get(key), key)}")
        path = base / "week.toml"
        path.write_text("\n".join(lines), encoding="utf-8")
        yield path


class Desk:
    def __init__(self, root: Path):
        self.root = root
        self.runs = {}
        if root.is_dir():
            for directory in root.iterdir():
                if (
                    re.fullmatch("[a-f0-9]{24}", directory.name)
                    and not directory.is_symlink()
                    and directory.is_dir()
                ):
                    self.runs[directory.name] = directory / "report.html"

    def review(self, ident: str) -> dict:
        state = weekly_report.read_state(self.report(ident))
        return {
            "week": state["week"],
            "rubric": state["rubric"],
            "progress": weekly_review.workload(state),
            "follow_up": weekly_review.follow_up(state),
            **weekly_review.review_status(state),
        }

    def history(self) -> list[dict]:
        items = []
        for ident in list(self.runs):
            try:
                state = weekly_report.read_state(self.report(ident))
            except (OSError, ValueError):
                items.append({"id": ident, "verified": False})
            else:
                items.append(
                    {
                        "id": ident,
                        "verified": True,
                        "week": state["week"],
                        "rubric": state["rubric"],
                        "progress": weekly_review.workload(state),
                    }
                )
        return sorted(items, key=lambda r: (r.get("week", ""), r["id"]), reverse=True)

    def create(self, fields: dict) -> str:
        with _plan(fields) as plan:
            ident = secrets.token_hex(12)
            report = weekly_report.start(plan, self.root / ident)
        self.runs[ident] = report
        return ident

    def preview(self, fields):
        from .planning import decision_lines, preview

        with _plan(fields) as plan:
            if "planning" not in fields:
                raise InputError("enter a staffing budget to preview the sample")
            forecast = preview(plan)
            return {**forecast, "decision_lines": decision_lines(forecast)}

    def report(self, ident: str) -> Path:
        path = self.runs.get(ident)
        if (
            path is None
            or path.parent.is_symlink()
            or path.is_symlink()
            or not path.is_file()
        ):
            raise InputError("unknown audit in this desk session")
        return path

    def grade(self, ident: str, fields: dict, *, second: bool) -> None:
        path = self.report(ident)
        data = fields.get("grades")
        reviewer = fields.get("reviewer")
        if not isinstance(data, str):
            raise InputError("select the returned CSV")
        with tempfile.TemporaryDirectory() as temp:
            returned = Path(temp) / "return.csv"
            returned.write_text(data, encoding="utf-8", newline="")
            if second:
                weekly_report.second_review(path, returned, reviewer)
            else:
                weekly_report.refresh(path, returned, reviewer=reviewer)

    def resolve(self, ident, fields):
        weekly_report.adjudicate(
            self.report(ident),
            fields.get("case_id"),
            fields.get("dispute_id"),
            fields.get("reviewer"),
            fields.get("choice"),
            fields.get("reason"),
            fields.get("minutes"),
        )


def handler(desk: Desk, token: str):
    class Handler(BaseHTTPRequestHandler):
        timeout = 20

        def log_message(self, *args):
            pass  # The local entry URL contains a session secret.

        def reply(self, status, body, mime, *, download=None, script_nonce=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            script_policy = f"'nonce-{script_nonce}'" if script_nonce else "'none'"
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                f"script-src {script_policy}; connect-src 'self'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'",
            )
            if download:
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{download}"'
                )
            self.end_headers()
            self.wfile.write(body)

        def route(self):
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host:
                return None
            parts = self.path.split("/")
            if len(parts) < 3 or not hmac.compare_digest(
                parts[1].encode(), token.encode()
            ):
                return None
            if (
                self.command == "POST"
                and self.headers.get("Origin") != "http://" + host
            ):
                return None
            return parts[2:]

        def do_GET(self):
            try:
                self.get(self.route())
            except (OSError, ValueError, csv.Error) as exc:
                self.reply(400, str(exc).encode(), "text/plain; charset=utf-8")

        def get(self, parts):
            if parts == [""]:
                nonce = secrets.token_hex(16)
                self.reply(
                    200,
                    PAGE.read_bytes().replace(b"SESSION_NONCE", nonce.encode()),
                    "text/html; charset=utf-8",
                    script_nonce=nonce,
                )
            elif parts == ["desk.js"]:
                self.reply(
                    200,
                    PAGE.with_suffix(".js").read_bytes(),
                    "text/javascript; charset=utf-8",
                )
            elif parts == ["rubrics"]:
                self.reply(200, json.dumps(RUBRICS).encode(), "application/json")
            elif parts == ["audits"]:
                self.reply(200, json.dumps(desk.history()).encode(), "application/json")
            elif parts and len(parts) == 2 and parts[0] in desk.runs:
                path = desk.report(parts[0])
                if parts[1] in ("grades.csv", "outstanding.csv"):
                    self.reply(
                        200,
                        weekly_report.verified_packet(
                            path, outstanding=parts[1] == "outstanding.csv"
                        ),
                        "text/csv",
                        download=parts[1],
                    )
                elif parts[1] == "report.html":
                    self.reply(
                        200,
                        weekly_report.verified_html(path),
                        "text/html; charset=utf-8",
                    )
                elif parts[1] == "review":
                    status = desk.review(parts[0])
                    self.reply(200, json.dumps(status).encode(), "application/json")
                else:
                    self.reply(404, b"Unknown artifact", "text/plain")
            else:
                self.reply(404, b"Unknown page", "text/plain")

        def do_POST(self):
            route = self.route()
            if route is None:
                self.reply(403, b"Unknown session or browser origin", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if length > MAX_BODY:
                    raise UploadLimit(
                        "The complete upload exceeds 64 MiB. Use the CLI for this batch."
                    )
                if length <= 0 or self.headers.get_content_type() not in (
                    "multipart/form-data",
                    "application/json",
                ):
                    raise InputError("use the desk intake form")
                fields = _form(self.headers["Content-Type"], self.rfile.read(length))
                if route == ["plan"]:
                    value = desk.preview(fields)
                elif route == ["audits"]:
                    value = {"id": desk.create(fields)}
                elif len(route) == 2 and route[1] in (
                    "grades",
                    "second-review",
                    "adjudicate",
                ):
                    if route[1] == "adjudicate":
                        desk.resolve(route[0], fields)
                    else:
                        desk.grade(route[0], fields, second=route[1] == "second-review")
                    value = {"id": route[0]}
                else:
                    raise InputError("unknown desk operation")
                self.reply(
                    200, json.dumps(value, allow_nan=False).encode(), "application/json"
                )
            except UploadLimit as exc:
                self.reply(413, str(exc).encode(), "text/plain; charset=utf-8")
            except (ValueError, OSError, csv.Error, OverflowError) as exc:
                self.reply(400, str(exc).encode(), "text/plain; charset=utf-8")

    return Handler


def serve(root: Path, *, open_browser: bool = True) -> int:
    token = secrets.token_hex(32)
    desk = Desk(root)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler(desk, token)) as server:
        url = f"http://127.0.0.1:{server.server_port}/{token}/"
        print(
            f"Weekly QA desk: {url}\nPrivate audit directory: {root.resolve()}",
            flush=True,
        )
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
    return 0
