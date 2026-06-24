"""ToSMod command-line entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cmd_serve(args: argparse.Namespace) -> int:
    host = args.host
    port = args.port
    # Run Flask app from project root so paths resolve correctly.
    code = (
        f"import sys; sys.path.insert(0, r'{ROOT}'); "
        f"from dashboard.app import app; "
        f"app.run(host={host!r}, port={port}, debug={args.debug})"
    )
    return subprocess.call([sys.executable, "-c", code], cwd=str(ROOT))


def _cmd_seed(_args: argparse.Namespace) -> int:
    from tosmod.seed import main as seed_main
    seed_main()
    return 0


def _cmd_test(_args: argparse.Namespace) -> int:
    return subprocess.call([sys.executable, "-m", "pytest", "tests", "-q"], cwd=str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tosmod", description="ToSMod research workbench")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the annotation dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5050)
    serve.add_argument("--debug", action="store_true")
    serve.set_defaults(func=_cmd_serve)

    seed = sub.add_parser("seed", help="Load synthetic demo data into data/tosmod.db")
    seed.set_defaults(func=_cmd_seed)

    test = sub.add_parser("test", help="Run pytest suite")
    test.set_defaults(func=_cmd_test)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
