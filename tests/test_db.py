from sqlalchemy import inspect, text

from app.core.db import get_engine, init_db


def test_init_db_adds_new_nullable_columns_to_old_tables():
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE project (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, "
                "status VARCHAR NOT NULL, created_at DATETIME NOT NULL)"
            )
        )
    init_db()
    columns = {c["name"] for c in inspect(engine).get_columns("project")}
    assert {"stage", "error", "error_type", "duration"} <= columns
    init_db()  # idempotent
