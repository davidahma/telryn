"""Exercise a Telryn distribution using its own interpreter outside the checkout."""

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
        env = {**os.environ, "TELRYN_TRUST_DIR": str(work / "private")}
        env.pop("PYTHONPATH", None)

        def run(*args):
            return subprocess.check_output(
                [binary, *args], cwd=work, env=env, text=True, timeout=180
            )

        assert run("--version").strip() == version
        packs = {row["reference"] for row in json.loads(run("pack", "list"))}
        assert packs == {
            name + "@1.0.0"
            for name in ("firm-example", "fca-disp", "us-reg-e", "cfpb-company-portal")
        }
        run("demo", "--out", "refunds")
        run("demo", "--complaints", "--out", "complaints")
        report = work / "complaints/2026-10-05/report.html"
        assert "C108" in report.read_text(encoding="utf-8")
        plan = installed / "_bundled/examples/fintech/week.toml"
        run("weekly", plan, "--out", "audit")
        assert (work / "audit/grades.csv").is_file()
        state = json.loads(run("weekly", "audit/report.html", "--format", "json"))
        assert state["verification"] == "team_hmac_verified"
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
