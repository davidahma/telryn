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
        demo_store = work / "complaints/trust"
        env = {
            **os.environ,
            "TELRYN_TRUST_DIR": str(team_store),
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
            name + "@1.1.0"
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
        assert "TELRYN_TRUST_DIR=" in guidance and "complaints/trust" in guidance
        comparison = json.loads((work / "complaints/comparison.json").read_text())
        assert "comparison.json" in guidance and comparison["evidence"] == "synthetic"
        assert comparison["week_two_help_minutes"] == 3
        assert [path.read_bytes() for path in team_files] == before
        demo_report = work / "complaints/2026-10-05/report.html"
        assert "C108" in demo_report.read_text(encoding="utf-8")
        demo_state = json.loads(
            run("weekly", demo_report, "--format", "json", trust=demo_store)
        )
        assert demo_state["verification"] == "team_hmac_verified"
        for week, case_id, acceptance, completion, entry in (
            (comparison["weeks"][0], "C102", "A12", "A13", "R12"),
            (comparison["weeks"][1], "C204", "A24", "A25", "R24"),
        ):
            verified = json.loads(
                run("weekly", week["report"], "--format", "json", trust=demo_store)
            )
            assert verified["verification"] == "team_hmac_verified"
            case = next(
                c for c in verified["complaints"]["cases"] if c["case_id"] == case_id
            )
            quote = (
                "Please open a complaint about this duplicate charge."
                if case_id == "C102"
                else "I want to make a complaint about your service."
            )
            cited = case["source_citation"]
            assert cited["quote"] == quote and cited["passage"] == 1
            assert (cited["evidence_ref"], cited["revision"]) == (
                f"conversations/{case_id}",
                "v1",
            )
            with (
                installed / "_bundled/examples/complaints_pilot/conversations.csv"
            ).open(newline="") as source:
                contact = next(
                    r for r in csv.DictReader(source) if r["case_id"] == case_id
                )
            assert cited["at"] == contact["message_at"] and contact["message"] == quote
            assert "complaint" in case["classification_reason"]
            assert (
                case["registration_at_cutoff"] == "missing"
                and case["owner_at_cutoff"] == "unobserved"
            )
            assert (
                case["registration_as_of"] == "registered"
                and case["owner_as_of"] == "completed"
            )
            assert [(a["action_id"], a["status"]) for a in case["actions"]] == [
                (acceptance, "accepted"),
                (completion, "completed"),
            ]
            assert [r["entry_id"] for r in case["register_entries"]] == [entry]
        exported = json.loads(
            run("demo", "--complaints", "--out", "second-trial", "--format", "json")
        )
        assert exported == json.loads(
            (work / "second-trial/comparison.json").read_text()
        )
        assert (work / "second-trial/trust/audit.key").read_bytes() != (
            demo_store / "audit.key"
        ).read_bytes()
        assert json.loads(run("weekly", report, "--format", "json")) == state
        scored = json.loads(
            run("demo", "--qa-passes", "--out", "score-audit", "--format", "json")
        )
        assert [
            week["score"]["classification_disagreement"] for week in scored["weeks"]
        ] == [True, False]
        assert all(week["score"]["deadline_miss"] for week in scored["weeks"])
        originals = work / "score-audit/source-records/2026-10-05"
        proof = json.loads(
            run(
                "bank-report",
                "verify",
                scored["bank_report"],
                "--sha256",
                scored["bank_report_sha256"],
                "--source-report",
                scored["weeks"][0]["report"],
                "--case",
                "C102",
                "--scores",
                originals / "scores.csv",
                "--register",
                originals / "register.csv",
                "--actions",
                originals / "owner-events.csv",
                trust=work / "recipient",
            )
        )
        assert (
            proof["case"]["qa"]["decision"] == "pass"
            and proof["case"]["complaint"]["owner_as_of"] == "completed"
        )
        assert not (work / "recipient").exists()
        accommodation = json.loads(
            run("outcome", "--example", "accommodation", "--format", "json")
        )
        assert accommodation["milestones"]["delivery"]["at_cutoff"] == "missing"
        assert accommodation["milestones"]["delivery"]["at_follow_up"] == "late"
        assert [
            r["passed"]
            for r in json.loads(
                run(
                    "demo",
                    "--agent-release",
                    "--out",
                    "agent-release",
                    "--format",
                    "json",
                )
            )["releases"]
        ] == [False, True]
        assert json.loads(
            run("outcome", "--example", "vulnerability", "--format", "json")
        )["groups_suppressed"]
        for form in ("board", "vendor", "benchmark"):
            flags = (
                ["--vendor", "test", "--configuration", "v1", "--opt-in"]
                if form == "benchmark"
                else []
            )
            run(
                "oversight",
                form,
                "--quarter",
                "2026-Q4",
                "--out",
                form,
                *flags,
                *(row["report"] for row in scored["weeks"]),
                trust=work / "score-audit/trust",
            )
        assert "queue" in run("--help") and "record-shadow" in run("queue", "--help")
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
