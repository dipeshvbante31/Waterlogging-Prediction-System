"""Operational commands: python -m app.cli [init-db|cleanup]"""
import sys

from app.config import get_settings
from app.container import Container
from app.services.cleanup_service import run_cleanup


def main(argv=None):
    cmd = (argv or sys.argv[1:] or ["help"])[0]
    c = Container(get_settings())
    if cmd == "init-db":
        c.init_schema(); print("schema created (prefer `alembic upgrade head` for PostgreSQL)")
    elif cmd == "cleanup":
        print(run_cleanup(c))
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
