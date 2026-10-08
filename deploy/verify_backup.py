"""Restore one backup into a disposable database; production is never replaced."""
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def main():
    dsn = dotenv_values(ROOT / ".env")["DATABASE_URL"]
    db = urlparse(dsn)
    dump = max((ROOT / "backups").glob("autoqc-*.dump"), key=lambda p: p.stat().st_mtime)
    name = "autoqc_restore_check_" + uuid4().hex
    env = os.environ.copy()
    env.update(PGPASSWORD=db.password, PGHOST=db.hostname, PGPORT=str(db.port), PGUSER=db.username)
    env["LD_LIBRARY_PATH"] = str(ROOT / "runtime/pg/usr/lib/x86_64-linux-gnu")
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            binary = ROOT / "runtime/pg/usr/lib/postgresql/16/bin/pg_restore"
            subprocess.run([str(binary), "--exit-on-error", "-d", name, str(dump)], env=env, check=True)
            test_dsn = db._replace(path="/" + name).geturl()
            with psycopg.connect(test_dsn) as restored:
                tables = {r[0] for r in restored.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
                assert tables == {"generations", "testsets", "modes", "benchmarks"}, tables
                counts = {t: restored.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0] for t in sorted(tables)}
            print(f"Backup restore verified: {dump.name}; row counts: {counts}")
        finally:
            # Only the disposable database created above is removed.
            assert name.startswith("autoqc_restore_check_")
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
