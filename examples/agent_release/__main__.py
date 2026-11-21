"""Exercise the public seed command against two synthetic agent revisions."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    here = Path(__file__).resolve().parent
    suite = json.loads((here / "suite.json").read_text())
    with TemporaryDirectory() as temp:
        tmp = Path(temp)
        for revision, expected in (("demo-prompt-v1", 1), ("demo-prompt-v2", 0)):
            suite["agent_revision"] = revision
            plan = tmp / "suite.json"
            plan.write_text(json.dumps(suite))
            bridge = str(here / "bridge.py")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "telryn",
                    "seed",
                    str(plan),
                    "--run-id",
                    revision,
                    "--agent-adapter",
                    bridge,
                    "--register-adapter",
                    bridge,
                ],
                env={
                    **os.environ,
                    "TELRYN_SEED_DEMO_DB": str(tmp / "lab.db"),
                    "TELRYN_DEMO_AGENT_REVISION": revision,
                },
                capture_output=True,
                text=True,
            )
            print(f"Synthetic workspace · {revision}", flush=True)
            print(result.stdout, end="")
            if result.returncode != expected:
                sys.exit(result.stderr or "Unexpected release result")


if __name__ == "__main__":
    main()
