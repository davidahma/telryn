"""Private loopback browser intake for a weekly support audit."""

import csv
import hmac
import json
import re
import secrets
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .contracts import InputError, mapping
from .screen import RUBRICS
from .weekly_report import read_state, refresh, start

PAGE = Path(__file__).with_name("desk.html")
MAX_BODY = 24 * 1024 * 1024


class Desk:
    def __init__(self, root: Path):
        self.root = root
        self.runs = {}

    def create(self, fields: dict) -> str:
        allowed = {
            "week",
            "rubric",
            "rubric_question",
            "screen_version",
            "flagged_sample",
            "unflagged_sample",
            "population",
            "reviewed",
            "receipt",
        }
        if set(fields) != allowed:
            raise InputError("the weekly intake form is incomplete")
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            lines = ["schema = 1"]
            for key in ("week", "rubric", "rubric_question", "screen_version"):
                if not isinstance(fields[key], str) or not fields[key].isprintable():
                    raise InputError(f"enter a single line for {key}")
                lines.append(f"{key} = {json.dumps(fields[key], ensure_ascii=False)}")
            for key in ("flagged_sample", "unflagged_sample"):
                if type(fields[key]) is not int or fields[key] < 0:
                    raise InputError("sample sizes must be whole numbers")
                lines.append(f"{key} = {fields[key]}")
            for key in ("population", "reviewed", "receipt"):
                value = fields[key]
                if key == "receipt" and not value:
                    continue
                if not isinstance(value, str):
                    raise InputError("choose the required export files")
                filename = key + (".json" if key == "receipt" else ".csv")
                (base / filename).write_text(value, encoding="utf-8", newline="")
                lines.append(
                    f'{("screen_receipt" if key == "receipt" else key)} = "{filename}"'
                )
            plan = base / "week.toml"
            plan.write_text("\n".join(lines), encoding="utf-8")
            ident = secrets.token_hex(12)
            # The path consists only of a generated identifier. Operators see the date in the report.
            report = start(plan, self.root / ident)
        self.runs[ident] = report
        return ident

    def report(self, ident: str) -> Path:
        if ident not in self.runs:
            raise InputError("unknown run in this desk session")
        return self.runs[ident]

    def grade(self, ident: str, data: str) -> None:
        path = self.report(ident)
        if not isinstance(data, str):
            raise InputError("select the returned CSV")
        with tempfile.TemporaryDirectory() as temp:
            returned = Path(temp) / "return.csv"
            returned.write_text(data, encoding="utf-8", newline="")
            refresh(path, returned)


def handler(desk: Desk, token: str):
    class Handler(BaseHTTPRequestHandler):
        timeout = 20

        def log_message(self, *args):
            pass  # The local entry URL contains a session secret.

        def reply(self, status, body, mime, *, download=False, script_nonce=None):
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
                f"script-src {script_policy}; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            if download:
                self.send_header(
                    "Content-Disposition", 'attachment; filename="grades.csv"'
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
            parts = self.route()
            if parts == [""]:
                nonce = secrets.token_hex(16)
                self.reply(
                    200,
                    PAGE.read_bytes().replace(b"SESSION_NONCE", nonce.encode()),
                    "text/html; charset=utf-8",
                    script_nonce=nonce,
                )
            elif parts == ["rubrics"]:
                self.reply(200, json.dumps(RUBRICS).encode(), "application/json")
            elif parts == ["audits"]:
                items = []
                for ident, path in list(desk.runs.items()):
                    try:
                        state = read_state(path)
                        items.append(
                            {
                                "id": ident,
                                "week": state["week"],
                                "rubric": state["rubric"],
                            }
                        )
                    except (OSError, ValueError):
                        continue
                self.reply(200, json.dumps(items).encode(), "application/json")
            elif (
                parts
                and len(parts) == 2
                and parts[0] in desk.runs
                and parts[1] in ("report.html", "grades.csv")
            ):
                path = desk.report(parts[0]).with_name(parts[1])
                if path.is_symlink() or not path.is_file() or path.parent.is_symlink():
                    self.reply(404, b"Unknown artifact", "text/plain")
                    return
                download = parts[1] == "grades.csv"
                self.reply(
                    200,
                    path.read_bytes(),
                    "text/csv" if download else "text/html; charset=utf-8",
                    download=download,
                )
            else:
                self.reply(404, b"Unknown page", "text/plain")

        def do_POST(self):
            route = self.route()
            if route is None:
                self.reply(403, b"Unknown session or browser origin", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if (
                    not 0 < length <= MAX_BODY
                    or self.headers.get_content_type() != "application/json"
                ):
                    raise InputError(
                        "use the intake form with exports below 24 MiB, or use the CLI"
                    )
                fields = mapping(json.loads(self.rfile.read(length)), "weekly intake")
                if route == ["audits"]:
                    ident = desk.create(fields)
                elif len(route) == 2 and route[1] == "grades":
                    ident = route[0]
                    desk.grade(ident, fields.get("grades"))
                else:
                    raise InputError("unknown desk operation")
                self.reply(200, json.dumps({"id": ident}).encode(), "application/json")
            except (ValueError, OSError, csv.Error, OverflowError) as exc:
                self.reply(400, str(exc).encode("utf-8"), "text/plain; charset=utf-8")

    return Handler


def serve(root: Path, *, open_browser: bool = True) -> int:
    token = secrets.token_hex(32)
    desk = Desk(root)
    # Make a previous run usable after restart without exposing arbitrary paths.
    if root.is_dir():
        for directory in root.iterdir():
            if (
                re.fullmatch("[a-f0-9]{24}", directory.name)
                and not directory.is_symlink()
                and directory.is_dir()
                and (directory / "report.html").is_file()
                and not (directory / "report.html").is_symlink()
            ):
                desk.runs[directory.name] = directory / "report.html"
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
