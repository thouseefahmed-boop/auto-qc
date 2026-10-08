"""Initialize an isolated user-owned PostgreSQL on H200, without sudo.

Run from ~/autoqc after extracting PostgreSQL Ubuntu packages into runtime/pg.
Existing clusters/configuration are preserved. No source DB is touched.
"""
import os
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PGROOT = ROOT / "runtime" / "pg"
PGBIN = PGROOT / "usr" / "lib" / "postgresql" / "16" / "bin"
DATA = ROOT / "data" / "postgres"


def main():
    (ROOT / "runtime").chmod(0o700)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(PGROOT / "usr" / "lib" / "x86_64-linux-gnu")
    envpath = ROOT / ".env"
    if not envpath.exists():
        dbpassword = secrets.token_urlsafe(32)
        worker_token = secrets.token_urlsafe(48)
        with envpath.open("x") as stream:
            stream.write(f"DATABASE_URL=postgresql://autoqc:{dbpassword}@127.0.0.1:55432/autoqc\n")
            stream.write(f"AUTOQC_WORKER_TOKEN={worker_token}\n")
            stream.write("ARTIFACTS_BASE=http://rtx-1.dev.internal:8090/artifacts-service\nARTIFACT_TENANT=Flipkart\nMEDIA_VERIFY_TLS=true\n")
        envpath.chmod(0o600)
    from dotenv import dotenv_values
    from urllib.parse import urlparse
    config = dotenv_values(envpath)
    password = urlparse(config["DATABASE_URL"]).password
    if not (DATA / "PG_VERSION").exists():
        DATA.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        pwfile = ROOT / "runtime" / "init-password"
        pwfile.touch(mode=0o600, exist_ok=False)
        try:
            pwfile.write_text(password + "\n")
            subprocess.run([str(PGBIN / "initdb"), "-D", str(DATA), "-L", str(PGROOT / "usr/share/postgresql/16"),
                            "-U", "autoqc", "--auth-local=scram-sha-256", "--auth-host=scram-sha-256",
                            "--pwfile", str(pwfile), "--encoding=UTF8", "--locale=C.UTF-8"], env=env, check=True)
        finally:
            pwfile.unlink(missing_ok=True)
    # Enforce password authentication on this generated, dedicated cluster,
    # including a cluster initialized by an earlier bootstrap invocation.
    import re
    hba = DATA / "pg_hba.conf"
    hba.write_text(re.sub(r"^(local\s+\S+\s+\S+\s+)trust\s*$", r"\1scram-sha-256", hba.read_text(), flags=re.MULTILINE))
    unitdir = Path.home() / ".config" / "systemd" / "user"
    unitdir.mkdir(parents=True, exist_ok=True)
    for unit in ("autoqc-db.service", "autoqc-api.service", "autoqc-backup.service", "autoqc-backup.timer"):
        target = unitdir / unit
        if target.exists() and "AutoQC" not in target.read_text():
            raise RuntimeError(f"Refusing to replace unexpected unit {target}")
        shutil.copyfile(ROOT / "deploy" / unit, target)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "autoqc-db"], check=True)
    import time
    import psycopg
    for attempt in range(30):
        try:
            dburl = urlparse(config["DATABASE_URL"])._replace(path="/postgres").geturl()
            with psycopg.connect(dburl, autocommit=True) as conn:
                if not conn.execute("SELECT 1 FROM pg_database WHERE datname='autoqc'").fetchone():
                    conn.execute("CREATE DATABASE autoqc")
            break
        except psycopg.OperationalError:
            if attempt == 29:
                raise
            time.sleep(1)
    subprocess.run(["systemctl", "--user", "enable", "--now", "autoqc-api"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "autoqc-backup.timer"], check=True)
    print("AutoQC services started. Credentials are private in .env; no source DB was modified.")


if __name__ == "__main__":
    main()
