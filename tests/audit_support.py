"""Keep trust keys created by qualification inside each test's temporary workspace."""

from pathlib import Path
from unittest.mock import patch


def isolate_trust(test, base):
    return test.enterContext(
        patch.dict("os.environ", {"TELRYN_TRUST_DIR": str(Path(base) / "audit-trust")})
    )
