import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv()
pool = ConnectionPool(os.getenv("DATABASE_URL", "postgresql://localhost/autoqc"),
                      min_size=1, max_size=12, open=False,
                      kwargs={"row_factory": dict_row}, timeout=10)


def initialize():
    pool.open()
    pool.wait(timeout=20)
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(582904401)")
        conn.execute(Path(__file__).with_name("schema.sql").read_text())

