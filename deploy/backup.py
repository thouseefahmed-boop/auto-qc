"""Consistent PostgreSQL backups. Does not remove old backups automatically."""
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = dotenv_values(ROOT / ".env")
    directory = ROOT / "backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"autoqc-{stamp}.dump"
    from urllib.parse import urlparse
    db = urlparse(config["DATABASE_URL"])
    env = os.environ.copy()
    env.update(PGPASSWORD=db.password, PGHOST=db.hostname, PGPORT=str(db.port), PGUSER=db.username)
    env["LD_LIBRARY_PATH"] = str(ROOT / "runtime/pg/usr/lib/x86_64-linux-gnu")
    binary = ROOT / "runtime/pg/usr/lib/postgresql/16/bin/pg_dump"
    # Password is not passed through argv or emitted to logs.
    subprocess.run([str(binary), "-Fc", "-f", str(target), db.path.lstrip("/")], env=env, check=True)
    target.chmod(0o600)
    print(f"Backup saved: {target}")


if __name__ == "__main__":
    main()

