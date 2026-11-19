"""Exercise the desk in installed Google Chrome with the optional Playwright test driver."""

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
            assert "screen_flag" not in packet.read_text(encoding="utf-8-sig")
            with page.expect_popup() as event:
                page.locator("#report").click()
            expect(event.value.locator("footer")).to_contain_text(
                "Signed weekly report"
            )
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    with TemporaryDirectory() as root:
        run(Path(root))
