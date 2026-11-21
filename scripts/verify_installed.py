"""Exercise a Telryn distribution using its own interpreter outside the checkout."""

import csv
import json
import os
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from urllib.request import urlopen


def main(version):
    dist = metadata.distribution("telryn")
    assert dist.version == version
    installed = Path(dist.locate_file("telryn")).resolve()
    binary = Path(sys.executable).parent / "telryn"
    assert installed.is_relative_to(Path(sys.prefix))
    with tempfile.TemporaryDirectory(prefix="telryn-installed-") as directory:
        work = Path(directory)
        team_store = work / "private"
        demo_store = work / "rehearsal"
        env = {
            **os.environ,
            "TELRYN_TRUST_DIR": str(team_store),
            "TELRYN_DEMO_TRUST_DIR": str(demo_store),
        }
        env.pop("PYTHONPATH", None)

        def run(*args, trust=team_store):
            return subprocess.check_output(
                [binary, *args],
                cwd=work,
                env={**env, "TELRYN_TRUST_DIR": str(trust)},
                text=True,
                timeout=180,
            )

        assert run("--version").strip() == version
        packs = {row["reference"] for row in json.loads(run("pack", "list"))}
        assert packs == {
            name + "@1.0.0"
            for name in ("firm-example", "fca-disp", "us-reg-e", "cfpb-company-portal")
        }
        assert "usage: telryn weekly" in run("weekly", "--help")
        run("demo", "--out", "refunds")
        assert "telryn refresh REPORT" in (work / "refunds/report.html").read_text()
        plan = installed / "_bundled/examples/fintech/week.toml"
        run("weekly", plan, "--delivery-date", "2026-11-09", "--out", "audit")
        report = work / "audit/report.html"
        assert (
            "<code>telryn weekly report.html --grades returned.csv</code>"
            in report.read_text()
        )
        with (work / "audit/grades.csv").open(
            encoding="utf-8-sig", newline=""
        ) as packet:
            row = next(csv.DictReader(packet))
        row.update(serious="no", disposition="none", minutes="3")
        with (work / "returned.csv").open(
            "w", encoding="utf-8", newline=""
        ) as returned:
            writer = csv.DictWriter(returned, fieldnames=row)
            writer.writeheader()
            writer.writerow(row)
        run("weekly", report, "--grades", "returned.csv", "--reviewer", "QA reviewer")
        run("weekly", report, "--outstanding", "remaining.csv")
        with (work / "remaining.csv").open(
            encoding="utf-8-sig", newline=""
        ) as remaining:
            unanswered = list(csv.DictReader(remaining))
        assert unanswered and all(
            item["case_id"] != row["case_id"] for item in unanswered
        )
        state = json.loads(run("weekly", "audit/report.html", "--format", "json"))
        assert state["verification"] == "team_hmac_verified" and state["graded"] == 1
        # The README rehearsal must preserve an already-issued team's evidence.
        team_files = [team_store / "audit.key", team_store / "ledger.sqlite3", report]
        before = [path.read_bytes() for path in team_files]
        guidance = run("demo", "--complaints", "--out", "complaints")
        assert "TELRYN_TRUST_DIR=" in guidance and str(demo_store) in guidance
        assert [path.read_bytes() for path in team_files] == before
        demo_report = work / "complaints/2026-10-05/report.html"
        assert "C108" in demo_report.read_text(encoding="utf-8")
        demo_state = json.loads(
            run("weekly", demo_report, "--format", "json", trust=demo_store)
        )
        assert demo_state["verification"] == "team_hmac_verified"
        assert json.loads(run("weekly", report, "--format", "json")) == state
        run("qualify")
        with subprocess.Popen(
            [binary, "desk", "--no-browser", "--out", "desk"],
            cwd=work,
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        ) as desk:
            try:
                url = desk.stdout.readline().strip().removeprefix("Weekly QA desk: ")
                assert url.startswith("http://127.0.0.1:")
                with urlopen(url, timeout=5) as page:
                    assert (
                        page.headers["Content-Security-Policy"]
                        and b"desk.js" in page.read()
                    )
                with urlopen(url + "desk.js", timeout=5) as script:
                    assert (
                        script.headers.get_content_type() == "text/javascript"
                        and script.read()
                    )
            finally:
                desk.terminate()
                desk.wait(timeout=10)
    print(
        f"Verified installed Telryn {version}, desk, policy packs, demos, weekly audit and qualification."
    )


if __name__ == "__main__":
    main(sys.argv[1])
