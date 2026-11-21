"""Start the Telryn CLI after checking its operating-system requirements."""

import sys


def main(argv: list[str] | None = None) -> int:
    if sys.platform not in {"linux", "darwin"}:
        print(
            "Telryn needs Linux or macOS for file locking and private audit storage. "
            "On Windows use WSL 2 or the signed Linux container. "
            "See https://github.com/davidahma/telryn#install",
            file=sys.stderr,
        )
        return 2
    from .cli import main as run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
