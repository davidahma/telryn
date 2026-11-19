"""Exercise the desk in installed Google Chrome with the optional Playwright test driver."""

import csv
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright


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
                "minutes": "32",
                "minutes_per_grade": "4",
                "expected_rate": "10",
            }.items():
                form.locator(f"[name={name}]").fill(value)
            for name, filename in (
                ("population", "population.csv"),
                ("reviewed", "ordinary-reviews.csv"),
            ):
                form.locator(f"[name={name}]").set_input_files(fixture / filename)
            page.locator("#preview").click()
            expect(page.locator("#forecast")).to_contain_text("8 cases fit")
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
            with returned.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, rows[0].keys())
                writer.writeheader()
                writer.writerow(answer)
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
            with page.expect_popup() as event:
                page.locator("#report").click()
            expect(event.value.locator("footer")).to_contain_text(
                "Signed weekly report"
            )
            # The same directory opened with damaged report bytes stays visible and closed for grading.
            saved = next((root / "runs").glob("*/report.html"))
            saved.write_text("unverified report text", encoding="utf-8")
            page.goto(line.split(": ", 1)[1].strip())
            expect(page.locator("#recovery")).to_be_visible()
            expect(page.locator("#history li")).to_contain_text("Unavailable audit")
            expect(page.locator("#history a")).to_have_count(0)
            expect(page.locator("#current")).to_be_hidden()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    with TemporaryDirectory() as root:
        run(Path(root))
