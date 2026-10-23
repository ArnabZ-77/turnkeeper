"""init_db() must bring an older file database up to the current models (additive column upgrade)."""

from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine

from app.db import upgrade_schema


def test_upgrade_schema_adds_missing_columns(tmp_path):
    import app.models  # noqa: F401

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    SQLModel.metadata.create_all(eng)
    with eng.begin() as conn:  # simulate a database created before `last_attempt_at` and `label` existed
        conn.exec_driver_sql("ALTER TABLE contributions DROP COLUMN last_attempt_at")
        conn.exec_driver_sql("ALTER TABLE contributions DROP COLUMN label")
    assert "last_attempt_at" not in {c["name"] for c in inspect(eng).get_columns("contributions")}

    added = upgrade_schema(eng)
    assert sorted(added) == ["contributions.label", "contributions.last_attempt_at"]
    cols = {c["name"]: c for c in inspect(eng).get_columns("contributions")}
    assert "last_attempt_at" in cols and "label" in cols and cols["last_attempt_at"]["nullable"]
    assert upgrade_schema(eng) == []  # idempotent
    eng.dispose()
