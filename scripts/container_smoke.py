"""Exercise the shipped image with private volumes and restricted network access."""

import http.client
from contextlib import closing
import json
import re
import secrets
import socket
import subprocess
import sys
import time


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main(image):
    prefix = "telryn-smoke-" + secrets.token_hex(6)
    data = prefix + "-data"
    trust = prefix + "-trust"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = str(probe.getsockname()[1])
    security = [
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=128m",
    ]
    try:
        docker("volume", "create", data)
        docker("volume", "create", trust)
        common = [*security, "-v", data + ":/data", "-v", trust + ":/trust"]
        offline = ["run", "--rm", "--network", "none", *common]
        identity = docker(
            *offline,
            "--entrypoint",
            "python",
            image,
            "-I",
            "-c",
            "import boto3, os, site; from zoneinfo import ZoneInfo; ZoneInfo('Europe/London'); assert site.ENABLE_USER_SITE is False; print(os.getuid())",
        )
        assert identity == "65532"
        # Exercise the installed AWS SDK and the complete release on the image interpreter.
        docker(*offline, image, "qualify")
        docker(
            *offline,
            image,
            "weekly",
            "examples/fintech/week.toml",
            "--out",
            "/data/runs/first",
        )
        summary = json.loads(
            docker(
                *offline,
                image,
                "weekly",
                "/data/runs/first/report.html",
                "--format",
                "json",
            )
        )
        assert summary["verification"] == "team_hmac_verified"
        docker("network", "create", prefix)
        docker(
            "run",
            "-d",
            "--name",
            prefix,
            "--network",
            prefix,
            *common,
            "-p",
            f"127.0.0.1:{port}:{port}",
            image,
            "desk",
            "--no-browser",
            "--out",
            "/data/runs",
            "--container-port",
            port,
        )
        for _ in range(100):
            match = re.search(r"/([0-9a-f]{64})/", docker("logs", prefix))
            if match:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("container desk did not become ready")
        with closing(
            http.client.HTTPConnection("127.0.0.1", int(port), timeout=15)
        ) as conn:
            conn.request("GET", "/" + match[1] + "/")
            response = conn.getresponse()
            assert response.status == 200 and response.getheader(
                "Content-Security-Policy"
            )
            assert b"Telryn" in response.read()
        print(
            "Verified nonroot weekly audit, signed readback with networking disabled, and loopback-published desk."
        )
    finally:
        for args in [
            ("rm", "-f", prefix),
            ("network", "rm", prefix),
            ("volume", "rm", data),
            ("volume", "rm", trust),
        ]:
            subprocess.run(
                ["docker", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )


if __name__ == "__main__":
    main(sys.argv[1])
