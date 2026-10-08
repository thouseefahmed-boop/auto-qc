import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture(scope="session")
def client():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    schema = "autoqc_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    os.environ["DATABASE_URL"] = make_conninfo(dsn, options=f"-c search_path={schema}")
    os.environ["AUTOQC_WORKER_TOKEN"] = "test-worker-secret"
    from autoqc.api import app
    from fastapi.testclient import TestClient
    try:
        with TestClient(app) as c:
            yield c
    finally:
        assert schema.startswith("autoqc_test_")
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def worker_headers():
    return {"Authorization": "Bearer test-worker-secret"}

