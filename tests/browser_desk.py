"""Exercise the desk in installed Google Chrome with the optional Playwright test driver."""

import csv
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from examples.complaints_pilot import __main__ as pilot


def write_return(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def scorecard_intake(page, root):
    week = "2026-10-05"
    cases = [
        c
        for c in pilot.read_csv(pilot.FIXTURE / "conversations.csv")
        if c["week"] == week
    ]
    protocol = tomllib.loads((pilot.FIXTURE / "protocol.toml").read_text())
    inputs = root / "score-input"
    config = {
        "file": "scores.csv",
        "scorecard": "payments-support",
        "version": "1.0",
        "cutoff": "2026-10-12T08:00:00Z",
        "scope": "all_scored",
    }
    pilot.pilot_plan(inputs, week, cases, protocol, score_audit=config)
    form = page.locator("#intake")
    form.get_by_text("Audit an external QA scorecard", exact=True).click()
    for name, value in {
        "week": week,
        "rubric": protocol["policy"],
        "rubric_question": protocol["question"],
        "screen_version": "none",
        "scorecard": config["scorecard"],
        "score_version": config["version"],
        "score_cutoff": config["cutoff"],
        "minutes": "80",
        "minutes_per_grade": "4",
        "expected_rate": "10",
    }.items():
        form.locator(f"[name={name}]").fill(value)
    for name, path in {
        "population": inputs / "population.csv",
        "reviewed": inputs / "ordinary.csv",
        "coverage": inputs / "population.csv.coverage.json",
        "scores": pilot.FIXTURE / "scores" / f"{week}.csv",
    }.items():
        form.locator(f"[name={name}]").set_input_files(path)
    for scope, count in (("unreviewed_scored", 9), ("all_scored", 10)):
        form.locator("[name=score_scope]").select_option(scope)
        form.locator("#preview").click()
        expect(page.locator("#forecast")).to_contain_text(f"{count} cases fit")
    form.locator("#create").click()
    expect(page.locator("#message")).to_contain_text("Audit ready")
    with page.expect_download() as download:
        page.locator("#packet").click()
    packet = root / "scored-packet.csv"
    download.value.save_as(packet)
    rows = pilot.read_csv(packet)
    assert {r["case_id"] for r in rows} == {c["case_id"] for c in cases}
    assert all(r["serious"] == "" for r in rows)
    assert not {"score_ref", "decision", "complaint_prediction"}.intersection(rows[0])
    with page.expect_popup() as report:
        page.locator("#report").click()
    expect(report.value.locator("#score-audit")).to_contain_text("payments-support")
    report.value.close()


def complaint_control_flow(page, root):
    # Three people use the same blind packet and adjudicate a category-only change.
    form = page.locator("#intake")
    for name, value in {
        "week": "2026-11-09",
        "rubric": "P1",
        "rubric_question": "Is this a complaint?",
        "screen_version": "none",
        "expected_rate": "10",
        "minutes": "5",
        "minutes_per_grade": "5",
    }.items():
        form.locator(f"[name={name}]").fill(value)
    control = {
        "schema": 1,
        "scope": "Example payments queue",
        "categories": ["service", "harm"],
        "gap_owner": "Complaints lead",
        "gap_review_date": "2026-11-20",
        "onboarding_minutes": 10,
        "channels": [
            {
                "name": "chat",
                "coverage": "covered",
                "ordinary_qa": "weekly",
                "register_link": "case reference",
                "retention": "90 days",
                "owner": "Support ops",
                "review_date": "2026-11-20",
            }
        ],
    }
    pack_path = (
        Path(__file__).resolve().parents[1]
        / "telryn/policy_packs/firm-example@1.0.0.json"
    )
    pack = json.loads(pack_path.read_text())
    pack["clock_policy"]["id"] = "desk-fixture"
    pack["categories"] = control.pop("categories")
    for name, text in {
        "policy_pack": json.dumps(pack),
        "control": json.dumps(control),
        "population": "case_id,revision,evidence_ref,screen_flag\nc1,v1,fixture/1,0\n",
        "reviewed": "case_id\n",
    }.items():
        filename = name + (".json" if name in ("control", "policy_pack") else ".csv")
        form.locator(f"[name={name}]").set_input_files(
            {"name": filename, "mimeType": "text/plain", "buffer": text.encode()}
        )
    expect(form.locator("[name=rubric_question]")).to_be_disabled()
    page.locator("#create").click()
    expect(page.locator("#message")).to_contain_text("Audit ready")
    with page.expect_download() as download:
        page.locator("#packet").click()
    packet = root / "complaints.csv"
    download.value.save_as(packet)
    with packet.open(encoding="utf-8-sig", newline="") as src:
        row = next(csv.DictReader(src))
    assert (
        json.loads(row["category_options"]) == ["service", "harm"]
        and "screen_flag" not in row
    )
    for stage, reviewer, category, reason in (
        ("grades", "first", "harm", "Clause two"),
        ("second-review", "second", "service", "Clause one"),
    ):
        write_return(
            packet,
            [
                {
                    **row,
                    "serious": "yes",
                    "disposition": "unknown",
                    "minutes": "5",
                    "category": category,
                    "vulnerability": "unknown",
                    "redress": "completed",
                    "classification_reason": reason,
                }
            ],
        )
        page.locator("#grade [name=round]").select_option(stage)
        page.locator("#grade [name=reviewer]").fill(reviewer)
        page.locator("#grade [name=grades]").set_input_files(packet)
        with page.expect_response("**/" + stage):
            page.locator("#grade button").click()
        expect(page.locator("#grade button")).to_be_enabled()
    resolution = page.locator("#resolution")
    expect(resolution.locator("#answers")).to_contain_text("category harm")
    expect(resolution.locator("#answers")).to_contain_text("Clause one")
    for name, value in (
        ("reviewer", "third"),
        ("minutes", "1"),
        ("reason", "Second clause concerns a different product"),
    ):
        resolution.locator(f"[name={name}]").fill(value)
    resolution.locator("[name=choice]").select_option("second")
    resolution.locator("button").click()
    expect(resolution).to_be_hidden()
    with page.expect_popup() as report:
        page.locator("#report").click()
    expect(report.value.get_by_role("heading", level=1)).to_have_text(
        "Weekly complaints control statement"
    )
    expect(report.value.locator("header")).to_contain_text("desk-fixture@1.0.0")
    expect(report.value.locator("#control-inventory")).to_contain_text("Support ops")
    expect(
        report.value.get_by_text("Category downgrade requested.", exact=False).first
    ).to_be_visible()


def run(root: Path):
    fixture = Path(__file__).resolve().parents[1] / "examples" / "fintech"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "telryn",
            "desk",
            "--out",
            str(root / "runs"),
            "--no-browser",
        ],
        env={**os.environ, "TELRYN_TRUST_DIR": str(root / "trust")},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        line = server.stdout.readline()
        if not line.startswith("Weekly QA desk: "):
            raise RuntimeError("Desk failed to start")
        with sync_playwright() as driver:
            browser = driver.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 850})
            page.goto(line.split(": ", 1)[1].strip())
            form = page.locator("#intake")
            for name, value in {
                "week": "2026-11-02",
                "rubric": "escalation-v1",
                "rubric_question": "Was the required escalation missed?",
                "screen_version": "synthetic-v1",
                "minutes": "40",
                "minutes_per_grade": "4",
                "second_review_minutes": "6",
                "adjudication_minutes": "2",
            }.items():
                form.locator(f"[name={name}]").fill(value)
            form.get_by_text("Decision this sample needs to support").click()
            form.locator("[name=detection_cases]").fill("2")
            form.locator("[name=detection_target]").fill("95")
            for name, filename in (
                ("population", "population.csv"),
                ("reviewed", "ordinary-reviews.csv"),
            ):
                form.locator(f"[name={name}]").set_input_files(fixture / filename)

            # Change the budget while a successfully submitted forecast is pending.
            def edit_during_preview(route):
                form.locator("[name=minutes]").fill("44")
                route.continue_()

            page.route("**/plan", edit_during_preview)
            with page.expect_response("**/plan"):
                page.locator("#preview").click()
            expect(page.locator("#preview")).to_be_enabled()
            expect(page.locator("#forecast")).to_be_hidden()
            page.unroute("**/plan", edit_during_preview)
            form.locator("[name=minutes]").fill("40")
            page.locator("#preview").click()
            expect(page.locator("#forecast")).to_contain_text("8 cases fit")
            expect(page.locator("#forecast")).to_contain_text("meets the 95% target")
            expect(page.locator("#forecast")).to_contain_text(
                "6 for second review and 2 for adjudication"
            )
            assert not list((root / "runs").glob("*/report.html"))
            page.locator("#create").click()
            expect(page.locator("#message")).to_contain_text("Audit ready")
            with page.expect_download() as event:
                page.locator("#packet").click()
            packet = root / "packet.csv"
            event.value.save_as(packet)
            with packet.open(encoding="utf-8-sig", newline="") as source:
                rows = list(csv.DictReader(source))
            assert len(rows) == 8 and "screen_flag" not in rows[0]
            answer = {
                **rows[0],
                "serious": "insufficient",
                "disposition": "unknown",
                "minutes": "4",
            }
            returned = root / "returned.csv"
            write_return(returned, [answer])
            page.locator("#grade [name=reviewer]").fill("Fixture reviewer")
            page.locator("#grade [name=grades]").set_input_files(returned)
            page.locator("#grade button").click()
            expect(page.locator("#progress")).to_contain_text(
                "7 unanswered · 1 insufficient"
            )
            case_row = page.locator("#follow-up tbody tr")
            expect(case_row).to_have_count(1)
            for value in (
                answer["case_id"],
                answer["revision"],
                answer["evidence_ref"],
                "Insufficient evidence",
                "Fixture reviewer",
                "First reviewer amends primary grade",
            ):
                expect(case_row).to_contain_text(value)
            page.reload()
            expect(page.locator("#progress")).to_contain_text(
                "7 unanswered · 1 insufficient"
            )
            page.locator("#history summary").click()
            expect(page.locator("#history li")).to_contain_text(
                "7 unanswered · 1 insufficient"
            )
            expect(page.locator("#history li")).not_to_contain_text(
                answer["evidence_ref"]
            )
            with page.expect_download() as event:
                page.locator("#outstanding").click()
            remaining = root / "remaining.csv"
            event.value.save_as(remaining)
            with remaining.open(encoding="utf-8-sig", newline="") as source:
                assert list(csv.DictReader(source)) == rows[1:]
            # A draft belongs to one dispute, including after a successful save.
            for review_round, reviewer, serious, disposition in (
                ("grades", "Primary", "no", "none"),
                ("second-review", "Independent", "yes", "already_known"),
            ):
                write_return(
                    returned,
                    [
                        {
                            **row,
                            "serious": serious,
                            "disposition": disposition,
                            "minutes": "3",
                        }
                        for row in rows[1:3]
                    ],
                )
                page.locator("#grade [name=round]").select_option(review_round)
                page.locator("#grade [name=reviewer]").fill(reviewer)
                page.locator("#grade [name=grades]").set_input_files(returned)
                with page.expect_response("**/" + review_round):
                    page.locator("#grade button").click()
                expect(page.locator("#grade button")).to_be_enabled()
            adjudicate = page.locator("#resolution")
            disputed = adjudicate.locator("[name=dispute]")
            expect(disputed.locator("option")).to_have_count(2)
            for choice in ("primary", "second"):
                adjudicate.locator("[name=reviewer]").fill("Adjudicator")
                adjudicate.locator("[name=reason]").fill(
                    "Read the frozen evidence for this case"
                )
                adjudicate.locator("[name=minutes]").fill("1")
                adjudicate.locator("[name=choice]").select_option(choice)
                if choice == "primary":
                    disputed.select_option(index=1)
                else:
                    adjudicate.locator("button").click()
                    expect(disputed.locator("option")).to_have_count(1)
                for name in ["reviewer", "reason", "minutes", "choice"]:
                    expect(adjudicate.locator(f"[name={name}]")).to_have_value("")
                assert not adjudicate.evaluate("(form) => form.checkValidity()")
            with page.expect_popup() as event:
                page.locator("#report").click()
            expect(event.value.locator("footer")).to_contain_text(
                "Signed weekly report"
            )
            expect(event.value.locator("main")).to_contain_text("meets the 95% target")

            # Returning to the audit list while the last decision is being saved
            # must leave the operator at their chosen destination.
            def navigate_during_save(route):
                page.evaluate("location.hash = ''")
                expect(page.locator("#current")).to_be_hidden()
                route.continue_()

            page.route("**/adjudicate", navigate_during_save)
            adjudicate.locator("[name=reviewer]").fill("Adjudicator")
            adjudicate.locator("[name=reason]").fill("Checked the remaining case")
            adjudicate.locator("[name=minutes]").fill("2")
            adjudicate.locator("[name=choice]").select_option("primary")
            with page.expect_response("**/adjudicate"):
                adjudicate.locator("button").click()
            expect(page.locator("#message")).to_contain_text("Changes saved")
            expect(page.locator("#current")).to_be_hidden()
            expect(page.locator("#history li")).not_to_contain_text(
                "awaiting adjudication"
            )
            # The same directory opened with damaged report bytes stays visible and closed for grading.
            saved = next((root / "runs").glob("*/report.html"))
            saved.write_text("unverified report text", encoding="utf-8")
            page.goto(line.split(": ", 1)[1].strip())
            expect(page.locator("#recovery")).to_be_visible()
            expect(page.locator("#history li")).to_contain_text("Unavailable audit")
            expect(page.locator("#history a")).to_have_count(0)
            expect(page.locator("#current")).to_be_hidden()
            page.unroute("**/adjudicate", navigate_during_save)
            complaint_control_flow(page, root)
            page.goto(line.split(": ", 1)[1].strip())
            scorecard_intake(page, root)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    with TemporaryDirectory() as root:
        run(Path(root))
